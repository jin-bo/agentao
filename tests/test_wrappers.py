"""Nested interpreter launches and process spawners.

PR-2 of the PowerShell ladder. ``WRAP-01`` through ``WRAP-06`` are defined once each in
``docs/design/powershell-support-spec.zh.md`` §2.
"""

from __future__ import annotations

import base64

import pytest

from agentao.capabilities.shell_spec import ShellDialect
from agentao.permissions_hardline._wrappers import (
    classify,
    is_spawner,
    nested_launch,
    parse_powershell_launch,
    resolve_powershell_switch,
    wrapper_for,
)


def encoded(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


# ------------------------------------------------------------------ WRAP-01


@pytest.mark.parametrize(
    "word,dialect",
    [
        ("bash", ShellDialect.POSIX),
        ("/bin/sh", ShellDialect.POSIX),
        ("pwsh", ShellDialect.POWERSHELL),
        ("powershell.exe", ShellDialect.POWERSHELL),
        ("C:\\Windows\\System32\\cmd.exe", ShellDialect.CMD),
    ],
)
def test_a_wrapper_is_recognised_by_its_basename(word, dialect):
    """WRAP-01: the value is the dialect the body will be read in.

    That is what makes re-entry meaningful. Re-reading a PowerShell body with POSIX patterns
    is the same mistake, one level down, that this whole ladder is about.
    """
    assert wrapper_for(word) is dialect


def test_a_nested_launch_is_opaque_however_harmless_its_body_looks():
    """WRAP-01 rule 2: an interpreter the child starts carries none of the guarantees.

    Not the pinned environment, not the filtered search path, not an attested image. So the
    launch is refused whatever the body says — re-entry buys a better reason, not approval.
    """
    reason = classify("bash", ["-c", "echo hi"], ShellDialect.POSIX)
    assert reason is not None and "WRAP-01" in reason


def test_the_body_is_recovered_so_the_refusal_can_name_the_real_reason():
    """WRAP-06: a dangerous nested body should be refused by its own reason, not generically."""
    nested = nested_launch("bash", ["-c", "rm -rf /"])
    assert nested is not None and nested.body == "rm -rf /"
    assert nested.callee is ShellDialect.POSIX


def test_an_ordinary_command_is_not_a_wrapper():
    """A rule that catches everything is not a rule."""
    assert wrapper_for("git") is None
    assert classify("git", ["status"], ShellDialect.POSIX) is None


# ------------------------------------------------------------------ WRAP-02


@pytest.mark.parametrize(
    "token,expected",
    [
        ("-Command", "command"),
        ("-c", "command"),  # the documented short form, though `c` prefixes two switches
        ("-cwa", "commandwithargs"),
        ("-EncodedCommand", "encodedcommand"),
        ("-e", "encodedcommand"),
        ("-ec", "encodedcommand"),
        ("-File", "file"),
        ("-nop", "noprofile"),
        ("/NoLogo", "nologo"),  # PowerShell accepts a slash as well as a dash
        ("-execu", "executionpolicy"),  # unambiguous prefix
    ],
)
def test_the_launch_switches_match_the_way_the_launcher_matches(token, expected):
    """WRAP-02: the launcher matches by prefix, so an abbreviation has to resolve the same."""
    assert resolve_powershell_switch(token) == expected


def test_an_ambiguous_abbreviation_resolves_to_nothing():
    """Guessing which switch the launcher would pick is how the parsed argv stops matching.

    `-no` prefixes four switches. Returning one of them would be inventing a decision that
    belongs to PowerShell.
    """
    assert resolve_powershell_switch("-no") is None


def test_command_takes_the_rest_of_the_line_as_the_body():
    launch = parse_powershell_launch(["-NoProfile", "-Command", "Get-Date", ";", "Get-Location"])
    assert launch.body == "Get-Date ; Get-Location"


def test_a_value_taking_switch_consumes_its_value():
    """`-ExecutionPolicy Bypass` is two tokens. Consuming one reads `Bypass` as the body."""
    launch = parse_powershell_launch(["-ExecutionPolicy", "Bypass", "-Command", "Get-Date"])
    assert launch.body == "Get-Date"


def test_an_encoded_command_is_decoded_before_re_entry():
    """WRAP-02: base64 of UTF-16LE. Refusing without decoding would refuse for the wrong reason.

    The point of decoding is the reason, not the verdict: the launch is opaque either way, and
    a denial that says "encoded command" tells a reader less than one naming what was in it.
    """
    launch = parse_powershell_launch(["-EncodedCommand", encoded("Remove-Item C:\\ -Recurse")])
    assert launch.body == "Remove-Item C:\\ -Recurse"


def test_an_undecodable_encoded_command_says_so():
    launch = parse_powershell_launch(["-EncodedCommand", "not-base64!!"])
    assert launch.body is None and launch.reason.endswith("undecodable")


@pytest.mark.parametrize(
    "args,expected",
    [
        (["-File", "script.ps1"], "file"),
        (["script.ps1"], "file"),  # the positional form is `-File` by another spelling
        (["-Sta"], "unknown-switch"),  # a real switch this parser has not worked out
        ([], "no-body"),
    ],
)
def test_the_forms_that_carry_no_readable_body(args, expected):
    """A file's bytes are not on the command line, so there is nothing here to read."""
    assert parse_powershell_launch(args).reason.endswith(expected)


def test_an_unlisted_switch_refuses_rather_than_being_ignored():
    """WRAP-02: anything else is opaque, and the list is short because the standard is high.

    The launcher accepts far more than this. What is missing is missing because nobody has
    worked out what it does to the body, which is the same bar the trusted table applies.
    """
    assert parse_powershell_launch(["-Sta", "-Command", "Get-Date"]).body is None


# ------------------------------------------------------------------ WRAP-03


def test_cmd_is_analysed_rather_than_skipped():
    """WRAP-03: `cmd /c` hands on a body, and that body has a grammar of its own."""
    nested = nested_launch("cmd.exe", ["/c", "dir", "C:\\"])
    assert nested is not None
    assert nested.callee is ShellDialect.CMD and nested.body == "dir C:\\"


# ------------------------------------------------------------------ WRAP-05


@pytest.mark.parametrize(
    "word,dialect",
    [
        ("Start-Process", ShellDialect.POWERSHELL),
        ("saps", ShellDialect.POWERSHELL),
        ("Invoke-Item", ShellDialect.POWERSHELL),
        ("ii", ShellDialect.POWERSHELL),
        ("Start-Job", ShellDialect.POWERSHELL),
        ("start", ShellDialect.CMD),
    ],
)
def test_a_spawner_is_opaque(word, dialect):
    """WRAP-05: the work goes to a process this floor never sees, started another way."""
    assert is_spawner(word, [], dialect) is True
    reason = classify(word, [], dialect)
    assert reason is not None and "WRAP-05" in reason


def test_invoke_command_is_a_spawner_only_when_it_leaves_the_machine():
    """The local form is an ordinary cmdlet; the remote parameter is what moves the work.

    Treating the word itself as a spawner would refuse a perfectly local call, and treating
    it as ordinary would miss the case that matters.
    """
    assert is_spawner("Invoke-Command", ["-ScriptBlock", "{}"], ShellDialect.POWERSHELL) is False
    assert is_spawner(
        "Invoke-Command", ["-ComputerName", "srv01"], ShellDialect.POWERSHELL
    ) is True


def test_every_remote_parameter_set_counts():
    """The rule enumerates eight, and an enumeration with a gap is the gap."""
    for parameter in (
        "-ComputerName",
        "-Session",
        "-ConnectionUri",
        "-VMId",
        "-VMName",
        "-ContainerId",
        "-HostName",
        "-SSHConnection",
    ):
        assert is_spawner("icm", [parameter, "x"], ShellDialect.POWERSHELL) is True


# ------------------------------------------------------------------ in the floors


def test_the_cmd_floor_refuses_a_spawner_and_a_nested_interpreter():
    """Wired where the dangerous table is, because both ask about the command word alone."""
    from agentao.permissions_hardline._cmd import scan_cmd

    assert scan_cmd("start notepad") == "hardline:cmd-opaque:WRAP-05:start"
    assert scan_cmd("cmd /c dir") == "hardline:cmd-opaque:WRAP-03:cmd"
    assert scan_cmd("dir /b") is None  # and an ordinary command still passes


def test_the_bash_gate_refuses_a_nested_interpreter_after_the_grammar_passes():
    """Wired last, because everything above it decides what the words are.

    `bash -c "git status"` clears every grammar check — no substitution, no keyword, no
    expansion — and is still a second interpreter with none of the guarantees.
    """
    from agentao.permissions_hardline._bash import scan_bash

    assert scan_bash('bash -c "git status"') == "hardline:posix-opaque:WRAP-01:posix"
    assert scan_bash("pwsh -Command Get-Date") == "hardline:posix-opaque:WRAP-02:command"
    assert scan_bash("git status") is None


def test_a_dangerous_nested_body_is_refused_by_its_own_reason():
    """WRAP-06: the launch is opaque either way, so nothing here can allow anything.

    What changes is what a reader is told. ``bash -c "rm -rf /"`` reported as "starts a second
    interpreter" hides the only fact that matters about it, and recovering the body is the
    whole reason :func:`nested_launch` returns one.
    """
    from agentao.permissions_hardline._bash import scan_bash
    from agentao.permissions_hardline._cmd import scan_cmd

    assert "delete" in (scan_bash('bash -c "rm -rf /"') or "")
    assert scan_cmd("echo hi && cmd /c format C:") == "hardline:format-volume"
    assert scan_cmd('"cmd" /c del C:\\*') == "hardline:delete-drive-root"


def test_nested_reading_is_bounded():
    """The body is untrusted input, and a stack overflow is not a refusal."""
    from agentao.permissions_hardline._bash import scan_bash

    body = "git status"
    for _ in range(30):
        body = f"bash -c '{body}'"
    assert scan_bash(body) is not None


def test_a_wrapper_is_recognised_at_every_command_position():
    r"""The bug this whole check was rewritten for, pinned so it cannot come back.

    Reading only the first token of a body reads one command out of however many the body
    holds. Every case below returned ``None`` at some point in this work — and for the cmd
    dialect that scanner is the only floor, with nothing behind it.

    The quoted and caret-escaped spellings are here for a second reason: the gate now splits
    with its own tokenizer, so ``"cmd"`` and ``c^md`` *resolve* to ``cmd`` rather than being
    reported as an unreadable word.
    """
    from agentao.permissions_hardline._bash import scan_bash
    from agentao.permissions_hardline._cmd import scan_cmd

    for body in (
        "echo hi & start notepad",
        "echo hi && cmd /c dir",
        '"cmd" /c dir',
        "c^md /c dir",
        "dir | cmd /c dir",
    ):
        assert scan_cmd(body) is not None, body
    for body in (
        "echo hi; sh -c foo",
        "true && bash -c foo",
        "echo hi | bash",
        "'bash' -c 'git status'",
    ):
        assert scan_bash(body) is not None, body
    # …and an ordinary body with several commands still passes.
    assert scan_cmd("echo hi & dir /b") is None
    assert scan_bash("echo hi ; git status") is None
