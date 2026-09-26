"""Prune provider-catalog entries the subscription backends do not serve.

Litellm's bundled catalog (``litellm.model_cost`` and the per-provider sets in
``litellm.models_by_provider``) advertises every model a provider *could* serve.
Subscription plans gate this independently, so the catalog lists models the
account actually rejects: the ChatGPT Codex backend returns 400 "not supported
when using Codex" for several ``chatgpt/`` entries, and the z.ai coding endpoint
returns 1220 "you do not have permission to access glm" for ``glm-5-code``.

Wildcard deployments (``zai-max/*`` -> ``zai/*``, ``zai-lite/*`` -> ``zai/*``)
enrich ``/model/info`` from these structures, so a stale entry surfaces as an
"available" model even though every real request against it fails.

This callback removes the known-dead entries from both structures at import time
(before the router loads), so each provider wildcard advertises only models the
plan actually serves.

It does NOT touch pricing: deployment costs are carried on ``litellm_params``
(see ``generate_models.py``), and the listing's fallback resolution of a model
name to its bare/``<provider>/`` catalog key is what supplies pricing display.
Popping catalog keys here would suppress that fallback, so only entries the plan
genuinely rejects are pruned.

Scope limits:
- Pruning is global (per-provider catalog, shared by all deployments), so a model
  pruned here disappears from every endpoint that consumes that provider. Only
  models dead on *every* plan are listed. z.ai models dead on the MAX key but
  live on the LITE key (``glm-4-32b-0414-128k``, ``glm-4.5-airx``, ``glm-4.5-x``
  -> error 1113 "not in plan") are deliberately NOT pruned: they are genuine
  models on one plan, and hiding them would break the endpoint where they work.

Validated against litellm v1.102.1 (2026-09) and probed against the live backends
2026-09-24. Re-check ``DEAD_BY_PROVIDER`` after image upgrades or plan changes —
upstream may add or remove catalog entries, and a pruned model may start responding.
"""

import litellm
from litellm.integrations.custom_logger import CustomLogger

# provider -> list of "<provider>/<model>" catalog keys the subscription backends
# reject on every plan.
DEAD_BY_PROVIDER: dict[str, list[str]] = {
    # Codex backend rejects these (400 "not supported when using Codex"), probed
    # 2026-08-28. The served gpt-5.5 / gpt-5.6-* / gpt-6-astra family is NOT
    # listed here — those are real deployments, not catalog phantoms.
    "chatgpt": [
        "chatgpt/gpt-5.1-codex-max",
        "chatgpt/gpt-5.1-codex-mini",
        "chatgpt/gpt-5.2",
        "chatgpt/gpt-5.2-codex",
        "chatgpt/gpt-5.3-chat-latest",
        "chatgpt/gpt-5.3-codex",
        "chatgpt/gpt-5.3-codex-spark",
        "chatgpt/gpt-5.3-instant",
        "chatgpt/gpt-5.4",
        "chatgpt/gpt-5.4-pro",
    ],
    # z.ai coding endpoint rejects this (1220 no permission), MAX and LITE both,
    # probed 2026-09-24.
    "zai": [
        "zai/glm-5-code",
    ],
}

for _provider, _dead in DEAD_BY_PROVIDER.items():
    for _key in _dead:
        litellm.model_cost.pop(_key, None)
    _set = litellm.models_by_provider.get(_provider)
    if _set is not None:
        for _key in _dead:
            _set.discard(_key)

# The OpenAI provider's bundled catalog (~201 entries: babbage, embeddings,
# gpt-image, gpt-4o-mini, …) describes OpenAI's *public API*, not this
# deployment. We serve nothing directly from provider `openai`: the only
# `openai/…` names that must resolve are the explicit `openai/gpt-*` ->
# `chatgpt/gpt-*` model-group aliases, which map straight onto real `chatgpt/*`
# deployments and never consult this set. But a request whose model access
# contains the provider wildcard `openai/*` is expanded by the router against
# ``litellm.models_by_provider["openai"]`` with no entitlement check, so every
# catalog member shows up as an "available" model that 400s on call. Emptying
# the set removes that phantom surface outright; concrete `openai/<name>`
# aliases are unaffected because alias resolution keys off ``model_group_alias``,
# not this catalog.
if litellm.models_by_provider.get("openai") is not None:
    litellm.models_by_provider["openai"] = set()

# Litellm callback loader requires a CustomLogger instance.
# This callback is pure side-effect (catalog pruning at import time).
class _CatalogFix(CustomLogger):
    """No-op logger; the patch is applied at module import."""


catalog_fix = _CatalogFix()
