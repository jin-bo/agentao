"""Per-section collectors for ``agentao doctor`` / ``agentao config validate``.

Each collector loads or probes one configuration surface and records its
findings into the shared :class:`DiagnosticReport`. None of them change runtime
startup semantics — they only report presence / parse-status / source paths.
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from .._globals import _plugin_inline_dirs
from .loaders import _load_json_object
from .models import DiagnosticReport, Finding


def _collect_settings(wd: Path, report: DiagnosticReport) -> Optional[Dict[str, Any]]:
    """Load ``.agentao/settings.json`` and record parse findings.

    Returns the parsed dict (``None`` on missing/malformed). The path is always
    reported in the section so consumers can see where the loader looked.
    """
    path = wd / ".agentao" / "settings.json"
    data, status, finding = _load_json_object(
        path, area="settings", label="settings.json",
    )
    if finding is not None:
        report.add(finding)

    section: Dict[str, Any] = {
        "path": str(path),
        "exists": status != "absent",
        "status": status,
    }
    if data is not None:
        section["keys"] = sorted(data.keys())
    report.sections["settings"] = section
    return data


def _collect_provider(report: DiagnosticReport) -> None:
    """Report which LLM provider env vars are present (no secret values)."""
    provider = os.getenv("LLM_PROVIDER", "OPENAI").strip().upper()
    api_key = os.getenv(f"{provider}_API_KEY")
    base_url = os.getenv(f"{provider}_BASE_URL")
    model = os.getenv(f"{provider}_MODEL")

    section: Dict[str, Any] = {
        "provider": provider,
        "api_key_present": bool(api_key),
        "base_url": base_url,
        "model": model,
    }
    temperature_raw = os.getenv("LLM_TEMPERATURE")
    if temperature_raw is not None:
        section["temperature_raw"] = temperature_raw
        try:
            float(temperature_raw)
        except ValueError:
            report.add(Finding(
                level="error",
                area="provider",
                message=(
                    f"LLM_TEMPERATURE='{temperature_raw}' is not a valid float."
                ),
                source="env:LLM_TEMPERATURE",
            ))
    max_tokens_raw = os.getenv("LLM_MAX_TOKENS")
    if max_tokens_raw is not None:
        section["max_tokens_raw"] = max_tokens_raw
        try:
            int(max_tokens_raw)
        except ValueError:
            report.add(Finding(
                level="error",
                area="provider",
                message=(
                    f"LLM_MAX_TOKENS='{max_tokens_raw}' is not a valid integer."
                ),
                source="env:LLM_MAX_TOKENS",
            ))

    report.sections["provider"] = section

    if not api_key:
        report.add(Finding(
            level="warning",
            area="provider",
            message=(
                f"{provider}_API_KEY is not set. LLM calls will fail until "
                f"the key is configured (run `agentao init` or edit .env)."
            ),
            source=f"env:{provider}_API_KEY",
        ))


def _collect_permissions(wd: Path, report: DiagnosticReport) -> None:
    """Load permission rules and capture parse-status findings.

    Mirrors ``embedding.permission_loader.load_permission_rules`` but
    distinguishes 'missing' from 'malformed' so the user knows when their file
    is being silently ignored.
    """
    from ...paths import user_root

    ur = user_root()
    user_path = ur / "permissions.json"
    project_path = wd / ".agentao" / "permissions.json"

    section: Dict[str, Any] = {
        "user_path": str(user_path),
        "user_status": "absent",
        "project_path": str(project_path),
        "project_status": "absent",
        "rule_count": 0,
        "loaded_sources": [],
    }

    data, status, finding = _load_json_object(
        user_path, area="permissions", label="user-scope permissions.json",
    )
    if finding is not None:
        report.add(finding)
    section["user_status"] = status
    if status == "ok" and data is not None:
        rules = data.get("rules", [])
        if not isinstance(rules, list):
            section["user_status"] = "malformed"
            report.add(Finding(
                level="error",
                area="permissions",
                message="'rules' in permissions.json must be a list",
                source=str(user_path),
            ))
        else:
            section["rule_count"] = len(rules)
            section["loaded_sources"].append(f"user:{user_path}")

    if project_path.exists():
        section["project_status"] = "ignored"
        report.add(Finding(
            level="warning",
            area="permissions",
            message=(
                "Project-scope permissions.json is no longer honored "
                "(could grant capabilities the user never approved). "
                "Move custom rules to the user-scope file."
            ),
            source=str(project_path),
        ))

    report.sections["permissions"] = section


def _validate_mcp_server_fields(servers: Dict[str, Any]) -> List[str]:
    """Return error messages for any server with non-string env/headers/args.

    The MCP runtime loader walks these fields and assumes every value is a
    string; non-string values raise ``TypeError`` from inside the env-var
    expansion regex. Validate them so the failure surfaces here rather than
    at agent startup.
    """
    messages: List[str] = []
    for name, cfg in servers.items():
        for field_name in ("env", "headers"):
            value = cfg.get(field_name)
            if value is None:
                continue
            if not isinstance(value, dict):
                messages.append(
                    f"server {name!r}: {field_name!r} must be an object"
                )
                continue
            bad = [k for k, v in value.items() if not isinstance(v, str)]
            if bad:
                messages.append(
                    f"server {name!r}: {field_name!r} values must be strings "
                    f"(non-string keys: {', '.join(repr(k) for k in bad)})"
                )
        args = cfg.get("args")
        if args is None:
            continue
        if not isinstance(args, list):
            messages.append(f"server {name!r}: 'args' must be a list")
            continue
        if not all(isinstance(a, str) for a in args):
            messages.append(f"server {name!r}: 'args' must contain only strings")
    return messages


def _collect_mcp(wd: Path, report: DiagnosticReport) -> None:
    """Validate MCP config files and report server counts."""
    from ...paths import user_root

    ur = user_root()
    user_path = ur / "mcp.json"
    project_path = wd / ".agentao" / "mcp.json"

    section: Dict[str, Any] = {
        "user_path": str(user_path),
        "user_status": "absent",
        "user_server_count": 0,
        "project_path": str(project_path),
        "project_status": "absent",
        "project_server_count": 0,
    }
    server_names: Dict[str, set] = {"user": set(), "project": set()}

    def _check(label: str, path: Path) -> None:
        data, status, finding = _load_json_object(
            path, area="mcp", label=f"{label}-scope mcp.json",
        )
        if finding is not None:
            report.add(finding)
        section[f"{label}_status"] = status
        if status != "ok" or data is None:
            return
        servers = data.get("mcpServers", {})
        if not isinstance(servers, dict):
            section[f"{label}_status"] = "malformed"
            report.add(Finding(
                level="error",
                area="mcp",
                message=f"'mcpServers' in {label}-scope mcp.json must be an object",
                source=str(path),
            ))
            return
        # Each entry value must also be a dict — the runtime loader's
        # ``_expand_config_env`` calls ``dict(config)`` on it and will raise
        # on a string/list/null, so validation should reject it up front.
        bad_entries = [
            name for name, cfg in servers.items() if not isinstance(cfg, dict)
        ]
        if bad_entries:
            section[f"{label}_status"] = "malformed"
            report.add(Finding(
                level="error",
                area="mcp",
                message=(
                    f"{label}-scope mcp.json has non-object server entries: "
                    f"{', '.join(repr(n) for n in bad_entries)}"
                ),
                source=str(path),
            ))
            return
        # The runtime loader's ``expand_env_vars`` walks ``env``, ``headers``,
        # and ``args`` and assumes every value is a string. Non-string values
        # would raise ``TypeError`` at startup. Validate them up front so the
        # failure is visible from ``config validate`` instead of first run.
        nested_errors = _validate_mcp_server_fields(servers)
        if nested_errors:
            section[f"{label}_status"] = "malformed"
            for msg in nested_errors:
                report.add(Finding(
                    level="error",
                    area="mcp",
                    message=f"{label}-scope mcp.json: {msg}",
                    source=str(path),
                ))
            return
        section[f"{label}_server_count"] = len(servers)
        server_names[label] = set(servers.keys())

    _check("user", user_path)
    _check("project", project_path)

    # User scope wins on name collision; the runtime drops project-scope
    # entries that share a name with a user entry and logs a warning. The
    # validator must mirror that so the user notices the project entry will
    # not take effect.
    shadowed = server_names["user"] & server_names["project"]
    if shadowed:
        section["shadowed_project_servers"] = sorted(shadowed)
        report.add(Finding(
            level="warning",
            area="mcp",
            message=(
                "Project-scope mcp.json entries are shadowed by user-scope "
                "entries with the same name (project entries ignored at "
                f"runtime): {', '.join(repr(n) for n in sorted(shadowed))}"
            ),
            source=str(project_path),
        ))

    report.sections["mcp"] = section


def _collect_replay(
    wd: Path,
    report: DiagnosticReport,
    settings_data: Optional[Dict[str, Any]],
) -> None:
    """Report effective replay configuration.

    Takes the already-parsed ``settings.json`` dict so the doctor doesn't
    open the same file twice. ``ReplayConfig.from_mapping`` is *deliberately*
    lenient at runtime — it coerces malformed values to defaults so a broken
    settings file never blocks startup. That leniency is the wrong default
    for an explicit validation command, so we walk the raw block first and
    surface findings for shapes that ``from_mapping`` would silently swallow.
    """
    from ...replay.config import CAPTURE_FLAG_DEFAULTS, ReplayConfig, settings_path

    raw = settings_data.get("replay") if settings_data else None
    source = str(settings_path(wd))

    if raw is not None and not isinstance(raw, dict):
        report.add(Finding(
            level="error",
            area="replay",
            message=(
                f"'replay' in settings.json must be an object, got "
                f"{type(raw).__name__}"
            ),
            source=source,
        ))
    elif isinstance(raw, dict):
        if "enabled" in raw and not isinstance(raw["enabled"], (bool, str)):
            report.add(Finding(
                level="error",
                area="replay",
                message=(
                    f"replay.enabled must be a bool or bool-like string, "
                    f"got {type(raw['enabled']).__name__}"
                ),
                source=source,
            ))
        if "max_instances" in raw:
            try:
                parsed = int(raw["max_instances"])
            except (TypeError, ValueError):
                report.add(Finding(
                    level="error",
                    area="replay",
                    message=(
                        f"replay.max_instances must be an integer, got "
                        f"{raw['max_instances']!r}"
                    ),
                    source=source,
                ))
            else:
                # ``from_mapping`` silently replaces any value < 1 with the
                # default — that means ``max_instances: 0`` parses but is
                # ignored at runtime. Surface it as a validation error so
                # the user does not assume their (no-op) value applied.
                if parsed < 1:
                    report.add(Finding(
                        level="error",
                        area="replay",
                        message=(
                            f"replay.max_instances must be >= 1, got "
                            f"{parsed} (ignored at runtime)"
                        ),
                        source=source,
                    ))
        raw_flags = raw.get("capture_flags")
        if raw_flags is not None and not isinstance(raw_flags, dict):
            report.add(Finding(
                level="error",
                area="replay",
                message=(
                    f"replay.capture_flags must be an object, got "
                    f"{type(raw_flags).__name__}"
                ),
                source=source,
            ))
        elif isinstance(raw_flags, dict):
            unknown = [k for k in raw_flags if k not in CAPTURE_FLAG_DEFAULTS]
            if unknown:
                report.add(Finding(
                    level="warning",
                    area="replay",
                    message=(
                        f"Unknown replay.capture_flags keys (ignored at "
                        f"runtime): {', '.join(repr(k) for k in unknown)}"
                    ),
                    source=source,
                ))
            for key, value in raw_flags.items():
                if key in CAPTURE_FLAG_DEFAULTS and not isinstance(value, (bool, str)):
                    report.add(Finding(
                        level="error",
                        area="replay",
                        message=(
                            f"replay.capture_flags[{key!r}] must be a bool or "
                            f"bool-like string, got {type(value).__name__}"
                        ),
                        source=source,
                    ))

    cfg = ReplayConfig.from_mapping(raw)
    report.sections["replay"] = {
        "source": source,
        "status": "ok",
        "enabled": cfg.enabled,
        "max_instances": cfg.max_instances,
        "capture_flags": dict(cfg.capture_flags),
        "deep_capture_enabled": cfg.deep_capture_enabled(),
    }


def _collect_acp_schema(report: DiagnosticReport) -> None:
    """Best-effort probe that the host/ACP schema exports still resolve."""
    section: Dict[str, Any] = {}
    try:
        from ...host.schema import (
            export_host_acp_json_schema,
            export_host_event_json_schema,
        )
        events_schema = export_host_event_json_schema()
        acp_schema = export_host_acp_json_schema()
        section["status"] = "ok"
        section["events_defs"] = len(events_schema.get("$defs", {}))
        section["acp_defs"] = len(acp_schema.get("$defs", {}))
    except Exception as exc:
        section["status"] = "error"
        section["error"] = f"{type(exc).__name__}: {exc}"
        report.add(Finding(
            level="error",
            area="acp_schema",
            message=f"Host/ACP schema export failed: {type(exc).__name__}: {exc}",
        ))
    report.sections["acp_schema"] = section


def _collect_memory_stores(wd: Path, report: DiagnosticReport) -> None:
    """Probe project + user SQLite memory stores without creating them.

    ``sqlite3.connect`` on a non-existent path silently creates an empty file,
    which would mean running ``agentao doctor`` bootstraps a memory DB in
    directories the user never opted into. We pre-check existence and probe
    read-only when the file is already there.
    """
    from ...paths import user_root

    project_path = wd / ".agentao" / "memory.db"
    ur = user_root()
    user_path: Optional[Path] = (ur / "memory.db") if ur is not None else None

    section: Dict[str, Any] = {
        "project_path": str(project_path),
        "project_status": "absent",
        "user_path": str(user_path) if user_path else None,
        "user_status": "absent" if user_path else "skipped",
    }

    def _probe(path: Path, label: str) -> None:
        if not path.exists():
            return
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            conn.close()
        except (OSError, sqlite3.Error) as exc:
            section[f"{label}_status"] = "unavailable"
            section[f"{label}_error"] = f"{type(exc).__name__}: {exc}"
            report.add(Finding(
                # Project-scope failure degrades to :memory: at runtime and
                # user-scope failure disables the scope entirely; neither
                # crashes the agent, so both are warnings, not errors.
                level="warning",
                area="memory",
                message=(
                    f"{label.capitalize()}-scope memory store unavailable: "
                    f"{type(exc).__name__}: {exc}"
                ),
                source=str(path),
            ))
            return
        section[f"{label}_status"] = "ok"

    _probe(project_path, "project")
    if user_path is not None:
        _probe(user_path, "user")

    report.sections["memory"] = section


def _collect_plugins(report: DiagnosticReport) -> None:
    """Reuse ``collect_full_plugin_diagnostics`` for the plugin section.

    Shares the post-load registration simulation with ``agentao plugin list``
    so the two commands cannot drift on which plugins they consider failed.
    """
    from ...embedding.plugins.diagnostics import collect_full_plugin_diagnostics

    try:
        loaded, failed_plugins, diag = collect_full_plugin_diagnostics(
            inline_dirs=_plugin_inline_dirs or None,
        )
    except Exception as exc:
        report.sections["plugins"] = {
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
        }
        report.add(Finding(
            level="error",
            area="plugins",
            message=f"Plugin discovery failed: {type(exc).__name__}: {exc}",
        ))
        return

    plugins = [
        {
            "name": p.name,
            "version": p.version,
            "source": p.source,
            "marketplace": p.marketplace,
            "qualified_name": p.qualified_name,
            "root_path": str(p.root_path),
            "status": "failed" if p.name in failed_plugins else "ok",
        }
        for p in loaded
    ]
    report.sections["plugins"] = {
        "status": "ok",
        "count": diag.plugin_count,
        "plugins": plugins,
        "warnings": [str(w) for w in diag.warnings],
        "errors": [str(e) for e in diag.errors],
    }
    for w in diag.warnings:
        report.add(Finding(level="warning", area="plugins", message=str(w)))
    for e in diag.errors:
        report.add(Finding(level="error", area="plugins", message=str(e)))


def _collect_optional_deps(report: DiagnosticReport) -> None:
    """Probe optional dependencies relevant to common features."""
    # Keep this list short — only deps the design doc explicitly allows
    # ("only where the feature is configured or obviously requested").
    probes = (
        ("rich", "cli"),
        ("prompt_toolkit", "cli"),
        ("readchar", "cli"),
        ("mcp", "mcp"),
        ("openai", "llm"),
        ("httpx", "web"),
    )
    deps: Dict[str, Dict[str, Any]] = {}
    for name, feature in probes:
        try:
            present = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            present = False
        deps[name] = {"feature": feature, "present": present}
    report.sections["optional_deps"] = deps
