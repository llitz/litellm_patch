# 001 — zai_thinking_fix

Z.AI's OpenAI-compatible API does not accept `thinking` and `reasoning_effort` as standard OpenAI request parameters, so litellm silently dropped them. This callback moves both into `extra_body` just before the call so they arrive as raw JSON fields, for Z.AI models only.

Created and tested on litellm v1.97.0.

## Deploying

1. Copy `zai_thinking_hook.py` into a directory mounted read-only at `/app/callbacks/`.

2. Reference it in the litellm config — the module path is relative to the config file's directory (`/app/config.yaml` → `/app/callbacks/`):

       litellm_settings:
         callbacks:
           - "callbacks.zai_thinking_hook.zai_thinking_hook_instance"

Restarts required to load; see `DEVELOP.md` at the repo root for our deployment specifics.
