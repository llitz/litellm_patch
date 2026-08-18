# litellm patches

Local modifications to the litellm proxy image (`ghcr.io/berriai/litellm-non_root:main-stable`), applied by mounting files into the container. The live mounts are declared in `~/docker/litellm/docker-compose.yml`.

**All patches here were created and tested against litellm v1.97.0; both diffs verified to apply cleanly to the v1.97.0 source (`patch -p1`).**

> **Disclaimer:** these patches were written by qwen-3.8-27b with only overall guidance on how/where/what to patch. Use at your own risk.

## patches/

### 001-alias_token_count

Model names created with `model_group_alias` (e.g. `qwen-27b`) were missing `max_input_tokens` in the `/v1/models` response, because the limit lookup only checked real deployments and never resolved aliases. The patch makes the lookup resolve the alias to its target group first, so aliases publish the same token limits as the model they point to.

### 002-anthropic_vllm_passthrough_params

The `AnthropicMessagesRequest` type did not know about vLLM-specific parameters, so requests through the Anthropic-format endpoint (`/v1/messages`) had `enable_thinking` and friends stripped out. The patch adds seven optional fields to the type so those parameters pass through to the vLLM backend.

## callbacks/

### 001_zai_thinking_fix

Z.AI's API does not accept `thinking` and `reasoning_effort` as standard OpenAI request parameters, so litellm silently dropped them. This callback moves both into the raw request body (`extra_body`) just before the call, for Z.AI models only.

## Docker mounts

Each file is mounted read-only over its counterpart inside the image (see `~/docker/litellm/docker-compose.yml`):

| Local file | Mounted at (in container) |
|---|---|
| `patches/001-alias_token_count/router.py` | `/app/.venv/lib/python3.13/site-packages/litellm/router.py` |
| `patches/002-anthropic_vllm_passthrough_params/anthropic.py` | `/app/.venv/lib/python3.13/site-packages/litellm/types/llms/anthropic.py` |
| `callbacks/001_zai_thinking_fix/zai_thinking_hook.py` | `/app/zai_thinking_hook.py` |

The callback is additionally wired up in `litellm-config.yaml` via `litellm_settings.callbacks: ["zai_thinking_hook.zai_thinking_hook_instance"]`.

**When updating the image:** mounted files shadow the image's files entirely. After pulling a new `main-stable`, re-check each patch still applies (or whether upstream fixed it and the mount can be dropped).
