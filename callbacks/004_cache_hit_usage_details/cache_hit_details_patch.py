"""Fix: cache-hit streaming responses lose prompt/completion tokens details.

Root cause: when litellm serves a CACHED response to a stream=true request,
CachingIntegration._convert_cached_stream_response() rebuilds the stream via
convert_to_streaming_response(_async)(). Those functions re-create the Usage
object copying only completion_tokens / prompt_tokens / total_tokens —
prompt_tokens_details (cached_tokens!) and completion_tokens_details
(reasoning_tokens!) are dropped. Clients then see no cache-hit info and spend
logs under-count. (Non-streaming cache hits are unaffected — they go through
convert_to_model_response_object, which keeps full fidelity.)

Fix: wrap both converters; capture the details from the cached response and
re-attach them to whichever yielded chunk carries the usage (the final replay
slice). Downstream, the 003 usage_details_patch helper then carries them into
the final SSE usage chunk exactly like the live path does.

Pairs with 003_usage_details_patch: that callback covers the live streaming
path (provider chunks); this one covers the cache-hit replay path. Both are
required for full end-to-end cache information on streaming responses.

Loaded via litellm_settings.callbacks at proxy startup.
"""

import litellm.litellm_core_utils.llm_response_utils.convert_dict_to_response as _cdr
import litellm.utils as _utils
from litellm.integrations.custom_logger import CustomLogger
from litellm.types.utils import CompletionTokensDetails, PromptTokensDetailsWrapper

_orig_async = _cdr.convert_to_streaming_response_async
_orig_sync = _cdr.convert_to_streaming_response


def _gv(obj, key):
    """dict.get or getattr — works for dicts and pydantic models alike."""
    return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)


def _coerce_details(obj, cls):
    """Coerce a details object (dict or pydantic model) to `cls`."""
    if obj is None:
        return None
    if isinstance(obj, cls):
        return obj
    if isinstance(obj, dict):
        return cls.model_validate(obj)
    dump = obj.model_dump() if hasattr(obj, "model_dump") else dict(obj)
    return cls.model_validate(dump)


def _capture(response_object):
    """Extract prompt/completion tokens details from the cached response."""
    usage = _gv(response_object, "usage")
    if usage is None:
        return None, None
    pts = _coerce_details(_gv(usage, "prompt_tokens_details"), PromptTokensDetailsWrapper)
    ctd = _coerce_details(_gv(usage, "completion_tokens_details"), CompletionTokensDetails)
    return pts, ctd


def _attach(chunk, pts, ctd):
    """Re-attach details to the chunk that carries usage (final replay slice)."""
    if pts is None and ctd is None:
        return chunk
    usage = getattr(chunk, "usage", None)
    if usage is None:
        return chunk
    if pts is not None and usage.prompt_tokens_details is None:
        usage.prompt_tokens_details = pts
    if ctd is not None and usage.completion_tokens_details is None:
        usage.completion_tokens_details = ctd
    return chunk


def convert_to_streaming_response(response_object=None):
    pts, ctd = _capture(response_object)
    for chunk in _orig_sync(response_object=response_object):
        yield _attach(chunk, pts, ctd)


async def convert_to_streaming_response_async(response_object=None):
    pts, ctd = _capture(response_object)
    async for chunk in _orig_async(response_object=response_object):
        yield _attach(chunk, pts, ctd)


# Patch the source module AND litellm.utils: caching_handler's
# _convert_cached_stream_response() imports both names from litellm.utils
# at call time, and litellm/__init__ lazy-resolves to the source module.
_cdr.convert_to_streaming_response = convert_to_streaming_response
_cdr.convert_to_streaming_response_async = convert_to_streaming_response_async
_utils.convert_to_streaming_response = convert_to_streaming_response
_utils.convert_to_streaming_response_async = convert_to_streaming_response_async


class _CacheHitDetailsPatch(CustomLogger):
    """No-op logger; the patch is applied at module import."""


cache_hit_details_patch = _CacheHitDetailsPatch()
