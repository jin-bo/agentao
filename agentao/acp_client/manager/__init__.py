"""ACP client manager — registry of per-server process handles and clients.

:class:`ACPManager` is the single entry point for the CLI and agent layers to
start, stop, query, and communicate with project-local ACP servers.

The implementation was split from a monolithic ``manager.py`` module into
focused submodules by concern (lifecycle, connection, turns, interactions,
status, recovery).  ``ACPManager`` is composed from mixins defined in those
submodules; the package's public import surface is unchanged.

``_TurnContext`` is re-exported for tests that construct it directly
(``tests/test_acp_client_cli.py``, ``tests/test_acp_client_embedding.py``).
"""

from __future__ import annotations

from ..client import AcpServerNotFound
from .core import ACPManager
from .helpers import (
    _extract_display_text,
    _extract_options,
    _format_permission_text,
    _format_session_update,
    _select_approve_option,
    _select_option_by_kind,
    _select_reject_option,
    _truncate,
)
from .turns import _TurnContext

__all__ = [
    "ACPManager",
    "AcpServerNotFound",
    "_TurnContext",
    "_extract_display_text",
    "_extract_options",
    "_format_permission_text",
    "_format_session_update",
    "_select_approve_option",
    "_select_option_by_kind",
    "_select_reject_option",
    "_truncate",
]
