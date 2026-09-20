# 002 — anthropic_vllm_passthrough_params

The `AnthropicMessagesRequest` TypedDict did not include vLLM-specific
parameters, so `enable_thinking`, `thinking_token_budget` and friends were
stripped from requests going through the Anthropic-format endpoint
(`/v1/messages`). This patch adds seven optional fields to the type so those
parameters pass through to the vLLM backend.

- Base: litellm v1.100.0
- File: `litellm/types/llms/anthropic.py`
- Patch: `anthropic_vllm_passthrough_params.diff` (`patch -p1` / `git apply`,
  from the litellm source root)

## Applying

    git clone --depth 1 --branch v1.100.0 https://github.com/BerriAI/litellm
    cd litellm
    git apply /path/to/002-anthropic_vllm_passthrough_params/anthropic_vllm_passthrough_params.diff

Then mount the resulting file read-only over the module path in the proxy
image:

    - ./litellm/types/llms/anthropic.py:/app/.venv/lib/python3.13/site-packages/litellm/types/llms/anthropic.py:ro

Restart the proxy after mounting — mounted files take effect on container
start only.

## Notes

Fields added: `chat_template_kwargs`, `enable_thinking`, `preserve_thinking`,
`thinking_token_budget`, `min_p`, `presence_penalty`, `repetition_penalty`. The
type is only a request-shape allowlist; whether a parameter is forwarded is
governed by `allowed_openai_params` on each deployment.
