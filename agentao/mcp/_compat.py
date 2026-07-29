"""Shims for the MCP SDK's 1.x → 2.x break.

mcp 2.0 moved its wire models into the split-out ``mcp-types`` package and,
in doing so, changed three things agentao depends on:

1. every wire field is now a snake_case Python attribute with the camelCase
   JSON name demoted to an alias (``inputSchema`` → ``input_schema``,
   ``isError`` → ``is_error``, …), so attribute reads by the old name raise
   ``AttributeError``;
2. the HTTP stack moved from ``httpx`` to ``httpx2``, and an ``httpx.Timeout``
   handed to the 2.x client factory raises ``TypeError: unhashable type``;
3. ``read_timeout_seconds`` went from ``timedelta`` to plain float seconds,
   which flow into ``anyio.fail_after`` (a ``timedelta`` there is a
   ``TypeError``).

agentao supports both majors, so each difference is **probed off the
installed SDK** rather than sniffed from a version string. A version string
would be a second source of truth that can disagree with what is actually
importable — a vendored or patched SDK, or a 2.x prerelease, would be
misread. Probing asks the object that will actually receive the value.
"""

import inspect
import sys
from datetime import timedelta
from typing import Any, Dict, Optional

from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client


def field(obj: Any, camel: str, snake: str) -> Any:
    """Read a wire field across the camelCase (1.x) / snake_case (2.x) split.

    Dispatches on ``hasattr``, not ``getattr(obj, snake, None)``: ``isError``
    is legitimately ``False`` and ``structuredContent`` legitimately ``None``,
    so a default-based probe would fall through to the wrong attribute for
    exactly the values that matter.
    """
    return getattr(obj, snake) if hasattr(obj, snake) else getattr(obj, camel)


def annotations_dict(ann: Any) -> Dict[str, Any]:
    """Dump ``ToolAnnotations`` with camelCase keys on both majors.

    ``by_alias=True`` is a no-op on 1.x — whose field names already *are* the
    camelCase wire names — and restores the wire names on 2.x. Callers (and
    hosts introspecting ``McpTool.mcp_annotations``) therefore keep reading
    ``readOnlyHint`` / ``destructiveHint``, the names the MCP spec defines.
    Returns ``{}`` when the server sent no annotations.
    """
    if ann is None:
        return {}
    return ann.model_dump(exclude_none=True, by_alias=True)


def _resolve_httpx():
    """Return the httpx flavour the installed SDK builds its clients with.

    Read off the client factory's own module rather than by import-probing
    ``httpx2``: both packages can coexist in one environment (agentao's core
    depends on ``httpx`` directly, and so does ``openai``), so the presence
    of ``httpx2`` says nothing about which one *this* SDK will accept.
    """
    module = sys.modules[create_mcp_http_client.__module__]
    return getattr(module, "httpx2", None) or module.httpx


#: The ``httpx``-compatible module whose ``Timeout`` / ``AsyncClient`` the
#: installed SDK accepts. Only used for objects handed *to* the SDK; agentao's
#: own preflight keeps using plain ``httpx`` (its own declared dependency).
httpx_for_mcp = _resolve_httpx()


# 1.x annotates ``read_timeout_seconds`` as a real ``datetime.timedelta``
# class; 2.x as the string ``'float | None'`` (the module uses PEP 563
# deferred annotations). Matching on the rendered text covers both forms.
_READ_TIMEOUT_IS_TIMEDELTA = "timedelta" in str(
    inspect.signature(ClientSession.call_tool)
    .parameters["read_timeout_seconds"]
    .annotation
)


def read_timeout(seconds: Optional[float]) -> Any:
    """Convert a per-request budget to what the installed SDK expects.

    ``None`` passes through unchanged — it means "unbounded" on both majors.
    """
    if seconds is None:
        return None
    return timedelta(seconds=seconds) if _READ_TIMEOUT_IS_TIMEDELTA else seconds
