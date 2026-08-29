# 003_usage_details_patch

## Problem

Streaming responses through OpenAI-compatible providers (zai, hosted_vllm) lost
`usage.prompt_tokens_details` (and `completion_tokens_details`) — the fields
carrying `cached_tokens` / `reasoning_tokens`. Non-streaming responses were
correct; streaming usage arrived with locally-recounted token totals and no
cache information, so cache hits were invisible to downstream usage dashboards
("cache read 0") and spend tracking priced cached tokens at full rate.

## Root cause

For OpenAI-compatible providers the streaming chunk's `usage` arrives as the
OpenAI SDK's `CompletionUsage` pydantic object — not a dict, not litellm's
`Usage`. `ChunkProcessor._usage_chunk_calculation_helper`
(`litellm/litellm_core_utils/streaming_chunk_builder_utils.py`, verified on
v1.98.x `main-stable`) only captures detail objects when they are a **dict**
or a **litellm wrapper class**:

```python
if hasattr(usage_chunk, "prompt_tokens_details"):
    if isinstance(usage_chunk.prompt_tokens_details, dict):          # no
        ...
    elif isinstance(usage_chunk.prompt_tokens_details, PromptTokensDetailsWrapper):  # no — it's openai.types.PromptTokensDetails
        ...
    # else: silently dropped
```

Additionally the token-count guards (`"prompt_tokens" in usage_chunk`,
`usage_chunk.get(...)`) are dict operations that no-op on pydantic models, so
`prompt_tokens`/`completion_tokens` fell back to local token counting
(completion tokens counted 0 + reasoning recounted separately, which is how
the bug stayed half-invisible).

## Fix

Monkeypatch at import time (loaded via `litellm_settings.callbacks`):
replaces the helper with a coercion-safe version — attribute access via
`getattr`/`dict.get` and `model_validate` coercion of foreign pydantic detail
objects into litellm's wrapper classes. No behavior change for dict/litellm
inputs; engine/scheduler untouched.

## Verification (2026-08-29, zai glm-5.3 via litellm proxy)

- before: stream call2 `prompt_tokens_details: None`, `completion_tokens: 0`
- after:  stream call2 `cached_tokens: 576` of 621 prompt tokens,
  `completion_tokens: 24`, `reasoning_tokens: 21` — identical to the raw
  upstream usage payload
- spend tracking now prices cached tokens at `cache_read_input_token_cost`

## Deploying

Deploy the file flat into the callbacks directory mounted at `/app/callbacks/`.
Requires the config entry:

```yaml
litellm_settings:
  callbacks:
    - "callbacks.usage_details_patch.usage_details_patch"
```

Like all mounted callbacks: takes effect on litellm restart only.

## Upstream

Trivial to upstream: make `_usage_chunk_calculation_helper` accept any object
with the attributes (or coerce via `model_validate`). Same class of issue may
exist in `calculate_total_usage` in `streaming_handler.py` (same dict-only
guards, guarded there because its inputs are already litellm `Usage`).
