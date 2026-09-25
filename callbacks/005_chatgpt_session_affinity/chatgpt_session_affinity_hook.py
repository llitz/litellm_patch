import hashlib
import json
import re

from litellm.integrations.custom_logger import CustomLogger

# Same shape gate the proxy applies to x-litellm-session-id header values;
# also guarantees the value is safe as an upstream HTTP header.
_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]{8,}$")

# Cap on the prefix material fed to the hash: enough to separate
# conversations, bounded so per-request cost stays negligible.
_PREFIX_CAP = 8192


def _extract_text(content) -> str:
    """Pull text out of a Responses-API input item's content (string or
    list of parts) for prefix hashing."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "\n".join(parts)
    return ""


def _conversation_prefix_hash(data: dict) -> str | None:
    """Deterministic id over the conversation's stable prefix: instructions
    plus the first message item. Both are unchanged on every subsequent turn
    of the same conversation, so the hash is stable across the session."""
    pieces = []
    instructions = data.get("instructions")
    if isinstance(instructions, str) and instructions:
        pieces.append(instructions)
    inp = data.get("input")
    if isinstance(inp, str):
        pieces.append(inp)
    elif isinstance(inp, list):
        for item in inp:
            if isinstance(item, dict) and item.get("type") == "message":
                pieces.append(_extract_text(item.get("content")))
                break
    prefix = "\n".join(pieces)[:_PREFIX_CAP]
    if not prefix.strip():
        return None
    return hashlib.sha256(prefix.encode("utf-8")).hexdigest()


class ChatgptSessionAffinityHook(CustomLogger):
    """Give every /v1/responses request a stable ChatGPT prompt-cache shard id
    without requiring client cooperation.

    The ChatGPT backend partitions its Codex prompt cache by the session_id
    header the chatgpt provider stamps from litellm_params["litellm_session_id"],
    falling back to a per-request uuid4 — so on stock litellm (through
    v1.102.1) every turn lands on a cold replica and the prompt cache never
    hits deterministically.

    Resolution order (first match wins):
      1. explicit x-litellm-session-id header (already stamped by the proxy)
      2. caller-supplied prompt_cache_key (e.g. oh-my-pi sends a stable
         per-conversation key)
      3. sha256 of the conversation's stable prefix (instructions + first
         message) — works for any client that merely replays its history,
         including openwebui and hermes, with no configuration

    Different conversations get different shards; identical prefixes (e.g.
    two chats starting with "hi") share a shard, which is harmless because
    the backend cache is still prefix-keyed within a shard. Upstream
    equivalents: PR #42014 (prompt_cache_key, open) and #37280/#37287
    (derived id, open/closed-unmerged). Drop this hook when #42014 or the
    derived-id feature lands in the deployed image.

    Scope: responses route only; chat-completions traffic (hosted_vllm, zai)
    and the /v1/messages bridge keep their existing session handling.
    """

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        if call_type not in ("responses", "aresponses"):
            return None
        if data.get("litellm_session_id"):
            return None  # explicit header wins
        pck = data.get("prompt_cache_key")
        if isinstance(pck, str) and _SESSION_ID_RE.match(pck):
            data["litellm_session_id"] = pck
            return data
        derived = _conversation_prefix_hash(data)
        if derived:
            data["litellm_session_id"] = derived
            return data
        return None


chatgpt_session_affinity_hook_instance = ChatgptSessionAffinityHook()
