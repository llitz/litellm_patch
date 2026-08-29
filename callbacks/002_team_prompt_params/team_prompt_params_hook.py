"""
002 — team_prompt_params

Proxy-only request-level callback. For callers matched by team_id or
key_alias it prepends a configured system prompt to the system prompt of
chat completion requests and locks selected request parameters (strip,
then force). Non-matching callers pass through unchanged.

Config file
-----------
Values are read from a JSON file (stdlib json only, no extra deps):

    path: $TEAM_PROMPT_PARAMS_CONFIG
          (default: /app/callbacks/team_prompt_params.json)

Primary form: a top-level ARRAY of rules, walked in list order:

    [
      {
        "match_team_ids":    [],
        "match_key_aliases": ["dev-key"],
        "extra_system_prompt": "text to prepend",
        "strip_params":      ["temperature", "top_p"],
        "force_params":      {"temperature": 0.7},
        "final":             false
      },
      ...
    ]

Matching & application
----------------------
A rule matches a request when team_id is in its match_team_ids OR
key_alias is in its match_key_aliases (a rule with both lists empty
never matches). ALL matching rules are applied in list order; for each
matching rule, in this order:

  1. extra_system_prompt is PREPENDED to the system prompt,
  2. strip_params are popped from the request,
  3. force_params are set (after this rule's strip).

A matching rule with "final": true STOPS the walk after it is applied:
no later rule is evaluated or applied, even if it would have matched.
"final": false or an absent "final" continues the walk (the default).
A non-matching rule with "final": true changes nothing — final only
matters on a match. Position matters: a final rule shadows all rules
after it for callers that match it.

Consequences:
- With two matching rules R1 then R2, the final system prompt is
  R2's text + "\n\n" + R1's text + "\n\n" + original — i.e. the LAST
  matched rule's text ends up at the very front.
- strip_params are the union across matching rules.
- force_params: later rule wins on conflicting keys; a later rule's
  strip can also remove an earlier rule's forced value (per-rule
  strip-then-force interleaving).
- An empty extra_system_prompt / strip_params / force_params in a
  matching rule is a no-op for that aspect of that rule.
- In list form a rule's missing key = empty (no prompt / no strip /
  no force); the DEFAULT_* constants are NOT applied per rule.

Legacy form: a top-level single OBJECT is still accepted and treated as
a one-rule list; for that form a missing key falls back to the DEFAULT_*
constant in this file (original behavior preserved). The optional
"final" key is also accepted there (it is the only rule, so final=true
is a no-op semantically, but it is parsed and validated).

Empty array [] is valid and means "no rules" (the hook is a no-op).

File missing at startup -> constants are used (info logged once).
Invalid JSON or wrong types (top-level neither object nor array, array
element not an object, wrong types on known keys) -> last-known-good
config is kept and an error is logged; the request path never crashes
because of config.

Reload
------
The file is loaded once at import time (proxy startup). To apply edits
without restarting the proxy, send SIGHUP to the litellm process:

    podman kill -s HUP litellm

The hook installs the SIGHUP handler at import time (main thread only;
otherwise it degrades to a logged warning and startup is unaffected).
A failed reload (missing file, bad JSON, wrong types) keeps the previous
config.

Wire up in litellm-config.yaml (in addition to the 001 callback; the
`callbacks.` prefix is required because the directory is mounted at
/app/callbacks/):

    litellm_settings:
      callbacks:
        - "callbacks.zai_thinking_hook.zai_thinking_hook_instance"
        - "callbacks.team_prompt_params_hook.team_prompt_params_hook_instance"
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
from typing import Any, Final, NamedTuple, Optional

from litellm.integrations.custom_logger import CustomLogger  # type: ignore[import-unresolved]

logger = logging.getLogger("litellm.team_prompt_params_hook")


# The proxy runs with the root logger at WARNING (LITELLM_LOG unset), which
# would swallow our INFO lines. Attach our own stderr handler so the INFO
# output of this logger is visible regardless of the root level (same
# pattern as uvicorn.access). propagate=False keeps lines from being
# duplicated when LITELLM_LOG is set. The handlers check prevents
# double-attachment if the module is ever loaded twice.
def _configure_logger() -> None:
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(name)s %(levelname)s: %(message)s")
        )
        logger.addHandler(handler)


_configure_logger()

# ---------------------------------------------------------------------------
# Config file
# ---------------------------------------------------------------------------

# Path of the JSON config file. Override with the TEAM_PROMPT_PARAMS_CONFIG
# environment variable if the mount layout differs.
TEAM_PROMPT_PARAMS_CONFIG: Final[str] = os.environ.get(
    "TEAM_PROMPT_PARAMS_CONFIG", "/app/callbacks/team_prompt_params.json"
)

# ---------------------------------------------------------------------------
# Defaults — fallback for every key missing from a LEGACY single-object
# config (and for the no-file startup case). NOT applied to individual
# rules in the list form (a rule's missing key = empty there).
# ---------------------------------------------------------------------------

# team_id values (user_api_key_dict.team_id) to match.
# Empty list = no team matches.
# Example: DEFAULT_MATCH_TEAM_IDS = ["123e4567-e89b-12d3-a456-426614174000"]
DEFAULT_MATCH_TEAM_IDS: list[str] = []

# key_alias values (user_api_key_dict.key_alias) to match.
# Empty list = no alias matches.
# Example: DEFAULT_MATCH_KEY_ALIASES = ["team-foo-key"]
DEFAULT_MATCH_KEY_ALIASES: list[str] = []

# Text to PREPEND to the matched caller's system prompt.
DEFAULT_EXTRA_SYSTEM_PROMPT: str = (
    "You are a concise assistant. Answer directly."
)

# Request-level parameters to DELETE for matched callers.
# Example: DEFAULT_STRIP_PARAMS = ["temperature", "top_p", "frequency_penalty", "presence_penalty"]
DEFAULT_STRIP_PARAMS: list[str] = []

# Values to SET for matched callers (applied after strip).
# Example: DEFAULT_FORCE_PARAMS = {"temperature": 0.7}
DEFAULT_FORCE_PARAMS: dict = {}


# ---------------------------------------------------------------------------
# Active config snapshot
# ---------------------------------------------------------------------------


class _Rule(NamedTuple):
    """One matching rule from the config file.

    NamedTuple (not a frozen dataclass): the proxy's callback loader execs
    this file without registering it in sys.modules, and Python 3.13
    dataclasses introspect sys.modules[cls.__module__] at class-creation
    time, which crashes on an unregistered module. NamedTuple is equally
    immutable and does not do that introspection.
    """

    match_team_ids: tuple[str, ...]
    match_key_aliases: tuple[str, ...]
    extra_system_prompt: str
    strip_params: tuple[str, ...]
    force_params: tuple[tuple[str, Any], ...]
    final: bool = False


class _Config(NamedTuple):
    """Immutable snapshot of the active config (ordered list of rules).

    Reload = build a new snapshot + single reference swap of
    ``_ACTIVE_CONFIG``. The swap is a plain attribute assignment, which is
    atomic under the GIL, so no lock is needed; readers always see either
    the old or the new complete snapshot, never a mix.
    """

    rules: tuple[_Rule, ...]

    @classmethod
    def from_defaults(cls) -> "_Config":
        return cls(
            rules=(
                _Rule(
                    match_team_ids=tuple(DEFAULT_MATCH_TEAM_IDS),
                    match_key_aliases=tuple(DEFAULT_MATCH_KEY_ALIASES),
                    extra_system_prompt=DEFAULT_EXTRA_SYSTEM_PROMPT,
                    strip_params=tuple(DEFAULT_STRIP_PARAMS),
                    force_params=tuple(DEFAULT_FORCE_PARAMS.items()),
                    final=False,
                ),
            )
        )


def _coerce_rule(raw: Any, index: int, legacy: bool) -> _Rule:
    """Validate + coerce one rule object into a _Rule.

    ``legacy=True`` (top-level single-object form): missing keys fall back
    to the DEFAULT_* constants. ``legacy=False`` (list form): missing keys
    become empty (no prompt / no strip / no force).

    The optional ``final`` key (bool, default False) stops the rule walk
    after this rule matches; a non-boolean ``final`` is invalid.

    Raises ValueError on wrong types; the caller keeps the last-known-good
    config.
    """
    where = "top-level object" if legacy else f"rule at index {index}"
    if not isinstance(raw, dict):
        raise ValueError(f"{where} must be an object")

    def _str_list(key: str, default: tuple[str, ...]) -> tuple[str, ...]:
        value = raw.get(key)
        if value is None:
            return default
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise ValueError(f"{where}: {key!r} must be a list of strings")
        return tuple(value)

    def _str(key: str, default: str) -> str:
        value = raw.get(key)
        if value is None:
            return default
        if not isinstance(value, str):
            raise ValueError(f"{where}: {key!r} must be a string")
        return value

    force_raw = raw.get("force_params")
    if force_raw is None:
        force_params: tuple[tuple[str, Any], ...] = (
            tuple(DEFAULT_FORCE_PARAMS.items()) if legacy else ()
        )
    else:
        if not isinstance(force_raw, dict):
            raise ValueError(f"{where}: 'force_params' must be an object")
        force_params = tuple(force_raw.items())

    final_raw = raw.get("final")
    if final_raw is None:
        final = False
    else:
        if not isinstance(final_raw, bool):
            raise ValueError(f"{where}: 'final' must be a boolean")
        final = final_raw

    return _Rule(
        match_team_ids=_str_list(
            "match_team_ids",
            tuple(DEFAULT_MATCH_TEAM_IDS) if legacy else (),
        ),
        match_key_aliases=_str_list(
            "match_key_aliases",
            tuple(DEFAULT_MATCH_KEY_ALIASES) if legacy else (),
        ),
        extra_system_prompt=_str(
            "extra_system_prompt",
            DEFAULT_EXTRA_SYSTEM_PROMPT if legacy else "",
        ),
        strip_params=_str_list(
            "strip_params",
            tuple(DEFAULT_STRIP_PARAMS) if legacy else (),
        ),
        force_params=force_params,
        final=final,
    )


def _coerce_config(raw: Any) -> _Config:
    """Validate + coerce a parsed JSON value into a _Config snapshot.

    Top-level array -> list of rules (missing per-rule keys = empty).
    Top-level object -> legacy single-object form, wrapped as a one-rule
    list (missing keys fall back to DEFAULT_* constants).

    Raises ValueError (or TypeError) on invalid shapes or wrong types; the
    caller keeps the last-known-good config.
    """
    if isinstance(raw, dict):
        return _Config(rules=(_coerce_rule(raw, 0, legacy=True),))
    if isinstance(raw, list):
        return _Config(
            rules=tuple(_coerce_rule(rule, i, legacy=False) for i, rule in enumerate(raw))
        )
    raise ValueError("top-level JSON value must be an object or an array")


def load_config(path: Optional[str] = None) -> _Config:
    """Initial-load semantics.

    Missing file -> in-file defaults (info logged once). Unreadable file,
    invalid JSON, or wrong types -> raises; the caller falls back to
    defaults (at startup) or keeps the previous config (on reload).
    """
    config_path = path if path is not None else TEAM_PROMPT_PARAMS_CONFIG
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        logger.info(
            "config file %s not found; using in-file defaults", config_path
        )
        return _Config.from_defaults()
    return _coerce_config(raw)


def _initial_config() -> _Config:
    try:
        return load_config()
    except (OSError, ValueError, TypeError) as e:
        logger.error(
            "failed to load config from %s: %s; using in-file defaults",
            TEAM_PROMPT_PARAMS_CONFIG,
            e,
        )
        return _Config.from_defaults()


# Active snapshot. The hook reads this once per request; reload swaps the
# reference (GIL-atomic), so no lock is needed.
_ACTIVE_CONFIG: _Config = _initial_config()


def reload_config() -> bool:
    """Re-read the config file and swap in a new snapshot.

    On ANY failure (missing file, unreadable, invalid JSON, wrong types)
    the previous config is kept and an error is logged. Returns True if a
    new snapshot was installed, False otherwise.
    """
    global _ACTIVE_CONFIG
    try:
        with open(TEAM_PROMPT_PARAMS_CONFIG, "r", encoding="utf-8") as f:
            raw = json.load(f)
        new_config = _coerce_config(raw)
    except FileNotFoundError:
        logger.error(
            "reload failed: %s not found; keeping previous config",
            TEAM_PROMPT_PARAMS_CONFIG,
        )
        return False
    except (OSError, ValueError, TypeError) as e:
        logger.error(
            "reload failed: %s; keeping previous config", e
        )
        return False
    _ACTIVE_CONFIG = new_config
    logger.info("reloaded team_prompt_params config from %s", TEAM_PROMPT_PARAMS_CONFIG)
    return True


# ---------------------------------------------------------------------------
# SIGHUP reload
# ---------------------------------------------------------------------------


def _sighup_handler(signum: int, frame: Any) -> None:
    try:
        reload_config()
    except Exception:
        # reload_config already handles expected failures; this is a
        # belt-and-braces guard so a signal can never take the proxy down.
        logger.exception("SIGHUP reload handler crashed; keeping previous config")


def _install_sighup_handler() -> None:
    """Install the SIGHUP reload handler at import time (proxy startup).

    signal.signal() only works on the main thread; the proxy imports this
    module from the FastAPI lifespan on the main thread. If that ever
    changes, degrade to a logged warning instead of breaking startup.
    """
    if threading.current_thread() is not threading.main_thread():
        logger.warning(
            "imported from a non-main thread; SIGHUP reload handler not installed"
        )
        return
    try:
        signal.signal(signal.SIGHUP, _sighup_handler)
    except (ValueError, OSError) as e:
        logger.warning(
            "could not install SIGHUP handler (%s); SIGHUP reload unavailable", e
        )
        return
    logger.info(
        "SIGHUP handler installed: send SIGHUP to the litellm process to reload %s",
        TEAM_PROMPT_PARAMS_CONFIG,
    )


_install_sighup_handler()


# ---------------------------------------------------------------------------
# Hook
# ---------------------------------------------------------------------------


class TeamPromptParamsHook(CustomLogger):
    """Prepend a system prompt and lock request params for matched callers.

    Rules are walked in list order; every matching rule is applied (prepend
    prompt, strip params, then force params). With multiple matches the
    last matched rule's prompt text ends up at the very front, and later
    rules win on conflicting force_params. A matching rule with final=True
    stops the walk: no later rule is evaluated or applied.
    """

    async def async_pre_call_hook(
        self,
        user_api_key_dict: Optional[Any],
        cache: Any,
        data: dict,
        call_type: Any,
    ) -> dict:
        # Read the active snapshot once per request: a single reference
        # read, so a concurrent SIGHUP reload can only switch us to the
        # new complete snapshot.
        cfg = _ACTIVE_CONFIG

        # Only chat completions. The proxy passes the ASYNC route type to
        # async_pre_call_hook (CallTypes.acompletion for /chat/completions,
        # CallTypes.atext_completion for /v1/completions), so both the sync
        # and async variants must be accepted.
        if call_type not in (
            "completion",
            "acompletion",
            "text_completion",
            "atext_completion",
        ):
            return data
        # user_api_key_dict may be None on some paths -> treat as no-match
        if user_api_key_dict is None:
            return data
        team_id = getattr(user_api_key_dict, "team_id", None)
        key_alias = getattr(user_api_key_dict, "key_alias", None)

        for rule_index, rule in enumerate(cfg.rules):
            team_matched = team_id is not None and team_id in rule.match_team_ids
            alias_matched = key_alias is not None and key_alias in rule.match_key_aliases
            if not (team_matched or alias_matched):
                continue
            if rule.extra_system_prompt:
                self._prepend_system_prompt(data, rule.extra_system_prompt)
            for key in rule.strip_params:
                data.pop(key, None)
            if rule.force_params:
                data.update(dict(rule.force_params))
            # Per-match audit line: one INFO per matched rule, caller
            # identified by whichever matched. Prompt text is NEVER logged
            # (length only); non-matching requests log nothing.
            who = " ".join(
                part
                for part in (
                    f"team_id={team_id}" if team_matched else "",
                    f"key_alias={key_alias}" if alias_matched else "",
                )
                if part
            )
            logger.info(
                "matched %s rule[%d]: stripped=%s forced=%s prepended_prompt=%s final=%s",
                who,
                rule_index,
                ",".join(rule.strip_params) or "-",
                ",".join(k for k, _ in rule.force_params) or "-",
                f"{len(rule.extra_system_prompt)}ch" if rule.extra_system_prompt else "-",
                rule.final,
            )
            if rule.final:
                # final rule: stop the walk; no later rule is evaluated or
                # applied, even if it would have matched.
                break

        return data

    @staticmethod
    def _prepend_system_prompt(data: dict, extra: str) -> None:
        # Anthropic /v1/messages shape: top-level "system" (string or list of
        # blocks) — prepend there instead of touching messages.
        if "system" in data:
            system = data["system"]
            if isinstance(system, str):
                data["system"] = extra + "\n\n" + system
            elif isinstance(system, list):
                data["system"] = [{"type": "text", "text": extra}] + system
            return

        messages = data.get("messages")
        if not isinstance(messages, list) or not messages:
            return
        first = messages[0]
        if isinstance(first, dict) and first.get("role") == "system":
            content = first.get("content")
            if isinstance(content, str):
                first["content"] = extra + "\n\n" + content
                return
            if isinstance(content, list):
                first["content"] = [{"type": "text", "text": extra}] + content
                return
        # No usable system message -> insert at index 0
        messages.insert(0, {"role": "system", "content": extra})


team_prompt_params_hook_instance = TeamPromptParamsHook()
