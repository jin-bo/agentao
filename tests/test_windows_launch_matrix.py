"""What actually happens when a rung is launched on Windows.

PR-6 of the PowerShell ladder. ``LAUNCH-02``..``LAUNCH-09a``, ``ENV-02``, ``ENV-04``,
``ENV-06`` and ``LADDER-05`` are defined once each in
``docs/design/powershell-support-spec.zh.md`` §2; the cases here are the gate matrix's
``windows / PR-6`` rows that need no identity oracle.

**These are measurements, not re-assertions.** Every other test in this repository runs on
ubuntu, so the command lines LAUNCH-03 and LAUNCH-02 specify have been written, reviewed
eleven times and never once executed. What is measured here is what the child process
actually receives: whether the ``/s`` quoting survives, whether the pinned environment
arrives, whether a bad working directory really exits 98 without running a byte of the body.

**The oracle is a stub and the launch is real.** Identity answers — access masks,
Authenticode — are about a machine's security state and belong to the native oracle that
does not exist yet. Everything downstream of the spec is genuine: a real ``cmd.exe``, a real
``ResolvedImage`` built from the real file's identity and hash, a real ``Popen``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="the launch matrix is about Windows"
)

if sys.platform == "win32":  # pragma: no cover - the module is skipped elsewhere
    from agentao.capabilities.shell import (
        LocalShellExecutor,
        ShellRequest,
        local_content_hash,
        local_filesystem_identity,
    )
    from agentao.capabilities.shell_spec import (
        AbsDir,
        AbsFile,
        AbsPath,
        DriveSpec,
        HashPin,
        Platform,
        ResolvedImage,
        RootRelPath,
        Rung,
        ShellBlock,
        ShellDialect,
        ShellSpec,
        default_spec,
        local_subject,
    )
    from agentao.permissions_hardline._analysis import decided_call
    from agentao.permissions_hardline._trust import attested_spec, request_for

    from ._trust_fakes import FakeOracle, interpreter


SENTINEL = "agentao-caret^ok"
"""ASCII on purpose, and the rest of G18-02 is still owed.

Reading a sentinel back through a child's stdout measures the console code page as much as
the transport: cmd writes in the OEM page, and whether a runner has been switched to UTF-8 is
not something this test should depend on. The non-ASCII, ``%``, ``"`` and newline half of
G18-02 needs a body that reports its own bytes — a program printing ``GetCommandLineW()`` —
and that instrument is not built yet. Saying so beats a flaky assertion that looks like
coverage."""


def text(launched) -> str:
    """A child's stdout as text. ``errors="replace"`` because the code page is the runner's."""
    return launched.out()


def real_image(path: str, subject) -> "ResolvedImage":
    """A ``ResolvedImage`` for a file that exists, built from its real identity and hash.

    The executor re-checks both immediately before spawning (LAUNCH-01d), so a fabricated
    one would be refused — which is the point: this is the launch path, not a bypass of it.
    """
    return ResolvedImage(
        canonical_path=AbsPath(path),
        filesystem_identity=local_filesystem_identity(path),
        execution_subject=subject,
        content_identity=HashPin(path=AbsPath(path), sha256=local_content_hash(path)),
    )


def pinned_from_environment() -> "PinnedEnv":  # noqa: F821 - Windows-only import above
    """ENV-06 (1) filled from this machine, so the child gets roots that actually exist."""
    from agentao.capabilities.shell_spec import PinnedEnv

    system_root = os.environ["SystemRoot"]
    profile = os.environ["USERPROFILE"]
    return PinnedEnv(
        system_root=AbsDir(system_root),
        windir=AbsDir(os.environ.get("windir", system_root)),
        system_drive=DriveSpec(os.environ.get("SystemDrive", "C:")),
        program_data=AbsDir(os.environ.get("ProgramData", "C:\\ProgramData")),
        program_files=AbsDir(os.environ.get("ProgramFiles", "C:\\Program Files")),
        common_program_files=AbsDir(
            os.environ.get("CommonProgramFiles", "C:\\Program Files\\Common Files")
        ),
        all_users_profile=AbsDir(os.environ.get("ALLUSERSPROFILE", "C:\\ProgramData")),
        public=AbsDir(os.environ.get("PUBLIC", "C:\\Users\\Public")),
        com_spec=AbsFile(os.environ.get("ComSpec", system_root + "\\System32\\cmd.exe")),
        home=AbsDir(profile),
        user_profile=AbsDir(profile),
        home_drive=DriveSpec(os.environ.get("HOMEDRIVE", profile[:2])),
        home_path=RootRelPath(os.environ.get("HOMEPATH", profile[2:])),
        appdata=AbsDir(os.environ["APPDATA"]),
        local_appdata=AbsDir(os.environ["LOCALAPPDATA"]),
        temp=AbsDir(os.environ["TEMP"]),
        tmp=AbsDir(os.environ.get("TMP", os.environ["TEMP"])),
    )


def rung_spec(rung, launcher_path: str, *, edition: str = "", version: str = "") -> "ShellSpec":
    """A policy-on spec for a real interpreter, with the identity answers stubbed."""
    subject = local_subject()
    image = real_image(launcher_path, subject)
    identity = interpreter(
        launcher_path, edition=edition, version=version, img=image,
        pshome=str(Path(launcher_path).parent),
    )
    oracle = FakeOracle(
        target=Platform.WINDOWS,
        subject=subject,
        local=True,
        writable=set(),  # the native oracle's job; this test measures the launch
        pinned=pinned_from_environment(),
        project_root=AbsPath(str(Path.cwd())),
        path_entries=(),
        base_env=dict(os.environ),
        identities={launcher_path: identity},
        trusted_publishers={launcher_path},
        pshome=AbsPath(str(Path(launcher_path).parent)),
    )
    spec = attested_spec(
        rung, image, identity, ShellBlock(), oracle, Platform.WINDOWS, subject, True
    )
    assert isinstance(spec, ShellSpec), spec
    return spec


class Launched:
    """A launch and everything needed to explain it when it goes wrong.

    The first Windows run reported ``AssertionError: (98, '')`` and could say no more: the
    assertion carried the exit code and stdout, and the reason a prelude refuses is on stderr.
    A test for a platform its author cannot reproduce on has to carry its own diagnosis.
    """

    def __init__(self, result, request) -> None:
        self.result = result
        self.request = request

    @property
    def returncode(self) -> int:
        return self.result.returncode

    def out(self) -> str:
        return self.result.stdout.decode("utf-8", errors="replace")

    def __repr__(self) -> str:
        # ``__repr__`` and not ``__str__``: ``assert x, obj`` reaches the report through
        # ``repr``, which is how the first run's message read
        # ``<Launched object at 0x...>`` and said nothing at all.
        command = (
            self.request.command_line if hasattr(self.request, "command_line")
            else subprocess.list2cmdline(list(self.request.argv))
        )
        return (
            f"\nexit={self.result.returncode}"
            f"\nstdout={self.out()!r}"
            f"\nstderr={self.result.stderr.decode('utf-8', errors='replace')!r}"
            f"\ncommand={command!r}"
            f"\ncwd={self.request.cwd!r} workdir={self.request.workdir!r}"
        )


def launch(spec, body: str, cwd: Path) -> Launched:
    """Decide the call, then run it through the executor exactly as the runtime would."""
    record = decided_call(spec, body, AbsPath(str(cwd)), None)
    from agentao.permissions_hardline._trust import encode_workdir

    literal = encode_workdir(AbsPath(str(cwd)), spec.dialect)
    assert literal is not None, cwd
    request = request_for(
        spec, spec.launcher, body, literal, record.child_env or {},
        AbsPath(str(cwd)), record.attested_images,
    )
    assert request is not None
    return Launched(LocalShellExecutor().run(ShellRequest(launch=request, timeout=60)), request)


# ------------------------------------------------------------------ LADDER-05


def test_the_pre_flip_default_is_todays_rung_and_todays_launch():
    """G10-02: before the flip Windows reports ``CMD x legacy_cmd`` and launches as it always did.

    This is the promise every stage before PR-7 rests on, and until this job existed it was
    only ever checked on a platform where the rung it names cannot occur.
    """
    spec = default_spec()
    assert spec.rung is Rung.legacy_cmd and spec.dialect is ShellDialect.CMD
    assert spec.policy_enabled is False
    assert spec.launcher is None and spec.pinned_env is None


def test_a_legacy_launch_still_runs_through_the_platform_shell(tmp_path):
    from agentao.capabilities.shell_spec import LegacyLaunch
    from agentao.capabilities.process import build_child_env

    request = LegacyLaunch(
        command="echo agentao-legacy-ok",
        cwd=AbsPath(str(tmp_path)),
        env=build_child_env(),
        spec_fingerprint=default_spec().fingerprint,
    )
    result = LocalShellExecutor().run(ShellRequest(launch=request, timeout=60))
    assert "agentao-legacy-ok" in result.stdout.decode("utf-8", errors="replace")


# ------------------------------------------------------------------ LAUNCH-03


@pytest.fixture
def cmd_spec():
    com_spec = os.environ.get("ComSpec")
    if not com_spec or not Path(com_spec).is_file():
        pytest.skip("no cmd.exe on this runner")
    return rung_spec(Rung.cmd, com_spec)


def test_the_cmd_rung_launches_and_its_body_arrives_intact(cmd_spec, tmp_path):
    """LAUNCH-03: ``/d /e:on /v:off /s /c "cd /d "<W>" || exit 98 & <body>"``.

    Eleven review rounds wrote that line and none of them ran it. What it has to survive is
    ``/s``'s outer-quote stripping with a quoted working directory *inside* the same string.
    """
    result = launch(cmd_spec, f'echo "{SENTINEL}"', tmp_path)
    assert SENTINEL in text(result), result


def test_a_working_directory_that_does_not_exist_exits_98_without_running_the_body(
    cmd_spec, tmp_path
):
    """LAUNCH-09a: ``cd … || exit 98 & <body>``, and the ``||`` is load-bearing.

    With ``&&`` the body's second command runs anyway, which is how a UNC working directory
    used to have cmd silently relocate the call to the system directory.
    """
    marker = tmp_path / "ran.txt"
    missing = tmp_path / "no-such-directory"
    result = launch(cmd_spec, f'echo x > "{marker}"', missing)
    assert result.returncode == 98, result
    assert not marker.exists()


def test_the_cmd_rung_pins_the_current_directory_out_of_the_search_path(cmd_spec, tmp_path):
    """ENV-04 / G18-01: cmd resolves a bare word against the current directory first."""
    result = launch(cmd_spec, "echo %NoDefaultCurrentDirectoryInExePath%", tmp_path)
    assert text(result).strip().endswith("1"), result


def test_the_child_environment_is_the_closed_set_and_not_the_inherited_one(
    cmd_spec, tmp_path, monkeypatch
):
    """ENV-06 / G18-08: a config-root key in the parent must not reach the child.

    ``%VAR%`` expands to itself when the variable is unset, which is how this reads back as a
    fact about the child's environment rather than about the test's own quoting.
    """
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "evil-config"))
    spec = rung_spec(Rung.cmd, os.environ["ComSpec"])
    result = launch(spec, "echo [%GIT_CONFIG_GLOBAL%]", tmp_path)
    assert "[%GIT_CONFIG_GLOBAL%]" in text(result), result


def test_a_launcher_path_containing_a_space_is_quoted_correctly(tmp_path):
    """G18-05: the path is quoted in the command line *and* passed as the application name."""
    com_spec = os.environ.get("ComSpec")
    if not com_spec:
        pytest.skip("no cmd.exe on this runner")
    copied = tmp_path / "a directory with spaces" / "cmd.exe"
    copied.parent.mkdir(parents=True)
    shutil.copy(com_spec, copied)
    result = launch(rung_spec(Rung.cmd, str(copied)), "echo spaced-ok", tmp_path)
    assert "spaced-ok" in text(result), result


# ------------------------------------------------------------------ LAUNCH-02 / LAUNCH-05


def every_powershell_on_this_runner():
    """All of them, because the two editions are two different measurements.

    Reporting only the first found makes a 5.1-specific fact invisible on a runner that also
    has 7.x, which is every GitHub Windows runner.
    """
    found = []
    for name, edition in (("pwsh", "Core"), ("powershell", "Desktop")):
        path = shutil.which(name)
        if path is None:
            continue
        probe = subprocess.run(
            [path, "-NoProfile", "-NonInteractive", "-Command",
             "$PSVersionTable.PSEdition + ' ' + $PSVersionTable.PSVersion.ToString()"
             " + ' ' + [System.IO.Path]::GetFullPath($PSHOME)"],
            capture_output=True, text=True, timeout=120,
        )
        if probe.returncode != 0:
            continue
        parts = probe.stdout.strip().split(" ", 2)
        if len(parts) == 3 and parts[0] == edition:
            found.append((path, parts[0], parts[1], parts[2]))
    return found


def powershell_on_this_runner():
    """The real interpreter and its own report of the two identity fields.

    Reading them by running it is fine *here* and not in the floor: this test measures the
    launch, and IMG-07's objection is to trusting a program's self-report as its identity.
    """
    for name, edition in (("pwsh", "Core"), ("powershell", "Desktop")):
        path = shutil.which(name)
        if path is None:
            continue
        probe = subprocess.run(
            [path, "-NoProfile", "-NonInteractive", "-Command",
             "$PSVersionTable.PSEdition + ' ' + $PSVersionTable.PSVersion.ToString()"
             " + ' ' + (Get-Item -LiteralPath $PSHOME).FullName"],
            capture_output=True, text=True, timeout=120,
        )
        if probe.returncode != 0:
            continue
        parts = probe.stdout.strip().split(" ", 2)
        if len(parts) == 3 and parts[0] == edition:
            return path, parts[0], parts[1], parts[2]
    return None


def test_the_powershell_rung_launches_with_its_guard_and_its_body_in_one_argument(tmp_path):
    """LAUNCH-02 / LAUNCH-05: the prelude and the body are one ``-Command`` argument.

    Splitting them hands PowerShell two arguments it rejoins by its own rules rather than by
    the floor's, and the guard has to pass before a byte of the body runs — so a launch that
    exits 97 here means the identity the spec was built from is not the one that started.
    """
    found = powershell_on_this_runner()
    if found is None:
        pytest.skip("neither pwsh nor powershell.exe answered on this runner")
    path, edition, version, pshome = found
    rung = Rung.pwsh if edition == "Core" else Rung.powershell
    spec = rung_spec(rung, path, edition=edition, version=version)
    from dataclasses import replace

    spec = replace(spec, launcher=replace(spec.launcher, pshome=AbsPath(pshome)))
    result = launch(spec, f"Write-Output '{SENTINEL}'", tmp_path)
    assert result.returncode == 0, result
    assert SENTINEL in text(result), result


def test_the_guard_refuses_when_the_recorded_identity_is_not_the_one_that_started(tmp_path):
    """LAUNCH-05: exit 97, and the working tree is untouched because the guard runs first."""
    found = powershell_on_this_runner()
    if found is None:
        pytest.skip("neither pwsh nor powershell.exe answered on this runner")
    path, edition, version, pshome = found
    rung = Rung.pwsh if edition == "Core" else Rung.powershell
    spec = rung_spec(rung, path, edition=edition, version="0.0.0-not-this-one")
    from dataclasses import replace

    spec = replace(spec, launcher=replace(spec.launcher, pshome=AbsPath(pshome)))
    marker = tmp_path / "ran.txt"
    result = launch(spec, f"Set-Content -LiteralPath '{marker}' -Value x", tmp_path)
    assert result.returncode == 97, result
    assert not marker.exists()


TABLE_CMDLETS = ("Set-Location", "Get-Item", "Write-Output", "Get-Date", "Get-ChildItem")
"""Five entries of the PowerShell trusted table, spread across both modules the prelude loads."""


def resolvable_under(path: str, script_prefix: str) -> str:
    """Which of ``TABLE_CMDLETS`` resolve after ``script_prefix`` has run."""
    probe = subprocess.run(
        [path, "-NoProfile", "-NonInteractive", "-Command",
         script_prefix + "; " + "; ".join(
             f"'{n}=' + [bool](Get-Command '{n}' -ErrorAction SilentlyContinue)"
             for n in TABLE_CMDLETS
         )],
        capture_output=True, text=True, timeout=120,
    )
    return probe.stdout + probe.stderr


def test_at_least_one_edition_needs_the_preludes_explicit_import():
    """The measurement that sent LAUNCH-05 back for a rewrite, stated as what it justifies.

    ``$PSModuleAutoLoadingPreference='None'`` is how ENV-05 adds depth on top of the pinned
    ``PSModulePath``. What it also does — measured on windows-latest for ``pwsh`` 7 — is make
    every cmdlet outside ``Microsoft.PowerShell.Core`` unavailable, including the two the
    prelude itself calls and most of the trusted table. A rung that can run nothing is not a
    hardened rung.

    It asserts the *consequence* and not the mechanism per edition, because only Core has been
    measured: Windows PowerShell 5.1 may well preload those modules, and asserting that it
    does not would be pinning a guess. What has to stay true is that some edition needs the
    import step, or that step is carrying nothing. The per-edition results are in the message.
    """
    measured = {
        edition: resolvable_under(path, "$PSModuleAutoLoadingPreference='None'")
        for path, edition, _version, _pshome in every_powershell_on_this_runner()
    }
    if not measured:
        pytest.skip("no PowerShell answered on this runner")
    assert any("Set-Location=False" in out for out in measured.values()), measured


@pytest.mark.parametrize("index", range(2))
def test_the_prelude_loads_what_the_table_needs_before_it_closes_the_door(index):
    """LAUNCH-05 step 2, measured: import first, then the door closes, and the table resolves.

    Driven directly rather than through the ladder, so a failure here is about PowerShell and
    not about the launch plumbing. Parametrised over both editions because they are two
    different measurements, and a runner that has both would otherwise report only the first.
    """
    found = every_powershell_on_this_runner()
    if index >= len(found):
        pytest.skip("this runner has fewer interpreters than that")
    path, edition, _version, _pshome = found[index]
    out = resolvable_under(
        path,
        "Import-Module -Name Microsoft.PowerShell.Management, Microsoft.PowerShell.Utility"
        " -ErrorAction Stop; $PSModuleAutoLoadingPreference='None'",
    )
    for name in TABLE_CMDLETS:
        assert f"{name}=True" in out, (edition, name, out)
