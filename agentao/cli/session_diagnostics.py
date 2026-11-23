"""What this session loaded, for ``/permission`` and ``/plugins hooks`` (#668).

Both views read what the session recorded at startup and at each mode switch.
They never read the config files again. A file edited after startup does not
change what the session enforces, so a fresh read would describe a state the
session is not in. Opening either view reads no file and runs no hook.

Output is metadata only. A hook's command, arguments, prompt and URL are never
shown, and every free-text reason goes through ``security.secret_scan.redact``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Mapping, Optional, Tuple

from ..security.secret_scan import redact

# ---------------------------------------------------------------------------
# Permission mode source
# ---------------------------------------------------------------------------

#: Readable labels for the ``cause`` of ``apply_permission_mode``. An unknown
#: cause (a host-defined one) is shown as written.
_CAUSE_LABELS = {
    # ``/clear`` and ``/new`` reset to workspace-write through ``_apply_mode``
    # with this cause too, so the label names both paths.
    "cli": "/mode in this session",
    "cli-reset": "session reset (/clear or /new)",
    "cli-allow-all": 'temporary full-access grant (answered "2" at a confirmation prompt)',
    "cli-plan": "/plan (plan mode)",
    "cli-plan-exit": "leaving plan mode (restored the mode in force before /plan)",
    "cli-plan-implement": "approving a plan (left read-only to implement it)",
    "cli-resume": "/sessions resume (ended the temporary full-access grant)",
}


@dataclass(frozen=True)
class ModeSource:
    """Where the current permission mode came from.

    ``origin`` is ``"default"``, ``"settings"``, or the ``cause`` of the last
    in-session switch. ``detail`` names the settings file for ``"settings"``.
    ``fallback`` says why a configured value was not used, so the default
    applies. It is kept apart from ``origin``: the mode *is* the default, and
    the reason is that the configured value was refused.
    """

    origin: str
    detail: Optional[str] = None
    fallback: Optional[str] = None


def startup_mode_source(
    settings: Mapping[str, Any], problem: Optional[str], path: Path,
) -> ModeSource:
    """The source of the mode read from ``settings.json`` at startup.

    Mirrors ``AgentaoCLI._load_saved_mode``: a missing key means the default,
    and a value that is not a mode falls back to the default without a word.
    This records that fallback so ``/permission`` can say it happened.
    """
    from ..permissions import PermissionMode

    if problem is not None:
        return ModeSource("default", fallback=f"{path} could not be read: {problem}")
    if "mode" not in settings:
        return ModeSource("default")
    value = settings.get("mode")
    try:
        PermissionMode(value)
    except (ValueError, TypeError):
        return ModeSource(
            "default",
            fallback=f'invalid config value {value!r} for "mode" in {path}',
        )
    return ModeSource("settings", detail=str(path))


def mode_source_for_cause(cause: Any) -> ModeSource:
    return ModeSource(str(cause))


def describe_mode_source(source: ModeSource) -> str:
    if source.origin == "default":
        return "default"
    if source.origin == "settings":
        return f"project settings ({source.detail})"
    return _CAUSE_LABELS.get(source.origin, source.origin)


def permission_source_lines(
    *,
    mode: str,
    source: Optional[ModeSource],
    loaded_sources: List[str],
    ignored_inputs: List[Tuple[str, str]],
) -> List[str]:
    """Plain-text lines for the "where this comes from" part of ``/permission``."""
    lines = [f"Mode: {mode}"]
    if source is None:
        lines.append("  Source: not recorded")
    else:
        lines.append(f"  Source: {describe_mode_source(source)}")
        if source.fallback:
            lines.append(f"  Fallback reason: {redact(source.fallback)}")
    lines.append("Policy sources (as loaded at startup):")
    for label in loaded_sources or ["(none)"]:
        lines.append(f"  - {label}")
    if ignored_inputs:
        lines.append("Found at startup, not used by the built-in loader:")
        for path, reason in ignored_inputs:
            lines.append(f"  - {path}: {redact(reason)}")
    return lines


# ---------------------------------------------------------------------------
# Plugin load record
# ---------------------------------------------------------------------------


@dataclass
class PluginLoadReport:
    """What ``_load_and_register_plugins`` did at startup.

    ``plugins`` holds ``(name, root, loaded)`` for every discovered plugin.
    ``failures`` holds ``(plugin_name, reason)`` for each skill or agent
    resolution or registration failure: a plugin with one is not active, so
    none of its hooks load. ``discovery_warnings`` and ``discovery_errors``
    come from the plugin manager. ``hook_rules`` are the rules the runtime
    dispatches, and ``hook_warnings`` the parse warnings for skipped entries.
    """

    plugins: List[Tuple[str, str, bool]] = field(default_factory=list)
    failures: List[Tuple[str, str]] = field(default_factory=list)
    discovery_warnings: List[Any] = field(default_factory=list)
    discovery_errors: List[Any] = field(default_factory=list)
    hook_rules: List[Any] = field(default_factory=list)
    hook_warnings: List[Any] = field(default_factory=list)


def _matcher_text(rule: Any) -> str:
    pattern = getattr(rule, "matcher_pattern", None)
    if pattern is not None:
        return str(pattern)
    matcher = getattr(rule, "matcher", None)
    if matcher:
        try:
            return json.dumps(matcher, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(matcher)
    return "*"


def hooks_report_lines(report: Optional[PluginLoadReport]) -> List[str]:
    """Plain-text lines for ``/plugins hooks``."""
    if report is None:
        return ["No plugin load was recorded for this session."]
    roots = {name: root for name, root, _ok in report.plugins}
    rules = list(report.hook_rules)
    lines = [
        f"Hooks loaded at startup: {len(rules)} rule(s)",
        "(From this session's startup load. Restart to pick up changed files.)",
        "",
    ]
    for rule in rules:
        plugin = getattr(rule, "plugin_name", None) or "?"
        root = getattr(rule, "plugin_root", None) or roots.get(plugin, "")
        root_text = f" ({root})" if root else ""
        lines.append(
            f"  - {rule.event}  matcher={redact(_matcher_text(rule))}  "
            f"type={rule.hook_type}  plugin={plugin}{root_text}"
        )
    if not rules:
        lines.append("  (none)")

    skipped = [str(w) for w in report.hook_warnings]
    if skipped:
        lines += ["", "Hook entries skipped at startup:"]
        lines += [f"  - {redact(text)}" for text in skipped]

    failed = [(n, r) for n, r, ok in report.plugins if not ok]
    if failed or report.failures:
        lines += ["", "Plugins not loaded (their hooks are absent):"]
        reasons: dict[str, List[str]] = {}
        for name, reason in report.failures:
            reasons.setdefault(name, []).append(reason)
        for name, root in failed:
            lines.append(f"  - {name} ({root})")
            for reason in reasons.pop(name, []):
                lines.append(f"      {redact(reason)}")
        for name, rs in reasons.items():
            lines.append(f"  - {name}")
            for reason in rs:
                lines.append(f"      {redact(reason)}")

    if report.discovery_errors:
        lines += ["", "Plugin discovery errors:"]
        lines += [f"  - {redact(str(e))}" for e in report.discovery_errors]
    if report.discovery_warnings:
        lines += ["", "Plugin discovery warnings:"]
        lines += [f"  - {redact(str(w))}" for w in report.discovery_warnings]
    return lines
