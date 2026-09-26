# 005 — model_info_access_groups

`GET /model/info` and `/v1/model/info` return `{"data": []}` for a key whose
model access comes only from DB-backed team access groups: the team's `models`
list names the group, and the granted model names live in
`LiteLLM_AccessGroupTable.access_model_names`. `/v1/models` lists the granted
models correctly, so the key works for chat completions while the inventory
endpoints look empty.

Root cause: `_get_v1_model_info_allowed_model_names` in
`litellm/proxy/proxy_server.py` duplicates the `/v1/models` resolver
`get_available_models_for_user` but omits the DB access-group resolution. The
group grants therefore never reach the allowlist, so it contains no deployable
model name and `_filter_v1_model_info_deployments` drops every deployment.

The patch makes the helper async, adds `prisma_client`, `user_api_key_cache`
and `proxy_logging_obj` parameters, and delegates the restricted-caller branch
to `get_available_models_for_user` — the same resolver `/v1/models` uses, so
the two endpoints agree by construction. Unrestricted callers keep the `None`
short-circuit and incur no DB round-trip.

Upstream: issue BerriAI/litellm#41730, fix BerriAI/litellm#41808 —
open/unmerged at apply time (2026-09-25). This is that PR applied source-only;
the upstream test hunks are not vendored.

- Base: litellm v1.102.1
- File: `litellm/proxy/proxy_server.py`
- Patch: `model_info_access_groups.diff` (`patch -p1` / `git apply`,
  from the litellm source root)

## Applying

    git clone --depth 1 --branch v1.102.1 https://github.com/BerriAI/litellm
    cd litellm
    git apply /path/to/005-model_info_access_groups/model_info_access_groups.diff

Then mount the resulting file read-only over the module path in the proxy image:

    - ./litellm/proxy/proxy_server.py:/app/.venv/lib/python3.13/site-packages/litellm/proxy/proxy_server.py:ro

Restart the proxy after mounting — mounted files take effect on container start
only.

## Notes

Independent of patches 001–004: it touches a different file
(`litellm/proxy/proxy_server.py`), so it applies cleanly to pristine stock
v1.102.1 on its own and in any order with the others.

`proxy_server.py` is a large module — mounting it shadows every other local
change to that file, so re-apply them to the patched copy if you have any.

Drop the mount once BerriAI/litellm#41808 merges into the image's litellm
version.
