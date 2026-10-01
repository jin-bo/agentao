"""A fake ACP agent with Terminal Auth, for the client-side auth tests (#381).

``AUTH_AGENT_SCRIPT`` runs in two modes, chosen by its arguments:

* **server** (no ``--login``): reads the credential file named by
  ``MOCK_AUTH_FILE`` **once, at process start**, then serves NDJSON ACP.
  ``initialize`` advertises a ``terminal`` method (``args: ["--login"]``,
  ``env: {"MOCK_LOGIN": "1"}``) only when the client declared Terminal Auth
  in either spelling, plus an ``agent`` method always. ``session/new``
  answers ``auth_required`` (-32000) unless the start-time read found a
  credential. Because the credential is read only at start, a client that
  logs in and then opens a session on the *same* process still gets
  ``auth_required`` — only a restart picks the login up.
* **login** (``--login`` anywhere in argv): reads one line from stdin; a
  non-empty line other than ``fail`` is written to ``MOCK_AUTH_FILE`` and the
  process exits 0, ``fail`` exits 3, end of input exits 1.

Every start appends one JSON line to ``MOCK_AUTH_LOG``: the mode, argv, cwd,
pid, the env keys the tests look at, and — for the server — each
``initialize``'s ``clientCapabilities``.

A fake, not a reference implementation: it handles only what the tests need.
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path
from typing import Any, Dict, List

AUTH_AGENT_SCRIPT = textwrap.dedent("""\
    import json
    import os
    import sys

    LOG = os.environ.get("MOCK_AUTH_LOG")
    CRED = os.environ.get("MOCK_AUTH_FILE")
    WATCHED = ("MOCK_LOGIN", "MOCK_BASE", "OPENAI_API_KEY")

    def log(entry):
        if LOG:
            with open(LOG, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\\n")

    def base_entry(mode):
        return {
            "mode": mode,
            "argv": sys.argv[1:],
            "cwd": os.getcwd(),
            "pid": os.getpid(),
            "env": {k: os.environ.get(k) for k in WATCHED},
        }

    if "--login" in sys.argv[1:]:
        log(base_entry("login"))
        line = sys.stdin.readline()
        if not line:
            sys.exit(1)
        token = line.strip()
        if not token or token == "fail":
            sys.exit(3)
        with open(CRED, "w", encoding="utf-8") as fh:
            fh.write(token)
        sys.exit(0)

    token = None
    if CRED and os.path.exists(CRED):
        with open(CRED, encoding="utf-8") as fh:
            token = fh.read().strip() or None
    log(base_entry("server"))

    def send(msg):
        sys.stdout.write(json.dumps(msg) + "\\n")
        sys.stdout.flush()

    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except ValueError:
            continue
        method, rid = req.get("method", ""), req.get("id")
        params = req.get("params") or {}
        if method == "initialize":
            caps = params.get("clientCapabilities") or {}
            log({"mode": "initialize", "pid": os.getpid(), "clientCapabilities": caps})
            auth = caps.get("auth") if isinstance(caps.get("auth"), dict) else {}
            meta = caps.get("_meta") if isinstance(caps.get("_meta"), dict) else {}
            methods = [{"id": "oauth", "name": "Browser login"}]
            declared = auth.get("terminal") is True or meta.get("terminal-auth") is True
            if declared and not os.environ.get("MOCK_NO_TERMINAL"):
                methods.insert(0, {
                    "id": "mock-login", "name": "Mock login", "type": "terminal",
                    "args": ["--login"], "env": {"MOCK_LOGIN": "1"},
                })
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": 1, "agentCapabilities": {}, "authMethods": methods,
            }})
        elif method == "session/new":
            if token is None:
                send({"jsonrpc": "2.0", "id": rid, "error": {
                    "code": -32000, "message": "Authentication required",
                }})
            else:
                send({"jsonrpc": "2.0", "id": rid, "result": {"sessionId": "s-" + token}})
        elif method == "session/prompt":
            send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})
        elif rid is not None:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": method}})
""")


class AuthAgent:
    """Paths and config for one :data:`AUTH_AGENT_SCRIPT` instance."""

    def __init__(self, tmp_path: Path) -> None:
        self.dir = tmp_path
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.script = tmp_path / "auth_agent.py"
        self.script.write_text(AUTH_AGENT_SCRIPT, encoding="utf-8")
        self.cred_file = tmp_path / "credential.txt"
        self.log_file = tmp_path / "auth_agent.log"
        self.cwd = tmp_path / "work"
        self.cwd.mkdir(exist_ok=True)

    def server_raw(self, **extra: Any) -> Dict[str, Any]:
        """An ``acp.json`` server object launching this agent."""
        raw: Dict[str, Any] = {
            "command": sys.executable,
            "args": [str(self.script), "--acp"],
            "env": {
                "MOCK_AUTH_FILE": str(self.cred_file),
                "MOCK_AUTH_LOG": str(self.log_file),
                "MOCK_BASE": "base",
            },
            "cwd": str(self.cwd),
            "autoStart": False,
            "startupTimeoutMs": 20_000,
            "requestTimeoutMs": 20_000,
        }
        raw.update(extra)
        return raw

    def log(self) -> List[Dict[str, Any]]:
        if not self.log_file.exists():
            return []
        return [
            json.loads(line)
            for line in self.log_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def entries(self, mode: str) -> List[Dict[str, Any]]:
        return [e for e in self.log() if e.get("mode") == mode]
