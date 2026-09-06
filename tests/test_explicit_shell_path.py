"""A configured ``shell.path`` must be the interpreter that actually runs.

The value travelled from ``permissions.json`` into ``ShellSpec.explicit_shell`` and into the
spec fingerprint, and then stopped: ``_popen_target`` built the legacy launch with
``resolve_shell_executable()``, which on POSIX is bash and on Windows is ``%COMSPEC%``. So a
user who named ``/bin/zsh`` got a spec that said zsh, a fingerprint that distinguished zsh
from bash, and a child running bash — with nothing anywhere reporting the substitution.

Both delivery faces are covered here, because they are two ``Popen`` call sites reached by a
single tool argument (``is_background``), and a fix proven on one says nothing about the
other.
"""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import pytest

from agentao.capabilities.shell import (
    LocalShellExecutor,
    ShellRequest,
    _popen_target,
)
from agentao.capabilities.shell_spec import AbsPath, LegacyLaunch, Sha256
from agentao.tools.shell import ShellTool


def _launch(executable=None) -> LegacyLaunch:
    return LegacyLaunch(
        command="echo hi",
        cwd=AbsPath(str(Path.cwd())),
        env=MappingProxyType({}),
        spec_fingerprint=Sha256(""),
        executable=executable,
    )


def test_a_named_interpreter_reaches_popen():
    _, kwargs = _popen_target(_launch(AbsPath("/bin/zsh")))
    assert kwargs["executable"] == "/bin/zsh"
    assert kwargs["shell"] is True


def test_an_unnamed_interpreter_keeps_the_platform_answer():
    from agentao.capabilities.shell import resolve_shell_executable

    _, kwargs = _popen_target(_launch())
    assert kwargs["executable"] == resolve_shell_executable()


def test_the_tool_puts_the_specs_named_interpreter_on_the_launch():
    tool = ShellTool()
    spec = SimpleNamespace(explicit_shell=AbsPath("/bin/zsh"), fingerprint=Sha256("f"))
    launch = tool._legacy_launch("echo hi", Path.cwd(), spec)
    # ``SimpleNamespace`` is deliberately not a ``ShellSpec``: the tool reads the field only
    # off a real spec, so this asserts the negative — an unrecognised provider answer must
    # not be able to inject an interpreter.
    assert launch.executable is None

    from agentao.capabilities.shell_spec import (
        Platform,
        Rung,
        ShellDialect,
        Subject,
        legacy_spec,
    )

    real = legacy_spec(
        ShellDialect.POSIX,
        Rung.system_posix,
        Platform.POSIX,
        Subject("x"),
        local=True,
        explicit_shell=AbsPath("/bin/zsh"),
    )
    assert tool._legacy_launch("echo hi", Path.cwd(), real).executable == "/bin/zsh"


@pytest.mark.parametrize("face", ["run", "run_background"])
def test_both_delivery_faces_spawn_the_named_interpreter(monkeypatch, tmp_path, face):
    seen = {}

    class _Proc:
        pid = 4321
        returncode = 0
        stdout = None
        stderr = None

        def poll(self):
            return 0

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
            spec_fingerprint=Sha256(""),
            executable=AbsPath("/bin/zsh"),
        )
    )
    if face == "run":
        executor.run(request)  # a spawn failure is reported, not raised
    else:
        with pytest.raises(RuntimeError):
            executor.run_background(request)
    assert seen["executable"] == "/bin/zsh"
