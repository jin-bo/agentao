"""A configured ``shell.path`` must be the interpreter that actually runs.

The value travelled from ``permissions.json`` into the spec and then stopped: the launch was
built with ``resolve_shell_executable()``, which is bash on POSIX and ``%COMSPEC%`` on
Windows. So a user who named ``/bin/zsh`` got a spec that said zsh and a child running bash,
with nothing anywhere reporting the substitution.

This is a defect independent of PowerShell, and it is filed separately because the fix has to
hold for the plain case — a POSIX shell, a cmd, an interpreter agentao knows nothing about —
rather than only for the dialect that motivated looking at it.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import MappingProxyType

import pytest

from agentao.capabilities.shell import (
    LocalShellExecutor,
    ShellRequest,
    _popen_target,
    resolve_shell_executable,
)
from agentao.capabilities.shell_spec import (
    AbsPath,
    LegacyLaunch,
    ShellBlock,
    ShellDialect,
    ShellSpec,
    default_spec,
)
from agentao.tools.shell import ShellTool


def _launch(executable=None) -> LegacyLaunch:
    return LegacyLaunch(
        command="echo hi",
        cwd=AbsPath(str(Path.cwd())),
        env=MappingProxyType({}),
        executable=executable,
    )


def test_a_named_interpreter_reaches_popen():
    _, kwargs = _popen_target(_launch(AbsPath("/bin/zsh")))
    assert kwargs["executable"] == "/bin/zsh"
    assert kwargs["shell"] is True


def test_an_unnamed_interpreter_keeps_the_platform_answer():
    _, kwargs = _popen_target(_launch())
    assert kwargs["executable"] == resolve_shell_executable()


def test_the_configured_path_survives_the_whole_route(tmp_path):
    """Configuration → block → spec → launch → ``Popen``.

    Asserted end to end rather than at each hop, because every hop had a test and the value
    was still dropped: what was missing was the last one.
    """
    spec = default_spec(
        ShellBlock(path=AbsPath("/bin/zsh"), dialect=ShellDialect.POSIX), windows=False
    )
    assert isinstance(spec, ShellSpec) and spec.interpreter == "/bin/zsh"
    launch = ShellTool()._launch("echo hi", tmp_path, spec)
    assert _popen_target(launch)[1]["executable"] == "/bin/zsh"


def test_an_unrecognised_provider_answer_cannot_inject_an_interpreter(tmp_path):
    """The field is read off a real spec only.

    A duck-typed object answering ``interpreter`` is not an executor's declaration, and
    treating it as one would let anything with that attribute name choose the image.
    """
    from types import SimpleNamespace

    launch = ShellTool()._launch(
        "echo hi", tmp_path, SimpleNamespace(interpreter=AbsPath("/bin/zsh"))
    )
    assert launch.executable is None


@pytest.mark.parametrize("face", ["run", "run_background"])
def test_both_delivery_faces_spawn_the_named_interpreter(monkeypatch, tmp_path, face):
    """``is_background`` is one boolean the model controls, and it picks the spawn site.

    A fix proven on the foreground face says nothing about the background one.
    """
    seen = {}

    def _fake_popen(target, **kwargs):
        seen["executable"] = kwargs.get("executable")
        raise RuntimeError("spawn suppressed")

    monkeypatch.setattr("agentao.capabilities.shell.subprocess.Popen", _fake_popen)
    executor = LocalShellExecutor()
    request = ShellRequest(
        launch=LegacyLaunch(
            command="echo hi",
            cwd=AbsPath(str(tmp_path)),
            env=MappingProxyType({}),
            executable=AbsPath("/bin/zsh"),
        )
    )
    if face == "run":
        executor.run(request)  # a spawn failure is reported, not raised
    else:
        with pytest.raises(RuntimeError):
            executor.run_background(request)
    assert seen["executable"] == "/bin/zsh"


def test_a_posix_interpreter_on_windows_is_not_handed_cmds_switch(monkeypatch, tmp_path):
    r"""``{"path": "…/bash.exe", "dialect": "posix"}`` is a documented configuration.

    Windows ``shell=True`` composes ``{executable} /c "<command>"`` — CPython substitutes
    ``executable`` for ``ComSpec`` — and ``/c`` is cmd's switch. Git Bash reads it as the name
    of a script to run, so the whole configuration produced a child that ran nothing. The
    named-interpreter shape carries ``-c``, which is the flag that interpreter takes.
    """
    from agentao.capabilities.shell_spec import WindowsLaunch

    monkeypatch.setattr("agentao.tools.shell.IS_WINDOWS", True)
    spec = ShellSpec(
        dialect=ShellDialect.POSIX,
        interpreter=AbsPath(r"C:\Program Files\Git\bin\bash.exe"),
    )
    launch = ShellTool()._launch("echo hi", tmp_path, spec)
    assert isinstance(launch, WindowsLaunch)
    assert launch.application_name == r"C:\Program Files\Git\bin\bash.exe"
    assert " -c " in launch.command_line and "/c" not in launch.command_line
    assert _popen_target(launch)[1]["shell"] is False


def test_a_powershell_spec_with_no_interpreter_refuses_rather_than_reaching_cmd(tmp_path):
    """``LegacyLaunch`` means "the platform's own shell", which on Windows is cmd.

    Falling through to it for a spec that named PowerShell is the one failure the whole
    dialect design is about: cmd reading a PowerShell body does not fail, it means something
    else — and the floor judged that body as PowerShell.
    """
    from agentao.capabilities.shell_spec import LaunchRefused

    with pytest.raises(LaunchRefused) as exc:
        ShellTool()._launch("Get-Date", tmp_path, ShellSpec(dialect=ShellDialect.POWERSHELL))
    assert "powershell" in exc.value.deny.reason


@pytest.mark.skipif(os.name == "nt", reason="POSIX shells in the probe")
def test_the_named_shell_is_the_one_that_answers(tmp_path):
    """A real child, reporting its own path.

    Under ``<shell> -c <command>`` with no trailing operands, ``$0`` is the shell itself — so
    this asks the process what it is rather than restating what was configured.
    """
    for candidate in ("/bin/sh", "/bin/zsh", "/bin/bash"):
        if not os.path.isfile(candidate):
            continue
        spec = ShellSpec(dialect=ShellDialect.POSIX, interpreter=AbsPath(candidate))
        result = LocalShellExecutor().run(
            ShellRequest(launch=ShellTool()._launch('printf %s "$0"', tmp_path, spec))
        )
        assert result.stdout.decode() == candidate, result
