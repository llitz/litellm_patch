# 001 — alias_token_count

Model names created with `model_group_alias` (e.g. `qwen-27b`) were missing `max_input_tokens`/`max_output_tokens` in the `/v1/models` response, because `Router.get_configured_token_limits` only looked up real deployments and never resolved aliases. This 4-line patch resolves the name through `model_group_alias` first, so alias rows publish the token limits of the model they point to.

- Base: litellm v1.97.0 — created and tested on this version (`router.py` byte-identical to the tag)
- `router.py` — full patched file (v1.97.0 diff record; deployment generates the mounted copy from the current patch branch — see `DEVELOP.md`)
- `alias_token_count.diff` — the patch against the v1.97.0 original

## Deploying

Mount the patched copy read-only over the router module in the image (site-packages path depends on the image's Python version):

    - ./router.py:/app/.venv/lib/python3.13/site-packages/litellm/router.py:ro

Restart the litellm service after mounting. See `DEVELOP.md` for our deployment specifics.
