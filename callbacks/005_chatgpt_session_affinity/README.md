# 005 — chatgpt_session_affinity

The ChatGPT backend partitions the Codex prompt cache by the `session_id`
header that the chatgpt provider stamps from
`litellm_params["litellm_session_id"]`. Stock litellm (through v1.102.1)
never consults the caller's `prompt_cache_key` there and falls back to a
fresh `uuid4()` per request — so **every turn lands on a cold backend
replica and the prompt cache never hits** (measured: 0% cached on identical
repeat turns; ~1-in-3 replica-collision luck with a large prefix and no
stable id).

This hook assigns a stable session id per conversation with **no client
configuration** — any Responses-API client benefits. Resolution order,
first match wins:

1. explicit `x-litellm-session-id` header (stamped by the proxy; always wins)
2. caller-supplied `prompt_cache_key` (agents like oh-my-pi send a stable
   per-conversation key) — used verbatim if it matches
   `^[a-zA-Z0-9_\-]{8,}$`
3. `sha256` of the conversation's stable prefix (`instructions` + first
   message item). Both are unchanged on every subsequent turn of the same
   conversation, so the hash pins the whole session to one shard.

Different conversations get different shards. Identical prefixes (two chats
both opening with "hi") share a shard — harmless, the backend cache is
prefix-keyed within a shard.

Upstream equivalents, all unmerged as of v1.102.1: PR BerriAI/litellm#42014
(prompt_cache_key → session id), #37280 (derived-id feature), #37287
(closed variant). Drop this hook when one lands in the deployed image.

Scope: `call_type` `responses`/`aresponses` only. Chat-completions traffic
and the `/v1/messages` bridge are untouched. Note the chat-completions →
chatgpt bridge is broken in v1.102.1 (`Unknown items in responses API
response: []`), so Responses-API clients are the only working path for
chatgpt/ models.

Measured behavior (v1.102.1, ~5.7k-token prefix): stable session id →
deterministic turns at ~86% cached input; no stable id → 0/3. The cache
needs a ~4-5k token stable prefix; shorter turns never hit regardless.

- Base: litellm v1.102.1
- `chatgpt_session_affinity_hook.py`

## Deploying

1. Copy `chatgpt_session_affinity_hook.py` into the directory mounted at
   `/app/callbacks/` (read-only), keeping the flat name.

2. Register in the litellm config:

       litellm_settings:
         callbacks:
           - "callbacks.chatgpt_session_affinity_hook.chatgpt_session_affinity_hook_instance"

3. Restart the proxy.

## Verification

Repeat the same request (no session header, no `prompt_cache_key`) with a
≥5k-token stable prefix — turns 2+ must report
`usage.input_tokens_details.cached_tokens > 0` (derived prefix hash pins
the shard). Different conversations show independent warm-up.
