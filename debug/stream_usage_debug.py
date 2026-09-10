"""Debug: trace streaming usage extraction (prompt_tokens_details loss).

Wraps ChunkProcessor._usage_chunk_calculation_helper and
ChunkProcessor.calculate_usage with stderr logging (visible in
`podman logs litellm`, grep STREAM_USAGE_DEBUG).

Purpose: determine where prompt_tokens_details (cached_tokens) is lost on
the is_simple_text_stream fast path (no reasoning_content in deltas).

Loaded via litellm_settings.callbacks — MUST come after
usage_details_patch so it wraps the patched helper.
"""
import sys

from litellm.integrations.custom_logger import CustomLogger
from litellm.litellm_core_utils.streaming_chunk_builder_utils import ChunkProcessor


def _log(msg):
    print(f"STREAM_USAGE_DEBUG {msg}", file=sys.stderr, flush=True)


_orig_helper = ChunkProcessor._usage_chunk_calculation_helper


def _debug_helper(self, usage_chunk):
    try:
        pts = getattr(usage_chunk, "prompt_tokens_details", "<no attr>")
        _log(f"helper IN: type={type(usage_chunk).__name__} pts={repr(pts)[:300]}")
        res = _orig_helper(self, usage_chunk)
        _log(f"helper OUT: pts={repr(res.get('prompt_tokens_details'))[:300]}")
        return res
    except Exception as e:
        _log(f"helper ERR: {e!r}")
        raise


_orig_calc = ChunkProcessor.calculate_usage


def _debug_calc(self, chunks, model, completion_output, messages=None, reasoning_tokens=None):
    res = _orig_calc(self, chunks, model, completion_output, messages, reasoning_tokens)
    n_with_usage = 0
    last_usage_pts = None
    for c in chunks:
        u = None
        if hasattr(c, "usage"):
            u = c.usage
        elif isinstance(c, dict):
            u = c.get("usage")
        if u is not None:
            n_with_usage += 1
            last_usage_pts = getattr(u, "prompt_tokens_details", "MISSING")
    _log(
        f"calculate_usage: chunks={len(chunks)} with_usage={n_with_usage} "
        f"reasoning_tokens={reasoning_tokens!r} last_chunk_pts={repr(last_usage_pts)[:200]} "
        f"-> res_pts={repr(getattr(res, 'prompt_tokens_details', None))[:200]} "
        f"res_ctd={repr(getattr(res, 'completion_tokens_details', None))[:120]}"
    )
    return res


ChunkProcessor._usage_chunk_calculation_helper = _debug_helper
ChunkProcessor.calculate_usage = _debug_calc


class _StreamUsageDebug(CustomLogger):
    """No-op logger; the patches above do the work."""


stream_usage_debug = _StreamUsageDebug()
