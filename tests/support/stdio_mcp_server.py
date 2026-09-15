"""A real stdio MCP server, written without the SDK, that records what it sees.

It speaks just enough MCP to connect, list three tools (``slow_a``, ``slow_b``,
``slow_c``) and answer calls, so it works on both SDK majors. Everything a test
needs to observe is a file in its ``marks`` directory:

- ``started-<pid>`` for every launch;
- ``called-<tool>`` when a call arrives;
- ``overlap`` when two calls are in flight at once;
- ``eof-<pid>`` when its stdin closes.

Files the test creates in the same directory change what it does:

- ``refuse``: exit at launch;
- ``mute``: read stdin and never answer;
- ``hang``: leave tool calls unanswered;
- ``deafen``: close its stdin after a tool call, and stay alive.

Its handshake echoes the client's protocol version, which suits tests of call
scheduling and connection lifetime, not of negotiation (``tests/support/mcp.py``
explains why a negotiation test must not echo).
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from typing import Any, Dict, Tuple

SCRIPT = textwrap.dedent('''
    import json, os, sys, threading, time
    from pathlib import Path

    marks = Path(sys.argv[1])
    delay = float(sys.argv[2])
    marks.mkdir(parents=True, exist_ok=True)
    (marks / f"started-{os.getpid()}").touch()
    if (marks / "refuse").exists():
        sys.exit(1)
    if (marks / "mute").exists():
        for _ in iter(sys.stdin.buffer.readline, b""):
            pass
        (marks / f"eof-{os.getpid()}").touch()
        sys.exit(0)

    out = sys.stdout.buffer
    write_lock = threading.Lock()
    active = [0]
    tools = [{"name": n, "inputSchema": {"type": "object"}} for n in ("slow_a", "slow_b", "slow_c")]

    def reply(msg_id, **body):
        with write_lock:
            out.write(json.dumps({"jsonrpc": "2.0", "id": msg_id, **body}).encode() + b"\\n")
            out.flush()

    def call(msg):
        name = msg["params"]["name"]
        with write_lock:
            active[0] += 1
            if active[0] >= 2:
                (marks / "overlap").touch()
        (marks / f"called-{name}").touch()
        if (marks / "hang").exists():
            return  # never answered
        time.sleep(delay)
        with write_lock:
            active[0] -= 1
        reply(msg["id"], result={"content": [{"type": "text", "text": name}], "isError": False})

    for line in iter(sys.stdin.buffer.readline, b""):
        msg = json.loads(line)
        if "id" not in msg:
            continue
        method = msg["method"]
        if method == "initialize":
            reply(msg["id"], result={
                "protocolVersion": msg["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "stdio-probe", "version": "0"},
            })
        elif method == "tools/list":
            reply(msg["id"], result={"tools": tools})
        elif method == "tools/call":
            threading.Thread(target=call, args=(msg,), daemon=True).start()
            if (marks / "deafen").exists():
                # Stop reading but stay alive with stdout open: the client's
                # next write fails, and it never sees end-of-file.
                os.close(0)  # ``sys.stdin.close()`` leaves the descriptor open
                time.sleep(30)
                sys.exit(0)
        else:
            reply(msg["id"], error={"code": -32601, "message": method})

    (marks / f"eof-{os.getpid()}").touch()
''')


def stdio_server(directory: Path, *, delay: float = 0.5) -> Tuple[Dict[str, Any], Path]:
    """Write the server into ``directory``; return ``(config, marks)``.

    ``config`` is a trusted stdio server entry whose calls take ``delay``
    seconds; ``marks`` is the directory the server records into.
    """
    script = directory / "stdio_probe_server.py"
    script.write_text(SCRIPT)
    marks = directory / "marks"
    config = {
        "command": sys.executable,
        "args": [str(script), str(marks), str(delay)],
        "trust": True,
    }
    return config, marks


def started(marks: Path) -> list:
    """Every launch the server recorded, sorted."""
    return sorted(marks.glob("started-*"))
