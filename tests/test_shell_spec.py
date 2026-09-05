"""The shell spec, the launch request, and the two guards that come with them.

PR-1 of the PowerShell ladder (``docs/design/powershell-support-implementation.zh.md``).
Every test names the rule ID it holds; the rules are defined once each in
``docs/design/powershell-support-spec.zh.md`` §2.

The thing worth stating up front: at this stage **nothing changes what runs**. Every rung
that can be constructed is policy-off, and a policy-off rung launches through the same three
fields the request carried before. These tests are here so that stays true, and so the
shapes the later stages fill are already refusing what they must refuse.
"""

from __future__ import annotations

import dataclasses
import os
import sys
from types import MappingProxyType

import pytest

from agentao.capabilities.process import build_child_env
from agentao.capabilities.shell import (
    LocalShellExecutor,
    ShellRequest,
    local_content_hash,
    local_filesystem_identity,
    verify_attested_launch,
)
from agentao.capabilities.shell_spec import (
    AbsPath,
    Exhausted,
    HashPin,
    InterpreterIdentity,
    LauncherIdentity,
    LaunchRefused,
    LegacyLaunch,
    Platform,
    PosixLaunch,
    PinnedEnv,
    PublisherTrust,
    ResolvedImage,
    Rung,
    Sha256,
    ShellDialect,
    ShellSpec,
    SpecConstructionError,
    Subject,
    default_spec,
    dialect_of,
    fingerprint_of,
    fingerprint_projection,
    legacy_spec,
    validate,
)
from agentao.tools.base import ToolRegistry
from agentao.tools.shell import ShellTool

SUBJ = Subject("subject")


def posix_spec(**over) -> ShellSpec:
    return legacy_spec(ShellDialect.POSIX, Rung.system_posix, Platform.POSIX, SUBJ, **over)


def image(path: str, *, fs: str = "1:2", pin: object = None) -> ResolvedImage:
    return ResolvedImage(
        canonical_path=AbsPath(path),
        filesystem_identity=fs,  # type: ignore[arg-type]
        execution_subject=SUBJ,
        content_identity=pin,  # type: ignore[arg-type]
    )


# ------------------------------------------------------------------ SPEC-01/02/03


def test_the_dialect_rung_matrix_is_enumerated_not_inferred():
    """SPEC-02: only the pairs in the table are legal, and the failure names the pair."""
    assert dialect_of(Rung.legacy_cmd) is ShellDialect.CMD
    assert dialect_of(Rung.git_bash) is ShellDialect.POSIX
    with pytest.raises(SpecConstructionError) as exc:
        legacy_spec(ShellDialect.POSIX, Rung.legacy_cmd, Platform.POSIX, SUBJ)
    assert "posix x legacy_cmd" in str(exc.value)


def test_an_unknown_dialect_is_refused_before_any_rule_matches():
    """SPEC-01: UNKNOWN is what a host executor naming no dialect arrives with."""
    spec = dataclasses.replace(posix_spec(), dialect=ShellDialect.UNKNOWN)
    assert validate(spec) == "hardline:unknown-dialect-opaque"


def test_policy_enabled_cannot_disagree_with_the_rung():
    """SPEC-03, first cross-invariant: `policy_enabled` must equal `rung not in POLICY_OFF`.

    The launcher and pinned environment are supplied on purpose. Leaving them out makes the
    spec violate the *second* invariant as well, which returns the same reason string — so
    the test would have passed with this invariant deleted, holding nothing.
    """
    lying = dataclasses.replace(
        posix_spec(),
        policy_enabled=True,
        launcher=LauncherIdentity(image=image("/bin/sh"), launcher_hash=Sha256("h")),
        pinned_env=PinnedEnv(),
    )
    assert validate(lying) == "hardline:unknown-rung-opaque"


def test_policy_off_must_carry_no_launcher_and_no_pinned_environment():
    """SPEC-03, the other direction: a policy-off rung with a launcher is not a stricter rung.

    It is a rung that would be launched through the attested path while promising to be
    byte-identical to today, which is two different launches under one name.
    """
    launcher = LauncherIdentity(image=image("/bin/sh"), launcher_hash=Sha256("h"))
    assert validate(dataclasses.replace(posix_spec(), launcher=launcher)) == "hardline:unknown-rung-opaque"
    assert validate(dataclasses.replace(posix_spec(), pinned_env=PinnedEnv())) == "hardline:unknown-rung-opaque"


def test_a_powershell_rung_needs_an_interpreter_identity_not_a_bare_launcher():
    """IMG-07: the edition decides the rung, so a launcher that cannot report one is wrong."""
    plain = LauncherIdentity(image=image("/x/pwsh"), launcher_hash=Sha256("h"))
    spec = dataclasses.replace(
        posix_spec(),
        dialect=ShellDialect.POWERSHELL,
        rung=Rung.pwsh,
        policy_enabled=True,
        launcher=plain,
        pinned_env=PinnedEnv(),
    )
    assert validate(spec) == "hardline:unknown-rung-opaque"
    ok = dataclasses.replace(
        spec, launcher=InterpreterIdentity(image=image("/x/pwsh"), launcher_hash=Sha256("h"), edition="Core")
    )
    assert validate(ok) is None


def test_an_explicit_shell_is_refused_once_policy_is_on():
    """CFG-02c: with policy on the launcher decides, so a second answer is a contradiction."""
    spec = dataclasses.replace(
        posix_spec(),
        rung=Rung.git_bash,
        policy_enabled=True,
        launcher=LauncherIdentity(image=image("/g/bash"), launcher_hash=Sha256("h")),
        pinned_env=PinnedEnv(),
        explicit_shell=AbsPath("/bin/zsh"),
    )
    assert validate(spec) == "hardline:unknown-rung-opaque"


# ------------------------------------------------------------------ SPEC-07/08


def test_a_spec_cannot_be_assigned_to_after_construction():
    """SPEC-07: re-resolution builds a new object; it never edits the one a call is holding."""
    with pytest.raises(dataclasses.FrozenInstanceError):
        posix_spec().rung = Rung.cmd  # type: ignore[misc]


def test_two_specs_differing_only_in_the_allowlist_have_different_fingerprints():
    """IMG-03a: the allowlist in force is part of what was decided, so it is in the projection.

    Leaving it out would let two configurations that differ by one pin produce one
    fingerprint, and the fingerprint is what notices that the configuration moved between
    the decision and the launch.
    """
    a = posix_spec()
    b = dataclasses.replace(a, allowlist=(PublisherTrust(signer="Acme"),))
    b = dataclasses.replace(b, fingerprint=fingerprint_of(fingerprint_projection(b)))
    assert a.fingerprint != b.fingerprint


def test_two_specs_differing_only_in_the_named_interpreter_have_different_fingerprints():
    """CFG-02c: /bin/bash and /bin/zsh must not produce one spec and one fingerprint."""
    bash = posix_spec(explicit_shell=AbsPath("/bin/bash"))
    zsh = posix_spec(explicit_shell=AbsPath("/bin/zsh"))
    assert bash.fingerprint != zsh.fingerprint


def test_an_absent_field_and_a_field_holding_its_name_do_not_collide():
    """The encoding is type-tagged: `None` and the string "None" are different projections."""
    absent = posix_spec()
    present = posix_spec(explicit_shell=AbsPath("None"))
    assert absent.fingerprint != present.fingerprint


def test_the_fingerprint_covers_the_spec_that_is_returned():
    """SPEC-08: stamped from the same object, so the hash cannot describe a different draft."""
    spec = posix_spec()
    assert spec.fingerprint == fingerprint_of(fingerprint_projection(spec))


# ------------------------------------------------------------------ LADDER-05


def test_the_default_rung_is_policy_off_on_both_platforms():
    """LADDER-05: until the flip, Windows reports legacy_cmd and everywhere else system_posix.

    Neither is a rung of the ladder. Without them the ladder is empty on a POSIX host and on
    a pre-flip Windows host, and an empty ladder denies every shell call.
    """
    win = default_spec(windows=True)
    assert (win.dialect, win.rung, win.policy_enabled) == (ShellDialect.CMD, Rung.legacy_cmd, False)
    other = default_spec(windows=False)
    assert (other.dialect, other.rung, other.policy_enabled) == (
        ShellDialect.POSIX,
        Rung.system_posix,
        False,
    )


def test_the_local_executor_declares_locality_and_a_stable_spec_object():
    """SPEC-04a: the executor declares it. SPEC-07b: the same object until re-resolution."""
    ex = LocalShellExecutor()
    assert ex.shell_spec.filesystem_is_local is True
    assert ex.shell_spec is ex.shell_spec


def test_a_tool_reads_the_executors_spec_not_its_own_guess():
    """TOOL-04: the spec comes from the executor, which is the party that knows."""

    class RemoteExecutor:
        shell_spec = Exhausted("every rung refused")

        def run(self, request):  # pragma: no cover - never reached
            raise AssertionError

        def run_background(self, request):  # pragma: no cover - never reached
            raise AssertionError

    tool = ShellTool()
    tool.shell = RemoteExecutor()
    assert tool.shell_spec == Exhausted("every rung refused")


def test_an_executor_predating_this_member_still_gets_todays_default():
    """This stage is invisible to users, so a host that changed nothing must keep working."""

    class OldExecutor:
        def run(self, request):  # pragma: no cover - never reached
            raise AssertionError

        def run_background(self, request):  # pragma: no cover - never reached
            raise AssertionError

    tool = ShellTool()
    tool.shell = OldExecutor()
    spec = tool.shell_spec
    assert isinstance(spec, ShellSpec) and spec.policy_enabled is False
    # ...and locality is not claimed for an executor that never declared it (SPEC-04a).
    assert spec.filesystem_is_local is False


# ------------------------------------------------------------------ LAUNCH-01


def test_a_legacy_launch_carries_none_of_the_attested_fields():
    """LAUNCH-01c: adding a variant to a union is not the same as splitting the fields.

    Evidence it never produced and an obligation it is exempt from would both be lies.
    """
    names = {f.name for f in dataclasses.fields(LegacyLaunch)}
    assert names == {"command", "cwd", "env", "spec_fingerprint"}
    assert not names & {"attested_images", "execution_subject", "workdir"}


def _attested(tmp_path, *, images, argv=()):
    exe = tmp_path / "interp"
    exe.write_bytes(b"#!/bin/sh\nexit 0\n")
    return exe, PosixLaunch(
        executable=AbsPath(str(exe)),
        argv=(str(exe),) + tuple(argv),
        cwd=AbsPath(str(tmp_path)),
        workdir=AbsPath(str(tmp_path)),
        env=MappingProxyType({}),
        execution_subject=SUBJ,
        attested_images=images,
        spec_fingerprint=Sha256("fp"),
    )


def test_a_target_with_no_entry_in_the_evidence_is_refused(tmp_path):
    """LAUNCH-01d: no entry is a refusal, not a pass. Absence of evidence is not evidence."""
    _, launch = _attested(tmp_path, images=())
    deny = verify_attested_launch(launch)
    assert deny is not None and "no-entry" in deny.reason


def test_a_target_whose_file_was_swapped_since_the_decision_is_refused(tmp_path):
    """LAUNCH-01d: the executor is the last place that can still notice the swap."""
    exe, launch = _attested(
        tmp_path, images=(image(str(tmp_path / "interp"), fs="0:0", pin=HashPin(path=AbsPath(str(tmp_path / "interp")), sha256=Sha256("x"))),)
    )
    deny = verify_attested_launch(launch)
    assert deny is not None and "filesystem-identity" in deny.reason


def test_a_target_whose_bytes_changed_is_refused(tmp_path):
    """LAUNCH-01d: same path, same inode, different content — a rewrite in place."""
    exe = tmp_path / "interp"
    exe.write_bytes(b"#!/bin/sh\nexit 0\n")
    pin = HashPin(path=AbsPath(str(exe)), sha256=Sha256("not-the-hash"))
    _, launch = _attested(
        tmp_path, images=(image(str(exe), fs=local_filesystem_identity(str(exe)), pin=pin),)
    )
    deny = verify_attested_launch(launch)
    assert deny is not None and "content-identity" in deny.reason


def test_an_entry_with_no_content_identity_is_refused(tmp_path):
    """LAUNCH-01d: an image nothing binds to its bytes has not been attested, only named."""
    exe = tmp_path / "interp"
    exe.write_bytes(b"#!/bin/sh\nexit 0\n")
    _, launch = _attested(tmp_path, images=(image(str(exe), fs=local_filesystem_identity(str(exe))),))
    deny = verify_attested_launch(launch)
    assert deny is not None and "no-content-identity" in deny.reason


def test_a_fully_attested_target_passes(tmp_path):
    """The check has to be passable, or it is only a disguised refusal of the whole path."""
    exe = tmp_path / "interp"
    exe.write_bytes(b"#!/bin/sh\nexit 0\n")
    pin = HashPin(path=AbsPath(str(exe)), sha256=local_content_hash(str(exe)))
    _, launch = _attested(
        tmp_path, images=(image(str(exe), fs=local_filesystem_identity(str(exe)), pin=pin),)
    )
    assert verify_attested_launch(launch) is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX argv shape")
@pytest.mark.parametrize("background", [False, True])
def test_both_delivery_faces_refuse_the_same_unattested_launch(tmp_path, background):
    """LAUNCH-01e: `is_background` picks which method delivers, and nothing else.

    It used to pick a second spawn path with its own ``shell=True`` and its own environment,
    which meant a property proved about one face said nothing at all about the other.
    """
    _, launch = _attested(tmp_path, images=())
    request = ShellRequest(launch=launch)
    ex = LocalShellExecutor()
    with pytest.raises(LaunchRefused) as exc:
        ex.run_background(request) if background else ex.run(request)
    assert "launch-attest" in exc.value.deny.reason


def test_a_legacy_launch_runs_without_any_attestation_check(tmp_path):
    """LAUNCH-01c: the obligation binds policy-enabled rungs only.

    Applying it here would refuse every call on both of today's rungs, which is the exact
    opposite of the promise that this stage is byte-for-byte what shipped before.
    """
    # ``build_child_env()``, not ``dict(os.environ)``. The executor no longer computes the
    # environment — the launch carries it — so this is the shape a host reads and copies, and
    # the raw process environment carries agentao's own provider credentials into the child.
    launch = LegacyLaunch(
        command="exit 7",
        cwd=AbsPath(str(tmp_path)),
        env=MappingProxyType(build_child_env()),
        spec_fingerprint=Sha256("fp"),
    )
    assert LocalShellExecutor().run(ShellRequest(launch=launch, timeout=30)).returncode == 7


# ------------------------------------------------------------------ TOOL-01


def test_a_replacement_shell_tool_without_a_spec_is_refused_by_name():
    """TOOL-01: the floor gates on this name, so the name is where the guard belongs."""

    class BareTool:
        name = "run_shell_command"

    with pytest.raises(ValueError, match="TOOL-01"):
        ToolRegistry().register(BareTool())  # type: ignore[arg-type]


def test_the_guard_does_not_evaluate_the_provider_while_registering():
    """A provider that walks a ladder can be slow, and one that raises is not "absent"."""

    class Exploding:
        name = "run_shell_command"

        @property
        def shell_spec(self):
            raise RuntimeError("resolution failed")

    ToolRegistry().register(Exploding())  # type: ignore[arg-type]


def test_the_real_shell_tool_satisfies_its_own_guard():
    """The rule has to admit the thing it was written for, or it is only a wall."""
    ToolRegistry().register(ShellTool())


# ------------------------------------------------------------------ SPEC-08, TOOL-04


def decided(body: str, cwd: str, verdict=None):
    from agentao.capabilities.shell_spec import DecidedCall, PASS

    return DecidedCall(
        spec=default_spec(local=True),
        body=body,
        cwd=AbsPath(cwd),
        verdict=verdict or PASS,
    )


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX exit code in the probe")
def test_the_launch_runs_the_body_that_was_judged_not_the_argument():
    """SPEC-08b: re-reading `command` at the launch would be a second source for the text.

    The two disagree here on purpose. A tool that prefers its own argument is a channel that
    gets one command approved and runs another under the same verdict.
    """
    out = ShellTool().execute(
        command="exit 3", working_directory=".", timeout=30, _decided=decided("exit 7", ".")
    )
    # Both halves have to be load-bearing. ``"7" in out`` is satisfied by a stray digit in an
    # error string, which would let an implementation that ran *neither* body pass.
    assert "Exit code: 7" in out
    assert "Exit code: 3" not in out


def test_a_record_carrying_a_deny_refuses_at_the_launch():
    """SPEC-08b: a record being present is not the same as this call having been allowed."""
    from agentao.capabilities.shell_spec import Deny

    out = ShellTool().execute(
        command="echo hi",
        working_directory=".",
        timeout=30,
        _decided=decided("echo hi", ".", verdict=Deny("permission:denied")),
    )
    assert out.startswith("Error: permission:denied")


def test_an_exhausted_provider_denies_before_any_pattern_is_matched():
    """LADDER-03: the reason names why no rung was established, not what the text contained."""
    from agentao.permissions_hardline import hardline_check

    reason = hardline_check(
        "run_shell_command", {"command": "echo hi"}, shell_spec=Exhausted("every rung refused")
    )
    assert reason == "hardline:no-trusted-rung-opaque:every rung refused"


def test_an_illegal_spec_reaching_the_floor_is_refused_there_too():
    """SPEC-02: construction checks the specs agentao builds; this catches the others."""
    from agentao.permissions_hardline import hardline_check

    smuggled = dataclasses.replace(posix_spec(), rung=Rung.cmd)
    assert hardline_check("run_shell_command", {"command": "echo hi"}, shell_spec=smuggled) == (
        "hardline:unknown-rung-opaque"
    )


def test_a_legal_spec_does_not_change_what_the_floor_says_about_the_text():
    """The floor still reads the body — naming the dialect must not become a bypass."""
    from agentao.permissions_hardline import hardline_check

    args = {"command": "rm -rf /"}
    assert hardline_check("run_shell_command", args) is not None
    assert hardline_check("run_shell_command", args, shell_spec=posix_spec()) is not None


def test_a_hook_rewrite_moves_the_record_with_the_arguments():
    """SPEC-08a: replaced whole. Swapping the arguments alone would launch the original.

    That is the one outcome the rewrite path names as the thing it must never do: a hook
    that replaces a command has already said the original must not run.
    """
    from agentao.runtime.tool_planning import ToolCallDecision
    from agentao.runtime.tool_runner import ToolRunner

    tool = ShellTool()
    plan = type("P", (), {})()
    plan.tool = tool
    plan.function_name = "run_shell_command"
    plan.function_args = {"command": "rm -rf /tmp/x", "working_directory": "."}
    plan.decision = ToolCallDecision.ALLOW
    plan.permission_detail = None
    plan.decided = decided("rm -rf /tmp/x", ".")

    runner = ToolRunner.__new__(ToolRunner)
    runner._planner = _PlannerStub()
    runner.readonly_mode = False
    runner._logger = _LoggerStub()
    ToolRunner._apply_updated_input(runner, plan, {"command": "echo safe", "working_directory": "."})

    assert plan.function_args["command"] == "echo safe"
    assert plan.decided.body == "echo safe"


class _PlannerStub:
    def _decide(self, tool, fn, args, readonly, shell_spec=None, decided=None):
        from agentao.runtime.tool_planning import ToolCallDecision

        return ToolCallDecision.ALLOW, None


class _LoggerStub:
    def warning(self, *a, **k):
        pass


# ------------------------------------------------------------------ PR-5


@pytest.mark.parametrize(
    "dialect,present,absent",
    [
        ("posix", "/tmp/out.log", "%TEMP%"),
        ("cmd", "%TEMP%", "/tmp/out.log"),
        ("powershell", "Select-String", "/tmp/out.log"),
    ],
)
def test_the_guidelines_speak_the_dialect_that_will_run_them(dialect, present, absent):
    """PR-5: advice in the wrong shell's syntax teaches a command that fails.

    The model then spends its next turn recovering from what this prompt told it, which is
    worse than saying nothing shell-specific at all.
    """
    from agentao.prompts.sections import build_operational_guidelines

    text = build_operational_guidelines(dialect=dialect)
    assert present in text
    assert absent not in text


def test_an_unknown_dialect_falls_back_to_what_the_text_said_before():
    """A prompt is advice. It never fails a turn, and it never renders an empty idiom."""
    from agentao.prompts.sections import build_operational_guidelines

    assert build_operational_guidelines(dialect="klingon") == build_operational_guidelines(
        dialect="posix"
    )


# ------------------------------------------------- the guards these fixes restored


def test_an_executor_predating_the_spec_member_still_satisfies_the_protocol():
    """The promise is "hosts written before this member existed keep working unchanged".

    ``ShellExecutor`` is ``@runtime_checkable``, and a non-method member is not a smaller
    version of that promise — it makes ``issubclass()`` raise ``TypeError`` for everyone and
    flips ``isinstance()`` to ``False`` for every executor that predates it. The declaration
    is an optional companion protocol (``ShellSpecProvider``) for exactly that reason.
    """
    from agentao.capabilities.shell import ShellExecutor

    class OldExecutor:
        def run(self, request):  # pragma: no cover - never reached
            raise AssertionError

        def run_background(self, request):  # pragma: no cover - never reached
            raise AssertionError

    assert issubclass(OldExecutor, ShellExecutor)
    assert isinstance(OldExecutor(), ShellExecutor)
    assert isinstance(LocalShellExecutor(), ShellExecutor)


def test_a_launch_refusal_is_not_reported_as_a_failed_start(tmp_path):
    """LAUNCH-01b: a launch-stage denial is "never a tool error the model would retry".

    Both faces used to catch it in their broad ``except Exception`` and hand back
    ``Error starting command: …`` — the shape of a transient failure, which invites exactly
    the retry the rule forbids.
    """
    _, launch = _attested(tmp_path, images=())

    class Attesting(LocalShellExecutor):
        def run(self, request):
            return LocalShellExecutor.run(self, ShellRequest(launch=launch))

        def run_background(self, request):
            return LocalShellExecutor.run_background(self, ShellRequest(launch=launch))

    tool = ShellTool()
    tool.shell = Attesting()
    for out in (tool._run_foreground("echo hi", tmp_path, 5), tool._run_background("echo hi", tmp_path)):
        assert out.startswith("Error: hardline:launch-attest:")
        assert "starting" not in out


def test_a_publisher_trust_entry_is_refused_while_nothing_can_verify_a_signer(tmp_path):
    """LAUNCH-01d: "a check that cannot be performed" refuses.

    Publisher trust attests the signer, and nothing in tree verifies one — so accepting the
    entry is a pass with no check behind it, for an image whose bytes may have changed.
    """
    exe = tmp_path / "interp"
    exe.write_bytes(b"#!/bin/sh\nexit 0\n")
    _, launch = _attested(
        tmp_path,
        images=(image(str(exe), fs=local_filesystem_identity(str(exe)), pin=PublisherTrust(signer="Acme")),),
    )
    deny = verify_attested_launch(launch)
    assert deny is not None and "unverifiable-content-identity" in deny.reason


def test_a_hash_pin_minted_for_another_path_does_not_answer_for_this_one(tmp_path):
    """IMG-03: a pin names the path it was taken for; comparing bytes across paths proves nothing."""
    exe = tmp_path / "interp"
    exe.write_bytes(b"#!/bin/sh\nexit 0\n")
    stray = HashPin(path=AbsPath(str(tmp_path / "somewhere-else")), sha256=local_content_hash(str(exe)))
    _, launch = _attested(
        tmp_path, images=(image(str(exe), fs=local_filesystem_identity(str(exe)), pin=stray),)
    )
    deny = verify_attested_launch(launch)
    assert deny is not None and "content-identity" in deny.reason


def test_an_unidentifiable_file_is_not_given_an_identity(monkeypatch, tmp_path):
    """SPEC-04: ``st_ino`` identifies a file only when non-zero, and Windows reports zero.

    Passing it through hands every such file the identity ``<dev>:0``, so a swap between two
    of them compares equal — a check reporting clean while proving nothing.
    """
    exe = tmp_path / "interp"
    exe.write_bytes(b"x")
    real = os.stat

    class _Zero:
        st_dev = 7
        st_ino = 0

    monkeypatch.setattr(os, "stat", lambda p, *a, **k: _Zero() if str(p) == str(exe) else real(p, *a, **k))
    assert local_filesystem_identity(str(exe)) is None


def test_the_launch_carries_the_spec_the_decision_froze_not_a_second_read():
    """TOOL-04 / SPEC-08: one spec governs the decision *and* the launch.

    Re-reading the provider inside the launch builder is the same second source SPEC-08b
    closes for the body, one field over: the fingerprint would describe whatever
    re-resolution had swapped in, while the verdict was computed against the frozen spec.
    """
    frozen = posix_spec(explicit_shell=AbsPath("/bin/decided"))
    seen = []

    class Recording:
        shell_spec = posix_spec(explicit_shell=AbsPath("/bin/current"))

        def run(self, request):
            seen.append(request.launch)
            from agentao.capabilities.shell import ShellResult

            return ShellResult(returncode=0, stdout=b"", stderr=b"", timed_out=False)

        def run_background(self, request):  # pragma: no cover - never reached
            raise AssertionError

    tool = ShellTool()
    tool.shell = Recording()
    tool.execute(command="echo hi", working_directory=".", timeout=5,
                 _decided=dataclasses.replace(decided("echo hi", "."), spec=frozen))
    assert seen[0].spec_fingerprint == frozen.fingerprint
    assert seen[0].spec_fingerprint != Recording.shell_spec.fingerprint


def test_a_raising_provider_is_a_failure_not_an_absent_declaration():
    """``getattr(x, "shell_spec", None)`` swallows an ``AttributeError`` raised *inside* the
    property, which reads as "declares nothing" and quietly reports the platform default for
    an executor whose resolution actually failed. The planner turns a raise into ``Exhausted``.
    """
    from agentao.runtime.tool_planning import _shell_spec_of

    class Broken:
        @property
        def shell_spec(self):
            raise AttributeError("resolution failed")

        def run(self, request):  # pragma: no cover - never reached
            raise AssertionError

        def run_background(self, request):  # pragma: no cover - never reached
            raise AssertionError

    tool = ShellTool()
    tool.shell = Broken()
    with pytest.raises(AttributeError):
        tool.shell_spec
    assert isinstance(_shell_spec_of(tool), Exhausted)


def test_the_fallback_spec_is_one_object_per_executor():
    """SPEC-07b: a call holds one spec object until re-resolution swaps it.

    Minting a fresh one on every read — and paying a ``geteuid`` plus a sha256 each time —
    is not that; the local executor is already tested for the same property.
    """

    class OldExecutor:
        def run(self, request):  # pragma: no cover - never reached
            raise AssertionError

        def run_background(self, request):  # pragma: no cover - never reached
            raise AssertionError

    tool = ShellTool()
    tool.shell = OldExecutor()
    first = tool.shell_spec
    assert first is tool.shell_spec
    tool.shell = OldExecutor()  # a different executor re-answers rather than reusing the cache
    assert tool.shell_spec is not first


def test_flipping_the_ladder_constant_is_loud_rather_than_silent(monkeypatch):
    """LADDER-05: a constant that changes nothing when flipped is worse than no constant.

    Someone sets it to True to try the post-flip path, gets the pre-flip answer, and
    concludes the flip already works. Selecting a rung is the ladder's job, and the ladder
    does not exist yet, so saying so is the only honest answer this function has.
    """
    import agentao.capabilities.shell_spec as ss

    monkeypatch.setattr(ss, "LADDER_FLIPPED", True)
    with pytest.raises(NotImplementedError, match="ladder"):
        ss.default_spec(windows=True)
