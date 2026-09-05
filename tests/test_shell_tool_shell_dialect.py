"""``run_shell_command`` must run the shell its description names.

``subprocess.Popen(command, shell=True)`` is hardcoded to ``/bin/sh`` on
POSIX, but the tool description has always told the model ``bash -c``. On
most Linux distributions ``/bin/sh`` is dash, so bashisms the model reaches
for by default died with a syntax error; macOS hid it because ``/bin/sh``
there is bash 3.2 in POSIX mode, which still rejects process substitution.
``LocalShellExecutor`` now passes ``executable=`` so bash is what actually
interprets the command.

The first test asserts *consistency* rather than a literal: it asks the live
interpreter what it is and requires the description to name that. A literal
would only restate the belief that was already wrong once, and would keep
passing if the executable were changed underneath it. Because bash is
resolved rather than assumed — Alpine and distroless images have no bash —
the description degrades with the resolver, and a test pins that too.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from agentao.capabilities import shell as shell_cap
from agentao.tools.shell import ShellTool

IS_WINDOWS = sys.platform == "win32"


def _make_tool(tmp_path) -> ShellTool:
    tool = ShellTool()
    tool.working_directory = str(tmp_path)
    return tool


def _descriptions(tool: ShellTool):
    return (
        ("description", tool.description),
        ("command param", tool.parameters["properties"]["command"]["description"]),
    )


# --------------------------------------------------------------------------
# The invariant: description == what actually interprets the command
# --------------------------------------------------------------------------


@pytest.mark.skipif(IS_WINDOWS, reason="`$0` is meaningless under cmd.exe")
def test_description_names_the_interpreter_that_actually_runs(tmp_path):
    tool = _make_tool(tmp_path)
    # Under `<shell> -c <command>` with no trailing operands, `$0` is the
    # shell's own path — so this reports the interpreter, whatever it is.
    out = tool.execute(command='printf %s "$0"', working_directory=".", timeout=10)
    actual = shell_cap.shell_display_name()
    assert actual in out, f"expected $0 == {actual!r}; got: {out!r}"
    for label, text in _descriptions(tool):
        assert actual in text, f"{label} does not name {actual}: {text!r}"


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX-only")
def test_description_degrades_when_bash_is_absent(monkeypatch, tmp_path):
    """A bash-less image (Alpine, distroless) falls back to Python's default
    ``/bin/sh``. Nothing else exercises that branch, and a description still
    promising bash there would be the original defect with a new value."""
    monkeypatch.setattr(shell_cap, "resolve_shell_executable", lambda: None)
    assert shell_cap.shell_display_name() == "/bin/sh"
    tool = _make_tool(tmp_path)
    for label, text in _descriptions(tool):
        assert "/bin/sh -c" in text, f"{label} did not degrade: {text!r}"
        assert "bash" not in text, f"{label} still promises bash: {text!r}"


# --------------------------------------------------------------------------
# The behaviour change itself
# --------------------------------------------------------------------------


class _PopenSpy:
    instances: list = []

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.pid = 4242
        self.stdout = _DummyStream()
        self.stderr = _DummyStream()
        self.returncode = 0
        _PopenSpy.instances.append(self)

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


class _DummyStream:
    def read(self, n=-1):
        return b""

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _reset_spy():
    _PopenSpy.instances = []
    yield
    _PopenSpy.instances = []


@pytest.mark.parametrize("background", [False, True])
def test_both_popen_sites_pass_the_resolved_executable(monkeypatch, tmp_path, background):
    """Foreground and background are separate ``Popen`` calls. The ``$0``
    test above only reaches the foreground one, so a background path left on
    Python's default would run a different shell than the tool advertises."""
    monkeypatch.setattr(subprocess, "Popen", _PopenSpy)
    # ``os.getpgid`` is POSIX-only; on Windows the executor never calls it, so there is
    # nothing to stub and ``setattr`` would invent an attribute the platform lacks.
    if hasattr(os, "getpgid"):
        monkeypatch.setattr(os, "getpgid", lambda pid: pid)
    tool = _make_tool(tmp_path)
    tool.execute(
        command="echo hi",
        working_directory=".",
        timeout=1,
        is_background=background,
    )
    expected = shell_cap.resolve_shell_executable()
    assert _PopenSpy.instances, "expected Popen to be invoked"
    for inst in _PopenSpy.instances:
        assert inst.kwargs.get("shell") is True, inst.kwargs
        assert inst.kwargs.get("executable") == expected, (
            f"{'background' if background else 'foreground'} Popen got "
            f"executable={inst.kwargs.get('executable')!r}, expected {expected!r}"
        )


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX-only shell dialect")
def test_bash_only_syntax_now_runs(tmp_path):
    """Process substitution is the concrete bashism the description has
    always promised. Under ``/bin/sh`` it is a syntax error — on macOS too,
    where ``/bin/sh`` is bash in POSIX mode."""
    if shell_cap.resolve_shell_executable() is None:
        pytest.skip("no bash on this system; the tool correctly advertises /bin/sh")
    tool = _make_tool(tmp_path)
    out = tool.execute(
        command="diff <(echo a) <(echo a) && printf 'procsub_%s' ok",
        working_directory=".",
        timeout=10,
    )
    assert "procsub_ok" in out, f"process substitution still rejected: {out!r}"
    assert "syntax error" not in out.lower(), out


def test_windows_description_names_cmd(monkeypatch, tmp_path):
    """The Windows wording is unconditional, so it can be checked anywhere:
    ``shell=True`` there means ``%COMSPEC% /c`` and is deliberately left
    alone — ``executable=`` would replace cmd.exe, not pick a dialect."""
    monkeypatch.setattr("agentao.tools.shell.IS_WINDOWS", True)
    tool = _make_tool(tmp_path)
    for label, text in _descriptions(tool):
        assert "cmd /c" in text, f"{label}: {text!r}"


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX-only")
def test_windows_resolver_returns_none(monkeypatch):
    """``executable=`` on Windows replaces cmd.exe rather than selecting a
    dialect, so the resolver must decline there."""
    monkeypatch.setattr(shell_cap, "IS_WINDOWS", True)
    shell_cap.resolve_shell_executable.cache_clear()
    try:
        assert shell_cap.resolve_shell_executable() is None
        assert shell_cap.shell_display_name() == "cmd"
    finally:
        shell_cap.resolve_shell_executable.cache_clear()


# --------------------------------------------------------------------------
# The sandbox path must not fork the dialect
# --------------------------------------------------------------------------


def test_sandbox_wrapper_reenters_the_same_shell(monkeypatch, tmp_path):
    """``_wrap_with_sandbox`` spawns a *second* shell inside sandbox-exec.
    Leaving that one on ``/bin/sh`` would mean the same command parses on a
    plain run and fails under ``--sandbox`` — a dialect split that only
    shows up on macOS with sandboxing on, which no other test covers."""
    from agentao.sandbox.policy import SandboxProfile
    from agentao.tools import shell as shell_tool

    monkeypatch.setattr(shell_tool, "IS_MACOS", True)
    profile = SandboxProfile(
        name="workspace-write",
        path=tmp_path / "p.sb",
        workspace_root=tmp_path,
        params={"_RW1": str(tmp_path)},
    )
    wrapped = shell_tool._wrap_with_sandbox("echo hi", profile)
    expected = shell_cap.resolve_shell_executable() or "/bin/sh"
    assert f"{expected} -c " in wrapped, wrapped
    assert wrapped.startswith("sandbox-exec "), wrapped
