"""Replay configuration — reads ``.agentao/settings.json`` under the ``replay`` key.

The project plan locks two rules:

- replay recording must have a configuration switch
- ``.agentao/settings.json`` stays JSON; replay settings live under ``"replay"``

This module provides the dataclass that represents those settings plus
safe loaders/writers used by both the CLI and runtime.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional


# v1.1 capture-flag defaults. The design decision (step 2 of
# SESSION_REPLAY_PLAN): ``capture_llm_delta`` is on by default so each
# turn carries its newly-added messages; the other two knobs stay off by
# default because they can dramatically grow file size and/or leak
# secrets even after regex-based redaction.
CAPTURE_FLAG_DEFAULTS: Dict[str, bool] = {
    "capture_llm_delta": True,
    "capture_full_llm_io": False,
    "capture_tool_result_full": False,
    "capture_plugin_hook_output_full": False,
}


REPLAY_DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "max_instances": 20,
    "capture_flags": dict(CAPTURE_FLAG_DEFAULTS),
}


def _coerce_bool(value: Any, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "no", "off", "")
    if value is None:
        return fallback
    return bool(value)


@dataclass
class ReplayConfig:
    """Effective replay configuration for a project.

    Values fall back to :data:`REPLAY_DEFAULTS` for missing fields and
    silently coerce malformed values rather than raising, so a broken
    settings file never blocks startup.
    """

    enabled: bool = False
    max_instances: int = 20
    capture_flags: Dict[str, bool] = field(
        default_factory=lambda: dict(CAPTURE_FLAG_DEFAULTS)
    )

    @classmethod
    def from_mapping(cls, raw: Any) -> "ReplayConfig":
        enabled = REPLAY_DEFAULTS["enabled"]
        max_instances = REPLAY_DEFAULTS["max_instances"]
        flags: Dict[str, bool] = dict(CAPTURE_FLAG_DEFAULTS)
        if isinstance(raw, dict):
            enabled = _coerce_bool(raw.get("enabled", enabled), enabled)
            try:
                max_instances = int(raw.get("max_instances", max_instances))
            except (TypeError, ValueError):
                max_instances = REPLAY_DEFAULTS["max_instances"]
            raw_flags = raw.get("capture_flags")
            if isinstance(raw_flags, dict):
                for key, default_value in CAPTURE_FLAG_DEFAULTS.items():
                    if key in raw_flags:
                        flags[key] = _coerce_bool(raw_flags[key], default_value)
        if max_instances < 1:
            max_instances = REPLAY_DEFAULTS["max_instances"]
        return cls(enabled=enabled, max_instances=max_instances, capture_flags=flags)

    def deep_capture_enabled(self) -> bool:
        """True when at least one deep-capture flag is on.

        The CLI uses this to warn the user at session start that the
        replay file will contain richer — and possibly more sensitive —
        content than normal.
        """
        return any(
            self.capture_flags.get(key, False)
            for key in (
                "capture_full_llm_io",
                "capture_tool_result_full",
                "capture_plugin_hook_output_full",
            )
        )


def settings_path(project_root: Optional[Path] = None) -> Path:
    root = project_root if project_root is not None else Path.cwd()
    return root / ".agentao" / "settings.json"


def _load_settings(project_root: Optional[Path] = None) -> Dict[str, Any]:
    path = settings_path(project_root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_replay_config(project_root: Optional[Path] = None) -> ReplayConfig:
    """Read and parse the ``replay`` block from ``.agentao/settings.json``."""
    return ReplayConfig.from_mapping(_load_settings(project_root).get("replay"))


def save_replay_enabled(
    enabled: bool,
    project_root: Optional[Path] = None,
) -> ReplayConfig:
    """Persist only the ``replay.enabled`` flag, preserving other keys.

    This is the write path used by ``/replay on`` and ``/replay off``.
    Returns the newly-effective :class:`ReplayConfig`.

    Creates the ``.agentao/`` directory if missing so the toggle works on
    a fresh project where settings.json does not yet exist.
    """
    path = settings_path(project_root)
    data = _load_settings(project_root)
    replay_block = data.get("replay") if isinstance(data.get("replay"), dict) else {}
    replay_block = dict(replay_block)
    replay_block["enabled"] = bool(enabled)
    if "max_instances" not in replay_block:
        replay_block["max_instances"] = REPLAY_DEFAULTS["max_instances"]
    data["replay"] = replay_block
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return ReplayConfig.from_mapping(replay_block)
