# litellm patches

Local modifications to the [LiteLLM](https://github.com/BerriAI/litellm) proxy
image (`ghcr.io/berriai/litellm-non_root:main-stable`), applied by mounting
files into the container instead of maintaining a fork.

**Target: litellm v1.100.0.** Per-item notes below state the version each
item was originally written/tested against; after an image upgrade, re-check
each patch against the new stock source (see "Using the patches").

## What's here

Two kinds of modifications:

- `patches/` — full-file overrides of litellm modules, one numbered directory
  per patch (patched file + a `.diff` against the stock version).
- `callbacks/` — request-hook files loaded via `litellm_settings.callbacks`,
  one numbered directory per callback.

### patches/

#### 001-alias_token_count

Model names created with `model_group_alias` (e.g. `qwen-27b`) were missing
`max_input_tokens`/`max_output_tokens` in the `/v1/models` response, because
the limit lookup only checked real deployments and never resolved aliases.
A 4-line patch resolves the alias to its target group first, so alias rows
publish the token limits of the model they point to. Base: litellm v1.97.0.

#### 002-anthropic_vllm_passthrough_params

The `AnthropicMessagesRequest` type did not know about vLLM-specific
parameters, so requests through the Anthropic-format endpoint (`/v1/messages`)
had `enable_thinking` and friends stripped out. Adds seven optional fields to
the type so those parameters pass through to the vLLM backend.
Base: litellm v1.97.0.

### callbacks/

#### 001_zai_thinking_fix

Z.AI's OpenAI-compatible API does not accept `thinking` and `reasoning_effort`
as standard OpenAI request parameters, so litellm silently dropped them. This
callback moves both into the raw request body (`extra_body`) just before the
call, for Z.AI models only. Tested on litellm v1.97.0.

#### 002_team_prompt_params

For callers matched by `team_id` or `key_alias`, prepends a configured system
prompt to chat completion requests and locks selected request parameters
(strip, then force) just before the call. Non-matching callers pass through
unchanged. Config is a JSON list of rules, hot-reloadable on SIGHUP. See
`team_prompt_params.json.sample`. Tested on litellm v1.98.0.

#### 003_usage_details_patch

Streaming usage from OpenAI-compatible providers (zai, hosted_vllm) arrived
as the OpenAI SDK's `CompletionUsage` pydantic object, which litellm's
`ChunkProcessor._usage_chunk_calculation_helper` cannot read (dict-only
guards) — `prompt_tokens_details.cached_tokens` was silently dropped and token
counts fell back to local recounting. This callback monkeypatches the helper
with a coercion-safe version at import time, restoring cache-read reporting
and correct token counts on streaming responses. Tested on `main-stable`
2026-08 (v1.98.x); also verified on v1.100.0 (2026-09).

Covers the **live** streaming path only (usage arriving from the provider).
Pairs with `004_cache_hit_usage_details`, which covers the **cache-hit**
replay path. Both are required for full end-to-end cache information on
streaming responses.

#### 004_cache_hit_usage_details

When litellm's response cache serves a cached response to a `stream=true`
request, `convert_to_streaming_response(_async)` (litellm
`litellm_core_utils/llm_response_utils/convert_dict_to_response.py`) rebuilds
the stream's `Usage` from only three scalar fields (`prompt_tokens`,
`completion_tokens`, `total_tokens`) — `prompt_tokens_details.cached_tokens`
and `completion_tokens_details` (reasoning tokens) were silently dropped, so
cache-hit streams carried no cache information on the wire. The cache store
itself keeps full fidelity; the loss happens in the re-stream conversion.
This callback wraps both converters at import time and re-attaches the detail
objects from the cached dict to the usage-carrying chunk (the final replay
slice). Non-streaming cache hits were never affected (full fidelity via
`convert_to_model_response_object`). Tested against the running container
build, source reference v1.100.0 (2026-09).

**Pairs with `003_usage_details_patch`**: 003 restores details on the live
streaming path (provider chunks); 004 on the cache-hit replay path (cached
dict). Both are needed for full end-to-end cache information (e.g. cached
prompt tokens / CH% in clients) on streaming responses.

## Using the patches

### Mounting full-file overrides

Mount each patched file read-only over its module path inside the image
(adjust the site-packages path to your image's Python version):

    - ./router.py:/app/.venv/lib/python3.13/site-packages/litellm/router.py:ro
    - ./anthropic.py:/app/.venv/lib/python3.13/site-packages/litellm/types/llms/anthropic.py:ro

Mounted files take effect on container start — restart the service after
changing them. Mounts shadow the image's files entirely: after pulling a new
image, re-check each patch still applies, or drop the mount if upstream fixed
the issue. Each patch directory's `.diff` is the authoritative record of what
was changed.

### Deploying callbacks

1. Copy the hook file(s) into a directory mounted at `/app/callbacks/`
   (read-only), keeping flat names:

       - ./callbacks/:/app/callbacks/:ro

2. Register each hook in the litellm config. Entries are
   `callbacks.<module>.<instance>` strings, resolved relative to the config
   file's directory:

       litellm_settings:
         callbacks:
           - "callbacks.zai_thinking_hook.zai_thinking_hook_instance"
           - "callbacks.team_prompt_params_hook.team_prompt_params_hook_instance"
           - "callbacks.usage_details_patch.usage_details_patch"

   No `__init__.py` is needed; each hook file must be self-contained (litellm
   + stdlib imports only). Callbacks load at startup — restart to activate.

### Notes

- `supported_openai_params` shown by `/v1/model/info` comes from litellm's
  bundled catalog (advisory). Request-parameter forwarding is governed by
  `allowed_openai_params` on each deployment.
- Streaming usage with `cached_tokens` requires clients to send
  `stream_options: {"include_usage": true}`.

## Disclaimer

These patches were written by local LLMs with only overall guidance on how,
where and what to patch. Use at your own risk.
