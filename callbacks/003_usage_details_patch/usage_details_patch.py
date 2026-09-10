"""Fix: streaming usage drops prompt_tokens_details/completion_tokens_details.

Root cause: for OpenAI-compatible providers (zai, etc.) the streaming chunk's
usage arrives as the OpenAI SDK's CompletionUsage pydantic object.
ChunkProcessor._usage_chunk_calculation_helper only recognizes dict or
litellm-wrapper detail objects, so the SDK's PromptTokensDetails
(cached_tokens!) and CompletionTokensDetails silently fall through — OMP and
spend logs then see cache_read 0 and locally-recounted token totals.

This monkeypatch replaces the helper with a coercion-safe version: attribute
access instead of dict-only guards, and model_validate coercion for foreign
pydantic detail objects. Verified against z.ai: cached 576/620 on second call.

Pairs with 004_cache_hit_usage_details: that callback covers the cache-hit
replay path (litellm response cache serving a cached response to stream=true),
which this one cannot reach — the details are dropped before any chunk gets
here. Both are required for full end-to-end cache information on streaming
responses.

Loaded via litellm_settings.callbacks at proxy startup.
"""
from litellm.integrations.custom_logger import CustomLogger
from litellm.litellm_core_utils.streaming_chunk_builder_utils import ChunkProcessor
from litellm.types.utils import CompletionTokensDetails, PromptTokensDetailsWrapper


def _gv(obj, key):
    """dict.get or getattr — works for dicts and pydantic models alike."""
    return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)


def _coerce_details(obj, cls):
    """Coerce a details object (openai SDK pydantic, dict, or litellm type) to `cls`."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return cls(**obj)
    if isinstance(obj, cls):
        return obj
    dump = obj.model_dump() if hasattr(obj, "model_dump") else dict(obj)
    return cls.model_validate(dump)


def _usage_chunk_calculation_helper(self, usage_chunk):
    return {
        "prompt_tokens": _gv(usage_chunk, "prompt_tokens") or 0,
        "completion_tokens": _gv(usage_chunk, "completion_tokens") or 0,
        "cache_creation_input_tokens": _gv(usage_chunk, "cache_creation_input_tokens"),
        "cache_read_input_tokens": _gv(usage_chunk, "cache_read_input_tokens"),
        "completion_tokens_details": _coerce_details(
            _gv(usage_chunk, "completion_tokens_details"), CompletionTokensDetails
        ),
        "prompt_tokens_details": _coerce_details(
            _gv(usage_chunk, "prompt_tokens_details"), PromptTokensDetailsWrapper
        ),
        "cost": _gv(usage_chunk, "cost"),
    }


ChunkProcessor._usage_chunk_calculation_helper = _usage_chunk_calculation_helper


class _UsageDetailsPatch(CustomLogger):
    """No-op logger; the patch is applied at module import."""


usage_details_patch = _UsageDetailsPatch()
