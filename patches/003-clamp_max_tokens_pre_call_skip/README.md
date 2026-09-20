# 003 — clamp_max_tokens_pre_call_skip

With `enable_pre_call_checks: true`, the router's context-window pre-check
estimates prompt tokens with a generic encoding (no chat template) and can
over-count roughly 2x against a vLLM backend's own tokenizer, rejecting prompts
that actually fit.

Deployments with `clamp_max_tokens` true enforce the window at the backend —
vLLM keeps the prompt whole and clamps the output budget to the remaining
headroom — so the backend tokenizer is the context authority. This patch skips
the estimate for those deployments and lets vLLM's own 400 carry the
authoritative count. A client-sent `clamp_max_tokens` overrides the deployment
default, including `false` (which re-enables the check).

- Base: litellm v1.100.0
- File: `litellm/router.py`
- Patch: `clamp_max_tokens_pre_call_skip.diff` (`patch -p1` / `git apply`,
  from the litellm source root)

## Applying

    git clone --depth 1 --branch v1.100.0 https://github.com/BerriAI/litellm
    cd litellm
    git apply /path/to/003-clamp_max_tokens_pre_call_skip/clamp_max_tokens_pre_call_skip.diff

Then mount the resulting file read-only over the module path in the proxy
image:

    - ./litellm/router.py:/app/.venv/lib/python3.13/site-packages/litellm/router.py:ro

Restart the proxy after mounting — mounted files take effect on container
start only.

## Notes

Adds `Router._deployment_clamp_max_tokens()` (static helper resolving the
client-over-deployment precedence) and threads `request_kwargs` through
`_pre_call_checks_need_token_count` so the token count is skipped entirely —
not just the comparison — for clamped deployments.

`clamp_max_tokens` needs to be in a deployment's `allowed_openai_params` for a
client override to survive litellm's request-parameter allowlist.
