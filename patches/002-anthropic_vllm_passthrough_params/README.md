# 002 — anthropic_vllm_passthrough_params

The `AnthropicMessagesRequest` TypedDict did not include vLLM-specific parameters, so `enable_thinking`, `thinking_token_budget` and friends were stripped from requests going through the Anthropic-format endpoint (`/v1/messages`). This patch adds seven optional fields to the type so those parameters pass through to the vLLM backend.

- Base: litellm v1.97.0 (`anthropic.py` byte-identical to the tag) — created and tested on this version
- `anthropic.py` — full patched file (what gets mounted)
- `anthropic_vllm_passthrough_params.diff` — the patch against the v1.97.0 original

## Docker mount

Mount `anthropic.py` read-only over the types module in the image (see `~/docker/litellm/docker-compose.yml`):

    - ./files/litellm/litellm_types_llms_anthropic.py:/app/.venv/lib/python3.13/site-packages/litellm/types/llms/anthropic.py:ro

The compose file currently references a copy under `~/docker/litellm/files/litellm/`; keep that copy in sync with this file.
