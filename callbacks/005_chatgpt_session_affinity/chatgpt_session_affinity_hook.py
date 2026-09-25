import hashlib
import re

from litellm.integrations.custom_logger import CustomLogger

# Same shape gate the proxy applies to x-litellm-session-id header values;
# also guarantees the value is safe as an upstream HTTP header.
_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]{8,}$")

# Cap on the prefix material fed to the hash: enough to separate
# conversations, bounded so per-request cost stays negligible.
_PREFIX_CAP = 8192

# call_type values seen by the deployment hook (CallTypes enum members are
# unreliable across versions; compare the string form).
_CALL_TYPES = frozenset({"completion", "acompletion", "responses", "aresponses"})


def _content_text(content) -> str:
    """Text of a chat message content (string or list of parts) or a
    Responses-API input item content."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "\n".join(parts)
    return ""


def _stable_prefix(kwargs: dict) -> str | None:
    """The conversation's prefix material: unchanged on every subsequent turn
    of the same conversation, so its hash pins the session to one shard.

    Chat wire: system message + first user message.
    Responses wire: instructions + first message input item.
    """
    pieces = []
    msgs = kwargs.get("messages")
    if isinstance(msgs, list) and msgs:
        first = msgs[0]
        if isinstance(first, dict) and first.get("role") == "system":
            pieces.append(_content_text(first.get("content")))
        for m in msgs:
            if isinstance(m, dict) and m.get("role") == "user":
                pieces.append(_content_text(m.get("content")))
                break
    else:
        instructions = kwargs.get("instructions")
        if isinstance(instructions, str) and instructions:
            pieces.append(instructions)
        inp = kwargs.get("input")
        if isinstance(inp, str):
            pieces.append(inp)
        elif isinstance(inp, list):
            for item in inp:
                if isinstance(item, dict) and item.get("type") == "message":
                    pieces.append(_content_text(item.get("content")))
                    break
    prefix = "\n".join(p for p in pieces if p)[:_PREFIX_CAP]
    return prefix or None


class ChatgptSessionAffinityHook(CustomLogger):
    """Give chatgpt/ requests a stable ChatGPT prompt-cache shard id without
    requiring client cooperation.

    The ChatGPT backend partitions its Codex prompt cache by the session_id
    header the chatgpt provider stamps from litellm_params["litellm_session_id"],
    falling back to a per-request uuid4 — so on stock litellm (through
    v1.102.1) every turn lands on a cold backend replica and the prompt cache
    never hits deterministically.

    Runs as a deployment hook: it fires only where custom_llm_provider is
    finally known to be "chatgpt" (aliases resolved), covering both the
    /v1/responses route and the /v1/chat/completions -> responses bridge.
    Keys set here land in litellm_params verbatim (llm_http_handler re-split
    logic), which is exactly where get_chatgpt_session_id reads them.

    Resolution order (first match wins):
      1. explicit x-litellm-session-id header (already stamped by the proxy)
      2. caller-supplied prompt_cache_key (e.g. oh-my-pi sends a stable
         per-conversation key), if shape-valid
      3. sha256 of the conversation's stable prefix (system message or
         instructions + first user message/item)

    Upstream equivalents, all unmerged as of v1.102.1: PR #42014
    (prompt_cache_key -> session id), #37280/#37287 (derived id). Drop this
    hook when one lands in the deployed image.
    """

    async def async_pre_call_deployment_hook(self, kwargs, call_type):
        if kwargs.get("custom_llm_provider") != "chatgpt":
            return None
        ct = getattr(call_type, "value", None) or str(call_type)
        if ct not in _CALL_TYPES:
            return None
        if kwargs.get("litellm_session_id"):
            return None  # explicit header wins
        pck = kwargs.get("prompt_cache_key")
        if isinstance(pck, str) and _SESSION_ID_RE.match(pck):
            kwargs["litellm_session_id"] = pck
            return kwargs
        prefix = _stable_prefix(kwargs)
        if prefix:
            kwargs["litellm_session_id"] = hashlib.sha256(prefix.encode("utf-8")).hexdigest()
            return kwargs
        return None


chatgpt_session_affinity_hook_instance = ChatgptSessionAffinityHook()
