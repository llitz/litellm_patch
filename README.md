# litellm patches

Local modifications to the [LiteLLM](https://github.com/BerriAI/litellm) proxy
image (`ghcr.io/berriai/litellm-non_root:main-stable`), applied by mounting
files into the container instead of maintaining a fork.

**Compatibility: litellm v1.102.1.** Every patch below is a diff against the
stock `v1.102.1` tag and was verified to apply cleanly to it. Per-item notes
state the version each item was originally written and tested against; after an
image upgrade, re-check each patch against the new stock source.

## What's here

Two kinds of modification:

- `patches/` — diffs against litellm source modules, one numbered directory per
  patch. Each directory holds the `.diff` plus a README describing the problem,
  the root cause, and how to apply it.
- `callbacks/` — request-hook files loaded via `litellm_settings.callbacks`,
  one numbered directory per callback. These are complete, self-contained files
  you mount as-is; no litellm source is modified.

## patches/

| Patch | File modified | Base |
|---|---|---|
| `001-alias_token_count` | `litellm/router.py` | v1.97.0, re-verified on v1.102.1 |
| `002-anthropic_vllm_passthrough_params` | `litellm/types/llms/anthropic.py` | v1.97.0, re-verified on v1.102.1 |
| `003-clamp_max_tokens_pre_call_skip` | `litellm/router.py` | v1.100.0, re-verified on v1.102.1 |
| `004-hosted_vllm_keep_reasoning_content` | `litellm/llms/hosted_vllm/chat/transformation.py` | v1.100.0, re-verified on v1.102.1 |

**The four patches are independent.** Each applies cleanly to pristine stock
`v1.102.1` on its own, and applying all four in any order produces identical
output. 001 and 003 both touch `litellm/router.py`, but in disjoint regions
(the `/v1/models` token-limit lookup in `get_model_listing_info` vs the
pre-call-check helpers), so they do not conflict; 002 and 004 each touch their
own file. Apply the subset you need.

To build a patched tree:

    git clone --depth 1 --branch v1.102.1 https://github.com/BerriAI/litellm
    cd litellm
    git apply /path/to/patches/001-alias_token_count/alias_token_count.diff
    git apply /path/to/patches/002-anthropic_vllm_passthrough_params/anthropic_vllm_passthrough_params.diff
    git apply /path/to/patches/003-clamp_max_tokens_pre_call_skip/clamp_max_tokens_pre_call_skip.diff
    git apply /path/to/patches/004-hosted_vllm_keep_reasoning_content/hosted_vllm_keep_reasoning_content.diff

`patch -p1` works equivalently. Then mount the resulting files read-only over
their module paths (see below).

## callbacks/

| Callback | Problem it fixes | Base |
|---|---|---|
| `001_zai_thinking_fix` | `thinking` / `reasoning_effort` dropped for Z.AI models | v1.97.0 |
| `002_team_prompt_params` | per-team system prompt injection + parameter locking | v1.98.0 |
| `003_usage_details_patch` | `cached_tokens` lost on live streaming usage | v1.98.x, verified v1.102.1 |
| `004_cache_hit_usage_details` | usage details lost on cache-hit stream replay | v1.100.0, verified v1.102.1 |
| `005_chatgpt_session_affinity` | ChatGPT prompt cache never hits: per-request random `session_id` shard | v1.102.1 |

`003` and `004` are complementary: 003 covers the live streaming path (usage
arriving from the provider), 004 the cache-hit replay path (usage rebuilt from
the cached dict). Both are needed for full cache information on streaming
responses.

`005_chatgpt_session_affinity` is a self-contained callback — it modifies no
litellm source and needs no mount beyond `/app/callbacks/`. It restores
deterministic ChatGPT prompt-cache hits by assigning a stable per-conversation
`session_id` (explicit header → valid `prompt_cache_key` → sha256 of
`instructions` + first message), scoped to the Responses API route. Enable it
with the registration line in "Deploying callbacks" below and restart; see
`callbacks/005_chatgpt_session_affinity/README.md` for the verification probe.
Drop it when upstream PR #42014 or #37280 lands in the deployed image.

## Mounting

Site-packages path depends on the image's Python version; adjust as needed.

    volumes:
      - ./litellm/router.py:/app/.venv/lib/python3.13/site-packages/litellm/router.py:ro
      - ./litellm/types/llms/anthropic.py:/app/.venv/lib/python3.13/site-packages/litellm/types/llms/anthropic.py:ro
      - ./litellm/llms/hosted_vllm/chat/transformation.py:/app/.venv/lib/python3.13/site-packages/litellm/llms/hosted_vllm/chat/transformation.py:ro
      - ./callbacks/:/app/callbacks/:ro

Mounted files take effect on container start — restart the proxy after changing
them. Mounts shadow the image's files entirely: after pulling a new image,
re-check each patch still applies, or drop the mount if upstream fixed the
issue.

## Deploying callbacks

1. Copy the hook file(s) into a directory mounted at `/app/callbacks/`
   (read-only), keeping flat names.

2. Register each hook in the litellm config. Entries are
   `callbacks.<module>.<instance>` strings, resolved relative to the config
   file's directory:

       litellm_settings:
         callbacks:
           - "callbacks.zai_thinking_hook.zai_thinking_hook_instance"
           - "callbacks.team_prompt_params_hook.team_prompt_params_hook_instance"
           - "callbacks.usage_details_patch.usage_details_patch"
           - "callbacks.cache_hit_details_patch.cache_hit_details_patch"
           - "callbacks.chatgpt_session_affinity_hook.chatgpt_session_affinity_hook_instance"

   No `__init__.py` is needed; each hook file must be self-contained (litellm +
   stdlib imports only, no cross-imports between hook files). Callbacks load at
   startup — restart to activate.

## Notes

- `supported_openai_params` shown by `/v1/model/info` comes from litellm's
  bundled catalog (advisory). Request-parameter forwarding is governed by
  `allowed_openai_params` on each deployment.
- Streaming usage with `cached_tokens` requires clients to send
  `stream_options: {"include_usage": true}`.
- `debug/` holds diagnostic tooling for the proxy (on-demand asyncio task stack
  dumper, streaming-usage repro script). Not loaded by default.

## Disclaimer

These patches were written by local LLMs with only overall guidance on how,
where and what to patch. Use at your own risk.
