"""A real stdio MCP server serving the Skills extension, written without the SDK.

Speaks both protocol eras — ``initialize`` (handshake) and ``server/discover``
(2026-07-28) — plus ``tools/list``, ``tools/call``, ``skills/list``,
``skills/get`` and ``resources/read``. What it serves is read from
``<marks>/spec.json`` on **every request**, so a test changes the server's
content between two calls by rewriting that file (a changed skill, a tampered
file). Every request method is appended to ``<marks>/methods``.

``spec.json`` keys (all optional):

- ``modern`` (default true): answer ``server/discover``; false → ``-32601``;
- ``handshake`` (default true): answer ``initialize``; false → ``-32022``;
- ``extension`` (default true): declare ``io.modelcontextprotocol/skills``;
- ``pages``: a list of ``skills/list`` pages (lists of entries), chained by
  cursors ``c1``, ``c2``, …; ``repeat_cursor``: every page names ``c1`` next;
- ``list_error``: answer ``skills/list`` with this error code;
- ``get``: ``{uri: entry}`` for ``skills/get``, falling back to the pages;
- ``files``: ``{uri: {"text": …} | {"blob": base64}, "mimeType": …}``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT = textwrap.dedent('''
    import json, sys
    from pathlib import Path

    marks = Path(sys.argv[1])
    out = sys.stdout.buffer

    def reply(msg_id, **body):
        out.write(json.dumps({"jsonrpc": "2.0", "id": msg_id, **body}).encode() + b"\\n")
        out.flush()

    def error(msg_id, code, message):
        reply(msg_id, error={"code": code, "message": message})

    CACHE = {"ttlMs": 0, "cacheScope": "private"}

    for line in iter(sys.stdin.buffer.readline, b""):
        msg = json.loads(line)
        if "id" not in msg:
            continue
        spec = json.loads((marks / "spec.json").read_text())
        method = msg["method"]
        params = msg.get("params") or {}
        with open(marks / "methods", "a") as log:
            log.write(method + "\\n")
        caps = {"tools": {}, "resources": {}}
        if spec.get("extension", True):
            caps["extensions"] = {"io.modelcontextprotocol/skills": {}}
        pages = spec.get("pages", [[]])
        if method == "initialize":
            if not spec.get("handshake", True):
                reply(msg["id"], error={"code": -32022, "message": "unsupported",
                      "data": {"supported": ["2026-07-28"], "requested": params.get("protocolVersion")}})
                continue
            reply(msg["id"], result={
                "protocolVersion": params["protocolVersion"],
                "capabilities": caps,
                "serverInfo": {"name": "skills-probe", "version": "0"},
            })
        elif method == "server/discover":
            if not spec.get("modern", True):
                error(msg["id"], -32601, "Method not found")
                continue
            reply(msg["id"], result={"resultType": "complete",
                  "supportedVersions": ["2026-07-28"], "capabilities": caps, **CACHE})
        elif method == "tools/list":
            reply(msg["id"], result={"resultType": "complete", **CACHE, "tools": [
                {"name": "echo", "inputSchema": {"type": "object"}},
            ]})
        elif method == "tools/call":
            reply(msg["id"], result={"resultType": "complete",
                  "content": [{"type": "text", "text": "echoed"}], "isError": False})
        elif method == "skills/list":
            if "list_error" in spec:
                error(msg["id"], spec["list_error"], "listing broke")
                continue
            cursor = params.get("cursor")
            index = 0 if cursor is None else int(cursor[1:])
            result = {"resultType": "complete", **CACHE, "skills": pages[index]}
            if spec.get("repeat_cursor"):
                result["nextCursor"] = "c1"
            elif index + 1 < len(pages):
                result["nextCursor"] = f"c{index + 1}"
            reply(msg["id"], result=result)
        elif method == "skills/get":
            uri = params.get("uri")
            entry = spec.get("get", {}).get(uri)
            if entry is None:
                entry = next((e for p in pages for e in p if e.get("uri") == uri), None)
            if entry is None:
                error(msg["id"], -32602, "no such skill")
            else:
                reply(msg["id"], result={"resultType": "complete", **CACHE, "skill": entry})
        elif method == "resources/read":
            uri = params["uri"]
            item = spec.get("files", {}).get(uri)
            if item is None:
                error(msg["id"], -32602, "Resource not found")
            else:
                reply(msg["id"], result={"resultType": "complete", **CACHE,
                      "contents": [{"uri": uri, **item}]})
        else:
            error(msg["id"], -32601, method)
''')

EXTENSION = "io.modelcontextprotocol/skills"

#: The spec's own example (ext-skills @ 167da6c, "Example Message Flow"): a
#: 151-byte SKILL.md whose digest the spec prints. Using it pins that the
#: digest is over the UTF-8 encoding of ``text`` (design §9, S3).
PDF_SKILL_MD = (
    "---\nname: pdf-processing\ndescription: Extract, fill, and assemble PDF documents\n"
    "---\n\n# PDF processing\n\nChoose the matching template from `templates/`.\n"
)
PDF_SKILL_MD_DIGEST = "sha256:99b737495721155ece826d57521e2d66141ebdc1344a400487481ea2642ab19e"
INVOICE = "# Invoice\n\nCustomer:\nAmount:\n"
INVOICE_DIGEST = "sha256:61f4ea6d2c75fde1b4977219e7e3107d491c3c26aefb6686e84d6281c088d9ee"


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def skill(
    path: str,
    files: Dict[str, str],
    *,
    frontmatter: Optional[Dict[str, Any]] = None,
    scheme: str = "skill",
) -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
    """``(entry, served files)`` for a skill at ``path`` with text ``files``.

    ``files`` maps a path relative to the skill root to its text; it must
    include ``SKILL.md``. The entry's manifest is computed from the bytes, and
    its frontmatter is read off the SKILL.md unless given.
    """
    root = f"{scheme}://{path}"
    served = {
        f"{root}/{rel}": {"text": text, "mimeType": "text/markdown"}
        for rel, text in files.items()
    }
    entry = {
        "uri": f"{root}/SKILL.md",
        "frontmatter": frontmatter or _frontmatter_of(files["SKILL.md"]),
        "resources": [
            {"uri": uri, "digest": digest(item["text"].encode()), "size": len(item["text"].encode())}
            for uri, item in served.items()
        ],
    }
    return entry, served


def _frontmatter_of(text: str) -> Dict[str, Any]:
    import yaml

    return yaml.safe_load(text.split("---", 2)[1])


def skill_md(name: str, description: str, body: str = "Do the thing.\n", **extra: str) -> str:
    lines = [f"name: {name}", f"description: {description}"] + [f"{k}: {v}" for k, v in extra.items()]
    return "---\n" + "\n".join(lines) + "\n---\n\n" + body


def pdf_skill() -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
    return skill(
        "pdf-processing",
        {"SKILL.md": PDF_SKILL_MD, "templates/invoice.md": INVOICE},
        frontmatter={
            "name": "pdf-processing",
            "description": "Extract, fill, and assemble PDF documents",
        },
    )


def blob(data: bytes, mime: str = "application/octet-stream") -> Dict[str, Any]:
    return {"blob": base64.b64encode(data).decode(), "mimeType": mime}


class SkillsServer:
    """The script on disk, its ``marks`` directory, and its live spec."""

    def __init__(self, directory: Path, **spec: Any):
        directory.mkdir(parents=True, exist_ok=True)
        self.script = directory / "skills_probe_server.py"
        self.script.write_text(SCRIPT)
        self.marks = directory / "marks"
        self.marks.mkdir(exist_ok=True)
        self.spec: Dict[str, Any] = {}
        self.write(**spec)

    def write(self, **spec: Any) -> None:
        self.spec = spec
        (self.marks / "spec.json").write_text(json.dumps(spec))

    def update(self, **changes: Any) -> None:
        self.write(**{**self.spec, **changes})

    def config(self, **extra: Any) -> Dict[str, Any]:
        return {"command": sys.executable, "args": [str(self.script), str(self.marks)],
                "skills": True, **extra}

    def methods(self) -> List[str]:
        path = self.marks / "methods"
        return path.read_text().split() if path.exists() else []
