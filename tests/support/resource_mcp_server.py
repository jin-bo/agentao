"""A real stdio MCP server with resources, written without the SDK.

Speaks just enough MCP — ``initialize``, ``tools/list``, ``tools/call``,
``resources/list``, ``resources/templates/list``, ``resources/read`` — to run
on both SDK majors. Every request method is appended to ``<marks>/methods``,
and every launch touches ``<marks>/started-<pid>``.

Files the test creates in ``marks`` change what it does:

- ``no-resources``: declare no ``resources`` capability;
- ``no-templates``: answer ``resources/templates/list`` with ``-32601``;
- ``fail-list``: answer ``resources/list`` with an internal error;
- ``drop-once``: exit (once) on the next ``resources/read`` instead of answering;
- ``resources-only``: declare no ``tools`` and answer ``tools/list`` with ``-32601``;
- ``tools-refused``: declare ``tools`` but answer ``tools/list`` with ``-32601``.
"""

from __future__ import annotations

import base64
import sys
import textwrap
from pathlib import Path
from typing import Any, Dict, Tuple

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16

SCRIPT = textwrap.dedent('''
    import base64, json, os, sys
    from pathlib import Path

    marks = Path(sys.argv[1])
    marks.mkdir(parents=True, exist_ok=True)
    (marks / f"started-{os.getpid()}").touch()
    out = sys.stdout.buffer
    PNG = base64.b64encode(PNG_BYTES).decode()

    def reply(msg_id, **body):
        out.write(json.dumps({"jsonrpc": "2.0", "id": msg_id, **body}).encode() + b"\\n")
        out.flush()

    PAGE1 = [
        {"uri": "note://a", "name": "a", "title": "Note A", "mimeType": "text/plain",
         "size": 5, "_meta": {"secret": 1}, "icons": [{"src": "x.png"}],
         "annotations": {"audience": ["user"]}},
        {"uri": "ui://widget", "name": "widget", "mimeType": "text/html;profile=mcp-app"},
    ]
    PAGE2 = [{"uri": "bin://img.png", "name": "img", "mimeType": "image/png"}]

    READS = {
        "note://a": [{"uri": "note://a", "mimeType": "text/plain", "text": "hello"}],
        "multi://x": [
            {"uri": "multi://x/1", "text": "one"},
            {"uri": "multi://x/2", "text": "two"},
        ],
        "bin://img.png": [{"uri": "bin://img.png", "mimeType": "image/png", "blob": PNG}],
        "json://data": [{"uri": "json://data", "mimeType": "application/json",
                         "blob": base64.b64encode(b'{"k": 1}').decode()}],
        "bad64://x": [{"uri": "bad64://x", "mimeType": "image/png", "blob": "!!!not base64!!!"}],
        "badutf://x": [{"uri": "badutf://x", "mimeType": "text/plain",
                        "blob": base64.b64encode(b"\\xff\\xfe\\xfa").decode()}],
        "empty://x": [],
        "https://example.invalid/doc": [{"uri": "https://example.invalid/doc", "text": "via server"}],
    }

    for line in iter(sys.stdin.buffer.readline, b""):
        msg = json.loads(line)
        if "id" not in msg:
            continue
        method = msg["method"]
        params = msg.get("params") or {}
        with open(marks / "methods", "a") as log:
            log.write(method + "\\n")
        if method == "initialize":
            caps = {} if (marks / "resources-only").exists() else {"tools": {}}
            if not (marks / "no-resources").exists():
                caps["resources"] = {}
            reply(msg["id"], result={
                "protocolVersion": params["protocolVersion"],
                "capabilities": caps,
                "serverInfo": {"name": "resource-probe", "version": "0"},
            })
        elif method == "tools/list" and (
            (marks / "resources-only").exists() or (marks / "tools-refused").exists()
        ):
            reply(msg["id"], error={"code": -32601, "message": "Method not found"})
        elif method == "tools/list":
            reply(msg["id"], result={"tools": [
                {"name": "link", "inputSchema": {"type": "object"}},
                {"name": "embed", "inputSchema": {"type": "object"}},
            ]})
        elif method == "tools/call":
            if params["name"] == "link":
                content = [{"type": "resource_link", "uri": "report://q3", "name": "q3",
                            "title": "Q3 report", "mimeType": "application/pdf", "size": 2048,
                            "description": "The quarterly report"}]
            else:
                content = [{"type": "resource", "resource": {
                    "uri": "bin://img.png", "mimeType": "image/png", "blob": PNG}}]
            reply(msg["id"], result={"content": content, "isError": False})
        elif method == "resources/list":
            if (marks / "fail-list").exists():
                reply(msg["id"], error={"code": -32603, "message": "listing broke"})
            elif params.get("cursor") == "p2":
                reply(msg["id"], result={"resources": PAGE2})
            else:
                reply(msg["id"], result={"resources": PAGE1, "nextCursor": "p2"})
        elif method == "resources/templates/list":
            if (marks / "no-templates").exists():
                reply(msg["id"], error={"code": -32601, "message": "Method not found"})
            else:
                reply(msg["id"], result={"resourceTemplates": [
                    {"uriTemplate": "note://{id}", "name": "note", "mimeType": "text/plain",
                     "_meta": {"x": 1}},
                ]})
        elif method == "resources/read":
            uri = params["uri"]
            drop = marks / "drop-once"
            if drop.exists():
                drop.unlink()
                sys.exit(0)
            if uri == "missing://x":
                reply(msg["id"], error={"code": -32602, "message": "Resource not found"})
            elif uri == "old-missing://x":
                reply(msg["id"], error={"code": -32002, "message": "Resource not found"})
            elif uri in READS:
                reply(msg["id"], result={"contents": READS[uri]})
            else:
                reply(msg["id"], error={"code": -32602, "message": "Resource not found"})
        else:
            reply(msg["id"], error={"code": -32601, "message": method})
''').replace("PNG_BYTES", repr(PNG))


def resource_server(directory: Path, **extra: Any) -> Tuple[Dict[str, Any], Path]:
    """Write the server into ``directory``; return ``(config, marks)``."""
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "resource_probe_server.py"
    script.write_text(SCRIPT)
    marks = directory / "marks"
    marks.mkdir(exist_ok=True)
    config = {"command": sys.executable, "args": [str(script), str(marks)], **extra}
    return config, marks


def methods(marks: Path) -> list:
    path = marks / "methods"
    return path.read_text().split() if path.exists() else []


def started(marks: Path) -> list:
    return sorted(marks.glob("started-*"))


PNG_B64 = base64.b64encode(PNG).decode()
