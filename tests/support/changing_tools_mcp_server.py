"""A real stdio MCP server, written without the SDK, whose tool list changes.

It serves the tools listed in ``<marks>/tools.json`` (read on every
``tools/list``) and declares ``tools.listChanged``. A call to its ``change``
tool (always served) rewrites that file with ``change`` and the call's
``tools`` argument and sends
``notifications/tools/list_changed`` ``notices`` times (default 1) before it
answers, so a test changes the catalogue through the client it is testing.
Any other tool call answers with the tool's name.

Every request method is appended to ``<marks>/methods``. While
``<marks>/list_error`` exists, ``tools/list`` answers with an error; while
``<marks>/list_hang`` exists, it is never answered.
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Tuple

SCRIPT = textwrap.dedent('''
    import json, sys
    from pathlib import Path

    marks = Path(sys.argv[1])
    resources = sys.argv[2:] == ["resources"]
    out = sys.stdout.buffer

    def send(msg):
        out.write(json.dumps({"jsonrpc": "2.0", **msg}).encode() + b"\\n")
        out.flush()

    for line in iter(sys.stdin.buffer.readline, b""):
        msg = json.loads(line)
        if "id" not in msg:
            continue
        method = msg["method"]
        with open(marks / "methods", "a") as log:
            log.write(method + "\\n")
        if method == "initialize":
            send({"id": msg["id"], "result": {
                "protocolVersion": msg["params"]["protocolVersion"],
                "capabilities": (
                    {"tools": {"listChanged": True}, "resources": {}} if resources
                    else {"tools": {"listChanged": True}}
                ),
                "serverInfo": {"name": "changing-tools", "version": "0"},
            }})
        elif method == "tools/list":
            if (marks / "list_hang").exists():
                continue  # never answered
            if (marks / "list_error").exists():
                send({"id": msg["id"], "error": {"code": -32603, "message": "list failed"}})
            else:
                tools = json.loads((marks / "tools.json").read_text())
                send({"id": msg["id"], "result": {"tools": tools}})
        elif method == "tools/call":
            params = msg["params"]
            name = params["name"]
            args = params.get("arguments") or {}
            if name == "change":
                change = {"name": "change", "inputSchema": {"type": "object"}}
                (marks / "tools.json").write_text(json.dumps([change] + args.get("tools", [])))
                for _ in range(int(args.get("notices", 1))):
                    send({"method": "notifications/tools/list_changed"})
            send({"id": msg["id"], "result": {
                "content": [{"type": "text", "text": name}], "isError": False,
            }})
        else:
            send({"id": msg["id"], "error": {"code": -32601, "message": method}})
''')


def tool(name: str, description: str = "") -> Dict[str, Any]:
    """One ``tools/list`` entry with an empty object schema."""
    return {"name": name, "description": description, "inputSchema": {"type": "object"}}


def changing_tools_server(
    directory: Path, tools: List[Dict[str, Any]], *, resources: bool = False, **config: Any,
) -> Tuple[Dict[str, Any], Path]:
    """Write the server into ``directory``; return ``(config, marks)``.

    ``tools`` is the list it serves at first; ``change`` is always added to
    it. ``resources`` declares the ``resources`` capability. ``config`` keys (``refresh_tools`` and so on) go into the trusted
    stdio server entry.
    """
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "changing_tools_server.py"
    script.write_text(SCRIPT)
    marks = directory / "marks"
    marks.mkdir(parents=True, exist_ok=True)
    change = {"name": "change", "inputSchema": {"type": "object"}}
    (marks / "tools.json").write_text(json.dumps([change] + list(tools)))
    entry = {
        "command": sys.executable,
        "args": [str(script), str(marks)] + (["resources"] if resources else []),
        "trust": True,
        **config,
    }
    return entry, marks


def methods(marks: Path) -> List[str]:
    """Every request method the server received, in order."""
    log = marks / "methods"
    return log.read_text().splitlines() if log.exists() else []
