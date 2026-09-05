"""The trusted table and the flags derived from it.

PR-2 of the PowerShell ladder. ``TOK-01``, ``EFF-01`` through ``EFF-08`` and ``NAME-01`` are
defined once each in ``docs/design/powershell-support-spec.zh.md`` §2. Question q9 (answered
2026-09-05) chose the table's width: the minimal set plus the everyday read-only toolchain.
"""

from __future__ import annotations

import pytest

from agentao.capabilities.shell_spec import ShellDialect
from agentao.permissions_hardline._effects import (
    ArgPattern,
    Dynamic,
    EffectFlag,
    EntryKind,
    ExitState,
    Literal,
    MatchMode,
    TrustedEntry,
    entries_for,
    lookup,
    names_provider_drive,
)


def lit(*words: str):
    return tuple(Literal(w) for w in words)


# ------------------------------------------------------------------ EFF-01 / EFF-08


def test_an_entry_with_no_triggers_is_inert():
    """EFF-01: the empty set is inertness, and registering nothing is a real registration.

    What cannot be registered is a command whose triggers nobody worked out. "Probably fine"
    has no spelling in this table.
    """
    entry = lookup("ls", ShellDialect.POSIX)
    assert entry is not None
    assert entry.flags(lit("-la")) == frozenset()


def test_every_entry_carries_a_source():
    """EFF-08: each row is an assertion someone has to be willing to defend."""
    for dialect in (ShellDialect.POSIX, ShellDialect.CMD, ShellDialect.POWERSHELL):
        for entry in entries_for(dialect):
            assert entry.source.strip(), entry.name


def test_flags_come_only_from_the_registered_fields():
    """EFF-08: the table is data. A synthetic entry proves the derivation has no other input."""
    entry = TrustedEntry(
        name="probe",
        dialect=ShellDialect.POSIX,
        kind=EntryKind.application,
        source="test",
        execution_triggers=(ArgPattern("--run", MatchMode.exact),),
    )
    assert entry.flags(lit("--dry")) == frozenset()
    assert entry.flags(lit("--run")) == {EffectFlag.executes_input}


# ------------------------------------------------------------------ the git surface


@pytest.mark.parametrize(
    "args",
    [
        ("-c", "core.pager=evil", "log"),
        ("-c", "core.fsmonitor=./evil", "status"),
        ("--exec-path=/tmp/evil", "status"),
        ("--upload-pack", "evil", "fetch"),
    ],
)
def test_gits_configuration_surface_runs_supplied_code(args):
    """EFF-08: `git` is trusted, and several of its own flags hand a command to a shell.

    This is why the entry registers argument shapes rather than a verdict about the program.
    The word `git` is not the question; `git -c core.pager=<cmd>` is.
    """
    entry = lookup("git", ShellDialect.POSIX)
    assert EffectFlag.executes_input in entry.flags(lit(*args))


@pytest.mark.parametrize("args", [("status",), ("log", "--oneline"), ("diff", "HEAD")])
def test_the_read_only_git_subcommands_stay_inert(args):
    """q9's whole point: the commands a developer runs constantly must not be refused."""
    entry = lookup("git", ShellDialect.POSIX)
    assert entry.flags(lit(*args)) == frozenset()


# ------------------------------------------------------------------ WRAP-07


@pytest.mark.parametrize("runner", ["timeout", "nice", "nohup", "env", "sudo", "xargs", "exec"])
def test_a_prefix_runner_is_never_inert(runner):
    """WRAP-07: their argv tail *is* a command, so the trigger always fires.

    Registering one of these as inert is what lets `timeout 5 ./evil` through, which is the
    exact shape of hole the existing floor was measured to have.
    """
    entry = lookup(runner, ShellDialect.POSIX)
    assert entry is not None
    assert EffectFlag.executes_input in entry.flags(lit("5", "./evil"))


def test_a_prefix_runner_with_no_tail_has_nothing_to_run():
    """The trigger is the tail, so an empty tail is not a command being run."""
    assert lookup("timeout", ShellDialect.POSIX).flags(()) == frozenset()


# ------------------------------------------------------------------ EFF-07 caller scope


def test_the_evaluator_carries_caller_scope_on_the_execution_trigger():
    """EFF-07's `+` sits on the *executes_input* set, and that placement is load-bearing.

    An evaluator has no intrinsic rebind trigger — the rebinding happens inside the body it
    evaluates. Hanging caller scope off the rebind set alone means that body's exit state can
    never be merged back, and `iex 'Set-Alias git evil'; git status` passes. Giving it an
    unconditional rebind trigger instead taints everything after `iex 'Get-Date'`.
    """
    entry = lookup("Invoke-Expression", ShellDialect.POWERSHELL)
    flags = entry.flags(lit("Set-Alias git evil"))
    assert flags == {EffectFlag.executes_input, EffectFlag.rebinds_caller}
    assert EffectFlag.rebinds_after not in flags  # the flag itself taints nothing


def test_caller_scope_does_not_fire_when_no_trigger_matched():
    """It is a property of a matched trigger, not of the entry."""
    entry = lookup("Invoke-Expression", ShellDialect.POWERSHELL)
    assert entry.flags(()) == frozenset()


def test_only_the_dialects_own_evaluator_may_reenter():
    """EFF-02: the literal-string exception belongs to the evaluator and to nothing else."""
    assert lookup("Invoke-Expression", ShellDialect.POWERSHELL).reenters is True
    assert lookup("eval", ShellDialect.POSIX).reenters is True
    assert lookup("Import-Module", ShellDialect.POWERSHELL).reenters is False
    assert lookup("source", ShellDialect.POSIX).reenters is False


# ------------------------------------------------------------------ EFF-07b rebinding


@pytest.mark.parametrize(
    "dialect,word",
    [
        (ShellDialect.POSIX, "export"),
        (ShellDialect.POSIX, "alias"),
        (ShellDialect.CMD, "set"),
        (ShellDialect.CMD, "path"),
        (ShellDialect.POWERSHELL, "Set-Alias"),
        (ShellDialect.POWERSHELL, "Set-Content"),
    ],
)
def test_the_enumerated_rebinding_forms_taint_what_follows(dialect, word):
    """EFF-07b: a name bound here changes what a later command word means."""
    entry = lookup(word, dialect)
    assert EffectFlag.rebinds_after in entry.flags(lit("X=1"))


# ------------------------------------------------------------------ EFF-04 / NAME-01


def test_an_unknown_word_has_no_entry():
    """EFF-04: nothing is implicitly trusted, and nothing implicitly executes its input.

    The expensive half of the design: this refuses the call the word appears in, because
    tainting only what follows is a hole one line wide — a one-command body has no follower.
    """
    assert lookup("some-tool-nobody-registered", ShellDialect.POSIX) is None


def test_command_words_fold_case_where_the_dialect_does():
    """cmd and PowerShell match command words case-insensitively; POSIX does not."""
    assert lookup("ECHO", ShellDialect.CMD) is not None
    assert lookup("get-date", ShellDialect.POWERSHELL) is not None
    assert lookup("LS", ShellDialect.POSIX) is None


# ------------------------------------------------------------------ EFF-05 / EFF-06


@pytest.mark.parametrize("arg", ["Env:PATH", "Alias:git", "Function:prompt", "Variable:x", "HKLM:\\Software"])
def test_naming_a_provider_drive_is_refused_whatever_the_cmdlet(arg):
    """EFF-05: one rule closes Env:, Alias:, Function:, Variable: and the registry at once."""
    assert names_provider_drive(lit(arg)) is True


@pytest.mark.parametrize("arg", ["C:\\Windows", "D:/data", "plain.txt", "-Recurse"])
def test_a_drive_letter_path_is_a_filesystem_path(arg):
    """A `Copy-Item` to `C:\\` stays inert; the shape that matters is a provider name."""
    assert names_provider_drive(lit(arg)) is False


def test_a_dynamic_token_matches_no_pattern_which_is_why_it_must_be_refused_first():
    """EFF-06 exists because this comparison cannot see a dynamic token.

    `ArgPattern` compares literal shapes, so a dynamic argument fires no trigger and reads as
    inert. That is the wrong answer, and the refusal has to happen before flags are derived —
    which is what makes EFF-06 a rule about ordering rather than a flag.
    """
    entry = lookup("git", ShellDialect.POSIX)
    # `git $FLAGS status` reads as inert here, and would be approved if nothing refused the
    # dynamic token first. `-c` in the same position is caught, which is what makes the point:
    # the difference between the two is knowability, not danger.
    assert entry.flags((Dynamic("variable"), Literal("status"))) == frozenset()
    assert entry.flags((Literal("-c"), Literal("core.pager=x"))) == {EffectFlag.executes_input}


# ------------------------------------------------------------------ ExitState


def test_exit_state_merges_toward_tainted():
    """EFF-03: a body that rebound a name did so regardless of what else ran cleanly."""
    assert ExitState(False).merge(ExitState(True)).tainted is True
    assert ExitState(True).merge(ExitState(False)).tainted is True
    assert ExitState(False).merge(ExitState(False)).tainted is False


def test_the_external_toolchain_is_registered_under_every_dialect():
    """IMG-02's name half asks about *that dialect's* table, and ``git`` is one program.

    G04-34 expects ``git status`` to pass on the pwsh, cmd and git_bash rungs alike. Defining
    the applications once and instantiating them per dialect is what keeps that true: three
    hand-kept copies drift, and a trigger added to the POSIX ``git`` and forgotten on the
    PowerShell one is a Windows bypass with nothing to notice it.
    """
    for name in ("git", "python", "node", "grep", "ls", "cat"):
        for dialect in (ShellDialect.POSIX, ShellDialect.CMD, ShellDialect.POWERSHELL):
            entry = lookup(name, dialect)
            assert entry is not None, (name, dialect)
            assert entry.dialect is dialect
    posix_git = lookup("git", ShellDialect.POSIX)
    for dialect in (ShellDialect.CMD, ShellDialect.POWERSHELL):
        assert lookup("git", dialect).execution_triggers == posix_git.execution_triggers
        assert lookup("git", dialect).rebind_triggers == posix_git.rebind_triggers


def test_in_process_entries_stay_with_their_own_interpreter():
    """A cmdlet is part of PowerShell, a cmd internal command part of cmd, a builtin part of
    bash. Sharing those would claim a name the interpreter does not have."""
    assert lookup("Get-Date", ShellDialect.CMD) is None
    assert lookup("dir", ShellDialect.POWERSHELL) is None
    assert lookup("pwd", ShellDialect.CMD) is None
