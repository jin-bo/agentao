"""Back-compat shim — implementation moved to the ``diagnostics`` subpackage.

Historically this module held the whole ``agentao doctor`` / ``config
validate`` implementation. It is now split across ``agentao.cli.diagnostics``
(see that package's ``__init__`` for the module map). This shim re-exports the
public entry points plus the records / collectors that ``agentao.cli`` and the
test suite import by name, so existing ``from agentao.cli.diagnostics_cli
import ...`` call sites keep working. New code should import from
``agentao.cli.diagnostics`` directly.
"""

from __future__ import annotations

from .diagnostics.collectors import (  # noqa: F401
    _collect_acp_schema,
    _collect_memory_stores,
    _collect_mcp,
    _collect_optional_deps,
    _collect_permissions,
    _collect_plugins,
    _collect_provider,
    _collect_replay,
    _collect_settings,
    _validate_mcp_server_fields,
)
from .diagnostics.commands import (
    handle_config_subcommand,
    handle_config_validate_subcommand,
    handle_doctor_subcommand,
)
from .diagnostics.loaders import _load_dotenv, _load_json_object  # noqa: F401
from .diagnostics.models import (  # noqa: F401
    DiagnosticReport,
    FileStatus,
    Finding,
    FindingLevel,
)
from .diagnostics.render import _FINDING_TAG, _render_human  # noqa: F401

__all__ = [
    "DiagnosticReport",
    "Finding",
    "handle_config_subcommand",
    "handle_config_validate_subcommand",
    "handle_doctor_subcommand",
]
