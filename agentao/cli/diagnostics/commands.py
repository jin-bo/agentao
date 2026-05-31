"""Public entry points for ``agentao doctor`` and ``agentao config validate``.

Both commands aggregate or validate signals Agentao already produces; they do
not introduce a new diagnostics subsystem and they do not change runtime
startup semantics. See ``docs/design/codex-reverse-review.md`` (2026-05-17
follow-up) for the rationale.

Redaction policy: neither command prints API keys, environment variable
values, or other secrets. They report presence / parse-status / source paths.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .collectors import (
    _collect_acp_schema,
    _collect_memory_stores,
    _collect_mcp,
    _collect_optional_deps,
    _collect_permissions,
    _collect_plugins,
    _collect_provider,
    _collect_replay,
    _collect_settings,
)
from .loaders import _load_dotenv
from .models import DiagnosticReport
from .render import _render_human


def handle_doctor_subcommand(args) -> None:
    """Run ``agentao doctor``: aggregate every health signal Agentao already owns."""
    wd = Path.cwd().resolve()
    _load_dotenv(wd)
    report = DiagnosticReport()

    settings_data = _collect_settings(wd, report)
    _collect_provider(report)
    _collect_permissions(wd, report)
    _collect_mcp(wd, report)
    _collect_replay(wd, report, settings_data)
    _collect_acp_schema(report)
    _collect_memory_stores(wd, report)
    _collect_plugins(report)
    _collect_optional_deps(report)

    if getattr(args, "json_output", False):
        print(json.dumps(report.to_dict(), indent=2))
    else:
        _render_human(report, header=f"agentao doctor — {wd}")

    # Doctor never exits non-zero on warnings; only errors flip the gate.
    if not report.ok:
        sys.exit(1)


def handle_config_validate_subcommand(args) -> None:
    """Run ``agentao config validate``: report config errors without changing startup."""
    wd = Path.cwd().resolve()
    _load_dotenv(wd)
    report = DiagnosticReport()

    settings_data = _collect_settings(wd, report)
    _collect_provider(report)
    _collect_permissions(wd, report)
    _collect_mcp(wd, report)
    _collect_replay(wd, report, settings_data)
    _collect_memory_stores(wd, report)

    if getattr(args, "json_output", False):
        print(json.dumps(report.to_dict(), indent=2))
    else:
        _render_human(report, header=f"agentao config validate — {wd}")

    if not report.ok:
        sys.exit(1)


def handle_config_subcommand(args) -> None:
    """Dispatch ``agentao config <action>`` subcommands."""
    action = getattr(args, "config_action", None)
    if action == "validate":
        handle_config_validate_subcommand(args)
    else:
        sys.stderr.write("Usage: agentao config {validate}\n")
        sys.exit(2)
