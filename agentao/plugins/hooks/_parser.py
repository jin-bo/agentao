"""``ClaudeHooksParser`` — parse Claude-compatible hooks.json files.

Reads either a ``{"hooks": {...}}`` wrapper or a bare ``{...}`` events
dict. Unknown event names, unsupported hook types, malformed timeouts,
and non-object matchers all degrade to ``PluginWarning`` rather than
hard errors so a single bad rule never disables a plugin's other hooks.
"""

from __future__ import annotations

import json
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


class ClaudeHooksParser:
    """Parse Claude-compatible ``hooks.json`` files."""

    def parse_file(
        self, path: Path, *, plugin_name: str = ""
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
        return self.parse_dict(raw, plugin_name=plugin_name)

    def parse_dict(
        self, raw: dict[str, Any], *, plugin_name: str = ""
    ) -> tuple[list[ParsedHookRule], list[PluginWarning]]:
        """Parse an already-deserialised hooks dict.

        Expected shape::

            {
              "hooks": {
                "EventName": [
                  { "type": "command", "command": "..." },
                  ...
                ],
                ...
              }
            }

        Also accepts the inner ``hooks`` dict directly (no wrapper).
        """
        warnings: list[PluginWarning] = []
        rules: list[ParsedHookRule] = []

        hooks_dict = raw.get("hooks", raw)
        if not isinstance(hooks_dict, dict):
            warnings.append(
                PluginWarning(
                    plugin_name=plugin_name,
                    message="hooks must be a JSON object",
                    field="hooks",
                )
            )
            return rules, warnings

        for event_name, hook_list in hooks_dict.items():
            if event_name not in SUPPORTED_HOOK_EVENTS:
                warnings.append(
                    PluginWarning(
                        plugin_name=plugin_name,
                        message=f"Unsupported hook event '{event_name}' — skipped",
                        field="hooks",
                    )
                )
                continue

            if not isinstance(hook_list, list):
                hook_list = [hook_list]

            for entry in hook_list:
                if not isinstance(entry, dict):
                    warnings.append(
                        PluginWarning(
                            plugin_name=plugin_name,
                            message=f"Hook entry under '{event_name}' is not an object — skipped",
                            field="hooks",
                        )
                    )
                    continue

                hook_type = entry.get("type", "")
                if hook_type in KNOWN_UNSUPPORTED_HOOK_TYPES:
                    warnings.append(
                        PluginWarning(
                            plugin_name=plugin_name,
                            message=f"Hook type '{hook_type}' under '{event_name}' is not supported — skipped",
                            field="hooks",
                        )
                    )
                    continue

                if hook_type not in SUPPORTED_HOOK_TYPES:
                    warnings.append(
                        PluginWarning(
                            plugin_name=plugin_name,
                            message=f"Unknown hook type '{hook_type}' under '{event_name}' — skipped",
                            field="hooks",
                        )
                    )
                    continue

                allowed_for_event = SUPPORTED_HOOK_TYPES_BY_EVENT.get(
                    event_name, SUPPORTED_HOOK_TYPES,
                )
                if hook_type not in allowed_for_event:
                    warnings.append(
                        PluginWarning(
                            plugin_name=plugin_name,
                            message=(
                                f"Hook type '{hook_type}' is not supported for event "
                                f"'{event_name}' — skipped. (Allowed for this event: "
                                f"{sorted(allowed_for_event)})"
                            ),
                            field="hooks",
                        )
                    )
                    continue

                try:
                    timeout = int(entry.get("timeout", 60))
                except (ValueError, TypeError):
                    warnings.append(
                        PluginWarning(
                            plugin_name=plugin_name,
                            message=f"Invalid timeout value '{entry.get('timeout')}' under '{event_name}' — using default 60s",
                            field="hooks",
                        )
                    )
                    timeout = 60

                matcher = entry.get("matcher")
                if matcher is not None and not isinstance(matcher, dict):
                    warnings.append(
                        PluginWarning(
                            plugin_name=plugin_name,
                            message=(
                                f"Hook rule under '{event_name}' has non-object matcher "
                                f"of type {type(matcher).__name__}; matcher must be an object "
                                f"like {{\"trigger\": \"manual|auto\"}} — rule skipped."
                            ),
                            field="hooks",
                        )
                    )
                    continue

                rules.append(
                    ParsedHookRule(
                        event=event_name,
                        hook_type=hook_type,
                        command=entry.get("command"),
                        prompt=entry.get("prompt"),
                        timeout=timeout,
                        matcher=matcher,
                        plugin_name=plugin_name,
                    )
                )

        return rules, warnings
