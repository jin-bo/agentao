r"""What actually happens when a shell is launched on Windows.

**These are measurements, not re-assertions.** Every other test in this repository runs on
ubuntu, so the PowerShell launch is written, reviewed and never once executed there. What is
measured here is what a real child process does: whether the encoded body arrives intact,
whether the exit code comes back, whether non-ASCII survives both directions, and whether a
background launch with no console still runs its body to completion.

The interpreters are whatever the runner has. A skip is legible — it says which one was
missing — and a silent pass on a runner with no PowerShell would be worse than a red one.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="the launch matrix is about Windows"
)

from agentao.capabilities import powershell as ps  # noqa: E402
from agentao.capabilities.shell import LocalShellExecutor, ShellRequest  # noqa: E402
from agentao.capabilities.shell_spec import (  # noqa: E402
    AbsPath,
    ShellBlock,
    ShellDialect,
    ShellSpec,
    default_spec,
)
from agentao.tools.shell import ShellTool  # noqa: E402


def _interpreters():
    """Every PowerShell on this runner, as ``(label, path)``."""
    found = []
    env = dict(os.environ)
    system_root = env.get("SystemRoot", r"C:\Windows")
    seven = ps.discover(env)
    if seven and os.path.basename(seven).lower() == "pwsh.exe":
        found.append(("pwsh", seven))
    five = os.path.join(system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    if os.path.isfile(five):
        found.append(("powershell", five))
    return found


INTERPRETERS = _interpreters()
PARAMS = [pytest.param(path, id=label) for label, path in INTERPRETERS]
needs_powershell = pytest.mark.skipif(not INTERPRETERS, reason="no PowerShell on this runner")


def run(interpreter: str, body: str, cwd: Path, timeout: float = 60):
    spec = ShellSpec(dialect=ShellDialect.POWERSHELL, interpreter=AbsPath(interpreter))
    launch = ShellTool()._launch(body, cwd, spec)
    return LocalShellExecutor().run(ShellRequest(launch=launch, timeout=timeout))


def text(result) -> str:
    return result.stdout.decode("utf-8", errors="replace")


# --------------------------------------------------------------- the default path


def test_the_default_windows_shell_is_still_cmd():
    """Nothing configured means nothing changes. This is the compatibility claim, measured."""
    spec = default_spec()
    assert isinstance(spec, ShellSpec)
    assert spec.dialect is ShellDialect.CMD and spec.interpreter is None


def test_todays_launch_still_runs_through_comspec(tmp_path):
    result = LocalShellExecutor().run(
        ShellRequest(launch=ShellTool()._launch("echo hi", tmp_path, default_spec()))
    )
    assert result.returncode == 0
    assert b"hi" in result.stdout


def test_a_configured_cmd_path_is_the_cmd_that_runs(tmp_path):
    """The independent defect: ``shell.path`` reached the spec and never the spawn.

    ``shell=True`` on Windows substitutes ``executable`` for ``ComSpec``, so naming cmd
    explicitly has to produce a child that reports that same image.
    """
    comspec = os.environ.get("ComSpec", r"C:\Windows\System32\cmd.exe")
    spec = default_spec(ShellBlock(path=AbsPath(comspec), dialect=ShellDialect.CMD))
    result = LocalShellExecutor().run(
        ShellRequest(launch=ShellTool()._launch("echo %COMSPEC%", tmp_path, spec))
    )
    assert result.returncode == 0
    assert b"cmd.exe" in result.stdout.lower()


# ---------------------------------------------------------------- discovery


@needs_powershell
def test_discovery_finds_an_interpreter_that_actually_starts():
    """The candidate list is written from documentation; this is the part that measures it."""
    found = ps.discover()
    assert found is not None and os.path.isfile(found)
    probe = subprocess.run(
        [found, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "$PSVersionTable.PSEdition"],
        capture_output=True, text=True, timeout=120,
    )
    assert probe.returncode == 0
    assert probe.stdout.strip() in {"Core", "Desktop"}


@needs_powershell
def test_the_configured_dialect_resolves_to_a_real_interpreter():
    spec = default_spec(ShellBlock(dialect=ShellDialect.POWERSHELL))
    assert isinstance(spec, ShellSpec)
    assert spec.interpreter and os.path.isfile(spec.interpreter)


# ------------------------------------------------------------------- the body


@pytest.mark.parametrize("interpreter", PARAMS)
def test_the_body_arrives_intact(interpreter, tmp_path):
    r"""Every character that any quoting layer would have mangled, in one body.

    Spaces, both quote characters, a percent sign, a caret, an ampersand, a pipe, redirection
    characters, a backslash and a newline. Under ``-Command`` most of these need escaping by
    a rule that differs between cmd and PowerShell; under ``-EncodedCommand`` none of them
    does, and that is the property being measured.
    """
    body = "Write-Output 'a b \"q\" % ^ & | < > \\'\nWrite-Output 'second line'"
    result = run(interpreter, body, tmp_path)
    assert result.returncode == 0, result.stderr
    out = text(result)
    assert 'a b "q" % ^ & | < > \\' in out
    assert "second line" in out


@pytest.mark.parametrize("interpreter", PARAMS)
def test_non_ascii_survives_stdout(interpreter, tmp_path):
    """The prelude's console assignment is what makes this true on 5.1.

    Without it PowerShell writes in the console code page and agentao decodes UTF-8, so every
    Chinese character comes back as replacement characters.
    """
    result = run(interpreter, "Write-Output '中文 ünïcode ✓'", tmp_path)
    assert result.returncode == 0, result.stderr
    assert "中文 ünïcode ✓" in text(result)


@pytest.mark.parametrize("interpreter", PARAMS)
def test_non_ascii_survives_the_pipe_to_a_native_command(interpreter, tmp_path):
    r"""``$OutputEncoding`` governs what PowerShell writes to a native command's stdin.

    5.1 defaults it to ASCII, so without the prelude's first line every non-ASCII byte piped
    into a native program becomes a question mark. Measured against a real native program —
    ``python`` reading UTF-8 stdin — because the claim is about the pipe, not about
    PowerShell talking to itself.
    """
    reader = tmp_path / "reader.py"
    reader.write_text(
        "import sys, io\n"
        "data = io.TextIOWrapper(sys.stdin.buffer, encoding='utf-8').read()\n"
        "sys.stdout.buffer.write(('GOT:' + data.strip()).encode('utf-8'))\n",
        encoding="utf-8",
    )
    body = f"'中文' | & '{sys.executable}' '{reader}'"
    result = run(interpreter, body, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "GOT:中文" in text(result)
    assert "?" not in text(result)


@pytest.mark.parametrize("interpreter", PARAMS)
def test_a_captured_native_output_is_decoded_as_utf8(interpreter, tmp_path):
    """The console assignment also decides how PowerShell decodes what it captures.

    A native program that writes its own UTF-8 is the case that works; one writing some other
    encoding is not promised, which is why the docs say so rather than claiming a universal
    transcode.
    """
    writer = tmp_path / "writer.py"
    writer.write_text(
        "import sys\nsys.stdout.buffer.write('中文'.encode('utf-8'))\n", encoding="utf-8"
    )
    body = f"$out = & '{sys.executable}' '{writer}'; Write-Output \"CAP:$out\""
    result = run(interpreter, body, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "CAP:中文" in text(result)


@pytest.mark.parametrize("interpreter", PARAMS)
def test_the_working_directory_is_the_one_that_was_asked_for(interpreter, tmp_path):
    work = tmp_path / "work dir"
    work.mkdir()
    result = run(interpreter, "Write-Output (Get-Location).Path", work)
    assert result.returncode == 0, result.stderr
    assert str(work) in text(result)


@pytest.mark.parametrize("interpreter", PARAMS)
def test_a_long_body_that_fits_is_not_refused(interpreter, tmp_path):
    """Just under the ceiling: the measurement has to be of the encoded line, and a body this
    size proves the arithmetic is not being applied to the raw text."""
    body = "Write-Output 'x'  " + "#" + "y" * 11_000
    result = run(interpreter, body, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "x" in text(result)


@needs_powershell
def test_an_oversized_body_is_refused_before_createprocess(tmp_path):
    """A clear refusal rather than an opaque ``CreateProcess`` failure, and no truncation.

    The tool has to be given a PowerShell executor. A bare ``ShellTool()`` resolves to
    Windows' default ``cmd`` spec, where the length check does not run at all and the
    assertion passes on ``[WinError 206] The filename or extension is too long`` — the
    ``CreateProcess`` failure this test exists to say does not happen.
    """
    tool = ShellTool()
    tool.shell = LocalShellExecutor(shell_block=ShellBlock(dialect=ShellDialect.POWERSHELL))
    # The tool's own working directory is the project root the path policy measures against,
    # and on a CI runner the repository and the temporary directory are on different drives.
    # Without this the call is refused for the cwd and never reaches the length check — a
    # green-looking assertion about a refusal that is not the one under test.
    tool.working_directory = str(tmp_path)
    out = tool.execute(
        command="Write-Output 'x'; " + "#" + "y" * 40_000,
        working_directory=str(tmp_path),
        timeout=30,
        _decided=None,
    )
    assert "not launchable" in out
    assert "UTF-16 units" in out


# ------------------------------------------------------------------ exit codes


@pytest.mark.parametrize("interpreter", PARAMS)
@pytest.mark.parametrize(
    "body,expected",
    [
        ("cmd.exe /c exit 7", 7),
        ("Write-Output 'ok'", 0),
        ("exit 9", 9),
        # A cmdlet error leaves ``$LASTEXITCODE`` alone, so the generic failure code stands
        # in. Appending a bare ``exit $LASTEXITCODE`` would report the stale 0 here.
        ("Get-Item 'C:\\definitely\\not\\here' -ErrorAction Continue", 1),
        # A terminating error: PowerShell exits on its own.
        ("throw 'boom'", 1),
        # The last statement decides. An earlier failure followed by a success is a success,
        # deliberately: the body is not rewritten into fail-fast.
        ("cmd.exe /c exit 3; Write-Output 'recovered'", 0),
        # A native failure after a success still reports the native code.
        ("Write-Output 'ok'; cmd.exe /c exit 5", 5),
    ],
)
def test_the_exit_code_follows_the_last_statement(interpreter, body, expected, tmp_path):
    result = run(interpreter, body, tmp_path)
    assert result.returncode == expected, (body, result.returncode, result.stderr)


@pytest.mark.parametrize("interpreter", PARAMS)
def test_a_trailing_continuation_backtick_is_recorded_rather_than_assumed(interpreter, tmp_path):
    """The trailer is parsed together with the body, so a trailing backtick can absorb it.

    This does not assert a particular outcome — it records that the launch survives it and
    reports *something*, because the exit-code guarantee is stated only for a body that ends
    on its own.
    """
    result = run(interpreter, "Write-Output 'x' `", tmp_path)
    assert isinstance(result.returncode, int)


# ------------------------------------------------------------------ CLIXML


@pytest.mark.parametrize("interpreter", PARAMS)
def test_a_redirected_error_stream_comes_back_as_readable_text(interpreter, tmp_path):
    """5.1 wraps a redirected error stream in CLIXML; 7 does not.

    Both are asserted the same way on purpose — the extraction has to be a no-op on the
    stream that is already text, and the model must read a message either way.
    """
    marker = "agentao-clixml-probe"
    out = _tool_output(interpreter, f"Write-Error '{marker}'", tmp_path)
    assert marker in out
    assert "#< CLIXML" not in out
    assert "_x000D_" not in out


def _tool_output(interpreter: str, body: str, cwd: Path) -> str:
    from agentao.capabilities.shell_spec import DecidedCall, PASS

    tool = ShellTool()
    tool.shell = LocalShellExecutor(
        shell_block=ShellBlock(path=AbsPath(interpreter), dialect=ShellDialect.POWERSHELL)
    )
    return tool.execute(
        command=body,
        working_directory=str(cwd),
        timeout=60,
        _decided=DecidedCall(
            spec=tool.shell_spec, body=body, cwd=AbsPath(str(cwd)), verdict=PASS
        ),
    )


# ------------------------------------------------------------------ background


@pytest.mark.parametrize("interpreter", PARAMS)
def test_a_background_launch_runs_its_body_to_completion(interpreter, tmp_path):
    """The one path with no console at all — ``DETACHED_PROCESS``, all three streams DEVNULL.

    That is where ``[Console]::OutputEncoding`` throws, so this is what proves the ``catch``
    is load-bearing rather than decorative. Asserting only that a PID came back would pass
    with a child that died on its first statement.
    """
    done = tmp_path / "done.txt"
    spec = ShellSpec(dialect=ShellDialect.POWERSHELL, interpreter=AbsPath(interpreter))
    launch = ShellTool()._launch(
        f"Set-Content -LiteralPath '{done}' -Value '中文 finished'", tmp_path, spec
    )
    handle = LocalShellExecutor().run_background(ShellRequest(launch=launch))
    assert handle.pid

    deadline = time.monotonic() + 90
    while time.monotonic() < deadline and not done.exists():
        time.sleep(0.2)
    assert done.exists(), "the background body never wrote its completion marker"
    assert "中文 finished" in done.read_text(encoding="utf-8")


@pytest.mark.parametrize("interpreter", PARAMS)
def test_a_background_pipe_to_a_native_command_keeps_its_encoding(interpreter, tmp_path):
    """``$OutputEncoding`` is set before the console assignment precisely so that it survives
    the path where that assignment throws."""
    reader = tmp_path / "reader.py"
    out = tmp_path / "piped.txt"
    reader.write_text(
        "import sys, io\n"
        "data = io.TextIOWrapper(sys.stdin.buffer, encoding='utf-8').read()\n"
        f"open(r'{out}', 'w', encoding='utf-8').write(data)\n",
        encoding="utf-8",
    )
    spec = ShellSpec(dialect=ShellDialect.POWERSHELL, interpreter=AbsPath(interpreter))
    launch = ShellTool()._launch(
        f"'中文' | & '{sys.executable}' '{reader}'", tmp_path, spec
    )
    LocalShellExecutor().run_background(ShellRequest(launch=launch))

    deadline = time.monotonic() + 90
    while time.monotonic() < deadline and not out.exists():
        time.sleep(0.2)
    assert out.exists(), "the background pipe never reached the native command"
    assert "中文" in out.read_text(encoding="utf-8")


# ------------------------------------------------------- the floor reaches a launch


@needs_powershell
def test_a_clean_body_travels_the_real_planning_chain_to_a_launch(tmp_path):
    """End to end through the planner, not through a hand-built record.

    Each half had tests and the combination had none, which is how the previous design
    shipped a PowerShell path that refused every clean body: the planner never passed the
    decided record the floor demanded, so the refusal was frozen in as the verdict.
    """
    from agentao.runtime.tool_planning import _decided_call

    tool = ShellTool()
    tool.shell = LocalShellExecutor(shell_block=ShellBlock(dialect=ShellDialect.POWERSHELL))
    tool.working_directory = str(tmp_path)
    args = {"command": "Write-Output 'planned'", "working_directory": str(tmp_path)}

    record = _decided_call(tool, tool.shell_spec, args)
    assert record is not None
    assert not hasattr(record.verdict, "reason"), record.verdict

    out = tool.execute(**args, timeout=60, _decided=record)
    assert "planned" in out


@needs_powershell
def test_a_dangerous_body_is_refused_and_never_launched(tmp_path):
    r"""The verdict, not the effect. Nothing here runs ``Remove-Item -Recurse C:\``."""
    from agentao.runtime.tool_planning import _decided_call

    tool = ShellTool()
    tool.shell = LocalShellExecutor(shell_block=ShellBlock(dialect=ShellDialect.POWERSHELL))
    tool.working_directory = str(tmp_path)
    for command in (r"Remove-Item -Recurse -Force C:\ ", r"ri -r -fo C:\ ", "rm -rf /"):
        args = {"command": command, "working_directory": str(tmp_path)}
        record = _decided_call(tool, tool.shell_spec, args)
        assert record is not None and hasattr(record.verdict, "reason"), command
