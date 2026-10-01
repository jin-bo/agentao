"""`run_terminal_login` owns the user's terminal (#381).

The login must stay in the CLI's session: a child launched like an ACP server
(``start_new_session``) has no controlling terminal. With a terminal it runs
as a foreground job — its own process group, given the terminal and giving it
back — so Ctrl+C reaches the login, and a cancelled login's whole group
(a runner's children included) can be ended without touching the CLI. The PTY
tests drive a real pseudo-terminal — CI has no TTY, so without one these
properties could not fail.
"""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import textwrap
import time

import pytest

from agentao.acp_client.auth import TerminalLoginCommand
from agentao.acp_client.process import resolve_executable
from agentao.cli.commands_ext import acp_login as login_mod

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX pty")


def _command(argv, tmp_path, env=None):
    return TerminalLoginCommand(
        argv=argv, env=dict(env or os.environ), cwd=str(tmp_path), method_id="m",
    )


# ---------------------------------------------------------------------------
# Exit status
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code, status", [(0, "ok"), (4, "failed")])
def test_exit_status_decides_success(tmp_path, code, status):
    outcome = login_mod.run_terminal_login(
        _command([sys.executable, "-c", f"import sys; sys.exit({code})"], tmp_path),
    )

    assert (outcome.status, outcome.returncode) == (status, code)


@posix_only
def test_death_by_signal_is_a_failure(tmp_path):
    outcome = login_mod.run_terminal_login(_command(
        [sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGTERM)"],
        tmp_path,
    ))

    assert outcome.status == "failed" and outcome.returncode < 0


def test_a_launch_failure_is_a_failure(tmp_path):
    outcome = login_mod.run_terminal_login(_command([str(tmp_path / "no-such-runner")], tmp_path))

    assert outcome.status == "failed" and outcome.error


def test_the_login_inherits_the_terminal_and_process_group(tmp_path, monkeypatch):
    seen = {}

    class Recorder:
        def __init__(self, argv, **kwargs):
            seen.update(kwargs, argv=argv)

        def wait(self):
            return 0

    monkeypatch.setattr(login_mod.subprocess, "Popen", Recorder)

    login_mod.run_terminal_login(_command(["agent", "--login"], tmp_path))

    assert seen["argv"] == ["agent", "--login"]
    assert set(seen) == {"argv", "cwd", "env"}  # no stdio, session or group flags


# ---------------------------------------------------------------------------
# A real terminal
# ---------------------------------------------------------------------------

_DRIVER = textwrap.dedent("""\
    import os, sys
    from agentao.acp_client.auth import TerminalLoginCommand
    from agentao.cli.commands_ext.acp_login import run_terminal_login

    child, out = sys.argv[1], sys.argv[2]
    os.environ["DRIVER_SID"] = str(os.getsid(0))
    os.environ["DRIVER_PGRP"] = str(os.getpgrp())
    command = TerminalLoginCommand(
        argv=[sys.executable, child, out], env=dict(os.environ),
        cwd=os.getcwd(), method_id="m",
    )
    outcome = run_terminal_login(command)
    print("OUTCOME", outcome.status, outcome.returncode, flush=True)
    print("FOREGROUND", os.tcgetpgrp(0) == os.getpgrp(), flush=True)
    print("USABLE", flush=True)
""")

_HIDDEN_INPUT_CHILD = textwrap.dedent("""\
    import getpass, json, os, sys
    out = sys.argv[1]
    state = {
        "foreground": os.tcgetpgrp(sys.stdin.fileno()) == os.getpgrp(),
        "same_session": os.getsid(0) == int(os.environ["DRIVER_SID"]),
        "own_group": os.getpgrp() != int(os.environ["DRIVER_PGRP"]),
    }
    state["secret"] = getpass.getpass("Key: ")
    with open(out, "w") as fh:
        json.dump(state, fh)
""")

_WAITING_CHILD = textwrap.dedent("""\
    import os, sys
    out = sys.argv[1]
    with open(out, "w") as fh:
        fh.write(str(os.getpid()))
    print("WAITING", flush=True)
    sys.stdin.readline()
""")

# A runner (think ``npx``) whose real login is a child that ignores SIGINT
# and SIGTERM: Ctrl+C ends the runner, and only a group kill ends the child.
_WRAPPER_CHILD = textwrap.dedent("""\
    import os, subprocess, sys, time
    out = sys.argv[1]
    inner = (
        "import os, signal, sys, time\\n"
        "signal.signal(signal.SIGINT, signal.SIG_IGN)\\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\\n"
        "open(sys.argv[1], 'w').write(str(os.getpid()))\\n"
        "time.sleep(120)\\n"
    )
    subprocess.Popen([sys.executable, "-c", inner, out])
    while not (os.path.exists(out) and open(out).read()):
        time.sleep(0.05)
    print("WAITING", flush=True)
    sys.stdin.readline()
""")


# Makes the pty its controlling terminal, then becomes the driver. Done in a
# fresh interpreter rather than with ``pty.fork()``: forking the (threaded)
# pytest process can deadlock the child.
_SESSION_WRAPPER = textwrap.dedent("""\
    import fcntl, os, sys, termios
    os.setsid()
    fd = os.open(sys.argv[1], os.O_RDWR)
    fcntl.ioctl(fd, termios.TIOCSCTTY, 0)
    for target in (0, 1, 2):
        os.dup2(fd, target)
    if fd > 2:
        os.close(fd)
    os.execv(sys.executable, [sys.executable, *sys.argv[2:]])
""")


def _run_in_pty(tmp_path, child_source, steps, env=None, timeout=20.0):
    """Run the driver on a fresh pty; *steps* is ``[(wait_for, send), ...]``."""
    driver = tmp_path / "driver.py"
    driver.write_text(_DRIVER)
    child = tmp_path / "child.py"
    child.write_text(child_source)
    wrapper = tmp_path / "session.py"
    wrapper.write_text(_SESSION_WRAPPER)
    out = tmp_path / "out"
    master, slave = os.openpty()
    slave_name = os.ttyname(slave)
    # Keep our slave fd open until the driver exits: on macOS a master whose
    # slave has no open fd reads as end-of-file, which races the driver's own
    # open of the tty.
    proc = subprocess.Popen(
        [sys.executable, str(wrapper), slave_name, str(driver), str(child), str(out)],
        cwd=tmp_path, env={**os.environ, **(env or {})},
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    transcript = b""
    deadline = time.monotonic() + timeout
    pending = list(steps) + [(b"USABLE", None)]
    try:
        while pending and time.monotonic() < deadline:
            wait_for, send = pending[0]
            if wait_for in transcript:
                pending.pop(0)
                if send is not None:
                    os.write(master, send)
                continue
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                transcript += chunk
        returncode = proc.wait(timeout=timeout)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        os.close(master)
        os.close(slave)
    return transcript, out, returncode


@posix_only
def test_hidden_input_in_the_foreground_process_group(tmp_path):
    transcript, out, returncode = _run_in_pty(
        tmp_path, _HIDDEN_INPUT_CHILD, [(b"Key: ", b"hunter2\n")],
    )

    assert returncode == 0, transcript
    result = json.loads(out.read_text())
    assert result == {
        "foreground": True, "same_session": True, "own_group": True, "secret": "hunter2",
    }
    assert b"hunter2" not in transcript  # echo was off
    assert b"OUTCOME ok 0" in transcript
    assert b"FOREGROUND True" in transcript and b"USABLE" in transcript


def _assert_gone(pid):
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@posix_only
def test_ctrl_c_cancels_and_gives_the_terminal_back(tmp_path):
    transcript, out, returncode = _run_in_pty(tmp_path, _WAITING_CHILD, [(b"WAITING", b"\x03")])

    assert returncode == 0, transcript  # the CLI itself was not interrupted
    assert b"OUTCOME cancelled" in transcript
    assert b"FOREGROUND True" in transcript and b"USABLE" in transcript
    _assert_gone(int(out.read_text()))


@posix_only
def test_cancelling_a_runner_ends_what_it_started(tmp_path):
    transcript, out, returncode = _run_in_pty(tmp_path, _WRAPPER_CHILD, [(b"WAITING", b"\x03")])

    assert returncode == 0, transcript
    assert b"OUTCOME cancelled" in transcript and b"FOREGROUND True" in transcript
    _assert_gone(int(out.read_text()))  # the SIGINT/SIGTERM-ignoring child


# ---------------------------------------------------------------------------
# Windows runners
# ---------------------------------------------------------------------------


def test_resolve_executable_is_identity_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert resolve_executable("npx", {"PATH": "/nowhere"}) == "npx"


def test_resolve_executable_searches_the_child_path_on_windows(monkeypatch):
    import agentao.acp_client.process as process_mod

    calls = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        process_mod.shutil, "which",
        lambda cmd, path=None: calls.append((cmd, path)) or r"C:\\node\\npx.cmd",
    )

    assert resolve_executable("npx", {"PATH": r"C:\\node"}) == r"C:\\node\\npx.cmd"
    assert calls == [("npx", r"C:\\node")]
    assert resolve_executable(r"C:\\tools\\agent.exe", {}) == r"C:\\tools\\agent.exe"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows .cmd shim launch")
def test_a_cmd_shim_on_path_actually_launches_on_windows(tmp_path):
    shim = tmp_path / "fakerunner.cmd"
    shim.write_text("@echo off\r\necho ran %1\r\nexit /b 7\r\n")
    env = {**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}"}

    done = subprocess.run(
        [resolve_executable("fakerunner", env), "pkg@1.0.0"],
        env=env, capture_output=True, text=True, timeout=30,
    )

    assert done.returncode == 7
    assert "ran pkg@1.0.0" in done.stdout


# ---------------------------------------------------------------------------
# Windows: a job object ends a failed or cancelled login's leftovers
# ---------------------------------------------------------------------------


class _FakeJob:
    def __init__(self, adopts=True):
        self.adopts, self.calls = adopts, []

    def adopt(self, handle):
        self.calls.append(("adopt", handle))
        return self.adopts

    def terminate(self):
        self.calls.append(("terminate",))

    def close(self):
        self.calls.append(("close",))


def _fake_popen(monkeypatch, returncode=None, interrupt=False):
    seen = {}

    class Proc:
        _handle = 77

        def __init__(self, argv, **kwargs):
            seen.update(kwargs)

        def wait(self, timeout=None):
            if interrupt and not seen.get("interrupted"):
                seen["interrupted"] = True
                raise KeyboardInterrupt
            return returncode if returncode is not None else -2

        def poll(self):
            return -2

        def terminate(self):
            pass

        kill = terminate

    monkeypatch.setattr(login_mod.subprocess, "Popen", Proc)
    return seen


@pytest.mark.parametrize(
    "returncode, interrupt, status, terminated",
    [(0, False, "ok", False), (3, False, "failed", True), (None, True, "cancelled", True)],
)
def test_a_job_ends_leftovers_unless_the_login_succeeded(
    tmp_path, monkeypatch, returncode, interrupt, status, terminated,
):
    from agentao.cli.commands_ext import _win_job

    job = _FakeJob()
    monkeypatch.setattr(_win_job.LoginJob, "create", classmethod(lambda cls: job))
    monkeypatch.setattr(login_mod, "_foreground_terminal", lambda: None)
    seen = _fake_popen(monkeypatch, returncode=returncode, interrupt=interrupt)

    outcome = login_mod.run_terminal_login(_command(["runner"], tmp_path))

    assert outcome.status == status
    assert seen["creationflags"] == _win_job.CREATE_SUSPENDED  # started inside the job
    assert job.calls[0] == ("adopt", 77)
    assert (("terminate",) in job.calls) is terminated
    assert job.calls[-1] == ("close",)


def test_a_job_that_cannot_adopt_is_dropped(tmp_path, monkeypatch):
    from agentao.cli.commands_ext import _win_job

    job = _FakeJob(adopts=False)
    monkeypatch.setattr(_win_job.LoginJob, "create", classmethod(lambda cls: job))
    monkeypatch.setattr(login_mod, "_foreground_terminal", lambda: None)
    _fake_popen(monkeypatch, returncode=3)

    assert login_mod.run_terminal_login(_command(["runner"], tmp_path)).status == "failed"
    assert job.calls == [("adopt", 77), ("close",)]


def test_no_job_off_windows():
    from agentao.cli.commands_ext._win_job import LoginJob

    if sys.platform != "win32":
        assert LoginJob.create() is None


_WIN_LEFTOVER_RUNNER = textwrap.dedent("""\
    import subprocess, sys, time
    out = sys.argv[1]
    inner = "import os, sys, time; open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(120)"
    subprocess.Popen([sys.executable, "-c", inner, out])
    for _ in range(200):
        try:
            if open(out).read():
                break
        except OSError:
            pass
        time.sleep(0.05)
    sys.exit(3)
""")


def _windows_process_alive(pid):
    import ctypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        k32.GetExitCodeProcess(handle, ctypes.byref(code))
        return code.value == 259  # STILL_ACTIVE
    finally:
        k32.CloseHandle(handle)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows job objects")
def test_a_failed_login_leaves_no_descendants_on_windows(tmp_path):
    runner = tmp_path / "runner.py"
    runner.write_text(_WIN_LEFTOVER_RUNNER)
    out = tmp_path / "pid"

    outcome = login_mod.run_terminal_login(
        _command([sys.executable, str(runner), str(out)], tmp_path),
    )

    assert outcome.status == "failed" and outcome.returncode == 3
    pid = int(out.read_text())
    deadline = time.monotonic() + 10
    while _windows_process_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not _windows_process_alive(pid)
