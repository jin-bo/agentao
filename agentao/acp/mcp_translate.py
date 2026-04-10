"""ACP ``mcpServers`` → Agentao MCP config translator (Issue 11).

ACP and Agentao both describe MCP servers, but they use different shapes:

**ACP wire shape** — a JSON array, one entry per server:

.. code-block:: json

    [
      {
        "name": "github",
        "command": "node",
        "args": ["server.js"],
        "env": [{"name": "GITHUB_TOKEN", "value": "ghp_..."}]
      },
      {
        "type": "sse",
        "name": "remote",
        "url": "https://api.example.com/sse",
        "headers": [{"name": "Authorization", "value": "Bearer ..."}]
      }
    ]

Notable: ``env`` and ``headers`` are arrays of ``{name, value}`` objects,
**not** plain dicts. This is the spec choice that buys ordered, duplicate-
preserving fidelity over JSON's unordered objects.

**Agentao internal shape** — a dict, keyed by server name:

.. code-block:: python

    {
      "github": {
        "command": "node",
        "args": ["server.js"],
        "env": {"GITHUB_TOKEN": "ghp_..."},
      },
      "remote": {
        "url": "https://api.example.com/sse",
        "headers": {"Authorization": "Bearer ..."},
      },
    }

This module owns the translation between the two — keeping it out of
``session_new.py`` so the same translator can serve ``session/load``
(Issue 10) and any future call sites without code duplication.

Validation policy
-----------------

The ACP entries that hit this function have already been *shape-checked*
by :func:`agentao.acp.session_new._parse_mcp_servers`: each entry is a
dict, ``name`` is a non-empty string, ``type`` is one of ``stdio | sse``,
and the transport-specific fields (``command`` for stdio, ``url`` for
sse) are present and have the right types. So this translator can assume
well-formed input and focus on shape conversion. Anything truly weird
that slips through (e.g. a duplicate server name, a missing ``env``
value, or a stray ``type: "http"`` from a non-conformant client) is
logged and the offending entry is dropped — we never raise from here,
because Issue 11's "non-fatal MCP failures" criterion applies to
translation as well as connection.

``type: "http"`` is intentionally NOT translated. ``McpClient``
distinguishes transports only by ``command`` vs ``url`` and always
opens URL servers via ``sse_client``, so a translated http entry would
silently fail to connect at first tool call. The agent advertises
``mcpCapabilities.http: false`` and the parser rejects ``http`` with
``INVALID_PARAMS``; this defensive branch only fires if a caller bypasses
the parser.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _name_value_list_to_dict(
    raw: Any, *, server_name: str, field: str
) -> Dict[str, str]:
    """Convert an ACP ``[{name, value}, ...]`` array to a ``{name: value}`` dict.

    Bad entries (non-dict, missing name/value, non-string values) are
    logged and dropped — Issue 11 says MCP-related failures must be
    non-fatal, and a single bogus header should not destroy the rest of
    the server's configuration.

    Returns an empty dict for ``None`` (the field was omitted) so the
    caller can still spread it into a config without an extra check.
    """
    if raw is None:
        return {}
    if not isinstance(raw, list):
        logger.warning(
            "acp: mcp server %r field %s is not a list; dropping",
            server_name,
            field,
        )
        return {}
    result: Dict[str, str] = {}
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            logger.warning(
                "acp: mcp server %r %s[%d] is not an object; skipping",
                server_name,
                field,
                i,
            )
            continue
        name = item.get("name")
        value = item.get("value")
        if not isinstance(name, str) or not name:
            logger.warning(
                "acp: mcp server %r %s[%d].name is missing or not a string; skipping",
                server_name,
                field,
                i,
            )
            continue
        if not isinstance(value, str):
            logger.warning(
                "acp: mcp server %r %s[%d].value is not a string; skipping",
                server_name,
                field,
                i,
            )
            continue
        if name in result:
            # Duplicate names: ACP's array shape allows duplicates, but
            # Agentao's dict shape does not. Last entry wins, matching
            # how most environment-variable parsers behave.
            logger.debug(
                "acp: mcp server %r %s has duplicate %r; later wins",
                server_name,
                field,
                name,
            )
        result[name] = value
    return result


# ---------------------------------------------------------------------------
# Main translator
# ---------------------------------------------------------------------------

def translate_acp_mcp_servers(
    entries: Iterable[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Translate ACP-shape MCP server entries to Agentao internal config.

    See the module docstring for the input and output shapes. Returns
    a dict keyed by server name. If two ACP entries share the same
    ``name``, the **last** one wins (logged at WARNING level) so the
    function is total — it never raises ``DuplicateServerError``.

    Failure semantics: anything that can't be translated cleanly is
    logged and dropped from the output, NOT raised. The caller can
    inspect the returned dict's size against the input length to detect
    partial failure if it cares.
    """
    result: Dict[str, Dict[str, Any]] = {}
    for index, entry in enumerate(entries or []):
        if not isinstance(entry, dict):
            logger.warning(
                "acp: mcpServers[%d] is not an object; skipping", index
            )
            continue

        name = entry.get("name")
        if not isinstance(name, str) or not name:
            logger.warning(
                "acp: mcpServers[%d].name missing or not a string; skipping",
                index,
            )
            continue

        if name in result:
            logger.warning(
                "acp: mcpServers contains duplicate name %r; later entry wins",
                name,
            )

        transport_type = entry.get("type", "stdio")
        cfg: Dict[str, Any]

        if transport_type == "stdio":
            command = entry.get("command")
            if not isinstance(command, str) or not command:
                logger.warning(
                    "acp: mcp stdio server %r missing 'command'; skipping",
                    name,
                )
                continue
            args = entry.get("args", [])
            if not isinstance(args, list) or not all(
                isinstance(a, str) for a in args
            ):
                logger.warning(
                    "acp: mcp stdio server %r 'args' is not a list of strings; "
                    "using empty args",
                    name,
                )
                args = []
            cfg = {"command": command, "args": list(args)}
            env = _name_value_list_to_dict(
                entry.get("env"), server_name=name, field="env"
            )
            if env:
                cfg["env"] = env

        elif transport_type == "sse":
            url = entry.get("url")
            if not isinstance(url, str) or not url:
                logger.warning(
                    "acp: mcp sse server %r missing 'url'; skipping",
                    name,
                )
                continue
            cfg = {"url": url}
            headers = _name_value_list_to_dict(
                entry.get("headers"), server_name=name, field="headers"
            )
            if headers:
                cfg["headers"] = headers

        else:
            # ``http`` falls into this branch because it is NOT in the
            # accepted set above. McpClient cannot dispatch http (only
            # stdio + sse), and accepting it would silently route through
            # ``sse_client`` and fail at runtime. The parser rejects http
            # earlier with INVALID_PARAMS; this is the defensive backstop.
            logger.warning(
                "acp: mcp server %r has unsupported transport type %r; "
                "skipping (only 'stdio' and 'sse' are supported)",
                name,
                transport_type,
            )
            continue

        # ACP-provided servers are NOT auto-trusted — clients still go
        # through the standard ``session/request_permission`` flow before
        # any MCP tool runs. The ``trust`` flag in Agentao's MCP config
        # is reserved for explicit project-level opt-in via
        # ``.agentao/mcp.json``.
        cfg["trust"] = False

        result[name] = cfg

    return result
