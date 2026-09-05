"""The closed runnable set: names, images, effects and re-entry, one body at a time.

PR-4 of the PowerShell ladder. ``IMG-02``, ``EFF-01``..``EFF-08``, ``WRAP-01``..``WRAP-06``,
``NAME-01``..``NAME-03`` and ``LAUNCH-08`` are defined once each in
``docs/design/powershell-support-spec.zh.md`` §2.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentao.capabilities.shell_spec import (
    AbsPath,
    Deny,
    FsId,
    HashPin,
    Pass,
    Platform,
    ResolvedImage,
    Rung,
    Sha256,
    ShellBlock,
    ShellDialect,
    ShellSpec,
    legacy_spec,
)
from agentao.permissions_hardline._analysis import (
    CommandKind,
    Opaque,
    analyse,
    analyse_body,
    decided_call,
    merge_images,
)
from agentao.permissions_hardline._trust import (
    MEASURED_COMMAND_TABLES,
    MeasuredEntry,
    attested_spec,
)

from ._trust_fakes import SUBJECT, FakeOracle, image, interpreter

PWSH = "C:\\Program Files\\PowerShell\\7\\pwsh.exe"
CMD = "C:\\Windows\\System32\\cmd.exe"
BASH = "C:\\Program Files\\Git\\bin\\bash.exe"
TOOLS = AbsPath("C:\\Program Files\\Git\\cmd")
GIT = "C:\\Program Files\\Git\\cmd\\git.COM"


def oracle(**overrides) -> FakeOracle:
    defaults = dict(
        discovered={Rung.pwsh: image(PWSH)},
        identities={
            PWSH: interpreter(PWSH),
            CMD: interpreter(CMD, edition=""),
            BASH: interpreter(BASH, edition=""),
        },
        trusted_publishers={PWSH, CMD, BASH},
        pshome=AbsPath("C:\\Program Files\\PowerShell\\7"),
        path_entries=(TOOLS,),
        base_env={},
    )
    defaults.update(overrides)
    return FakeOracle(**defaults)


def spec_for(rung: Rung, launcher: str, source=None, **block) -> ShellSpec:
    built = attested_spec(
        rung, image(launcher), interpreter(launcher, edition="Core" if rung is Rung.pwsh else ""),
        ShellBlock(**block), source or oracle(), Platform.WINDOWS, SUBJECT, True,
    )
    assert isinstance(built, ShellSpec), built
    return built


@pytest.fixture
def measured(monkeypatch):
    """A PowerShell command table, so NAME-02 can resolve at all (IMG-07)."""
    monkeypatch.setitem(MEASURED_COMMAND_TABLES, ("Core", "7.4.6"), (
        MeasuredEntry(name="Get-Date", kind="cmdlet"),
        MeasuredEntry(name="Get-Location", kind="cmdlet"),
        MeasuredEntry(name="Invoke-Expression", kind="cmdlet"),
        MeasuredEntry(name="iex", kind="alias", alias_target="Invoke-Expression"),
        MeasuredEntry(name="Set-Alias", kind="cmdlet"),
        MeasuredEntry(name="Write-Output", kind="cmdlet"),
    ))


# ------------------------------------------------------------------ analyse


def test_each_dialect_splits_its_own_body_and_keeps_its_own_reason():
    """The grammar gate owns the refusal; the splitter only runs on text it accepted."""
    assert len(analyse(ShellDialect.CMD, "dir & git status")) == 2
    assert len(analyse(ShellDialect.POSIX, "pwd ; git status")) == 2
    assert len(analyse(ShellDialect.POWERSHELL, "Get-Date; Get-Location")) == 2
    refused = analyse(ShellDialect.POSIX, "echo $(rm -rf /)")
    assert isinstance(refused, Opaque) and "BASH-01" in refused.reason


def test_a_wrapper_and_a_spawner_are_classified_before_anything_looks_them_up():
    """WRAP-01 / WRAP-05: what a command *is* decides the question asked about it."""
    # cmd and bash classify wrappers inside their own gate, so `analyse` never sees one:
    # their gates are text scanners, and a wrapper body is not readable text in their
    # grammar. PowerShell's gate is a lowering pipeline that already produces argv, so
    # classification happens after it — which is what lets `analyse_body` re-enter with the
    # spec and the search path the inner body has to be judged against.
    assert isinstance(analyse(ShellDialect.CMD, "start notepad"), Opaque)
    assert isinstance(analyse(ShellDialect.POSIX, "bash -c 'echo hi'"), Opaque)
    nested = analyse(ShellDialect.POWERSHELL, "pwsh -Command Get-Date")
    assert nested[0].kind is CommandKind.interpreter_launch
    assert nested[0].inner_body == "Get-Date"


# ------------------------------------------------------------------ IMG-02


def test_a_trusted_toolchain_command_passes_on_every_policy_on_rung(measured):
    """G04-34: an in-process entry binds the rung's launcher image, an external one its own.

    A ``PATH`` search for ``dir`` either always fails or is quietly skipped, and neither is an
    answer about whether ``dir`` may run.
    """
    for rung, launcher, body in (
        (Rung.cmd, CMD, "dir & git status"),
        (Rung.git_bash, BASH, "pwd ; git status"),
        (Rung.pwsh, PWSH, "Get-Date; git status"),
    ):
        result = analyse_body(spec_for(rung, launcher), body, (TOOLS,))
        assert isinstance(result.verdict, Pass), (rung, result.verdict)
        paths = [str(i.canonical_path) for i in result.attested]
        # `.COM` before `.EXE` on the two Windows dialects (ENV-02 pins PATHEXT); the POSIX
        # layer searches the exact filename and has no extension rules at all (NAME-03).
        assert any(x.startswith("C:\\Program Files\\Git\\cmd\\git") for x in paths), paths


def test_an_unclassified_program_in_a_trusted_directory_is_opaque():
    """G04-03 / IMG-02: an image with no name is a program nobody has classified."""
    result = analyse_body(spec_for(Rung.cmd, CMD), "unknown-tool --go", (TOOLS,))
    assert isinstance(result.verdict, Deny) and "name" in result.verdict.reason


def test_a_classified_name_whose_image_is_untrusted_is_opaque():
    """G04-02 / IMG-02: a ``git.exe`` copied into the work tree has the name and not the image."""
    planted = AbsPath("C:\\repo\\bin")
    source = oracle(writable={"C:\\repo\\bin"}, path_entries=(planted,))
    result = analyse_body(spec_for(Rung.cmd, CMD, source=source), "git status", (planted,))
    assert isinstance(result.verdict, Deny) and "image" in result.verdict.reason


def test_a_command_word_that_resolves_nowhere_is_opaque():
    """G07-05: not found is unresolvable, not harmless."""
    source = oracle(resolvable={CMD})  # the launcher still resolves; nothing else does
    result = analyse_body(spec_for(Rung.cmd, CMD, source=source), "git status", (TOOLS,))
    assert isinstance(result.verdict, Deny) and "not-found" in result.verdict.reason


def test_the_launcher_is_attested_whatever_the_body_contains():
    """G01-09 / LAUNCH-01: it is the direct target being started, body or no body.

    An empty body and a comment-only body launch the same interpreter, and an executor that
    must re-check every named image would refuse a launch whose evidence omitted it.
    """
    spec = spec_for(Rung.cmd, CMD)
    for body in ("", "rem nothing here", "git status"):
        record = decided_call(spec, body, AbsPath("C:\\work"), None)
        assert AbsPath(CMD) in [i.canonical_path for i in record.attested_images], body


# ------------------------------------------------------------------ EFF


def test_a_rebinding_command_refuses_everything_after_it(measured):
    """EFF-02: once a name can mean something else, no later word resolves by table."""
    result = analyse_body(
        spec_for(Rung.pwsh, PWSH), "Set-Alias git evil; git status", (TOOLS,)
    )
    assert isinstance(result.verdict, Deny) and "rebinds_after" in result.verdict.reason


def test_an_evaluator_re_enters_only_on_a_literal_string(measured):
    """EFF-03 / WRAP-04 4a: a file target or a pipe is not text this floor can see."""
    inert = analyse_body(spec_for(Rung.pwsh, PWSH), "iex 'Get-Date'", (TOOLS,))
    assert isinstance(inert.verdict, Pass)
    dangerous = analyse_body(spec_for(Rung.pwsh, PWSH), "iex 'Format-Volume'", (TOOLS,))
    assert isinstance(dangerous.verdict, Deny)


def test_an_evaluators_own_exit_state_is_merged_only_when_it_binds_the_caller(measured):
    """EFF-07's ``+``: ``iex`` executes into the *caller's* scope, so what it rebinds sticks.

    Without the merge, ``iex 'Set-Alias git evil'; git status`` would pass — the rebinding
    happens inside the string, and the outer body would never hear about it.
    """
    result = analyse_body(
        spec_for(Rung.pwsh, PWSH), "iex 'Set-Alias git evil'; git status", (TOOLS,)
    )
    assert isinstance(result.verdict, Deny) and "rebinds_after" in result.verdict.reason


def test_an_inert_evaluation_does_not_taint_what_follows(measured):
    """The other half of the same rule: ``rebinds_caller`` carries the inner exit state, and
    an inert inner body has nothing to carry."""
    result = analyse_body(
        spec_for(Rung.pwsh, PWSH), "iex 'Get-Date'; Get-Location", (TOOLS,)
    )
    assert isinstance(result.verdict, Pass)


def test_re_entry_is_bounded():
    """EFF-03: the body is untrusted input, and a stack overflow is not a refusal."""
    spec = spec_for(Rung.git_bash, BASH)
    deep = analyse_body(spec, "git status", (TOOLS,), depth=99)
    assert isinstance(deep.verdict, Deny) and "reenter-depth" in deep.verdict.reason


# ------------------------------------------------------------------ WRAP


def test_a_nested_interpreter_is_refused_by_the_inner_bodys_own_reason():
    """WRAP-06: re-entry buys a better reason, never an approval."""
    spec = spec_for(Rung.git_bash, BASH)
    harmless = analyse_body(spec, "bash -c 'git status'", (TOOLS,))
    assert isinstance(harmless.verdict, Deny) and "WRAP-01" in harmless.verdict.reason


def test_a_spawner_hands_the_work_somewhere_this_floor_never_sees():
    spec = spec_for(Rung.pwsh, PWSH)
    result = analyse_body(spec, "Start-Process notepad", (TOOLS,))
    assert isinstance(result.verdict, Deny) and "WRAP-05" in result.verdict.reason


# ------------------------------------------------------------------ LAUNCH-01d


def test_two_attestations_of_one_path_that_disagree_refuse_the_call():
    """G24-21: keeping whichever was seen first hands the executor a stale identity.

    That is precisely the situation the executor's re-check exists to catch, not a duplicate
    to drop quietly.
    """
    one = ResolvedImage(
        canonical_path=AbsPath(GIT), filesystem_identity=FsId("a"),
        execution_subject=SUBJECT, content_identity=HashPin(path=AbsPath(GIT), sha256=Sha256("aa")),
    )
    other = ResolvedImage(
        canonical_path=AbsPath(GIT), filesystem_identity=FsId("b"),
        execution_subject=SUBJECT, content_identity=HashPin(path=AbsPath(GIT), sha256=Sha256("bb")),
    )
    assert merge_images([one, one]) == (one,)
    assert merge_images([one, other]) is None


# ------------------------------------------------------------------ the record


def test_a_policy_off_rung_carries_no_environment_and_no_evidence():
    """LADDER-05: the two policy-off rungs are what shipped before, field for field."""
    spec = legacy_spec(ShellDialect.POSIX, Rung.system_posix, Platform.POSIX, SUBJECT, True)
    record = decided_call(spec, "git status", AbsPath("/work"), None)
    assert isinstance(record.verdict, Pass)
    assert record.child_env is None and record.attested_images == ()


def test_todays_floor_refuses_first_and_names_its_own_reason():
    """The dangerous classes and the closed set answer different questions, and both run."""
    spec = legacy_spec(ShellDialect.POSIX, Rung.system_posix, Platform.POSIX, SUBJECT, True)
    record = decided_call(spec, "rm -rf /", AbsPath("/work"), "hardline:recursive-root-delete")
    assert isinstance(record.verdict, Deny)
    assert record.verdict.reason == "hardline:recursive-root-delete"


def test_the_record_carries_the_environment_the_length_guard_measured():
    """G18-13 / ENV-06: one environment per call, computed once and frozen onto the record."""
    record = decided_call(spec_for(Rung.cmd, CMD), "dir", AbsPath("C:\\work"), None)
    assert record.child_env is not None
    assert record.child_env["PATHEXT"] == ".COM;.EXE"
    assert record.child_env["NoDefaultCurrentDirectoryInExePath"] == "1"


def test_a_lone_surrogate_is_a_verdict_and_not_an_exception():
    """G24-23 / LAUNCH-08e: all three measurements encode first."""
    record = decided_call(spec_for(Rung.cmd, CMD), "dir \ud800", AbsPath("C:\\work"), None)
    assert isinstance(record.verdict, Deny) and "lone-surrogate" in record.verdict.reason


def test_an_over_long_body_is_refused_before_it_is_analysed():
    """LAUNCH-08: a body that cannot be launched has no reading worth computing."""
    record = decided_call(
        spec_for(Rung.cmd, CMD), "dir " + "x" * 9000, AbsPath("C:\\work"), None
    )
    assert isinstance(record.verdict, Deny) and "launch-oversize" in record.verdict.reason


def test_an_unattested_rung_refuses_every_command_that_needs_an_image():
    """G24-04 / SPEC-05: with no oracle nothing can answer the image half."""
    from dataclasses import replace

    spec = replace(spec_for(Rung.cmd, CMD), identity_oracle=None)
    record = decided_call(spec, "git status", AbsPath("C:\\work"), None)
    assert isinstance(record.verdict, Deny) and "SPEC-05c" in record.verdict.reason


def test_an_environment_the_target_cannot_answer_leaves_the_rung_unattested():
    source = oracle(project_root=None)
    spec = spec_for(Rung.cmd, CMD, source=source)
    record = decided_call(spec, "dir", AbsPath("C:\\work"), None)
    assert isinstance(record.verdict, Deny) and "project root" in record.verdict.reason


def test_a_host_that_disabled_the_floor_gets_no_denial_through_the_record():
    """``enable_hardline=False`` is a documented escape hatch, and the record must honour it.

    The planner builds this record before the engine sees it, so a floor verdict frozen onto
    it would deny at the launch even though the engine skipped the floor entirely — turning a
    host's opt-out into a no-op for exactly the tool it matters most for.

    What survives is the launch shape. LAUNCH-08's measurements and LAUNCH-09's encoding
    answer whether a command line can be *built*, which is not a policy question, and dropping
    them would trade a denial for a ``UnicodeEncodeError``.
    """
    spec = spec_for(Rung.cmd, CMD)
    judged = decided_call(spec, "unknown-tool --go", AbsPath("C:\\work"), None)
    assert isinstance(judged.verdict, Deny)

    unjudged = decided_call(
        spec, "unknown-tool --go", AbsPath("C:\\work"), None, closed_set=False
    )
    assert isinstance(unjudged.verdict, Pass)
    assert unjudged.child_env is not None  # still a launchable request
    assert AbsPath(CMD) in [i.canonical_path for i in unjudged.attested_images]

    structural = decided_call(
        spec, "dir \ud800", AbsPath("C:\\work"), None, closed_set=False
    )
    assert isinstance(structural.verdict, Deny)


def test_the_planner_reads_the_floor_switch_from_the_engine_that_owns_it():
    """One flag, two readers. Reaching for the private attribute would let them disagree."""
    from agentao.permissions import PermissionEngine
    from agentao.runtime.tool_planning import ToolCallPlanner

    for enabled in (True, False):
        planner = ToolCallPlanner.__new__(ToolCallPlanner)
        planner._permission_engine = PermissionEngine(
            project_root=Path("."), enable_hardline=enabled,
        )
        assert planner._floor_enabled is enabled
    planner = ToolCallPlanner.__new__(ToolCallPlanner)
    planner._permission_engine = None
    assert planner._floor_enabled is True  # no engine is not the same as opting out


def test_the_planners_record_follows_the_engines_floor_switch(tmp_path):
    """The wiring, not just the parameter: the planner has to pass the flag through.

    Asserting on ``decided_call(closed_set=False)`` alone leaves the one edge that matters
    untested — the planner reading the engine's flag — and that edge is where a host's opt-out
    would silently stop working.
    """
    from agentao.permissions import PermissionEngine
    from agentao.runtime.tool_planning import ToolCallPlanner, _decided_call
    from agentao.tools.shell import ShellTool

    tool = ShellTool()
    args = {"command": "rm -rf /", "working_directory": "."}
    spec = tool.shell_spec

    on = _decided_call(tool, spec, args, True)
    off = _decided_call(tool, spec, args, False)
    assert isinstance(on.verdict, Deny) and "hardline" in on.verdict.reason
    assert isinstance(off.verdict, Pass)

    for enabled, expected in ((True, Deny), (False, Pass)):
        planner = ToolCallPlanner.__new__(ToolCallPlanner)
        planner._permission_engine = PermissionEngine(
            project_root=tmp_path, enable_hardline=enabled,
        )
        record = _decided_call(tool, spec, args, planner._floor_enabled)
        assert isinstance(record.verdict, expected), enabled


def test_a_provider_with_no_rung_answers_with_a_verdict_and_not_an_exception():
    """Method rule 22: an exception inside the floor is not a verdict.

    An ``Exhausted`` provider reaches this function only if a caller skipped the floor that
    already refuses it, and ``spec.policy_enabled`` on that object would be an
    ``AttributeError`` — which carries no reason and is not on the DENY channel at all.
    """
    from agentao.capabilities.shell_spec import Exhausted

    for absent in (Exhausted("every rung refused"), None):
        record = decided_call(absent, "dir", AbsPath("C:\\work"), None)
        assert isinstance(record.verdict, Deny)
        assert record.verdict.reason == "hardline:unknown-rung-opaque"
