# 001 — zai_thinking_fix

Z.AI's OpenAI-compatible API does not accept `thinking` and `reasoning_effort` as standard request parameters, so litellm silently dropped them. This callback (registered via `litellm_settings.callbacks` in `litellm-config.yaml`) moves both into `extra_body` just before the call so they arrive as raw JSON fields, for Z.AI models only.

Created and tested on litellm v1.97.0.

## Docker mount

Mount the hook at the app root and reference it from the config (see `~/docker/litellm/docker-compose.yml`):

    - ./files/litellm/zai_thinking_hook.py:/app/zai_thinking_hook.py:ro

    litellm_settings:
      callbacks:
        - "zai_thinking_hook.zai_thinking_hook_instance"
