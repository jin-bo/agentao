"""An attested launch, actually launched — on whatever platform is running the tests.

PR-4 / PR-6 of the PowerShell ladder. ``LAUNCH-01d``, ``LAUNCH-04`` and ``LAUNCH-09a`` are
defined once each in ``docs/design/powershell-support-spec.zh.md`` §2.

Every rung this design serves is a Windows rung, so the command lines it specifies have been
written and reviewed many times and never executed. The Git Bash rung's spelling
(``--noprofile --norc -p -c "cd -P -- '<W>' || exit 98; <body>"``) is ordinary POSIX shell,
which means it can be measured *here* — and a plumbing error found on this machine is one
the Windows job does not have to find first.

**The spec is hand-built and could not be selected.** ``derive_rung`` maps a POSIX target to
``system_posix``, which is policy-off, so no ladder run produces this object. What is real is
everything after it: the assembled argv, the executor's re-check of the attested image, and
the child process.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from agentao.capabilities.shell import (
    LocalShellExecutor,
    ShellRequest,
    local_content_hash,
    local_filesystem_identity,
)
from agentao.capabilities.shell_spec import (
    AbsDir,
    AbsPath,
    HashPin,
    Platform,
    ResolvedImage,
    Rung,
    ShellBlock,
    ShellSpec,
    local_subject,
)
from agentao.permissions_hardline._analysis import decided_call
from agentao.permissions_hardline._trust import attested_spec, encode_workdir, request_for

from ._trust_fakes import FakeOracle, interpreter

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="the Windows rungs have their own launch matrix"
)

BASH = shutil.which("bash")


def posix_pinned():
    """ENV-06 (1) for a POSIX target: the three fields that platform must answer."""
    from agentao.capabilities.shell_spec import PinnedEnv

    return PinnedEnv(
        home=AbsDir(str(Path.home())),
        temp=AbsDir("/tmp"),
        tmp=AbsDir("/tmp"),
        tmpdir=AbsDir("/tmp"),
    )


def bash_spec(project_root: Path) -> ShellSpec:
    subject = local_subject()
    image = ResolvedImage(
        canonical_path=AbsPath(BASH),
        filesystem_identity=local_filesystem_identity(BASH),
        execution_subject=subject,
        content_identity=HashPin(path=AbsPath(BASH), sha256=local_content_hash(BASH)),
    )
    identity = interpreter(BASH, edition="", img=image)
    oracle = FakeOracle(
        target=Platform.POSIX, subject=subject, local=True, writable=set(),
        pinned=posix_pinned(), project_root=AbsPath(str(project_root)),
        path_entries=(),
        # The base environment carries exactly the keys ENV-06 exists to drop, so the child's
        # environment proves *removal* rather than absence. With an empty base every one of
        # them would be missing for the uninteresting reason.
        base_env={
            "XDG_CONFIG_HOME": "/tmp/evil-config",
            "GIT_CONFIG_GLOBAL": "/tmp/evil-gitconfig",
            "NODE_OPTIONS": "--require /tmp/evil.js",
            "LD_PRELOAD": "/tmp/evil.so",
            "SHELLOPTS": "xtrace",
            "BASH_ENV": "/tmp/evil-rc",
            "HOME": "/tmp/not-the-pinned-home",
            "TERM": "xterm-256color",
        },
        identities={BASH: identity},
        trusted_publishers={BASH},
    )
    spec = attested_spec(
        Rung.git_bash, image, identity, ShellBlock(), oracle, Platform.POSIX, subject, True
    )
    assert isinstance(spec, ShellSpec), spec
    return spec


def run(spec: ShellSpec, body: str, cwd: Path):
    record = decided_call(spec, body, AbsPath(str(cwd)), None)
    literal = encode_workdir(AbsPath(str(cwd)), spec.dialect)
    assert literal is not None
    request = request_for(
        spec, spec.launcher, body, literal, record.child_env or {},
        AbsPath(str(cwd)), record.attested_images,
    )
    assert request is not None, "the rung produced no launch request"
    return LocalShellExecutor().run(ShellRequest(launch=request, timeout=60))


@pytest.mark.skipif(BASH is None, reason="no bash on this machine")
def test_an_attested_launch_reaches_a_real_child_and_runs_the_body(tmp_path):
    """LAUNCH-04, end to end: the long options, ``-p``, and the body as one ``-c`` argument."""
    result = run(bash_spec(tmp_path), "echo agentao-attested-ok", tmp_path)
    assert result.returncode == 0, result.stderr
    assert b"agentao-attested-ok" in result.stdout


@pytest.mark.skipif(BASH is None, reason="no bash on this machine")
def test_a_working_directory_that_does_not_exist_exits_98_and_runs_no_body(tmp_path):
    """LAUNCH-09a: ``cd … || exit 98; <body>``. With ``&&`` the second command still runs."""
    marker = tmp_path / "ran"
    result = run(
        bash_spec(tmp_path), f"echo x > {marker}; echo y > {marker}",
        tmp_path / "no-such-directory",
    )
    assert result.returncode == 98, (result.returncode, result.stderr)
    assert not marker.exists()


@pytest.mark.skipif(BASH is None, reason="no bash on this machine")
def test_the_child_starts_in_the_launchers_directory_and_moves_to_the_call_directory(tmp_path):
    """LAUNCH-09: the process starts where the launcher lives, and the prelude relocates it.

    ``$PWD`` alone cannot tell those two apart — the prelude has already moved, so it reads
    the same whichever directory the process started in. ``$OLDPWD`` is what ``cd`` left
    behind, and it is one of the three start-state changes LAUNCH-07a permits this rung, so
    reading it is not reaching for an accident.
    """
    result = run(bash_spec(tmp_path), "echo $OLDPWD $PWD", tmp_path)
    assert result.returncode == 0, result.stderr
    started, moved = result.stdout.decode().split()
    assert Path(started) == Path(BASH).resolve().parent  # LAUNCH-09's start directory
    assert Path(moved) == Path(tmp_path).resolve()  # LAUNCH-09a's relocation


@pytest.mark.skipif(BASH is None, reason="no bash on this machine")
def test_the_child_environment_is_the_closed_set(tmp_path):
    """ENV-06: a config-root key set in this process must not reach the child."""
    spec = bash_spec(tmp_path)
    body = (
        "echo [${XDG_CONFIG_HOME-unset}] [${GIT_CONFIG_GLOBAL-unset}] "
        "[${NODE_OPTIONS-unset}] [${LD_PRELOAD-unset}] [${BASH_ENV-unset}] "
        "[${TERM-unset}] [${HOME}]"
    )
    result = run(spec, body, tmp_path)
    assert result.returncode == 0, result.stderr
    out = result.stdout.decode()
    assert out.count("[unset]") == 5, out  # the five config/trust roots are gone
    assert "[xterm-256color]" in out, out  # a registered descriptive key survives
    assert f"[{Path.home()}]" in out, out  # HOME is the pinned value, not the inherited one
    assert "not-the-pinned-home" not in out, out


@pytest.mark.skipif(BASH is None, reason="no bash on this machine")
def test_the_executor_refuses_a_launch_whose_image_was_swapped(tmp_path):
    """LAUNCH-01d: the executor re-checks the attested images immediately before spawning.

    The obligation is a MUST rather than a courtesy — between the decision and the spawn the
    file behind a path can change, and the executor is the last place that can still notice.
    """
    from agentao.capabilities.shell_spec import LaunchRefused, Sha256

    spec = bash_spec(tmp_path)
    record = decided_call(spec, "echo hi", AbsPath(str(tmp_path)), None)
    tampered = tuple(
        replace(image, content_identity=HashPin(path=image.canonical_path, sha256=Sha256("00")))
        for image in record.attested_images
    )
    request = request_for(
        spec, spec.launcher, "echo hi", str(tmp_path), record.child_env or {},
        AbsPath(str(tmp_path)), tampered,
    )
    with pytest.raises(LaunchRefused) as refusal:
        LocalShellExecutor().run(ShellRequest(launch=request, timeout=60))
    assert "launch-attest" in str(refusal.value)


@pytest.mark.skipif(BASH is None, reason="no bash on this machine")
def test_an_image_whose_identity_cannot_be_read_refuses_rather_than_matching(tmp_path, monkeypatch):
    """"A check that cannot be performed refuses" — including when *both* sides are unanswerable.

    Comparing two ``None``s finds them equal and reports a clean result while proving nothing.
    ``st_ino == 0`` is reachable on exactly the platform this ladder targets, so the entry
    could carry ``None`` too and the two would agree their way past the gate.
    """
    from agentao.capabilities import shell as shell_module
    from agentao.capabilities.shell_spec import LaunchRefused

    spec = bash_spec(tmp_path)
    record = decided_call(spec, "echo hi", AbsPath(str(tmp_path)), None)
    blind = tuple(
        replace(image, filesystem_identity=None) for image in record.attested_images
    )
    request = request_for(
        spec, spec.launcher, "echo hi", str(tmp_path), record.child_env or {},
        AbsPath(str(tmp_path)), blind,
    )
    monkeypatch.setattr(shell_module, "local_filesystem_identity", lambda path: None)
    with pytest.raises(LaunchRefused) as refusal:
        LocalShellExecutor().run(ShellRequest(launch=request, timeout=60))
    assert "unidentifiable" in str(refusal.value)
