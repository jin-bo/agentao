"""Shims for the MCP SDK's 1.x → 2.x break.

mcp 2.0 moved its wire models into the split-out ``mcp-types`` package and,
in doing so, changed four things agentao depends on:

1. every wire field is now a snake_case Python attribute with the camelCase
   JSON name demoted to an alias (``inputSchema`` → ``input_schema``,
   ``isError`` → ``is_error``, …), so attribute reads by the old name raise
   ``AttributeError``;
2. the HTTP stack moved from ``httpx`` to ``httpx2``, and an ``httpx.Timeout``
   handed to the 2.x client factory raises ``TypeError: unhashable type``;
3. ``read_timeout_seconds`` went from ``timedelta`` to plain float seconds,
   which flow into ``anyio.fail_after`` (a ``timedelta`` there is a
   ``TypeError``);
4. 2.0 added a second protocol *era* — ``server/discover`` (2026-07-28 and up)
   alongside the legacy ``initialize`` handshake — and renamed the JSON-RPC
   error class ``McpError`` → ``MCPError`` with no alias left behind.

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

import mcp.types
from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client
from mcp.shared import exceptions as _mcp_exceptions


def field(obj: Any, camel: str, snake: str) -> Any:
    """Read a wire field across the camelCase (1.x) / snake_case (2.x) split.

    Dispatches on the model's **declared fields**, not on ``hasattr``. The wire
    models are ``extra='allow'`` on both majors, so a server can ship a
    ``protocol_version`` / ``is_error`` key of its own; on 1.x — where those
    names are not fields — pydantic keeps them as *extras*, and a ``hasattr``
    probe would then resolve to the server's unvalidated extra in preference to
    the SDK-validated camelCase field. Reading the declared field takes that
    choice away from the peer.

    ``getattr(obj, snake, None)`` is not an option either: ``isError`` is
    legitimately ``False`` and ``structuredContent`` legitimately ``None``, so a
    default-based probe would fall through for exactly the values that matter.
    The ``hasattr`` dispatch survives only as the tail case, for an object that
    declares neither name (a non-pydantic stand-in).
    """
    declared = getattr(type(obj), "model_fields", None) or {}
    if snake in declared:
        return getattr(obj, snake)
    if camel in declared:
        return getattr(obj, camel)
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


#: The exception the SDK raises for a JSON-RPC error arriving over the wire.
#: 1.x spells it ``McpError``, 2.x ``MCPError``, with no alias left behind.
#:
#: **Only safe for ``except`` and for reading ``.error``.** The two classes are
#: not interchangeable beyond that: 1.x is ``McpError(error: ErrorData)`` and
#: exposes *no* ``.code`` / ``.message`` / ``.data``, while 2.x is
#: ``MCPError(code, message, data=None)`` and does. So read the code as
#: ``exc.error.code`` (the one spelling that works on both) and never construct
#: one through this alias — a ``McpProtocolError(code=…)`` call is green on the
#: 2.x lock and a ``TypeError`` on both 1.x CI cells.
McpProtocolError = getattr(_mcp_exceptions, "MCPError", None) or _mcp_exceptions.McpError

#: True when the installed SDK knows the *modern* protocol era (2026-07-28 and
#: up). Only that era has ``server/discover``, the one round-trip that returns a
#: server's full ``supportedVersions`` list — the legacy ``initialize``
#: handshake returns a single counter-offer and can never report anything above
#: the version the client proposed. mcp 1.x has no modern era at all, so the
#: probe is skipped outright rather than attempted and caught.
#:
#: Probed off ``ClientSession`` because that is the object agentao actually
#: drives; 2.x's high-level ``Client`` facade (with its ``mode='auto'``) owns
#: the transport, which agentao establishes itself.
SUPPORTS_MODERN_ERA = hasattr(ClientSession, "discover")

#: Whether ``ClientSession.call_tool`` takes ``allow_input_required`` — the
#: modern era's opt-in for *receiving* an ``InputRequiredResult`` rather than
#: having the SDK raise on one. Absent on 1.x, whose era has no such result.
SUPPORTS_INPUT_REQUIRED = (
    "allow_input_required" in inspect.signature(ClientSession.call_tool).parameters
)

#: The modern-era "I need more input before I can answer" result, or ``None``
#: on an SDK with no modern era. Paired with the flag above so callers can
#: ``isinstance`` the outcome instead of pattern-matching an error string.
InputRequiredResult = getattr(mcp.types, "InputRequiredResult", None)

#: The SDK's "server used an extension you did not opt into" error, or ``None``
#: on 1.x. Like ``InputRequiredResult`` its message is written for a
#: *programmer*, so callers catch it by type and say something else.
UnexpectedClaimedResult = getattr(
    sys.modules[ClientSession.__module__], "UnexpectedClaimedResult", None
)

#: The spec's ``UnsupportedProtocolVersion`` JSON-RPC code (-32022).
#:
#: Preferred off the SDK, but with a literal fallback — unlike everything else
#: in this module, and deliberately. This is not an SDK *shape*: it is a number
#: the **peer** puts on the wire, and a modern-only server sends it whichever
#: major the client happens to run. 1.x simply has no name for it, so a
#: probe-only read would make the code invisible on exactly the two cells where
#: recognizing it matters most — that is where the mismatch is unresolvable and
#: the diagnosis has to be right.
UNSUPPORTED_PROTOCOL_VERSION: int = getattr(
    mcp.types, "UNSUPPORTED_PROTOCOL_VERSION", -32022
)
