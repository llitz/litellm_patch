# 002 — team_prompt_params

For callers matched by `team_id` or `key_alias`, this callback (registered via `litellm_settings.callbacks` in `litellm-config.yaml`) prepends a configurable system prompt to chat completion requests and locks selected request parameters (strip, then force) just before the call. Non-matching callers pass through unchanged. The config is a LIST of rules; every rule that matches a request is applied, in list order, until a matching rule with `"final": true` stops the walk (no later rule is then evaluated or applied).

**Deploying:** modify `team_prompt_params.json.sample` to your needs and deploy it as `team_prompt_params.json` (the live config is gitignored — the repo only ships the sample).

## Config file

The hook reads its settings from a JSON file at startup (and on SIGHUP reload):

- **Path**: `$TEAM_PROMPT_PARAMS_CONFIG` environment variable, default `/app/callbacks/team_prompt_params.json`
- **Format** — a top-level JSON ARRAY of rules (all keys optional; in list form a rule's missing key = empty, i.e. no prompt / no strip / no force):

  ```json
  [
    {
      "match_team_ids": [],
      "match_key_aliases": ["dev-key"],
      "extra_system_prompt": "You are a concise assistant. Answer directly.",
      "strip_params": ["temperature", "top_p"],
      "force_params": {}
    },
    {
      "match_team_ids": [],
      "match_key_aliases": ["anotherkey"],
      "extra_system_prompt": "Overthink bro!",
      "strip_params": ["min_p", "top_p"],
      "force_params": {}
    },
    {
      "match_team_ids": ["123e4567-e89b-12d3-a456-426614174000"],
      "match_key_aliases": [],
      "extra_system_prompt": "You are a careful assistant. Double-check your work before answering.",
      "strip_params": [],
      "force_params": {},
      "final": true
    }
  ]
  ```

  The third rule shows `"final": true`: JSON has no comments, so note that for a caller matching that team, its prompt is applied and NO rule after it is evaluated or applied — even rules that would also have matched that caller.

  **Legacy form** — a top-level single OBJECT is still accepted and treated as a one-rule list. Only for this legacy form does a missing key fall back to the `DEFAULT_*` constant in the hook file. The optional `final` key is also accepted there (it is the only rule, so `final: true` is a no-op semantically, but it is parsed and validated).

  JSON has no comments — key semantics:

  | Key | Type | Meaning |
  | --- | --- | --- |
  | `match_team_ids` | list of strings | `team_id` values to match (empty list = no team matches) |
  | `match_key_aliases` | list of strings | `key_alias` values to match (empty list = no alias matches) |
  | `extra_system_prompt` | string | text to PREPEND to the system prompt |
  | `strip_params` | list of strings | request params to delete for matched callers (e.g. `["temperature", "top_p", "frequency_penalty", "presence_penalty"]`) |
  | `force_params` | object | values to set for matched callers, applied after `strip_params` (e.g. `{"temperature": 0.7}`) |
  | `final` | boolean | optional, default `false`. When a rule with `final: true` MATCHES, the rule walk STOPS after it: no later rule is evaluated or applied, even if it would have matched. A non-matching rule with `final: true` changes nothing — `final` only matters on a match. `final` present but not a boolean → invalid config, last-known-good kept (same as other wrong-type keys). Also accepted in the legacy single-object form (no-op there, but parsed/validated) |

A ready-to-edit template is in `team_prompt_params.json.sample` (modify to your needs, deploy as `team_prompt_params.json` in the mounted callbacks dir; the deployed file is gitignored).

**Matching & multi-match semantics**

- A rule matches a request when `team_id` is in its `match_team_ids` OR `key_alias` is in its `match_key_aliases`. A rule with both lists empty never matches.
- ALL matching rules are applied, in list order. For each matching rule, in this order: (1) `extra_system_prompt` is PREPENDED to the system prompt, (2) `strip_params` are popped, (3) `force_params` are set.
- Prepend order: each prepend puts that rule's text at the very front, so with two matching rules R1 then R2 the final system prompt is `R2 text + "\n\n" + R1 text + "\n\n" + original` — the LAST matched rule's text ends up closest to the front.
- `strip_params` are the union across matching rules.
- `force_params`: applied after each rule's own strip, in rule order — a later rule wins on conflicting keys, and a later rule's strip can remove an earlier rule's forced value.
- An empty `extra_system_prompt` / `strip_params` / `force_params` in a matching rule is a no-op for that aspect of that rule.
- `final` semantics: `final` only matters on a MATCH. Position matters — a final rule shadows all rules after it for callers that match it: those later rules are neither evaluated nor applied for such callers (callers that do not match the final rule are unaffected by it).
- An empty array `[]` is valid and means "no rules" — the hook is a no-op.

**Fallback semantics**

- File missing at startup → the `DEFAULT_*` constants in `team_prompt_params_hook.py` are used (info logged once).
- Invalid JSON or wrong types (top-level neither object nor array, array element not an object, wrong types on known keys) → the last-known-good config is kept and an error is logged. The request path never crashes because of config.
- On reload, a missing file is also treated as a failed reload (previous config kept), so deleting the file mid-run does not silently reset to constants.

## Reloading (SIGHUP)

The config file is loaded once at import time (proxy startup). To apply edits without restarting the proxy, send SIGHUP to the litellm process:

```sh
podman kill -s HUP litellm
```

- The hook installs the SIGHUP handler at import time (main thread only; if that ever changes it degrades to a logged warning and startup is unaffected).
- litellm and uvicorn do not claim SIGHUP (uvicorn only handles SIGINT/SIGTERM), so the default action would be process termination — the installed handler prevents that.
- A successful reload logs `reloaded team_prompt_params config from ...`; a failed reload logs `reload failed: ...; keeping previous config`.
- Code changes to the hook itself still require a container restart; SIGHUP only reloads the JSON config.

## Logging

The hook's logger (`litellm.team_prompt_params_hook`) self-attaches a stderr handler at INFO level at import time, with `propagate = False` (same pattern as `uvicorn.access`). Its lines — startup load, SIGHUP reload, per-match audit — are therefore visible in the container log even with `LITELLM_LOG` unset (root logger at WARNING). A guard prevents double-attachment if the module is ever loaded twice.

On every rule match the hook logs ONE INFO audit line per matched rule, e.g.:

    2026-08-26 16:21:58,891 litellm.team_prompt_params_hook INFO: matched key_alias=dev-key rule[0]: stripped=temperature,top_p forced=- prepended_prompt=344ch final=False

- The caller is identified by whichever matched: `team_id=...` and/or `key_alias=...` (both if both matched).
- `stripped` / `forced` are the parameter names (comma-joined, `-` when empty); `prepended_prompt` is the prompt length in characters (`-` when empty); `final` is the rule's final flag.
- The prompt text itself is NEVER logged (length only). Non-matching requests log nothing.

The `DEFAULT_*` constants at the top of `team_prompt_params_hook.py` are the fallback values for keys missing from a LEGACY single-object config (and for the no-file startup case). They are NOT applied to individual rules in the list form:

- `DEFAULT_MATCH_TEAM_IDS` — `team_id` values to match (empty list = no team matches)
- `DEFAULT_MATCH_KEY_ALIASES` — `key_alias` values to match (empty list = no alias matches)
- `DEFAULT_EXTRA_SYSTEM_PROMPT` — text to PREPEND to the system prompt
- `DEFAULT_STRIP_PARAMS` — request params to delete for matched callers (e.g. `["temperature", "top_p", "frequency_penalty", "presence_penalty"]`)
- `DEFAULT_FORCE_PARAMS` — values to set for matched callers, applied after `DEFAULT_STRIP_PARAMS` (e.g. `{"temperature": 0.7}`)

System prompt handling covers: existing system message with string content, system message with list-of-blocks content, no system message (one is inserted at index 0), and the Anthropic `/v1/messages` top-level `system` (string or list of blocks).

## Docker mount

Deploy the hook flat into the callbacks directory that is mounted read-only at `/app/callbacks/`:

    - ./callbacks/:/app/callbacks/:ro

Copy `team_prompt_params_hook.py` (and your `team_prompt_params.json`) into that mounted directory. Wire it IN ADDITION to the existing 001 callback (`callbacks` is a list — both hooks run; the entry must be the module path to the INSTANCE, not the class; module paths are relative to the config file's directory):

    litellm_settings:
      callbacks:
        - "callbacks.zai_thinking_hook.zai_thinking_hook_instance"
        - "callbacks.team_prompt_params_hook.team_prompt_params_hook_instance"
