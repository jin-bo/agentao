"""Tests for ACP client process handle and manager (Issue 02)."""

import json
import sys
import time
from pathlib import Path
from typing import Dict

import pytest

from agentao.acp_client.manager import ACPManager
from agentao.acp_client.models import (
    AcpClientConfig,
    AcpProcessInfo,
    AcpServerConfig,
    ServerState,
)
from agentao.acp_client.process import ACPProcessHandle

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    *,
    command: str = sys.executable,
    args: list | None = None,
    auto_start: bool = True,
    cwd: str | None = None,
) -> AcpServerConfig:
    """Build a minimal AcpServerConfig for tests."""
    return AcpServerConfig(
        command=command,
        args=args or ["-c", "import time; time.sleep(60)"],
        env={},
        cwd=cwd or str(Path.cwd()),
        auto_start=auto_start,
    )


def _sleeper_config(seconds: float = 60) -> AcpServerConfig:
    """Config that spawns a long-running Python process."""
    return _make_config(args=["-c", f"import time; time.sleep({seconds})"])


def _instant_exit_config(code: int = 0) -> AcpServerConfig:
    """Config that exits immediately."""
    return _make_config(args=["-c", f"import sys; sys.exit({code})"])


def _write_acp_config(root: Path, servers: Dict[str, dict]) -> None:
    cfg_dir = root / ".agentao"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "acp.json").write_text(
        json.dumps({"servers": servers}), encoding="utf-8"
    )


VALID_SERVER_DICT: dict = {
    "command": sys.executable,
    "args": ["-c", "import time; time.sleep(60)"],
    "env": {},
    "cwd": ".",
}


# ---------------------------------------------------------------------------
# ServerState enum
# ---------------------------------------------------------------------------


class TestServerState:
    def test_values(self) -> None:
        assert ServerState.CONFIGURED.value == "configured"
        assert ServerState.STARTING.value == "starting"
        assert ServerState.READY.value == "ready"
        assert ServerState.FAILED.value == "failed"
        assert ServerState.STOPPED.value == "stopped"

    def test_is_str_enum(self) -> None:
        assert isinstance(ServerState.READY, str)
        assert ServerState.READY == "ready"


# ---------------------------------------------------------------------------
# AcpProcessInfo
# ---------------------------------------------------------------------------


class TestAcpProcessInfo:
    def test_defaults(self) -> None:
        info = AcpProcessInfo()
        assert info.state == ServerState.CONFIGURED
        assert info.pid is None
        assert info.last_error is None
        assert info.last_activity is None

    def test_touch(self) -> None:
        info = AcpProcessInfo()
        before = time.time()
        info.touch()
        after = time.time()
        assert info.last_activity is not None
        assert before <= info.last_activity <= after


# ---------------------------------------------------------------------------
# ACPProcessHandle — lifecycle
# ---------------------------------------------------------------------------


class TestACPProcessHandle:
    def test_start_and_stop(self) -> None:
        handle = ACPProcessHandle("test", _sleeper_config())
        assert handle.state == ServerState.CONFIGURED

        handle.start()
        assert handle.state == ServerState.STARTING
        assert handle.pid is not None
        assert handle.info.last_activity is not None

        handle.stop()
        assert handle.state == ServerState.STOPPED
        assert handle.pid is None

    def test_duplicate_start_is_noop(self) -> None:
        handle = ACPProcessHandle("test", _sleeper_config())
        handle.start()
        pid1 = handle.pid

        handle.start()  # should be a no-op
        assert handle.pid == pid1

        handle.stop()

    def test_stop_already_stopped_is_noop(self) -> None:
        handle = ACPProcessHandle("test", _sleeper_config())
        handle.start()
        handle.stop()
        assert handle.state == ServerState.STOPPED

        handle.stop()  # idempotent
        assert handle.state == ServerState.STOPPED

    def test_stop_never_started(self) -> None:
        handle = ACPProcessHandle("test", _sleeper_config())
        handle.stop()
        assert handle.state == ServerState.STOPPED

    def test_start_failure_bad_command(self) -> None:
        cfg = _make_config(command="/nonexistent/binary/xyz")
        handle = ACPProcessHandle("bad", cfg)

        with pytest.raises(RuntimeError, match="failed to start"):
            handle.start()

        assert handle.state == ServerState.FAILED
        assert handle.info.last_error is not None

    def test_start_failure_immediate_exit(self) -> None:
        cfg = _instant_exit_config(code=1)
        handle = ACPProcessHandle("crash", cfg)

        with pytest.raises(RuntimeError, match="exited immediately"):
            handle.start()

        assert handle.state == ServerState.FAILED

    def test_restart_replaces_process(self) -> None:
        handle = ACPProcessHandle("test", _sleeper_config())
        handle.start()
        pid1 = handle.pid

        handle.restart()
        pid2 = handle.pid

        assert pid2 is not None
        assert pid1 != pid2
        assert handle.state == ServerState.STARTING

        handle.stop()

    def test_stdin_stdout_available(self) -> None:
        handle = ACPProcessHandle("test", _sleeper_config())
        handle.start()

        assert handle.stdin is not None
        assert handle.stdout is not None

        handle.stop()

        assert handle.stdin is None
        assert handle.stdout is None


# ---------------------------------------------------------------------------
# ACPManager
# ---------------------------------------------------------------------------


class TestACPManager:
    def test_from_config(self) -> None:
        cfg = AcpClientConfig(servers={
            "a": _sleeper_config(),
            "b": _sleeper_config(),
        })
        mgr = ACPManager(cfg)
        assert set(mgr.server_names) == {"a", "b"}

    def test_from_project(self, tmp_path: Path) -> None:
        _write_acp_config(tmp_path, {"srv": VALID_SERVER_DICT})
        mgr = ACPManager.from_project(project_root=tmp_path)
        assert "srv" in mgr.server_names

    def test_from_project_empty(self, tmp_path: Path) -> None:
        mgr = ACPManager.from_project(project_root=tmp_path)
        assert mgr.server_names == []

    def test_start_stop_all(self) -> None:
        cfg = AcpClientConfig(servers={"a": _sleeper_config()})
        mgr = ACPManager(cfg)

        mgr.start_all()
        handle = mgr.get_handle("a")
        assert handle is not None
        assert handle.state == ServerState.STARTING
        assert handle.pid is not None

        mgr.stop_all()
        assert handle.state == ServerState.STOPPED

    def test_start_all_respects_auto_start(self) -> None:
        cfg = AcpClientConfig(servers={
            "auto": _sleeper_config(),
            "manual": _make_config(auto_start=False),
        })
        mgr = ACPManager(cfg)
        mgr.start_all(only_auto=True)

        assert mgr.get_handle("auto").state == ServerState.STARTING
        assert mgr.get_handle("manual").state == ServerState.CONFIGURED

        mgr.stop_all()

    def test_start_stop_single(self) -> None:
        cfg = AcpClientConfig(servers={"s": _sleeper_config()})
        mgr = ACPManager(cfg)

        mgr.start_server("s")
        assert mgr.get_handle("s").state == ServerState.STARTING

        mgr.stop_server("s")
        assert mgr.get_handle("s").state == ServerState.STOPPED

    def test_start_unknown_server(self) -> None:
        mgr = ACPManager(AcpClientConfig())
        with pytest.raises(KeyError, match="no ACP server"):
            mgr.start_server("nope")

    def test_restart_server(self) -> None:
        cfg = AcpClientConfig(servers={"s": _sleeper_config()})
        mgr = ACPManager(cfg)
        mgr.start_server("s")
        pid1 = mgr.get_handle("s").pid

        mgr.restart_server("s")
        pid2 = mgr.get_handle("s").pid

        assert pid1 != pid2

        mgr.stop_all()

    def test_get_status(self) -> None:
        srv = _sleeper_config()
        srv.description = "Alpha server"
        cfg = AcpClientConfig(servers={"a": srv})
        mgr = ACPManager(cfg)
        mgr.start_all()

        status = mgr.get_status()
        assert len(status) == 1
        assert status[0].server == "a"
        assert status[0].state == "starting"
        assert status[0].pid is not None
        assert status[0].has_active_turn is False

        mgr.stop_all()

    def test_stop_all_no_leftover_processes(self) -> None:
        cfg = AcpClientConfig(servers={
            "a": _sleeper_config(),
            "b": _sleeper_config(),
        })
        mgr = ACPManager(cfg)
        mgr.start_all()

        pids = [mgr.get_handle(n).pid for n in mgr.server_names]
        assert all(p is not None for p in pids)

        mgr.stop_all()

        for name in mgr.server_names:
            h = mgr.get_handle(name)
            assert h.state == ServerState.STOPPED
            assert h.pid is None
