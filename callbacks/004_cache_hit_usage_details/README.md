# 004_cache_hit_usage_details

## Problem

When litellm's response cache serves a cached response to a `stream=true`
request, the replayed stream's final usage chunk carried no
`prompt_tokens_details` (`cached_tokens`) and no
`completion_tokens_details` (`reasoning_tokens`). Clients saw no cache-hit
information and spend logs under-counted. Non-streaming cache hits were never
affected.

## Root cause

`CachingIntegration._convert_cached_stream_response()` rebuilds the stream via
`convert_to_streaming_response(_async)()`
(`litellm/litellm_core_utils/llm_response_utils/convert_dict_to_response.py`,
verified on v1.100.0). Those converters re-create the `Usage` object copying
only the three scalar fields (`prompt_tokens`, `completion_tokens`,
`total_tokens`) — the detail objects are dropped in the conversion. The cache
store itself keeps full fidelity; the loss happens on the way out.

## Fix

Wrap both converters at import time (loaded via `litellm_settings.callbacks`):
capture the detail objects from the cached response dict and re-attach them to
whichever yielded chunk carries the usage (the final replay slice). Both the
source module and `litellm.utils` are patched because the caching handler
imports the names from `litellm.utils` at call time.

Pairs with `003_usage_details_patch`: 003 covers the live streaming path
(provider chunks); this one covers the cache-hit replay path. Both are
required for full end-to-end cache information on streaming responses.

## Deploying

Deploy the file flat into the callbacks directory mounted at `/app/callbacks/`
(same as the other callbacks). Requires the config entry:

```yaml
litellm_settings:
  callbacks:
    - "callbacks.cache_hit_details_patch.cache_hit_details_patch"
```

Like all mounted callbacks: takes effect on litellm restart only.
