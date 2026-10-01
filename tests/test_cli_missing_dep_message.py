"""Friendly missing-dep message from the ``agentao`` CLI in core-only installs.

Slow-marked tests against a built wheel:

- core-only: ``agentao`` exits 2 with the named-package + install-line message
- core-only + ``[cli]``: ``agentao --help`` boots and exits 0
- core-only: ``from agentao.cli import entrypoint`` resolves without
  tripping rich/prompt_toolkit (precondition for the friendly path)
- core-only: ``agentao --acp`` runs a whole turn and ``agentao --login``
  saves a configuration — what an ACP Registry client's
  ``uvx agentao@<version>`` gets

Run with::

    uv build && uv run pytest tests/test_cli_missing_dep_message.py -m slow
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.support.wheel import make_venv, require_wheel


pytestmark = [pytest.mark.slow]


def test_core_only_cli_prints_friendly_missing_dep(tmp_path: Path) -> None:
    wheel = require_wheel()
    venv = make_venv(tmp_path)
    venv.pip_install(str(wheel))

    proc = subprocess.run([str(venv.agentao_script)], capture_output=True, text=True)

    assert proc.returncode == 2, (
        f"expected exit code 2, got {proc.returncode}.\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "agentao CLI requires extra packages" in proc.stderr
    assert "pip install 'agentao[cli]'" in proc.stderr
    assert "pip install 'agentao[full]'" in proc.stderr
    # The shim must catch the ImportError before the traceback escapes —
    # otherwise the user sees the opaque ModuleNotFoundError we are here to hide.
    assert "Traceback" not in proc.stderr
    assert "ModuleNotFoundError" not in proc.stderr


def test_cli_extra_makes_agentao_help_work(tmp_path: Path) -> None:
    wheel = require_wheel()
    venv = make_venv(tmp_path)
    venv.pip_install(f"{wheel}[cli]")

    proc = subprocess.run(
        [str(venv.agentao_script), "--help"],
        capture_output=True, text=True,
    )

    assert proc.returncode == 0, (
        f"agentao --help failed after [cli] install:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "usage: agentao" in proc.stdout


def test_core_only_can_import_agentao_cli_entrypoint(tmp_path: Path) -> None:
    """The shim only fires on call; the import itself must stay light."""
    wheel = require_wheel()
    venv = make_venv(tmp_path)
    venv.pip_install(str(wheel))

    # ``cwd``: ``python -c`` puts the cwd first on sys.path, so run from the repository
    # root this imported the source tree's ``agentao.cli`` rather than the wheel's.
    proc = subprocess.run(
        [str(venv.python), "-c", "from agentao.cli import entrypoint; print('import OK')"],
        capture_output=True, text=True, cwd=tmp_path,
    )
    assert proc.returncode == 0, (
        f"`from agentao.cli import entrypoint` failed in core-only venv:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "import OK" in proc.stdout


def test_partial_cli_install_still_hits_friendly_path(tmp_path: Path) -> None:
    """Rich installed but other [cli] deps missing must still exit 2 cleanly.

    Without an upfront preflight, ``entrypoints.run_init_wizard``'s broad
    ``except Exception`` would swallow the deep ``ModuleNotFoundError``
    and emit a generic "Fatal error" — bypassing the friendly path that
    points at ``pip install 'agentao[cli]'``.
    """
    wheel = require_wheel()
    venv = make_venv(tmp_path)
    venv.pip_install(str(wheel), "rich")  # rich only — prompt_toolkit/readchar/pygments still missing

    proc = subprocess.run([str(venv.agentao_script)], capture_output=True, text=True)

    assert proc.returncode == 2, (
        f"expected exit code 2 in partial install, got {proc.returncode}.\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "agentao CLI requires extra packages" in proc.stderr
    assert "Fatal error" not in proc.stdout and "Fatal error" not in proc.stderr


def test_core_only_acp_server_runs_a_turn(tmp_path: Path) -> None:
    """``agentao --acp`` from a bare install answers a prompt end to end.

    What an ACP Registry client runs: ``uvx agentao@<version> --acp``, no
    ``[cli]``. The model is a local Chat Completions stub, so the turn goes
    through the real agent loop — construction, request, streamed reply,
    ``session/update`` notifications, ``stopReason``.
    """
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Model(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 — http.server's naming
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            chunks = [
                {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "hello from stub"}, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            ]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for c in chunks:
                c.update(id="c1", object="chat.completion.chunk", created=0, model="stub")
                self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Model)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    wheel = require_wheel()
    venv = make_venv(tmp_path)
    venv.pip_install(str(wheel))
    no_rich = subprocess.run([str(venv.python), "-c", "import rich"], capture_output=True)
    assert no_rich.returncode != 0, "rich is installed; this test would prove nothing"
    work = tmp_path / "work"
    work.mkdir()
    env = {
        "PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), "USERPROFILE": str(tmp_path),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "LLM_PROVIDER": "OPENAI", "OPENAI_API_KEY": "sk-stub",
        "OPENAI_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1", "OPENAI_MODEL": "stub",
    }
    requests = [
        {"jsonrpc": "2.0", "id": 0, "method": "initialize",
         "params": {"protocolVersion": 1, "clientCapabilities": {}}},
        {"jsonrpc": "2.0", "id": 1, "method": "session/new",
         "params": {"cwd": str(work), "mcpServers": []}},
    ]
    proc = subprocess.Popen(
        [str(venv.agentao_script), "--acp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, cwd=work, env=env,
    )
    try:
        def call(msg):
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()
            seen = []
            while True:
                line = proc.stdout.readline()
                assert line, f"server exited:\n{proc.stderr.read()}"
                reply = json.loads(line)
                if reply.get("id") == msg["id"]:
                    return reply, seen
                seen.append(reply)

        for msg in requests:
            reply, _ = call(msg)
            assert "result" in reply, reply
        session_id = reply["result"]["sessionId"]
        reply, updates = call({
            "jsonrpc": "2.0", "id": 2, "method": "session/prompt",
            "params": {"sessionId": session_id, "prompt": [{"type": "text", "text": "hi"}]},
        })
    finally:
        proc.kill()
        server.shutdown()

    assert reply["result"]["stopReason"] == "end_turn", reply
    text = "".join(
        u["params"]["update"].get("content", {}).get("text", "")
        for u in updates
        if u.get("method") == "session/update"
        and u["params"]["update"].get("sessionUpdate") == "agent_message_chunk"
    )
    assert "hello from stub" in text


def test_core_only_login_saves_a_configuration(tmp_path: Path) -> None:
    """``agentao --login`` from a bare install, answered on stdin."""
    import json

    wheel = require_wheel()
    venv = make_venv(tmp_path)
    venv.pip_install(str(wheel))
    env = {
        "PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), "USERPROFILE": str(tmp_path),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
    }

    proc = subprocess.run(
        [str(venv.agentao_script), "--login"], input="1\nsk-bare\n\n\n\n",
        capture_output=True, text=True, cwd=tmp_path, env=env, timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    saved = json.loads((tmp_path / ".agentao" / "llm.json").read_text(encoding="utf-8"))
    assert saved["provider"] == "OPENAI" and saved["api_key"] == "sk-bare"
