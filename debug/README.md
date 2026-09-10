# debug/

Diagnostic tooling for the litellm proxy. Not loaded by default.

## task_dump_hook.py — on-demand asyncio task stack dumper

Finds where a hanging request's task is suspended, with frame locals
(exception, retry counters, request data). Built 2026-09 while debugging a
9999-retry loop on context-window errors (see "Why this exists" below).

### What it does

A `CustomLogger` subclass whose **import side effect** starts a daemon
watcher thread. The logger methods are no-ops; all work happens in the
thread, on file triggers. For each asyncio task it walks the full coroutine
chain (`cr_frame` + `cr_await`, down to the innermost await) and writes it
to a file. In live mode it also prints selected frame locals for frames
inside `site-packages/litellm/` — including the request `data` dict keys.

Every failure inside a dump is appended to the err file. Nothing is
silently swallowed.

### Files (inside the container)

| File                    | Purpose                                            |
|-------------------------|----------------------------------------------------|
| `/tmp/.taskdump_trigger`  | input: touch = 1 dump; `echo N` = N dumps, 5s apart |
| `/tmp/.taskdump_live`     | input: marker; dumps every ~2s while present         |
| `/tmp/.taskdump.txt`      | output: full chain dump (trigger mode)               |
| `/tmp/.taskdump_live.txt` | output: chain + locals (live mode)                   |
| `/tmp/.taskdump_err.txt`  | tracebacks of any internal dump failure              |

### Usage

1. Copy `task_dump_hook.py` into the callbacks mount (or mount it at
   `/app/task_dump_hook.py`, read-only), and add to config:

   ```yaml
   litellm_settings:
     callbacks:
       - "task_dump_hook.task_dump_hook"
   ```

   Recreate the container (`podman compose up -d --force-recreate litellm`).

2. While the request is in flight (from the host):

   ```sh
   podman exec litellm touch /tmp/.taskdump_trigger      # single dump
   podman exec litellm sh -c 'echo 6 > /tmp/.taskdump_trigger'  # 6 dumps, 5s apart
   podman exec litellm touch /tmp/.taskdump_live         # start live mode
   podman exec litellm sh -c 'cat /tmp/.taskdump.txt'
   podman exec litellm rm /tmp/.taskdump_live            # stop live mode
   ```

3. Read the task whose chain shows `router.py:2355 in acompletion` — that is
   the request. The `AWAITING:` line is the suspension point; the `LOCALS`
   lines show retry state / exception / request data.

4. Remove the config line + mount, recreate, when done.

### Caveats

- Python 3.13 `asyncio.get_stack()` / `print_task` don't return full chains
  — hence the manual `cr_frame`/`cr_await` walk.
- A full dump does a `gc.get_objects()` scan (~1-2s, GIL held per frame
  inspection) — fine interactively, keep live mode short.
- `f_locals` on an async frame are valid (frame is live); values are
  truncated (300 chars, 400 for exceptions) to keep the file small.

### Why this exists

A request with a payload over the model context window hung "forever"
(~7 days). Chain of cause:

1. DB `LiteLLM_Config.router_settings` carried
   `model_group_retry_policy."qwen3.6-27b"` with **all** error types set to
   9999 (stale config; `qwen3.6-27b` is only an alias of `qwen3.8-27b`).
2. The retry-policy lookup keys on the **raw client-sent model string,
   before alias resolution** — so the alias key matched.
3. Context-window error is a `BadRequestError` → `BadRequestErrorRetries=9999`
   → `num_retries=9999` and `_retry_policy_applies=True`.
4. `_retry_policy_applies=True` skips the "non-retryable error" gate
   (`should_retry_this_error`) — litellm design: an explicit retry policy
   wins.
5. Result: the same pre-call-check failure retried ~9999x at ~60s/cycle,
   before vLLM was ever contacted. Overnight `OverflowError: (34, ...)` was
   `pow(2.0, 9999)` inside that loop.

Fix applied (data, not code): removed the stale `model_group_retry_policy`
entries from `LiteLLM_Config` (now `{}`). Lesson: an explicit per-group
retry policy silences the non-retryable-error gate for that group.

This hook is how the stuck task was captured (suspension point at
`router.py:7322` `await asyncio.sleep(_timeout)` inside the retry loop,
with `num_retries=9999`, `e=ContextWindowExceededError ... Got=264311`).
