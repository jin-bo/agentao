r"""A temporary probe. Two questions one CI run can answer and no ubuntu run can.

**Q1 — a detached PowerShell does not run its body.** Both interpreters, both background
tests, marker file never written. The probe varies one thing at a time: the creation flags,
whether the streams go to files instead of ``NUL``, and whether agentao's prelude/trailer is
present at all — with a cmd child as the control, since nothing has ever measured whether a
*detached* launch works on Windows for any dialect.

**Q2 — Windows PowerShell 5.1 puts a UTF-8 BOM into a native command's stdin.** ``pwsh`` does
not. The prelude already builds the encoding with ``encoderShouldEmitUTF8Identifier`` false,
so the reason has to be measured rather than reasoned about; the variants below differ only
in whether and when ``[Console]::OutputEncoding`` is assigned.

This file reports by failing: a passing test prints nothing. It is deleted once the two
answers are in.
"""

from __future__ import annotations

import base64
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

# Selected explicitly, never by a plain ``pytest tests/``: these fail on purpose, and a
# deliberate red is indistinguishable from a real one in a suite run.
pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="a Windows probe"),
    pytest.mark.skipif(
        not os.environ.get("AGENTAO_PROBE"), reason="set AGENTAO_PROBE=1 to run the probe"
    ),
]

from agentao.capabilities import powershell as ps  # noqa: E402
from agentao.capabilities.process import build_child_env  # noqa: E402

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000


def _interpreters():
    found = []
    env = dict(os.environ)
    seven = ps.discover(env)
    if seven and os.path.basename(seven).lower() == "pwsh.exe":
        found.append(("pwsh", seven))
    five = os.path.join(
        env.get("SystemRoot", r"C:\Windows"),
        "System32", "WindowsPowerShell", "v1.0", "powershell.exe",
    )
    if os.path.isfile(five):
        found.append(("powershell", five))
    return found


INTERPRETERS = _interpreters()
needs_powershell = pytest.mark.skipif(not INTERPRETERS, reason="no PowerShell on this runner")


def _encoded(interpreter: str, script: str) -> str:
    """agentao's own argument list and encoding, with the script under the probe's control."""
    b64 = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return subprocess.list2cmdline([interpreter, *ps.ARGUMENTS, "-EncodedCommand", b64])


def _wait_for(path: Path, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.1)
    return False


# ---------------------------------------------------------------- Q1: the background launch


@needs_powershell
def test_probe_which_background_launch_runs_its_body(tmp_path):
    report = []
    env = build_child_env()

    def attempt(label, target, popen_kwargs, marker, wait=15.0):
        try:
            proc = subprocess.Popen(target, **popen_kwargs)
        except Exception as exc:  # noqa: BLE001 - the probe records, it does not judge
            report.append(f"{label}: Popen raised {type(exc).__name__}: {exc}")
            return
        ran = _wait_for(marker, wait)
        try:
            code = proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            code = "still running"
        report.append(f"{label}: marker={'yes' if ran else 'NO'} exit={code}")

    for label, interpreter in INTERPRETERS:
        for flag_name, flags in (
            ("detached|newgroup", DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP),
            ("detached", DETACHED_PROCESS),
            ("newgroup", CREATE_NEW_PROCESS_GROUP),
            ("no_window", CREATE_NO_WINDOW),
            ("none", 0),
        ):
            marker = tmp_path / f"{label}-{flag_name.replace('|', '-')}.txt"
            attempt(
                f"{label} wrapped flags={flag_name}",
                _encoded(interpreter, ps.wrap(f"Set-Content -LiteralPath '{marker}' -Value 'ok'")),
                dict(
                    shell=False, executable=interpreter, cwd=str(tmp_path), env=dict(env),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=flags,
                ),
                marker,
            )

        # No prelude, no trailer: is it the wrapper or the launch?
        bare = tmp_path / f"{label}-bare.txt"
        attempt(
            f"{label} bare flags=detached|newgroup",
            _encoded(interpreter, f"Set-Content -LiteralPath '{bare}' -Value 'ok'"),
            dict(
                shell=False, executable=interpreter, cwd=str(tmp_path), env=dict(env),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            ),
            bare,
        )

        # The child's own words, which DEVNULL throws away.
        logged = tmp_path / f"{label}-logged.txt"
        out_path = tmp_path / f"{label}-stdout.log"
        err_path = tmp_path / f"{label}-stderr.log"
        with open(out_path, "wb") as out, open(err_path, "wb") as err:
            attempt(
                f"{label} wrapped flags=detached|newgroup streams=files",
                _encoded(interpreter, ps.wrap(f"Set-Content -LiteralPath '{logged}' -Value 'ok'")),
                dict(
                    shell=False, executable=interpreter, cwd=str(tmp_path), env=dict(env),
                    stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                    creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                ),
                logged,
            )
        report.append(f"  {label} stdout={out_path.read_bytes()[:400]!r}")
        report.append(f"  {label} stderr={err_path.read_bytes()[:400]!r}")

    # The control: has a detached launch on this runner ever worked, for any dialect?
    cmd_marker = tmp_path / "cmd-detached.txt"
    attempt(
        "cmd detached|newgroup",
        f'echo ok> "{cmd_marker}"',
        dict(
            shell=True, executable=os.environ.get("ComSpec"), cwd=str(tmp_path), env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        ),
        cmd_marker,
    )

    raise AssertionError("BACKGROUND PROBE\n" + "\n".join(report))


# ------------------------------------------------------------------- Q2: the BOM on the pipe


PRELUDE_VARIANTS = {
    "v1_current": (
        "$OutputEncoding = [System.Text.UTF8Encoding]::new($false)\n"
        "try { [Console]::OutputEncoding = $OutputEncoding } catch {}"
    ),
    "v2_pipe_only": "$OutputEncoding = [System.Text.UTF8Encoding]::new($false)",
    "v3_console_first": (
        "try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false) } catch {}\n"
        "$OutputEncoding = [System.Text.UTF8Encoding]::new($false)"
    ),
    "v4_console_utf8_static": (
        "$OutputEncoding = [System.Text.UTF8Encoding]::new($false)\n"
        "try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}"
    ),
    "v5_none": "",
}


@needs_powershell
def test_probe_which_prelude_keeps_a_bom_out_of_a_native_pipe(tmp_path):
    report = []
    reader = tmp_path / "reader.py"
    reader.write_text(
        "import sys\n"
        "sys.stdout.buffer.write(b'GOT:' + sys.stdin.buffer.read())\n",
        encoding="utf-8",
    )
    env = build_child_env()

    for label, interpreter in INTERPRETERS:
        for name, prelude in PRELUDE_VARIANTS.items():
            script = f"{prelude}\n'中文' | & '{sys.executable}' '{reader}'"
            proc = subprocess.run(
                _encoded(interpreter, script),
                shell=False, executable=interpreter, cwd=str(tmp_path), env=dict(env),
                stdin=subprocess.DEVNULL, capture_output=True, timeout=90,
            )
            report.append(f"{label} {name}: rc={proc.returncode} stdout={proc.stdout!r}")

    raise AssertionError("PIPE ENCODING PROBE\n" + "\n".join(report))
