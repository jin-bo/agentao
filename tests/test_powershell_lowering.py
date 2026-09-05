"""The PowerShell lowering, graded against codex's own corpus.

PR-2 of the PowerShell ladder. ``LOWER-01`` through ``LOWER-04`` are defined once each in
``docs/design/powershell-support-spec.zh.md`` §2.

``tests/fixtures/powershell_lowering.json`` is codex's file, copied verbatim. It is the point
of the exercise: a lowering graded only by tests its own author wrote is graded against the
author's belief about PowerShell. Twenty-four rows pin the exact argv, and forty-four pin a
refusal — and asking only "did it refuse" would be satisfied by refusing everything, which is
why the step each one refuses at is pinned too.
"""

from __future__ import annotations

import collections
import json
from pathlib import Path

import pytest

from agentao.permissions_hardline._powershell import (
    ALLOWED_KINDS,
    LoweringError,
    lower_powershell,
    parser_available,
    scan_powershell,
)

pytestmark = pytest.mark.skipif(
    not parser_available(), reason="tree-sitter-powershell is not installed"
)

CORPUS = json.loads(
    (Path(__file__).parent / "fixtures" / "powershell_lowering.json").read_text(encoding="utf-8")
)
LOWERS = [c for c in CORPUS if c["expected"] is not None]
REFUSES = [c for c in CORPUS if c["expected"] is None]


def _refusal(script: str):
    try:
        lower_powershell(script)
    except LoweringError as exc:
        return exc
    return None


# ------------------------------------------------------------------ LOWER-04


def test_the_corpus_is_the_one_that_was_promised():
    """Sixty-eight rows, twenty-four of them expecting an exact argv. A shrunken corpus is a
    weaker gate wearing the same name."""
    assert len(CORPUS) == 68
    assert len(LOWERS) == 24
    assert len(REFUSES) == 44


@pytest.mark.parametrize("case", LOWERS, ids=[c["name"] for c in LOWERS])
def test_every_accepted_script_lowers_to_exactly_the_expected_argv(case):
    """LOWER-04: the whole argv, not merely "it lowered".

    Asking only whether lowering succeeded would pass on wrong quoting, a wrong escape, or an
    argument boundary cut in the wrong place — and each of those hands the trusted table a
    different argv than PowerShell will build, which is the one failure this must not have.
    """
    assert lower_powershell(case["script"]) == [list(w) for w in case["expected"]]


@pytest.mark.parametrize("case", REFUSES, ids=[c["name"] for c in REFUSES])
def test_every_refused_script_is_refused(case):
    assert _refusal(case["script"]) is not None


def test_the_refusals_are_spread_across_the_steps():
    """LOWER-04's real requirement: refusing for the right reason, not merely refusing.

    An implementation that failed every script at step 1 would pass the previous test
    completely. The distribution is pinned so that collapse is visible, and so is any drift
    in which step catches what.
    """
    distribution = collections.Counter(_refusal(c["script"]).step for c in REFUSES)
    assert dict(distribution) == {1: 2, 3: 5, 5: 23, 7: 11, 8: 1, 9: 2}
    assert sum(distribution.values()) == 44


# ------------------------------------------------------------------ LOWER-02


def test_the_accepted_kind_list_is_exactly_the_twenty_one():
    """LOWER-02: the list is pinned to a grammar version, so a rename fails closed.

    A grammar upgrade that renames a node makes that node unrecognised, which refuses the
    body. The alternative — matching loosely so renames keep working — silently widens what
    is accepted, and nobody finds out.
    """
    assert len(ALLOWED_KINDS) == 21
    assert "comment" in ALLOWED_KINDS  # only because the #requires step has already run
    for absent in ("assignment_expression", "variable", "script_block", "member_access"):
        assert absent not in ALLOWED_KINDS


def test_an_assignment_forms_no_command_and_is_refused_by_kind():
    """The rule's own motivating case: `$Function:git = { … }` passes no arguments anywhere.

    A command-level rule never sees it, so the closure has to come from the shape of the
    tree. This is why the gate is an allowlist over node kinds and not a list of bad commands.
    """
    exc = _refusal("$Function:git = { Start-Process calc }")
    assert exc is not None and exc.step == 5


# ------------------------------------------------------------------ LOWER-01 steps


def test_a_unicode_syntax_alias_refuses_before_parsing():
    """Step 1: PowerShell treats these as syntax even inside what tree-sitter calls one token."""
    exc = _refusal("Get-Content \u2018foo\u2019")
    assert exc is not None and exc.step == 1


def test_a_requires_directive_refuses_although_it_is_only_a_comment():
    """Step 4: it runs before the body and can load modules, so `comment` is not harmless."""
    exc = _refusal("#Requires -Modules Evil\nGet-Date")
    assert exc is not None and exc.step == 4


def test_a_recovered_parse_is_a_parse_of_something_else():
    """Step 3: tree-sitter recovers from errors, and the tree it recovers is not the script."""
    exc = _refusal("Get-Content 'unterminated")
    assert exc is not None and exc.step == 3


def test_an_attached_parameter_value_is_refused():
    """Step 7: `-Path:x` needs PowerShell's own binding rules to say what the argv becomes."""
    exc = _refusal("Get-ChildItem -Path:C:\\Windows")
    assert exc is not None and exc.step == 7


def test_an_expandable_string_is_refused_but_a_verbatim_one_is_not():
    """Step 7: the lowered argv has to equal what PowerShell will build, or it is a guess."""
    assert _refusal('Get-Content "$env:PATH"') is not None
    assert lower_powershell("Get-Content 'literal $notavar'") == [
        ["Get-Content", "literal $notavar"]
    ]


def test_a_using_declaration_is_refused():
    """Step 9: the engine resolves it before the body runs, so lowering it proves nothing."""
    exc = _refusal("using namespace System.Diagnostics")
    assert exc is not None and exc.step in (5, 9)


# ------------------------------------------------------------------ LOWER-03


def test_the_flag_equals_mask_is_one_byte_so_the_ranges_still_line_up():
    """Step 2 exists to let step 8 compare node ranges against the *original* source.

    Replacing the `=` with a space keeps every later offset where it was. Deleting it, or
    rewriting the token, would leave step 8 comparing against a source that no longer exists.
    """
    assert lower_powershell("git log --format=oneline") == [["git", "log", "--format=oneline"]]


def test_text_between_commands_must_be_a_joiner_the_walk_understands():
    """LOWER-03: anything the tree dropped lives in the gaps, and this refuses to ignore them."""
    assert lower_powershell("Get-Date; Get-Location") == [["Get-Date"], ["Get-Location"]]
    assert lower_powershell("Get-Date | Select-Object") == [["Get-Date"], ["Select-Object"]]


def test_a_pipe_with_nothing_after_it_is_refused():
    """The walk carries `needs_command`, so a trailing pipe cannot end a readable script."""
    assert _refusal("Get-Date |") is not None


def test_a_comment_only_opens_at_a_token_boundary():
    """LOWER-03: tree-sitter can split an embedded `#` out of a bare token.

    Accepting that would silently drop the rest of the line, which is the whole line that
    matters when what follows the `#` is a second command.
    """
    assert lower_powershell("Get-Date # trailing note") == [["Get-Date"]]


# ------------------------------------------------------------------ the floor's entry point


def test_the_scanner_reports_the_step_that_refused():
    """A refusal nobody can locate cannot be acted on, by a user or by the next reviewer."""
    reason = scan_powershell("$x = 1")
    assert reason is not None and reason.startswith("hardline:powershell-opaque:5:")


def test_a_script_that_lowers_cleanly_is_not_thereby_approved():
    """Lowering is where the trusted table starts, not a verdict that the script is safe."""
    assert scan_powershell("Start-Process calc.exe") is None


# ------------------------------------------------------------------ the dispatch


def _ps_spec(policy: bool):
    import dataclasses

    from agentao.capabilities.shell_spec import (
        InterpreterIdentity,
        PinnedEnv,
        Platform,
        ResolvedImage,
        Rung,
        Sha256,
        ShellDialect,
        Subject,
        legacy_spec,
    )

    subject = Subject("subject")
    base = legacy_spec(ShellDialect.CMD, Rung.legacy_cmd, Platform.WINDOWS, subject)
    if not policy:
        return base
    launcher = InterpreterIdentity(
        image=ResolvedImage(
            canonical_path="C:\\pwsh\\pwsh.exe",  # type: ignore[arg-type]
            filesystem_identity="1:2",  # type: ignore[arg-type]
            execution_subject=subject,
        ),
        launcher_hash=Sha256("h"),
        edition="Core",
    )
    return dataclasses.replace(
        base,
        dialect=ShellDialect.POWERSHELL,
        rung=Rung.pwsh,
        policy_enabled=True,
        launcher=launcher,
        pinned_env=PinnedEnv(),
    )


def test_a_lowered_script_needs_the_decided_record_before_the_closed_set_can_run():
    """Fail closed at the seam, for a reason that outlives the stage that put it there.

    The closed set needs this call's working directory and child environment, and this
    function is given neither — the planner builds a frozen record that carries both. A caller
    without one gets a refusal rather than a pass, because "I could not run the second half"
    is a different answer from "the second half found nothing".
    """
    from agentao.permissions_hardline import hardline_check

    reason = hardline_check(
        "run_shell_command", {"command": "Get-Date"}, shell_spec=_ps_spec(True)
    )
    assert reason is not None and "decided record" in reason


def test_the_powershell_floor_does_not_reach_the_policy_off_rung():
    """LADDER-05 again: today's rungs keep today's floor, whatever grammar this module learns."""
    from agentao.permissions_hardline import hardline_check

    assert hardline_check(
        "run_shell_command", {"command": "Get-Date"}, shell_spec=_ps_spec(False)
    ) is None


# ------------------------------------------------------------------ the Windows classes


def test_the_powershell_floor_refuses_the_windows_dangerous_classes():
    """q2's classes are about the platform, not the syntax that reached them.

    The table lived in the cmd module and was read only by the cmd floor, so every class in it
    was unreachable from a PowerShell rung — although two of its entries were already spelled
    as PowerShell. Formatting a volume destroys the same bytes whichever interpreter typed it.
    """
    assert scan_powershell("Format-Volume -DriveLetter D") == "hardline:format-volume"
    assert scan_powershell("Clear-Disk -Number 1") == "hardline:diskpart-clean"
    assert scan_powershell("Disable-BitLocker -MountPoint C:") == "hardline:bitlocker-disable"
    assert scan_powershell("Remove-Item C:\\ -Recurse -Force") == "hardline:delete-drive-root"
    assert scan_powershell("Get-Date; Clear-Disk -Number 1") == "hardline:diskpart-clean"


def test_a_dangerous_word_that_is_only_an_argument_is_not_a_dangerous_command():
    """The class has to *start* the command.

    Searching the whole line reads `Write-Output Format-Volume` as a format — the same false
    positive cmd's command-position anchor exists to prevent. Here the anchor is free: the
    lowering has already cut the body into commands, so matching at position zero is exactly
    "in command position".
    """
    assert scan_powershell("Write-Output Format-Volume") is None
    assert scan_powershell("Write-Output 'format C:'") is None
    assert scan_powershell("Get-Content Clear-Disk.txt") is None
