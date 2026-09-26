# 006 — catalog_fix

Litellm's bundled provider catalog (`litellm.model_cost` plus the
per-provider sets in `litellm.models_by_provider`) advertises every model
a provider *could* serve. Subscription plans gate this independently, so
the catalog lists models the account actually rejects: the ChatGPT Codex
backend returns 400 "not supported when using Codex" for several
`chatgpt/` entries, and the z.ai coding endpoint returns error 1220 for
`glm-5-code`. Wildcard deployments (`zai-max/*` -> `zai/*`) enrich
`/model/info` from these structures, so a stale entry surfaces as an
"available" model even though every real request against it fails. The
`openai` provider set is the extreme case: ~201 public-API entries
(babbage, embeddings, gpt-image, …) that this deployment does not serve
at all, yet a key with `openai/*` wildcard access expands to every one of
them.

At import time (before the router loads) this callback prunes the
known-dead keys in `DEAD_BY_PROVIDER` from both structures and empties
the `openai` provider set, so each provider wildcard advertises only
models the plan actually serves. Concrete `openai/<name>` model-group
aliases are unaffected — alias resolution keys off `model_group_alias`,
not this catalog. No pricing impact: deployment costs ride on
`litellm_params`, and the listing's fallback resolution of a model name
to its catalog key only supplies pricing display for entries that remain.

Pruning is global (the per-provider catalog is shared by all
deployments), so only models dead on *every* plan are listed. z.ai models
dead on one key but live on another (`glm-4-32b-0414-128k`,
`glm-4.5-airx`, `glm-4.5-x` -> error 1113 "not in plan") are deliberately
**not** pruned: hiding them would break the endpoint where they work.

The handle `catalog_fix` is a no-op `CustomLogger` instance — the
callback loader requires a CustomLogger instance (same shape as
`003_usage_details_patch`). The patch itself is a pure import-time
side-effect.

## Deploying

1. The file is deployed flat into the `/app/callbacks/` mount:

       - ./callbacks/:/app/callbacks/:ro

2. Register in the litellm config:

       litellm_settings:
         callbacks:
           - "callbacks.catalog_fix.catalog_fix"

3. Restart the litellm service.

## Verification

Repeat a request against a pruned model name (e.g. `chatgpt/gpt-5.2` or
`zai/glm-5-code`) through a wildcard deployment: expect rejection by name
(400 / 1220), not a phantom "available" listing in `/model/info`. A key
with `openai/*` wildcard access must see no openai models. Re-check
`DEAD_BY_PROVIDER` after image upgrades or plan changes — upstream may
add or remove catalog entries, and a pruned model may start responding.

- Base: litellm v1.102.1
- `catalog_fix.py`
