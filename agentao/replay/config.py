"""Replay configuration — reads ``.agentao/settings.json`` under the ``replay`` key.

The project plan locks two rules:

- replay recording must have a configuration switch
- ``.agentao/settings.json`` stays JSON; replay settings live under ``"replay"``

This module provides the dataclass that represents those settings plus
safe loaders/writers used by both the CLI and runtime.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, NamedTuple, Optional, Tuple

from .. import json_parse

_logger = logging.getLogger(__name__)


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


class MaxInstancesCheck(NamedTuple):
    """How a ``replay.max_instances`` value is read: the count used, and a
    finding about it, if any. ``level`` is ``"error"`` when the value is
    ignored (the default is used) and ``"warning"`` when it was converted."""

    value: int
    level: Optional[str]
    message: Optional[str]


def check_max_instances(raw: Any) -> MaxInstancesCheck:
    """Read ``replay.max_instances`` the one way runtime and doctor share.

    ``int()`` is lenient, and that stays, with one exception: a JSON
    ``true`` / ``false`` is not a count. ``int(True)`` is 1, so ``true``
    kept only the newest replay and pruning deleted the rest, while doctor
    reported nothing. A bool now falls back to the default, which never
    keeps fewer replays than before. Every other accepted value keeps its
    count, because the fallback runs the other way for them: rejecting a
    ``"100"`` that works today would prune down to 20. Doctor flags the
    conversions instead (a fraction cut off, a number given as a string).
    """
    default = REPLAY_DEFAULTS["max_instances"]
    if isinstance(raw, bool):
        return MaxInstancesCheck(
            default, "error",
            f"replay.max_instances must be an integer, got {json.dumps(raw)} "
            f"(ignored at runtime; the default {default} is used)",
        )
    try:
        parsed = int(raw)
    except (TypeError, ValueError, OverflowError):
        return MaxInstancesCheck(
            default, "error",
            f"replay.max_instances must be an integer, got {raw!r}",
        )
    if parsed < 1:
        return MaxInstancesCheck(
            default, "error",
            f"replay.max_instances must be >= 1, got {parsed} (ignored at runtime)",
        )
    if isinstance(raw, str):
        return MaxInstancesCheck(
            parsed, "warning",
            f"replay.max_instances is a string, {raw!r}; {parsed} is used. "
            "Write it as a number.",
        )
    if isinstance(raw, float) and raw != parsed:
        return MaxInstancesCheck(
            parsed, "warning",
            f"replay.max_instances is not a whole number, {raw!r}; "
            f"{parsed} is used.",
        )
    return MaxInstancesCheck(parsed, None, None)


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
            if "max_instances" in raw:
                max_instances = check_max_instances(raw["max_instances"]).value
            raw_flags = raw.get("capture_flags")
            if isinstance(raw_flags, dict):
                for key, default_value in CAPTURE_FLAG_DEFAULTS.items():
                    if key in raw_flags:
                        flags[key] = _coerce_bool(raw_flags[key], default_value)
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


class ReplaySettingsError(ValueError):
    """``settings.json`` was left unchanged because writing it would lose data.

    Raised by :func:`save_replay_enabled` when the existing file cannot be
    read as a JSON object (rewriting it from scratch would drop every other
    setting in it), or when its contents hold a number JSON cannot represent.
    """


def _read_settings(path: Path) -> Tuple[Dict[str, Any], Optional[str]]:
    """``(data, problem)``: the parsed object, or ``({}, why it is unusable)``."""
    if not path.exists():
        return {}, None
    if not path.is_file():
        # A FIFO here would block ``read_text`` forever, at startup of every
        # entry path. Still a problem, not ``{}``: the writers must refuse.
        return {}, "not a regular file"
    try:
        data = json_parse.loads(path.read_text(encoding="utf-8-sig"))
    except UnicodeDecodeError as exc:
        return {}, (
            f"not valid UTF-8 ({exc.reason} at byte {exc.start}). Re-save it "
            "as UTF-8 — PowerShell 5.1 writes UTF-16LE from `>` and `Out-File`."
        )
    except (OSError, json.JSONDecodeError) as exc:
        return {}, f"{type(exc).__name__}: {exc}"
    if not isinstance(data, dict):
        return {}, f"top-level value must be a JSON object, got {type(data).__name__}"
    return data, None


def _load_settings(project_root: Optional[Path] = None) -> Dict[str, Any]:
    path = settings_path(project_root)
    data, problem = _read_settings(path)
    if problem is not None:
        _logger.warning("Ignoring %s: %s", path, problem)
    return data


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

    Raises :class:`ReplaySettingsError`, leaving the file untouched, when an
    existing file cannot be read as a JSON object: startup ignores such a
    file, but writing ``{"replay": ...}`` over it would delete everything
    else in it. A non-finite ``replay.max_instances`` (``1e309`` reads as
    infinity) is written back as the count actually in effect, since
    ``json.dumps`` would otherwise emit the non-standard ``Infinity``.
    """
    path = settings_path(project_root)
    data, problem = _read_settings(path)
    if problem is not None:
        raise ReplaySettingsError(
            f"{path} was not changed because it could not be read: {problem}. "
            "Fix the file or move it aside, then try again."
        )
    replay_block = data.get("replay") if isinstance(data.get("replay"), dict) else {}
    replay_block = dict(replay_block)
    replay_block["enabled"] = bool(enabled)
    count = replay_block.get("max_instances", REPLAY_DEFAULTS["max_instances"])
    if isinstance(count, float) and not math.isfinite(count):
        count = ReplayConfig.from_mapping(replay_block).max_instances
    replay_block["max_instances"] = count
    data["replay"] = replay_block
    try:
        text = json.dumps(data, indent=2, allow_nan=False)
    except ValueError as exc:
        raise ReplaySettingsError(
            f"{path} was not changed: it holds a number JSON cannot "
            f"represent ({exc}). Replace it with a finite value."
        ) from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return ReplayConfig.from_mapping(replay_block)
