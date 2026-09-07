"""Handing a body to PowerShell, and getting an exit code back.

Everything between the model's text and ``CreateProcessW`` is here: the wrapping, the
encoding, the argument list, the length measurement, and which of the two delivery faces
builds it. These are shape assertions rather than measurements — a real PowerShell is a
Windows fact and lives in ``tests/test_windows_launch_matrix.py``.

The invariant the whole file is about: **the body is passed through untouched, and both
fixed halves are agentao's own text.** Nothing quotes, escapes or rewrites what the model
wrote, because every such transformation is a place where the text the floor scanned and the
text PowerShell parses could differ.
"""

from __future__ import annotations

import base64
import os
import subprocess
from pathlib import Path
from types import MappingProxyType

import pytest

from agentao.capabilities import powershell as ps
from agentao.capabilities.shell import LocalShellExecutor, ShellRequest, _popen_target
from agentao.capabilities.shell_spec import (
    AbsPath,
    LaunchRefused,
    LegacyLaunch,
    ShellDialect,
    ShellSpec,
    WindowsLaunch,
)
from agentao.tools.shell import ShellTool

PWSH = AbsPath(r"C:\Program Files\PowerShell\7\pwsh.exe")


def _spec() -> ShellSpec:
    return ShellSpec(dialect=ShellDialect.POWERSHELL, interpreter=PWSH)


def _decoded(line: str) -> str:
    encoded = line.rsplit(" ", 1)[-1].strip('"')
    return base64.b64decode(encoded).decode("utf-16-le")


# ------------------------------------------------------------------- the script


def test_the_body_is_passed_through_byte_for_byte():
    """Not quoted, not escaped, not re-indented.

    Encoding is what removes the quoting problem, so there is nothing left for the wrapper to
    do to the body — and anything it did would be a difference between what was scanned and
    what runs.
    """
    body = 'Write-Output "a`nb"; $x = @{ k = 1 }\r\n# 中文 % ^ & | < >'
    assert f"\n{body}\n" in ps.wrap(body)


def test_the_prelude_sets_the_pipe_encoding_before_it_touches_the_console():
    """Two assignments, and the order is the point.

    ``[Console]::OutputEncoding`` throws when there is no console, which is exactly the
    background launch. Doing it first would leave ``$OutputEncoding`` unset on that path, and
    Windows PowerShell 5.1 defaults it to ASCII — so every non-ASCII byte piped into a native
    program becomes a question mark.
    """
    script = ps.wrap("Get-Date")
    pipe = script.index("$OutputEncoding =")
    console = script.index("[Console]::OutputEncoding =")
    assert pipe < console
    assert "try {" in script and "} catch {}" in script


def test_the_console_assignment_is_the_only_thing_the_catch_covers():
    """A ``try`` around the body would swallow the user's own errors and report success."""
    script = ps.wrap("throw 'boom'")
    guarded = script[script.index("try {"): script.index("} catch {}") + len("} catch {}")]
    assert "throw 'boom'" not in guarded


def test_last_exit_code_is_initialised_before_the_body_runs():
    """The trailer reads it. Without this it would consult whatever was left over."""
    script = ps.wrap("Get-Date")
    assert script.index("$LASTEXITCODE = 0") < script.index("Get-Date")


def test_the_trailer_captures_the_success_flag_first():
    """``$?`` is replaced by every statement, including the ``if`` that would read it.

    And the two failure branches are distinct on purpose: a *native* command's own exit code
    is the useful one, while a cmdlet that wrote an error leaves ``$LASTEXITCODE`` untouched,
    so appending a bare ``exit $LASTEXITCODE`` would report a stale 0 — a failing command
    reported as success.
    """
    trailer = ps.EPILOGUE
    assert trailer.startswith("$__agentao_ok = $?;")
    assert "exit 0" in trailer and "exit $LASTEXITCODE" in trailer and "exit 1" in trailer


def test_the_two_fixed_halves_contain_no_byte_of_the_body():
    """Stated as a property rather than argued case by case, which is why it is testable."""
    body = "$__agentao_ok = 'spoofed'"
    assert body not in ps.PRELUDE and body not in ps.EPILOGUE


# ------------------------------------------------------------------ the encoding


def test_the_encoded_command_round_trips_through_utf16le():
    body = "Write-Output '中文 ünïcode'"
    assert _decoded(ps.command_line(PWSH, body)) == ps.wrap(body)


def test_the_argument_list_is_fixed_and_non_interactive():
    """``-NoProfile`` matters most: a user profile can redefine any name the body uses."""
    line = ps.command_line(PWSH, "Get-Date")
    for flag in ("-NoLogo", "-NoProfile", "-NonInteractive", "-OutputFormat", "Text",
                 "-EncodedCommand"):
        assert flag in line
    assert line.startswith(subprocess.list2cmdline([PWSH]))


def test_a_body_that_would_need_quoting_needs_none():
    """The whole reason for ``-EncodedCommand``: no layer in between has to agree about
    backslashes, percent signs, embedded quotes or newlines."""
    line = ps.command_line(PWSH, 'echo "a b" & ^ % | > < \n `')
    assert '"' not in line.split("-EncodedCommand ")[-1]


# -------------------------------------------------------------------- the length


def test_a_command_line_that_fits_is_not_refused():
    assert ps.oversize(ps.command_line(PWSH, "Get-Date")) is None


def test_an_oversized_command_line_is_refused_with_the_measurement():
    """Not truncated, and not spilled to a temporary script.

    A cut inside the base64 changes what runs, and writing a file would run something the
    floor never scanned, under a name nobody chose. So the answer is a refusal that says how
    far over it was and what to do instead.
    """
    reason = ps.oversize(ps.command_line(PWSH, "x" * 20_000))
    assert reason is not None
    assert str(ps.CREATEPROCESS_MAX_UNITS) in reason.replace(",", "")
    assert "split the work" in reason


def test_the_measurement_is_of_the_encoded_line_not_the_body():
    """Base64 of UTF-16LE costs 8 units per 3 characters, so measuring the body would be
    wrong by a factor of nearly three — in the permissive direction."""
    body = "x" * 13_000
    assert len(body) < ps.CREATEPROCESS_MAX_UNITS
    assert ps.oversize(ps.command_line(PWSH, body)) is not None


# --------------------------------------------------------------- the launch shape


def test_the_tool_builds_a_named_launch_for_a_powershell_spec(tmp_path):
    launch = ShellTool()._launch("Get-Date", tmp_path, _spec())
    assert isinstance(launch, WindowsLaunch)
    assert launch.application_name == PWSH
    assert _decoded(launch.command_line) == ps.wrap("Get-Date")
    assert launch.cwd == str(tmp_path)


def test_the_tool_still_builds_todays_launch_for_everything_else(tmp_path):
    launch = ShellTool()._launch("echo hi", tmp_path, ShellSpec(dialect=ShellDialect.POSIX))
    assert isinstance(launch, LegacyLaunch)
    assert launch.command == "echo hi" and launch.executable is None


def test_a_named_interpreter_reaches_popen_without_a_shell(tmp_path):
    """``shell=False``, and the image fixed by path rather than resolved from a name."""
    launch = ShellTool()._launch("Get-Date", tmp_path, _spec())
    target, kwargs = _popen_target(launch)
    assert kwargs["shell"] is False
    assert kwargs["executable"] == PWSH
    assert target == launch.command_line


def test_a_configured_interpreter_reaches_popen(tmp_path):
    """The interpreter is the same on both platforms; the route to it is not.

    POSIX ``shell=True`` runs ``/bin/sh -c`` with ``argv[0]`` replaced by ``executable``, so
    one field is enough there. Windows ``shell=True`` composes ``{executable} /c "<command>"``
    instead, and an interpreter that is not cmd reads ``/c`` as a script name and runs
    nothing — so a POSIX interpreter configured there is launched directly with its own
    ``-c``. The assertion is written as one expression rather than two branches so that a
    platform silently losing the interpreter cannot pass.
    """
    spec = ShellSpec(dialect=ShellDialect.POSIX, interpreter=AbsPath("/bin/zsh"))
    _, kwargs = _popen_target(ShellTool()._launch("echo hi", tmp_path, spec))
    assert kwargs["executable"] == "/bin/zsh"
    assert kwargs["shell"] is (os.name != "nt")


def test_an_unnamed_interpreter_keeps_the_platform_answer(tmp_path):
    from agentao.capabilities.shell import resolve_shell_executable

    spec = ShellSpec(dialect=ShellDialect.POSIX)
    _, kwargs = _popen_target(ShellTool()._launch("echo hi", tmp_path, spec))
    assert kwargs["executable"] == resolve_shell_executable()


def test_an_oversized_body_refuses_at_the_launch_rather_than_at_createprocess(tmp_path):
    with pytest.raises(LaunchRefused) as exc:
        ShellTool()._launch("x" * 20_000, tmp_path, _spec())
    assert "not launchable" in exc.value.deny.reason


@pytest.mark.parametrize("background", [False, True])
def test_both_delivery_faces_build_the_same_launch(monkeypatch, tmp_path, background):
    """``is_background`` chooses which method delivers the request and nothing else.

    It used to choose a second spawn path with its own ``shell=True``, which meant a property
    proved about one face said nothing about the other — and the model picks between them
    with one boolean argument.
    """
    seen = {}

    def _fake_popen(target, **kwargs):
        seen["target"], seen["kwargs"] = target, kwargs
        raise RuntimeError("spawn suppressed")

    monkeypatch.setattr("agentao.capabilities.shell.subprocess.Popen", _fake_popen)
    launch = ShellTool()._launch("Get-Date", tmp_path, _spec())
    executor = LocalShellExecutor()
    request = ShellRequest(launch=launch)
    if background:
        with pytest.raises(RuntimeError):
            executor.run_background(request)
    else:
        executor.run(request)  # a spawn failure is reported, not raised
    assert seen["kwargs"]["executable"] == PWSH
    assert seen["kwargs"]["shell"] is False
    assert _decoded(seen["target"]) == ps.wrap("Get-Date")


def test_the_background_report_names_the_body_not_the_encoded_line(monkeypatch, tmp_path):
    """base64 tells a reader nothing, and the handle is what a user reads to stop the job."""
    from agentao.capabilities.shell import BackgroundHandle

    class Fake:
        def run_background(self, request):
            return BackgroundHandle(pid=99, pgid=None, command=request.command, cwd=Path("."))

        def run(self, request):  # pragma: no cover - never reached
            raise AssertionError

    tool = ShellTool()
    tool.shell = Fake()
    out = tool._run_background("Get-Date", tmp_path, _spec())
    assert "Command: Get-Date" in out
    assert "EncodedCommand" not in out


def test_the_environment_is_rebuilt_per_call(tmp_path, monkeypatch):
    """An installer that edits ``PATH`` does not reach a process that is already running, so
    the child gets the host's environment as it is *now* rather than as it was at startup."""
    monkeypatch.setenv("AGENTAO_PROBE_VALUE", "first")
    first = ShellTool()._launch("Get-Date", tmp_path, _spec())
    monkeypatch.setenv("AGENTAO_PROBE_VALUE", "second")
    second = ShellTool()._launch("Get-Date", tmp_path, _spec())
    assert first.env["AGENTAO_PROBE_VALUE"] == "first"
    assert second.env["AGENTAO_PROBE_VALUE"] == "second"


def test_the_child_environment_still_drops_agentaos_own_credentials(tmp_path, monkeypatch):
    """``build_child_env`` on both paths: a command the model wrote must not be able to read
    back the key that pays for the model."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    launch = ShellTool()._launch("Get-Date", tmp_path, _spec())
    assert "OPENAI_API_KEY" not in launch.env


def test_a_launch_env_is_not_writable_by_its_holder(tmp_path):
    launch = ShellTool()._launch("Get-Date", tmp_path, _spec())
    assert isinstance(launch.env, MappingProxyType)


def test_a_windows_background_launch_asks_for_no_window_never_a_detached_process(
    monkeypatch, tmp_path
):
    r"""``DETACHED_PROCESS`` starts a PowerShell that never runs its script.

    Measured on a Windows runner, both interpreters, both wrapped and bare: the child exits
    **0 with empty stdout and stderr** and the body's first statement never happens. Only the
    pid comes back, so a background command reads as started and silently does nothing.
    ``CREATE_NO_WINDOW`` gives the child a console nobody looks at, and the body runs; the two
    flags are mutually exclusive, so it is a swap.

    This runs on every platform on purpose. The Windows measurement lives in
    ``tests/test_windows_launch_matrix.py``, but the edit that would undo it — "these two
    flags mean the same thing" — gets made on a machine that is not Windows, where those
    ``subprocess`` constants do not even exist. The Win32 values are written out here for
    that reason.
    """
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    CREATE_NO_WINDOW = 0x08000000

    import agentao.capabilities.shell as shell_module

    monkeypatch.setattr(shell_module, "IS_WINDOWS", True)
    for name, value in (
        ("CREATE_NEW_PROCESS_GROUP", CREATE_NEW_PROCESS_GROUP),
        ("CREATE_NO_WINDOW", CREATE_NO_WINDOW),
        ("DETACHED_PROCESS", DETACHED_PROCESS),
    ):
        monkeypatch.setattr(shell_module.subprocess, name, value, raising=False)

    seen = {}

    def _fake_popen(target, **kwargs):
        seen.update(kwargs)
        raise RuntimeError("spawn suppressed")

    monkeypatch.setattr(shell_module.subprocess, "Popen", _fake_popen)
    with pytest.raises(RuntimeError):
        shell_module.LocalShellExecutor().run_background(
            ShellRequest(launch=ShellTool()._launch("Get-Date", tmp_path, _spec()))
        )

    assert seen["creationflags"] & CREATE_NO_WINDOW
    assert not seen["creationflags"] & DETACHED_PROCESS
    assert seen["creationflags"] & CREATE_NEW_PROCESS_GROUP
