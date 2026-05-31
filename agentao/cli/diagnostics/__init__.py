"""Diagnostics subpackage backing ``agentao doctor`` / ``agentao config validate``.

The implementation is split across focused modules:

- ``models``     — :class:`Finding` / :class:`DiagnosticReport` records.
- ``loaders``    — dotenv bootstrap + the shared JSON-object loader.
- ``collectors`` — the per-section ``_collect_*`` probes.
- ``render``     — human-readable console rendering.
- ``commands``   — the public ``handle_*`` subcommand entry points.

``agentao.cli.diagnostics_cli`` remains as a back-compat re-export shim.
"""

from __future__ import annotations

from .commands import (
    handle_config_subcommand,
    handle_config_validate_subcommand,
    handle_doctor_subcommand,
)
from .models import DiagnosticReport, Finding

__all__ = [
    "DiagnosticReport",
    "Finding",
    "handle_config_subcommand",
    "handle_config_validate_subcommand",
    "handle_doctor_subcommand",
]
