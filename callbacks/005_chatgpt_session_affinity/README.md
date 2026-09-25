# 005 — chatgpt_session_affinity

The ChatGPT backend partitions the Codex prompt cache by the `session_id`
header that the chatgpt provider stamps from
`litellm_params["litellm_session_id"]`. Stock litellm (through v1.102.1)
never consults the caller's `prompt_cache_key` there and falls back to a
fresh `uuid4()` per request — so **every turn lands on a cold backend
replica and the prompt cache never hits** (measured: 0% cached on
identical repeat turns; ~1-in-3 replica-collision luck with a large prefix
and no stable id).

This hook assigns a stable session id per conversation with **no client
configuration** — openwebui, hermes, oh-my-pi, or raw curl all benefit.
Resolution order, first match wins:

1. explicit `x-litellm-session-id` header (stamped by the proxy; always wins)
2. caller-supplied `prompt_cache_key` (oh-my-pi sends a stable
   per-conversation key) — used verbatim if it matches
   `^[a-zA-Z0-9_\-]{8,}$`
3. `sha256` of the conversation's stable prefix — chat wire: system message
   + first user message; responses wire: `instructions` + first message
   item. Unchanged on every subsequent turn of the same conversation, so
   the hash pins the whole session to one shard.

How the backend actually partitions: the prompt-cache namespace is the
ChatGPT **account**, keyed by exact prefix bytes; `session_id` biases *which
replica* serves the request. A stable per-conversation id therefore keeps a
growing history landing on the replica that already holds its prefix, while
spreading distinct conversations across the fleet. Measured against a single
hardcoded id (4 never-seen conversations sharing a ~5.8k-token prefix, first
turn each): derived hit 4/4 at 5888 cached; hardcoded warmed up only from
conversation 3 (0, 0, 5888, 5888). Per-conversation derivation is never worse
and avoids one replica's LRU thrashing every conversation's accumulated
history. Two conversations with identical prefixes share a shard — harmless.

Upstream equivalents, all unmerged as of v1.102.1: PR #42014
(prompt_cache_key → session id), #37280 (derived-id feature), #37287
(closed variant). Drop this hook when one lands in the deployed image.

Scope: fires as a deployment hook only when the resolved deployment has
`custom_llm_provider == "chatgpt"`, covering both the `/v1/responses` route
and the `/v1/chat/completions` -> responses bridge. Other providers
(hosted_vllm, zai) and the `/v1/messages` bridge pass through untouched.
Bridge clients must stream: non-streaming chat-completions -> chatgpt is
broken upstream (`Unknown items in responses API response: []`, #37039 and
related), so streaming bridge or native Responses are the working paths
for chatgpt/ models.

Measured (v1.102.1, a Codex-class chatgpt model, ~5.7k-token prefix, no client header and
no `prompt_cache_key`): chat-completions bridge 3/3 turns at 4864/5672
(~86%) cached, primary 11/12 live calls at 5888/6231 (~94%); `/v1/responses`
deterministic; before the hook, 0/3 on both routes. Hook cost is ~58us worst
case (8KB hash cap, 20k-token opener) — noise against model TTFT. On
instance failover across different ChatGPT accounts expects exactly one cold
turn per account switch: the id is re-derived at the hop that reaches the
backend, so affinity self-heals. Cache needs a ~4-5k token stable prefix;
shorter turns never hit regardless.

- Base: litellm v1.102.1
- `chatgpt_session_affinity_hook.py`

## Deploying

1. The file is deployed flat into the `/app/callbacks/` mount:

       - ./callbacks/:/app/callbacks/:ro

2. Register in the litellm config:

       litellm_settings:
         callbacks:
           - "callbacks.chatgpt_session_affinity_hook.chatgpt_session_affinity_hook_instance"

3. Restart the litellm service.

## Verification

Two-turn replay with **no** session header and **no** `prompt_cache_key` —
turn 2 must report cached tokens (derived prefix hash pins the shard):

```sh
PREFIX='<~5k tokens of stable text>'
curl -sk https://<proxy>:4000/v1/responses -H "Authorization: Bearer $KEY" \
  -H content-type:application/json \
  -d "{\"model\":\"chatgpt/<model>\",\"input\":[{\"type\":\"message\",\"role\":\"user\",\"content\":[{\"type\":\"input_text\",\"text\":\"$PREFIX question one\"}]}],\"stream\":false}"
# repeat identically: turns 2+ show usage.input_tokens_details.cached_tokens > 0
```

Unit-level checks (run against the file with a stubbed `CustomLogger`):
turn1/turn2 payloads hash identically; different conversations differ;
header > prompt_cache_key > derived precedence holds; derived hex passes
the header-shape gate.
