# 001 — alias_token_count

Model names created with `model_group_alias` (e.g. `qwen-27b`) were missing
`max_input_tokens`/`max_output_tokens` in the `/v1/models` response, because
`Router.get_configured_token_limits` only looked up real deployments and never
resolved aliases. This 4-line patch resolves the name through
`model_group_alias` first, so alias rows publish the token limits of the model
they point to.

- Base: litellm v1.100.0
- File: `litellm/router.py`
- Patch: `alias_token_count.diff` (`patch -p1` / `git apply`, from the litellm
  source root)

## Applying

    git clone --depth 1 --branch v1.100.0 https://github.com/BerriAI/litellm
    cd litellm
    git apply /path/to/001-alias_token_count/alias_token_count.diff

Then mount the resulting file read-only over the module path in the proxy
image (site-packages path depends on the image's Python version):

    - ./litellm/router.py:/app/.venv/lib/python3.13/site-packages/litellm/router.py:ro

Restart the proxy after mounting — mounted files take effect on container
start only.

## Notes

`resolve_model_group_alias` lives in `litellm/router_utils/common_utils.py` and
is already imported by `router.py` at v1.100.0, so the patch adds no new
import.
