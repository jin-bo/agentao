r"""What the command floor refuses when PowerShell is the interpreter.

Two properties, and the first one is the compatibility contract:

**The general floor runs on every dialect, always.** A body the floor refuses today is a body
the PowerShell path refuses too — same input, same permission settings, same answer. The
PowerShell table *adds* to that; it never replaces it, and it is never a fallback for when
the general floor found nothing.

**A script this cannot parse is not thereby refused.** Lowering exists so the table can read
a command instead of searching raw text. When it fails, that is a statement about this parser
and about nothing else, so the call carries on to the permission rules with the general
floor's answer standing. Refusing there would deny ordinary PowerShell for using a construct
the grammar allowlist does not cover.
"""

from __future__ import annotations

import pytest

from agentao.capabilities.shell_spec import ShellDialect, ShellSpec
from agentao.permissions_hardline import generic_floor, hardline_check
from agentao.permissions_hardline._powershell import parser_available
from agentao.permissions_hardline._windows import canonical_command, dangerous_reason

POWERSHELL = ShellSpec(dialect=ShellDialect.POWERSHELL)
CMD = ShellSpec(dialect=ShellDialect.CMD)
POSIX = ShellSpec(dialect=ShellDialect.POSIX)

needs_grammar = pytest.mark.skipif(
    not parser_available(), reason="tree-sitter-powershell is not installed"
)


def check(command: str, spec=None):
    return hardline_check("run_shell_command", {"command": command}, shell_spec=spec)


# ------------------------------------------------- the general floor runs everywhere


GENERAL_DENIALS = [
    "rm -rf /",
    "rm -rf ~",
    "sudo rm -rf /usr",
    "mkfs.ext4 /dev/sda1",
    "dd if=/dev/zero of=/dev/sda",
    ":(){ :|:& };:",
    "sh -c 'echo ok; rm -rf /'",
    "echo 'rm -rf /' | bash",
]


@pytest.mark.parametrize("command", GENERAL_DENIALS)
@pytest.mark.parametrize("spec", [None, POSIX, CMD, POWERSHELL], ids=lambda s: getattr(
    getattr(s, "dialect", None), "value", "none"))
def test_every_general_denial_survives_every_dialect(command, spec):
    """The corpus is checked dialect by dialect rather than once.

    Running it only against the default would leave the PowerShell path free to refuse less
    than today's, which is the one regression this whole change must not have.
    """
    assert check(command, spec) is not None


def test_the_general_floor_is_the_same_function_both_callers_use():
    """One definition point. Two copies of "today's floor" is how they start to disagree."""
    assert generic_floor("rm -rf /") == check("rm -rf /") == check("rm -rf /", POWERSHELL)


def test_a_benign_command_stays_benign_on_every_dialect():
    for spec in (None, POSIX, CMD, POWERSHELL):
        assert check("git status", spec) is None


# ------------------------------------------------------- the Windows danger classes


@needs_grammar
@pytest.mark.parametrize(
    "command",
    [
        r"Remove-Item -Recurse -Force C:\ ",
        r"ri -r -fo C:\ ",
        r"rm -Recurse -Force C:\ ",
        r"Remove-Item -Force -Recurse 'C:\'",
        r"Remove-Item -Recurse C:",
        r"del -Recurse C:/",
        r"Remove-Item -Path C:\ -Recurse",
        # PowerShell command names are case-insensitive, and the alias half of the resolution
        # already was — so the canonical spelling was the one that had to be typed exactly.
        r"remove-item -Recurse -Force C:\ ",
        r"REMOVE-ITEM -r C:\ ",
    ],
)
def test_a_recursive_delete_of_a_drive_root_is_refused_in_every_spelling(command):
    """Six built-in aliases and any unambiguous prefix of ``-Recurse`` are the same call.

    A table written in canonical names alone would refuse one spelling and allow the other
    five, which is worse than refusing none: it reads as coverage.
    """
    assert check(command, POWERSHELL) == "hardline:delete-drive-root"


@needs_grammar
@pytest.mark.parametrize(
    "command,reason",
    [
        ("Format-Volume -DriveLetter D", "hardline:format-volume"),
        ("Clear-Disk -Number 0", "hardline:diskpart-clean"),
        ("Disable-BitLocker -MountPoint C:", "hardline:bitlocker-disable"),
        ("vssadmin delete shadows /all", "hardline:shadow-copy-delete"),
        ("cipher /w:C", "hardline:cipher-wipe"),
    ],
)
def test_the_other_windows_classes_are_refused(command, reason):
    assert check(command, POWERSHELL) == reason


@needs_grammar
@pytest.mark.parametrize(
    "command",
    [
        r"Write-Output 'Remove-Item -Recurse -Force C:\'",
        r"Write-Output 'Format-Volume'",
        r"Write-Output Format-Volume",
        r"Get-ChildItem -Recurse C:\ ",
        r"Remove-Item -Recurse -Force .\build",
        r"Remove-Item -Force C:\ ",
    ],
)
def test_printing_the_same_text_is_not_doing_it(command):
    """Lowering separates the commands, so the table matches a command *word*.

    That is the whole reason for parsing rather than searching: a substring search reads
    ``Write-Output Format-Volume`` as a format, and a comment mentioning one as a call.
    """
    assert check(command, POWERSHELL) is None


@needs_grammar
def test_the_windows_table_is_not_reached_by_the_default_windows_shell():
    """A deliberate compatibility decision, recorded rather than assumed.

    Windows' default stays ``%COMSPEC% /c`` running the general floor exactly as it did, so
    these classes are not stopped by *it*. The permission rules still run; what is being kept
    is the status quo, not a claim that the command is safe.
    """
    assert check(r"Remove-Item -Recurse -Force C:\ ", CMD) is None
    assert check(r"Remove-Item -Recurse -Force C:\ ", None) is None
    assert check(r"Remove-Item -Recurse -Force C:\ ", POWERSHELL) is not None


# ------------------------------------------------------------- lowering failures


@needs_grammar
@pytest.mark.parametrize(
    "command",
    [
        "$target = 'C:\\'",                 # an assignment forms no command node
        "Get-Content x | ForEach-Object { $_ }",  # a script block is not on the kind list
        "Get-Date ; ) unbalanced",          # a recovered parse
        "#requires -Modules Foo\nGet-Date",  # runs before the body
    ],
)
def test_a_script_that_cannot_be_lowered_is_not_thereby_refused(command):
    """A parse failure is a fact about this parser, not about the script.

    Denying here would refuse ordinary PowerShell — variables, pipelines into script blocks,
    most real scripts — and the floor is not the layer that decides those. The general floor
    has already had its say, and the permission rules take it from here.
    """
    assert check(command, POWERSHELL) is None


@needs_grammar
def test_a_lowering_failure_does_not_suppress_the_general_floor():
    """The two are sequential, not alternatives.

    Wiring the PowerShell table as a fallback for *either* outcome is the mistake this pins:
    the general floor's verdict has to survive a body the grammar cannot read.
    """
    assert check("$x = 1; rm -rf /", POWERSHELL) is not None


# ----------------------------------------------------------- the alias resolution


@pytest.mark.parametrize(
    "word,canonical",
    [
        ("rm", "Remove-Item"),
        ("RI", "Remove-Item"),
        ("Del", "Remove-Item"),
        ("rmdir", "Remove-Item"),
        ("rwmi", "Remove-WmiObject"),
        (r"C:\Windows\System32\format.com", "format"),
        ("format.exe", "format"),
        ("Get-Date", "Get-Date"),
        ("some-unknown-thing", "some-unknown-thing"),
    ],
)
def test_a_command_word_resolves_to_the_cmdlet_it_names(word, canonical):
    assert canonical_command(word) == canonical


def test_an_empty_command_is_not_a_dangerous_one():
    assert dangerous_reason([]) is None


def test_the_recurse_switch_accepts_prefixes_and_nothing_else():
    r"""``-r`` is a real spelling of ``-Recurse`` for ``Remove-Item``: no other parameter of
    that cmdlet, common parameters included, starts with ``r``. ``-rf`` is not — PowerShell
    would reject it, so treating it as recursion would be a false positive."""
    assert dangerous_reason(["Remove-Item", "-r", r"C:\ ".strip()])
    assert dangerous_reason(["Remove-Item", "-RECU", "C:"])
    assert dangerous_reason(["Remove-Item", "-rf", "C:"]) is None
    assert dangerous_reason(["Remove-Item", "-Force", "C:"]) is None


def test_the_command_word_is_matched_case_insensitively():
    """PowerShell does not care about the case of a command name, so neither can this.

    The alias half already folded case (``RM`` resolved), which made the canonical spelling
    the single form that had to be typed exactly — coverage that reads as complete and is not.
    """
    for word in ("Remove-Item", "remove-item", "REMOVE-ITEM", "ReMoVe-ItEm"):
        assert dangerous_reason([word, "-Recurse", "C:"]) == "hardline:delete-drive-root"
