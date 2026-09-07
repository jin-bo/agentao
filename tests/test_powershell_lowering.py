"""The PowerShell lowering, graded against codex's own corpus.

Lowering turns a PowerShell script into literal argv, or refuses. It is what lets the
dangerous table read a *command* rather than search raw text, and a refusal from it denies
nothing on its own.

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


# ------------------------------------------------------------------- the corpus


def test_the_corpus_is_the_one_that_was_promised():
    """Sixty-eight rows, twenty-four of them expecting an exact argv. A shrunken corpus is a
    weaker gate wearing the same name."""
    assert len(CORPUS) == 68
    assert len(LOWERS) == 24
    assert len(REFUSES) == 44


@pytest.mark.parametrize("case", LOWERS, ids=[c["name"] for c in LOWERS])
def test_every_accepted_script_lowers_to_exactly_the_expected_argv(case):
    """The whole argv, not merely "it lowered".

    Asking only whether lowering succeeded would pass on wrong quoting, a wrong escape, or an
    argument boundary cut in the wrong place — and each of those hands the trusted table a
    different argv than PowerShell will build, which is the one failure this must not have.
    """
    assert lower_powershell(case["script"]) == [list(w) for w in case["expected"]]


@pytest.mark.parametrize("case", REFUSES, ids=[c["name"] for c in REFUSES])
def test_every_refused_script_is_refused(case):
    assert _refusal(case["script"]) is not None


def test_the_refusals_are_spread_across_the_steps():
    """The real requirement: refusing for the right reason, not merely refusing.

    An implementation that failed every script at step 1 would pass the previous test
    completely. The distribution is pinned so that collapse is visible, and so is any drift
    in which step catches what.
    """
    distribution = collections.Counter(_refusal(c["script"]).step for c in REFUSES)
    assert dict(distribution) == {1: 2, 3: 5, 5: 23, 7: 11, 8: 1, 9: 2}
    assert sum(distribution.values()) == 44


# -------------------------------------------------------------- the kind list


def test_the_accepted_kind_list_is_exactly_the_twenty_one():
    """The list is pinned to a grammar version, so a rename fails closed.

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


# ------------------------------------------------------------------- the steps


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


# ------------------------------------------------------------- the coverage walk


def test_the_flag_equals_mask_is_one_byte_so_the_ranges_still_line_up():
    """Step 2 exists to let step 8 compare node ranges against the *original* source.

    Replacing the `=` with a space keeps every later offset where it was. Deleting it, or
    rewriting the token, would leave step 8 comparing against a source that no longer exists.
    """
    assert lower_powershell("git log --format=oneline") == [["git", "log", "--format=oneline"]]


def test_text_between_commands_must_be_a_joiner_the_walk_understands():
    """Anything the tree dropped lives in the gaps, and this refuses to ignore them."""
    assert lower_powershell("Get-Date; Get-Location") == [["Get-Date"], ["Get-Location"]]
    assert lower_powershell("Get-Date | Select-Object") == [["Get-Date"], ["Select-Object"]]


def test_a_pipe_with_nothing_after_it_is_refused():
    """The walk carries `needs_command`, so a trailing pipe cannot end a readable script."""
    assert _refusal("Get-Date |") is not None


def test_a_comment_only_opens_at_a_token_boundary():
    """tree-sitter can split an embedded `#` out of a bare token.

    Accepting that would silently drop the rest of the line, which is the whole line that
    matters when what follows the `#` is a second command.
    """
    assert lower_powershell("Get-Date # trailing note") == [["Get-Date"]]


# ------------------------------------------------------------ the floor's entry point


def test_a_script_that_cannot_be_lowered_is_not_thereby_refused():
    """A parse failure is a statement about this parser, not about the script.

    ``$x = 1`` forms no command node, and there is nothing dishonest about that — most real
    PowerShell does something the grammar allowlist does not cover. Denying here would refuse
    ordinary work, and the general floor has already had its say on the same body.
    """
    assert scan_powershell("$x = 1") is None


def test_a_script_that_lowers_cleanly_is_not_thereby_approved():
    """Lowering is where the dangerous table starts, not a verdict that the script is safe."""
    assert scan_powershell("Start-Process calc.exe") is None


# ------------------------------------------------------------------ the Windows classes


def test_the_powershell_floor_refuses_the_windows_dangerous_classes():
    """These classes are about the platform, not the syntax that reached them.

    The table lived in the cmd module and was read only by the cmd floor, so every class in it
    was unreachable from PowerShell — although two of its entries were already spelled as
    PowerShell. Formatting a volume destroys the same bytes whichever interpreter typed it.
    """
    assert scan_powershell("Format-Volume -DriveLetter D") == "hardline:format-volume"
    assert scan_powershell("Clear-Disk -Number 1") == "hardline:diskpart-clean"
    assert scan_powershell("Disable-BitLocker -MountPoint C:") == "hardline:bitlocker-disable"
    assert scan_powershell("Remove-Item C:\\ -Recurse -Force") == "hardline:delete-drive-root"
    assert scan_powershell("Get-Date; Clear-Disk -Number 1") == "hardline:diskpart-clean"


def test_a_dangerous_word_that_is_only_an_argument_is_not_a_dangerous_command():
    """The class has to *start* the command.

    Searching the whole line reads `Write-Output Format-Volume` as a format. Here the anchor
    is free: lowering has already cut the body into commands, so matching at position zero is
    exactly "in command position".
    """
    assert scan_powershell("Write-Output Format-Volume") is None
    assert scan_powershell("Write-Output 'format C:'") is None
    assert scan_powershell("Get-Content Clear-Disk.txt") is None
