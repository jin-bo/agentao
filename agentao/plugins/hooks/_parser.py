"""``ClaudeHooksParser`` — parse both hook configuration contracts.

Two shapes, detected per file, never translated into each other:

* **`agentao-v1`** — a flat handler list, a dict matcher, ``{event, data}`` on
  the wire. Frozen: this parser's behavior for a flat file is exactly what it
  was.
* **`claude-code@profile-1`** — the reference's own four-level nesting
  (event → matcher group → ``hooks[]`` → handler) with a **string** matcher.

**Why detection rather than a declaration.** Gating official-shape parsing on a
``contract`` key is the obvious design and it defeats the entire point: a file
copied out of a Claude Code setup *has no such key* — it is a Claude file, not
an agentao one — so gating on it leaves the copied file parsing to **zero
rules**, which is the deviation this exists to close.

**Every failure here is file-level.** An ambiguous entry, a file mixing both
shapes, or a shape that disagrees with an explicit ``contract`` disables the
whole file with a warning. A silently half-parsed file is worse than a refused
one: half a hook configuration is not a configuration, and per-entry rejection is
exactly how you get one.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..models import (
    KNOWN_UNSUPPORTED_HOOK_TYPES,
    SUPPORTED_HOOK_EVENTS,
    SUPPORTED_HOOK_TYPES,
    SUPPORTED_HOOK_TYPES_BY_EVENT,
    ParsedHookRule,
    PluginWarning,
)
from ._profile import LEGACY_CONTRACT_ID, PROFILE_ID

#: ``claude-code`` is an alias for the newest profile agentao ships. It is
#: convenient and it **drifts by design**; a plugin that needs stability pins the
#: numbered form.
CONTRACT_ALIAS = "claude-code"
KNOWN_CONTRACTS: frozenset[str] = frozenset({LEGACY_CONTRACT_ID, PROFILE_ID, CONTRACT_ALIAS})

#: Handler types the profile refuses, each for a stated reason (§2.4). ``prompt``
#: is the subtle one: agentao's ``prompt`` hook calls no model — it substitutes
#: into a template and injects the result as context, the inverse of upstream's
#: "send the prompt to a model and read a decision back". Accepting a Claude
#: ``prompt`` hook here would paste an evaluation instruction into the
#: conversation with ``$ARGUMENTS`` unsubstituted.
PROFILE_REJECTED_HOOK_TYPES: frozenset[str] = frozenset({
    "prompt", "http", "agent", "mcp_tool",
})

#: Handler fields the profile refuses outright, with the reason surfaced.
PROFILE_REJECTED_FIELDS: dict[str, str] = {
    "async": "agentao has no background hook runner",
    "asyncRewake": "agentao has no background hook runner",
    "if": (
        "a permission-rule sub-feature with its own Bash-subcommand semantics, "
        "not a field to wire up"
    ),
}

#: Fields parsed and ignored — no contract effect either way.
PROFILE_IGNORED_FIELDS: frozenset[str] = frozenset({"statusMessage", "once"})

#: The reference's per-event command-hook timeout defaults. ``UserPromptSubmit``
#: lowers the default because the user is waiting on it.
_PROFILE_TIMEOUT_DEFAULTS: dict[str, int] = {"UserPromptSubmit": 30}
_PROFILE_TIMEOUT_DEFAULT = 600
_LEGACY_TIMEOUT_DEFAULT = 60

logger = logging.getLogger(__name__)


def _detect_entry_shape(entry: dict[str, Any]) -> str:
    """``"official"`` | ``"flat"`` | ``"ambiguous"`` | ``"undetermined"``.

    **Four values, where §2.2's table has three**, and the split is deliberate.
    An entry carrying *both* keys is contradictory — it claims to be both
    shapes — and disabling the file is right. An entry carrying *neither* claims
    nothing: it is a malformed handler, not a shape conflict, and the frozen
    ``agentao-v1`` contract has always handled that with a per-rule warning
    ("Unknown hook type ''") while its siblings kept working. Treating the two
    alike made one typo'd entry disable every other hook in an existing v1 file,
    which §3's freeze forbids more strongly than §2.2's table demands.
    """
    has_hooks = isinstance(entry.get("hooks"), list)
    has_type = "type" in entry
    if has_hooks and not has_type:
        return "official"
    if has_type and not has_hooks:
        return "flat"
    if has_hooks and has_type:
        return "ambiguous"
    return "undetermined"


class ClaudeHooksParser:
    """Parse Claude-compatible ``hooks.json`` files."""

    def parse_file(
        self, path: Path, *, plugin_name: str = "", plugin_root: str | None = None,
    ) -> tuple[list[ParsedHookRule], list[PluginWarning]]:
        """Parse a hooks JSON file and return ``(rules, warnings)``."""
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            return [], [
                PluginWarning(
                    plugin_name=plugin_name,
                    message=f"Could not parse hooks file {path}: {exc}",
                    field="hooks",
                )
            ]
        return self.parse_dict(raw, plugin_name=plugin_name, plugin_root=plugin_root)

    # ------------------------------------------------------------------
    def parse_dict(
        self,
        raw: dict[str, Any],
        *,
        plugin_name: str = "",
        plugin_root: str | None = None,
    ) -> tuple[list[ParsedHookRule], list[PluginWarning]]:
        """Parse an already-deserialised hooks dict.

        Accepts the ``{"hooks": {...}}`` wrapper or a bare events dict, in
        either contract's shape.
        """
        warnings: list[PluginWarning] = []

        def warn(message: str) -> None:
            warnings.append(
                PluginWarning(plugin_name=plugin_name, message=message, field="hooks")
            )

        wrapper = isinstance(raw, dict) and isinstance(raw.get("hooks"), dict)
        hooks_dict = raw.get("hooks", raw) if isinstance(raw, dict) else raw
        if not isinstance(hooks_dict, dict):
            warn("hooks must be a JSON object")
            return [], warnings

        # ``contract`` is metadata only in the wrapper form. In the bare form the
        # key is an event name, and falls through to the "unsupported event"
        # warning below — the bare form has nowhere to put it, which is why shape
        # detection has to work without it.
        declared = raw.get("contract") if wrapper else None

        contract, fatal = self._resolve_contract(declared, hooks_dict, warn)
        if fatal:
            return [], warnings

        rules: list[ParsedHookRule] = []
        for event_name, hook_list in hooks_dict.items():
            if event_name not in SUPPORTED_HOOK_EVENTS:
                warn(f"Unsupported hook event '{event_name}' — skipped")
                continue
            if not isinstance(hook_list, list):
                hook_list = [hook_list]
            for entry in hook_list:
                if not isinstance(entry, dict):
                    warn(f"Hook entry under '{event_name}' is not an object — skipped")
                    continue
                if contract == LEGACY_CONTRACT_ID:
                    rule = self._parse_flat_entry(entry, event_name, plugin_name, plugin_root, warn)
                    if rule is not None:
                        rules.append(rule)
                else:
                    rules.extend(self._parse_group(
                        entry, event_name, plugin_name, plugin_root, warn,
                    ))
        return rules, warnings

    # ------------------------------------------------------------------
    def _resolve_contract(self, declared, hooks_dict, warn) -> tuple[str, bool]:
        """Return ``(contract, fatal)``; ``fatal`` disables the whole file."""
        shapes: set[str] = set()
        for event_name, hook_list in hooks_dict.items():
            # Only entries this parser will actually read get a vote. An
            # unsupported event is already skipped with its own warning, so
            # letting its shape disable the *file* would kill every working
            # rule over an entry that was never going to run — and a copied
            # Claude config routinely carries events outside the profile.
            if event_name not in SUPPORTED_HOOK_EVENTS:
                continue
            entries = hook_list if isinstance(hook_list, list) else [hook_list]
            for entry in entries:
                if isinstance(entry, dict):
                    shapes.add(_detect_entry_shape(entry))

        if "ambiguous" in shapes:
            warn(
                "Hook entry has both 'type' and 'hooks', so it claims both shapes "
                "at once — the whole file is disabled. Half a hook configuration "
                "is not a configuration."
            )
            return LEGACY_CONTRACT_ID, True
        # ``undetermined`` casts no vote: it is a malformed handler, and the
        # contract the file resolves to reports it per rule.
        shapes.discard("undetermined")
        if {"official", "flat"} <= shapes:
            warn(
                "Hooks file mixes the official nested shape with agentao's flat "
                "shape — the whole file is disabled. One file has one contract."
            )
            return LEGACY_CONTRACT_ID, True

        detected = PROFILE_ID if "official" in shapes else LEGACY_CONTRACT_ID

        if declared is None:
            return detected, False

        if not isinstance(declared, str) or declared not in KNOWN_CONTRACTS:
            # Not a fallback to the frozen contract: the author named semantics
            # agentao does not have, and running their hooks under *different*
            # semantics is a silent misinterpretation.
            warn(
                f"Unknown hook contract {declared!r} — the whole file is disabled. "
                f"Known: {sorted(KNOWN_CONTRACTS)}"
            )
            return LEGACY_CONTRACT_ID, True

        resolved = LEGACY_CONTRACT_ID if declared == LEGACY_CONTRACT_ID else PROFILE_ID
        if shapes and resolved != detected:
            warn(
                f"Hooks file declares contract {declared!r} but its entries are in "
                f"the {'official nested' if detected == PROFILE_ID else 'agentao flat'} "
                f"shape — the whole file is disabled. A shape that disagrees with an "
                f"explicit contract is a rejection, not a coercion."
            )
            return resolved, True
        return resolved, False

    # ------------------------------------------------------------------
    def _parse_flat_entry(
        self, entry, event_name, plugin_name, plugin_root, warn,
    ) -> ParsedHookRule | None:
        """``agentao-v1``: today's behavior, unchanged."""
        hook_type = entry.get("type", "")
        if hook_type in KNOWN_UNSUPPORTED_HOOK_TYPES:
            warn(f"Hook type '{hook_type}' under '{event_name}' is not supported — skipped")
            return None
        if hook_type not in SUPPORTED_HOOK_TYPES:
            warn(f"Unknown hook type '{hook_type}' under '{event_name}' — skipped")
            return None
        allowed_for_event = SUPPORTED_HOOK_TYPES_BY_EVENT.get(event_name, SUPPORTED_HOOK_TYPES)
        if hook_type not in allowed_for_event:
            warn(
                f"Hook type '{hook_type}' is not supported for event '{event_name}' "
                f"— skipped. (Allowed for this event: {sorted(allowed_for_event)})"
            )
            return None

        try:
            timeout = int(entry.get("timeout", _LEGACY_TIMEOUT_DEFAULT))
        except (ValueError, TypeError):
            warn(
                f"Invalid timeout value '{entry.get('timeout')}' under '{event_name}' "
                f"— using default {_LEGACY_TIMEOUT_DEFAULT}s"
            )
            timeout = _LEGACY_TIMEOUT_DEFAULT

        matcher = entry.get("matcher")
        if matcher is not None and not isinstance(matcher, dict):
            warn(
                f"Hook rule under '{event_name}' has non-object matcher of type "
                f"{type(matcher).__name__}; matcher must be an object like "
                f'{{"trigger": "manual|auto"}} — rule skipped.'
            )
            return None

        return ParsedHookRule(
            event=event_name,
            hook_type=hook_type,
            command=entry.get("command"),
            prompt=entry.get("prompt"),
            timeout=timeout,
            matcher=matcher,
            plugin_name=plugin_name,
            contract=LEGACY_CONTRACT_ID,
            plugin_root=plugin_root,
        )

    # ------------------------------------------------------------------
    def _parse_group(
        self, entry, event_name, plugin_name, plugin_root, warn,
    ) -> list[ParsedHookRule]:
        """``claude-code@profile-1``: one matcher group → zero or more rules."""
        matcher = entry.get("matcher")
        if matcher is not None and not isinstance(matcher, str):
            warn(
                f"Hook matcher under '{event_name}' must be a string in "
                f"{PROFILE_ID}; got {type(matcher).__name__} — group skipped."
            )
            return []

        handlers = entry.get("hooks")
        if not handlers:
            warn(
                f"Hook matcher group under '{event_name}' has no 'hooks' list — "
                f"nothing to run, group skipped."
            )
            return []

        rules: list[ParsedHookRule] = []
        for index, handler in enumerate(handlers):
            if not isinstance(handler, dict):
                warn(f"Hook handler under '{event_name}' is not an object — skipped")
                continue
            rule = self._parse_handler(
                handler, event_name, matcher, index, plugin_name, plugin_root, warn,
            )
            if rule is not None:
                rules.append(rule)
        return rules

    def _parse_handler(
        self, handler, event_name, matcher, index, plugin_name, plugin_root, warn,
    ) -> ParsedHookRule | None:
        hook_type = handler.get("type", "")
        if hook_type in PROFILE_REJECTED_HOOK_TYPES:
            why = (
                "agentao's prompt hook injects the template as context rather than "
                "sending it to a model, so it is a different feature wearing the "
                "same type"
                if hook_type == "prompt"
                else "not implemented"
            )
            warn(
                f"Hook type '{hook_type}' under '{event_name}' is not in {PROFILE_ID} "
                f"— skipped ({why})."
            )
            return None
        if hook_type != "command":
            warn(f"Unknown hook type '{hook_type}' under '{event_name}' — skipped")
            return None

        for field_name, why in PROFILE_REJECTED_FIELDS.items():
            if field_name in handler:
                warn(
                    f"Hook field '{field_name}' under '{event_name}' is not in "
                    f"{PROFILE_ID} — rule skipped ({why})."
                )
                return None

        for ignored in PROFILE_IGNORED_FIELDS & set(handler):
            # Recognized and inert. Named here rather than left to fall through
            # the unknown-key path, so the set is a declaration rather than a
            # comment: ``statusMessage`` is spinner text and ``once`` is
            # skill-frontmatter only, and agentao has neither surface.
            logger.debug(
                "Hook field %r under %r is recognized and has no effect in %s",
                ignored, event_name, PROFILE_ID,
            )

        if "shell" in handler:
            # Measured: Claude Code 2.1.251 runs command hooks under `sh` and does
            # not honor this field either. Rejecting the *rule* would disable a
            # hook that runs upstream, so the field is ignored and said so.
            warn(
                f"Hook field 'shell' under '{event_name}' has no effect — agentao "
                f"runs command hooks under /bin/sh, which is what Claude Code "
                f"2.1.251 was measured to do with and without this field."
            )

        default_timeout = _PROFILE_TIMEOUT_DEFAULTS.get(event_name, _PROFILE_TIMEOUT_DEFAULT)
        try:
            timeout = int(handler.get("timeout", default_timeout))
        except (ValueError, TypeError):
            warn(
                f"Invalid timeout value '{handler.get('timeout')}' under "
                f"'{event_name}' — using default {default_timeout}s"
            )
            timeout = default_timeout

        args = handler.get("args")
        if args is not None:
            if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
                warn(
                    f"Hook field 'args' under '{event_name}' must be a list of "
                    f"strings — rule skipped."
                )
                return None

        return ParsedHookRule(
            event=event_name,
            hook_type="command",
            command=handler.get("command"),
            prompt=None,
            timeout=timeout,
            matcher=None,
            matcher_pattern=matcher,
            args=list(args) if args else None,
            plugin_name=plugin_name,
            contract=PROFILE_ID,
            plugin_root=plugin_root,
            handler_index=index,
        )
