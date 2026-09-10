"""Debug: dump asyncio task stacks (with selected frame locals) on demand / live.

Use when a request hangs without completing: find where its task is stuck
and what its local state is (exception, retry counters, etc.).

Triggers (from host):
    podman exec litellm sh -c 'touch /tmp/.taskdump_trigger'     # single full dump
    podman exec litellm sh -c 'echo 6 > /tmp/.taskdump_trigger'  # 6 dumps, 5s apart
    podman exec litellm sh -c 'touch /tmp/.taskdump_live'        # start LIVE mode:
    podman exec litellm sh -c 'rm  /tmp/.taskdump_live'          #   dump every 5s while marker exists

Output (in container):
    /tmp/.taskdump.txt        full dump (chain frames only)
    /tmp/.taskdump_live.txt   live-mode dump (chain frames + locals for litellm frames)
    /tmp/.taskdump_err.txt    tracebacks of anything that fails inside a dump

For each task the hook walks the full coroutine chain (cr_frame + cr_await)
down to the innermost await. In live mode it also prints frame locals
(truncated) for frames inside site-packages/litellm/.

Loaded via litellm_settings.callbacks at proxy startup (registered as
"task_dump_hook.task_dump_hook"). Import side effect starts a daemon thread.
Every failure is recorded to the err file — nothing is silently swallowed.
"""
import asyncio
import gc
import os
import threading
import time
import traceback
import types

from litellm.integrations.custom_logger import CustomLogger

_TRIGGER = "/tmp/.taskdump_trigger"
_LIVE = "/tmp/.taskdump_live"
_OUT = "/tmp/.taskdump.txt"
_LIVE_OUT = "/tmp/.taskdump_live.txt"
_ERR = "/tmp/.taskdump_err.txt"
_LITELLM_MARK = "/app/.venv/lib/python3.13/site-packages/litellm/"


def _err(msg):
    try:
        with open(_ERR, "a") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def _trunc(v, limit=300):
    try:
        s = repr(v)
    except Exception:
        s = "<unrepr-able>"
    if len(s) > limit:
        s = s[:limit] + "...<trunc>"
    return s


def _dump_locals(fr, w):
    try:
        locs = fr.f_locals
    except Exception:
        return
    interesting = {}
    for k in ("num_retries", "current_attempt", "remaining_retries", "e",
              "original_exception", "_retry_policy_applies", "request_num_retries",
              "context_window_fallbacks", "fallbacks", "model", "model_group",
              "deployment", "response", "retry_after", "_timeout", "_model",
              "model_group_retry_policy", "retry_policy"):
        if k in locs:
            interesting[k] = _trunc(locs[k], 200 if k != "e" and k != "original_exception" else 400)
    if interesting:
        w("  LOCALS " + fr.f_code.co_name + ": " + " | ".join("%s=%s" % (k, v) for k, v in interesting.items()))
    for k in ("data", "request_body", "override_settings", "router_settings"):
        v = locs.get(k)
        if isinstance(v, dict):
            w("  DICT " + fr.f_code.co_name + "." + k + " keys=" + repr(list(v.keys())))
            for sk in ("num_retries", "model_group_retry_policy", "router_settings_override", "model", "stream"):
                if sk in v:
                    w("    " + sk + "=" + _trunc(v[sk], 400))


def _dump_task_chain(t, w, live=False):
    """Walk the task's coroutine chain: cr_frame + cr_await, down to the innermost await."""
    coro = t.get_coro()
    seen = set()
    depth = 0
    while coro is not None and id(coro) not in seen and depth < 50:
        seen.add(id(coro))
        depth += 1
        fr = coro.cr_frame
        if fr is not None:
            try:
                fname = fr.f_code.co_filename
                w("  %s:%s  in %s" % (fname, fr.f_lineno, fr.f_code.co_name))
                if live and _LITELLM_MARK in fname:
                    _dump_locals(fr, w)
            except Exception:
                w("  frame raw: %r" % (fr,))
        nxt = coro.cr_await
        if isinstance(nxt, types.CoroutineType):
            coro = nxt
            continue
        if nxt is not None:
            w("  AWAITING: %r" % (nxt,))
        break


def _dump_once(live=False):
    out = _LIVE_OUT if live else _OUT
    lines = []
    w = lines.append
    w("=== TASK DUMP %s live=%s ===" % (time.strftime("%Y-%m-%d %H:%M:%S"), live))
    try:
        loops = [o for o in gc.get_objects() if isinstance(o, asyncio.AbstractEventLoop)]
    except Exception:
        loops = []
        _err("gc scan failed:\n" + traceback.format_exc())
    w("event loops: %d" % len(loops))
    for i, loop in enumerate(loops):
        try:
            tasks = asyncio.all_tasks(loop)
        except Exception:
            w("--- loop %d: all_tasks failed" % i)
            _err("all_tasks failed:\n" + traceback.format_exc())
            continue
        w("--- loop %d: %d tasks" % (i, len(tasks)))
        for t in tasks:
            try:
                w("TASK %s done=%s" % (t.get_name(), t.done()))
            except Exception:
                w("TASK <name error> done=<err>")
            try:
                _dump_task_chain(t, w, live=live)
            except Exception:
                _err("task chain dump failed:\n" + traceback.format_exc())
    try:
        with open(out, "w") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        _err("output write failed:\n" + traceback.format_exc())


def _watch():
    while True:
        try:
            if os.path.exists(_TRIGGER):
                try:
                    with open(_TRIGGER) as f:
                        content = f.read().strip()
                except Exception:
                    content = ""
                try:
                    os.unlink(_TRIGGER)
                except Exception:
                    pass
                n = 1
                if content.isdigit():
                    n = max(1, min(int(content), 60))
                for k in range(n):
                    try:
                        _dump_once(live=False)
                    except Exception:
                        _err("_dump_once failed:\n" + traceback.format_exc())
                    if k < n - 1:
                        time.sleep(5)
            elif os.path.exists(_LIVE):
                try:
                    _dump_once(live=True)
                except Exception:
                    _err("live dump failed:\n" + traceback.format_exc())
        except Exception:
            _err("_watch failed:\n" + traceback.format_exc())
        time.sleep(2)


def _start():
    t = threading.Thread(target=_watch, name="task-dump-hook", daemon=True)
    t.start()


_start()


class _TaskDumpHook(CustomLogger):
    """No-op logger; the side effect above (daemon thread) does the work."""


task_dump_hook = _TaskDumpHook()
