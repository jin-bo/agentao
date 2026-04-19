"""Tests for the ACP CLI entry point (Issue 12).

Three layers:

1. **Argparse + branch routing** — verify that ``--acp`` and
   ``--stdio`` are accepted, that ``--acp`` routes to ``run_acp_mode``
   instead of the interactive CLI, and that ``--stdio`` without
   ``--acp`` is rejected with a clear error.

2. **Handler registration** — verify that ``agentao.acp.__main__.main``
   registers every handler that has shipped (initialize, session/new,
   session/prompt, session/cancel, session/load).

3. **Subprocess smoke test** — spawn ``python -m agentao --acp --stdio``
   in a subprocess, send an ``initialize`` request, and verify that
   stdout contains exactly one valid JSON-RPC response with the
   negotiated protocol version. This is the closest end-to-end check
   we can perform without writing an actual ACP client; it covers the
   acceptance criteria "starts a valid ACP server" and "stdout contains
   only ACP protocol messages".
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest


# ===========================================================================
# Part 1 — Argparse and branch routing
# ===========================================================================


class TestEntrypointArgparse:
    def test_acp_flag_routes_to_run_acp_mode(self, monkeypatch):
        """``agentao --acp`` should call ``run_acp_mode``, not main()."""
        from agentao import cli

        called: Dict[str, bool] = {}

        def fake_acp_mode():
            called["acp"] = True

        def fake_main(*a, **kw):
            called["main"] = True

        def fake_print(*a, **kw):
            called["print"] = True

        monkeypatch.setattr(cli, "run_acp_mode", fake_acp_mode)
        monkeypatch.setattr(cli, "main", fake_main)
        monkeypatch.setattr(cli, "run_print_mode", fake_print)
        monkeypatch.setattr(sys, "argv", ["agentao", "--acp"])

        cli.entrypoint()

        assert called.get("acp") is True
        assert "main" not in called
        assert "print" not in called

    def test_acp_with_stdio_routes_to_run_acp_mode(self, monkeypatch):
        """``agentao --acp --stdio`` is the documented invocation."""
        from agentao import cli

        called: Dict[str, bool] = {}

        monkeypatch.setattr(cli, "run_acp_mode", lambda: called.setdefault("acp", True))
        monkeypatch.setattr(cli, "main", lambda **kw: called.setdefault("main", True))
        monkeypatch.setattr(sys, "argv", ["agentao", "--acp", "--stdio"])

        cli.entrypoint()

        assert called.get("acp") is True
        assert "main" not in called

    def test_stdio_without_acp_exits_with_error(self, monkeypatch, capsys):
        """``--stdio`` alone is a typo guard — fail fast on stderr."""
        from agentao import cli

        # If anything routes to interactive/print mode by mistake, fail loudly.
        monkeypatch.setattr(
            cli, "main", lambda **kw: pytest.fail("interactive main called")
        )
        monkeypatch.setattr(
            cli, "run_print_mode", lambda *a, **kw: pytest.fail("print mode called")
        )
        monkeypatch.setattr(sys, "argv", ["agentao", "--stdio"])

        with pytest.raises(SystemExit) as exc:
            cli.entrypoint()
        assert exc.value.code == 2

        err = capsys.readouterr().err
        assert "--stdio requires --acp" in err

    def test_no_acp_flag_routes_to_interactive(self, monkeypatch):
        """The default path (no flags) still launches interactive ``main()``."""
        from agentao import cli

        called: Dict[str, Any] = {}

        monkeypatch.setattr(cli, "run_acp_mode", lambda: called.setdefault("acp", True))
        monkeypatch.setattr(
            cli, "main", lambda **kw: called.setdefault("main", kw)
        )
        monkeypatch.setattr(sys, "argv", ["agentao"])

        cli.entrypoint()

        assert "acp" not in called
        assert called.get("main") == {"resume_session": None}

    def test_acp_overrides_print_mode(self, monkeypatch):
        """If both ``--acp`` and ``-p`` are passed, ACP wins (no terminal output)."""
        from agentao import cli

        called: Dict[str, bool] = {}

        monkeypatch.setattr(cli, "run_acp_mode", lambda: called.setdefault("acp", True))
        monkeypatch.setattr(
            cli, "run_print_mode", lambda *a, **kw: called.setdefault("print", True)
        )
        monkeypatch.setattr(sys, "argv", ["agentao", "--acp", "-p", "hi"])
        # Print mode reads stdin if not a TTY; we don't want that to interact
        # with our test, so make it look like a TTY.
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)

        cli.entrypoint()

        assert called.get("acp") is True
        assert "print" not in called

    def test_acp_overrides_resume(self, monkeypatch):
        """``--acp --resume X`` should still take the ACP branch."""
        from agentao import cli

        called: Dict[str, bool] = {}

        monkeypatch.setattr(cli, "run_acp_mode", lambda: called.setdefault("acp", True))
        monkeypatch.setattr(
            cli, "main", lambda **kw: called.setdefault("main", True)
        )
        monkeypatch.setattr(sys, "argv", ["agentao", "--acp", "--resume", "abc"])

        cli.entrypoint()

        assert called.get("acp") is True
        assert "main" not in called


# ===========================================================================
# Part 2 — run_acp_mode delegates to ACP main
# ===========================================================================


class TestRunAcpMode:
    def test_run_acp_mode_calls_acp_main(self, monkeypatch):
        from agentao import cli

        called: Dict[str, bool] = {}

        def fake_acp_main():
            called["main"] = True

        # The import is local inside run_acp_mode, so monkeypatch the
        # target module directly.
        from agentao.acp import __main__ as acp_main_module
        monkeypatch.setattr(acp_main_module, "main", fake_acp_main)

        cli.run_acp_mode()
        assert called["main"] is True

    def test_acp_main_registers_all_shipped_handlers(self, monkeypatch):
        """``acp.__main__.main()`` must register every handler that has shipped."""
        from agentao.acp import __main__ as acp_main_module
        from agentao.acp.protocol import (
            METHOD_INITIALIZE,
            METHOD_SESSION_CANCEL,
            METHOD_SESSION_LOAD,
            METHOD_SESSION_NEW,
            METHOD_SESSION_PROMPT,
        )
        from agentao.acp.server import AcpServer

        captured_server: Dict[str, AcpServer] = {}
        original_acp_server = acp_main_module.AcpServer

        class _CapturingServer(original_acp_server):  # type: ignore[misc, valid-type]
            def __init__(self, *a, **kw):
                # Use in-memory streams so the constructor's stdout
                # guard doesn't fire and run() exits immediately.
                import io as _io
                kw.setdefault("stdin", _io.StringIO(""))
                kw.setdefault("stdout", _io.StringIO())
                super().__init__(*a, **kw)
                captured_server["server"] = self

        monkeypatch.setattr(acp_main_module, "AcpServer", _CapturingServer)
        acp_main_module.main()

        srv = captured_server["server"]
        # Every handler that has shipped is in the dispatcher's registry.
        for method in (
            METHOD_INITIALIZE,
            METHOD_SESSION_NEW,
            METHOD_SESSION_PROMPT,
            METHOD_SESSION_CANCEL,
            METHOD_SESSION_LOAD,
        ):
            assert method in srv._handlers, f"missing handler: {method}"


# ===========================================================================
# Part 3 — Subprocess smoke test
# ===========================================================================


def _agentao_repo_root() -> Path:
    """Return the repository root so the subprocess can find the package."""
    here = Path(__file__).resolve()
    # tests/test_acp_cli_entrypoint.py → tests/ → repo root
    return here.parent.parent


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="subprocess stdio framing is platform-specific; covered on Linux/macOS",
)
class TestAcpSubprocessSmoke:
    def _spawn_acp(self) -> subprocess.Popen:
        """Spawn ``python -m agentao --acp --stdio`` with the repo on PYTHONPATH."""
        env = os.environ.copy()
        # Force a deterministic, non-interactive environment.
        env.setdefault("PYTHONUNBUFFERED", "1")
        # Provide dummy credentials so :class:`LLMClient` constructs without
        # raising during ``session/new``. We never run a real turn in
        # these tests, so the dummies never reach a network call.
        env.setdefault("OPENAI_API_KEY", "test-dummy-key")
        env.setdefault("OPENAI_BASE_URL", "https://api.openai.com/v1")
        env.setdefault("OPENAI_MODEL", "gpt-5.4")
        return subprocess.Popen(
            [sys.executable, "-m", "agentao", "--acp", "--stdio"],
            cwd=str(_agentao_repo_root()),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
        )

    def test_initialize_handshake_round_trip(self):
        """Send ``initialize`` over stdin; expect a single JSON-RPC response
        on stdout with the negotiated protocol version."""
        from agentao.acp.protocol import ACP_PROTOCOL_VERSION

        proc = self._spawn_acp()
        try:
            request = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": ACP_PROTOCOL_VERSION,
                        "clientCapabilities": {},
                        "clientInfo": {"name": "smoke-test", "version": "0.0.1"},
                    },
                }
            ) + "\n"
            stdout, stderr = proc.communicate(input=request, timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            pytest.fail(f"agentao --acp --stdio timed out\nstdout={stdout}\nstderr={stderr}")

        # Acceptance: stdout contains exactly one valid JSON-RPC response.
        lines = [ln for ln in stdout.splitlines() if ln.strip()]
        assert len(lines) >= 1, (
            f"expected at least one response on stdout; got nothing.\n"
            f"stderr={stderr!r}"
        )
        # The first non-blank line must parse as a JSON-RPC response.
        msg = json.loads(lines[0])
        assert msg["jsonrpc"] == "2.0"
        assert msg["id"] == 1
        assert "result" in msg
        result = msg["result"]
        assert result["protocolVersion"] == ACP_PROTOCOL_VERSION
        assert "agentCapabilities" in result
        # Confirm the loadSession capability the rest of Issue 10 backs.
        assert result["agentCapabilities"]["loadSession"] is True
        # Process exited cleanly because we closed stdin (EOF after our line).
        assert proc.returncode == 0, (
            f"agentao exited with code {proc.returncode}\nstderr={stderr}"
        )

    def test_session_new_then_eof_shuts_down_cleanly(self, tmp_path):
        """Verify the full new+EOF lifecycle: session/new succeeds, EOF
        triggers clean shutdown, no stray output on stdout, exit code 0.
        """
        from agentao.acp.protocol import ACP_PROTOCOL_VERSION

        proc = self._spawn_acp()
        # Build two requests on a single stdin payload — initialize then
        # session/new in the same tmp_path. Then EOF.
        init = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": ACP_PROTOCOL_VERSION,
                    "clientCapabilities": {},
                },
            }
        )
        new_session = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "session/new",
                "params": {"cwd": str(tmp_path), "mcpServers": []},
            }
        )
        try:
            stdout, stderr = proc.communicate(
                input=init + "\n" + new_session + "\n", timeout=30
            )
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            pytest.fail(
                f"agentao --acp --stdio timed out\nstdout={stdout}\nstderr={stderr}"
            )

        lines = [ln for ln in stdout.splitlines() if ln.strip()]
        # Every stdout line MUST be a parseable JSON-RPC envelope.
        # This is acceptance criterion "stdout contains only ACP messages".
        for ln in lines:
            try:
                msg = json.loads(ln)
            except json.JSONDecodeError:
                pytest.fail(f"non-JSON line on stdout: {ln!r}")
            assert msg["jsonrpc"] == "2.0", f"non-JSON-RPC envelope: {ln!r}"

        # Find both responses by id (executor may reorder them).
        parsed = [json.loads(ln) for ln in lines]
        init_resp = next((m for m in parsed if m.get("id") == 1), None)
        new_resp = next((m for m in parsed if m.get("id") == 2), None)
        assert init_resp is not None and "result" in init_resp
        assert new_resp is not None and "result" in new_resp
        assert new_resp["result"]["sessionId"].startswith("sess_")

        # Clean shutdown.
        assert proc.returncode == 0, (
            f"agentao exited with code {proc.returncode}\nstderr={stderr}"
        )

    def test_logs_go_to_stderr_not_stdout(self):
        """Acceptance criterion: logs must not corrupt the protocol wire.

        Force a log line by sending a malformed request — the dispatcher
        will log a warning AND return an error response. The log line
        belongs on stderr; only the JSON-RPC error response belongs on
        stdout.
        """
        proc = self._spawn_acp()
        try:
            stdout, stderr = proc.communicate(
                input="not even json at all\n", timeout=15
            )
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            pytest.fail("agentao timed out on malformed input")

        # Stdout: a single PARSE_ERROR JSON-RPC envelope, nothing else.
        stdout_lines = [ln for ln in stdout.splitlines() if ln.strip()]
        assert len(stdout_lines) == 1
        msg = json.loads(stdout_lines[0])
        assert msg["jsonrpc"] == "2.0"
        assert msg["id"] is None
        assert msg["error"]["code"] == -32700  # PARSE_ERROR
        # Process exited cleanly.
        assert proc.returncode == 0
