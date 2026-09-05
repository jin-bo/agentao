"""The cmd floor, and the gate that decides whether it runs at all.

PR-2 of the PowerShell ladder. Rules ``CMD-01``, ``TOK-02``, ``NAME-01`` and the Windows half
of the dangerous table (question q2, decided 2026-09-05) are defined once each in
``docs/design/powershell-support-spec.zh.md`` §2.

The gate is the first thing tested, because it is what keeps this stage invisible: the two
policy-off rungs are promised to be verdict-for-verdict what shipped before, so none of this
may run for them.
"""

from __future__ import annotations

import dataclasses

import pytest

from agentao.capabilities.shell_spec import (
    LauncherIdentity,
    PinnedEnv,
    Platform,
    ResolvedImage,
    Rung,
    Sha256,
    ShellDialect,
    Subject,
    legacy_spec,
)
from agentao.permissions_hardline import hardline_check
from agentao.permissions_hardline._cmd import CMD_CONTROL, CMD_INTERNAL, scan_cmd

SUBJ = Subject("subject")


def cmd_spec(policy: bool):
    """A CMD spec with policy on or off. Policy-on needs a launcher to satisfy SPEC-03."""
    base = legacy_spec(ShellDialect.CMD, Rung.legacy_cmd, Platform.WINDOWS, SUBJ)
    if not policy:
        return base
    launcher = LauncherIdentity(
        image=ResolvedImage(
            canonical_path="C:\\Windows\\System32\\cmd.exe",  # type: ignore[arg-type]
            filesystem_identity="1:2",  # type: ignore[arg-type]
            execution_subject=SUBJ,
        ),
        launcher_hash=Sha256("h"),
    )
    return dataclasses.replace(
        base, rung=Rung.cmd, policy_enabled=True, launcher=launcher, pinned_env=PinnedEnv()
    )


# ------------------------------------------------------------------ the gate


def test_a_policy_off_rung_keeps_running_todays_floor():
    """LADDER-05: the pre-flip rungs are promised identical, so the new grammar must not run.

    ``for %%i in (*) do echo`` is refused outright by the cmd floor and is invisible to the
    POSIX one. Seeing it pass here is what proves the gate, not the absence of a crash.
    """
    args = {"command": "for %%i in (*) do echo %%i"}
    assert hardline_check("run_shell_command", args, shell_spec=cmd_spec(policy=False)) is None
    assert hardline_check("run_shell_command", args, shell_spec=cmd_spec(policy=True)) is not None


def test_a_posix_body_is_still_judged_by_the_posix_floor_when_policy_is_off():
    """The old floor keeps its reach: this stage adds a grammar, it does not swap one out."""
    args = {"command": "rm -rf /"}
    assert hardline_check("run_shell_command", args) is not None
    assert hardline_check("run_shell_command", args, shell_spec=cmd_spec(policy=False)) is not None


def test_no_spec_at_all_runs_todays_floor():
    """Every caller outside the shell planner names no spec, and must be unaffected."""
    assert hardline_check("run_shell_command", {"command": "rm -rf /"}) is not None
    assert hardline_check("run_shell_command", {"command": "echo hi"}) is None


# ------------------------------------------------------------------ TOK-02


@pytest.mark.parametrize(
    "body",
    [
        "echo %PATH%",  # environment variable
        "echo %1",  # batch parameter
        "echo %*",  # all parameters
        "echo %~1",  # parameter with modifiers
        "echo !VAR!",  # delayed expansion under /v:on
        "type %USERPROFILE%\\notes.txt",
    ],
)
def test_any_dynamic_token_anywhere_refuses(body):
    """TOK-02, cmd row: cmd substitutes before it parses, so no position is safe.

    Unlike PowerShell, where an expansion becomes one argument, and unlike bash, where it is
    split on IFS, a cmd variable can introduce a separator, a redirect or a second command.
    There is no position where knowing a token is dynamic is enough to keep reading.
    """
    assert scan_cmd(body) == "hardline:cmd-opaque:TOK-02"


def test_a_body_with_no_dynamic_token_is_not_refused_for_being_dynamic():
    """The rule has to be passable, or it is a ban on cmd rather than a floor."""
    assert scan_cmd("echo hello") is None
    assert scan_cmd("dir /b") is None


def test_a_percent_that_is_not_a_variable_does_not_refuse():
    """`50%` in text is not an expansion, and refusing it would teach the wrong lesson."""
    assert scan_cmd("echo done 50% complete") is None


# ------------------------------------------------------------------ CMD-01


@pytest.mark.parametrize("keyword", sorted(CMD_CONTROL))
def test_every_control_keyword_refuses_in_command_position(keyword):
    """CMD-01: control flow decides which line is read, and read time is expansion time."""
    reason = scan_cmd(f"{keyword} something")
    assert reason is not None and "CMD-01" in reason


def test_a_control_word_used_as_an_argument_does_not_refuse():
    """The rule is about command position. Banning the letters would refuse `echo if`."""
    assert scan_cmd("echo if") is None
    assert scan_cmd("echo do or do not") is None


def test_a_keyword_after_a_separator_still_refuses():
    """Command position is also whatever follows a separator, not only the start."""
    reason = scan_cmd("echo hi & goto end")
    assert reason is not None and "CMD-01" in reason


@pytest.mark.parametrize("body", ["(echo a & echo b)", "echo a & (echo b)", "echo )"])
def test_any_grouping_parenthesis_refuses(body):
    """CMD-01: including the unbalanced one.

    A lone `)` is a syntax error to cmd rather than a literal, so treating it as harmless
    would be a guess about how the interpreter recovers from something it rejected.
    """
    assert scan_cmd(body) == "hardline:cmd-opaque:CMD-01:grouping"


@pytest.mark.parametrize("body", ['echo "(hello)"', "echo ^(hello^)"])
def test_a_quoted_or_escaped_parenthesis_is_a_literal(body):
    """cmd has one quote character and one escape character, and both make a paren literal."""
    assert scan_cmd(body) is None


# ------------------------------------------------------------------ the dangerous table


@pytest.mark.parametrize(
    "body,expected",
    [
        ("format C:", "hardline:format-volume"),
        ("format D: /fs:ntfs /q", "hardline:format-volume"),
        ("cipher /w:C:\\", "hardline:cipher-wipe"),
        ("vssadmin delete shadows /all /quiet", "hardline:shadow-copy-delete"),
        ("wmic shadowcopy delete", "hardline:shadow-copy-delete"),
        ("manage-bde -off C:", "hardline:bitlocker-disable"),
        ("del /f /s /q C:\\*", "hardline:delete-drive-root"),
    ],
)
def test_the_windows_irrecoverable_classes_refuse(body, expected):
    """q2: only the counterparts of classes the table already refuses, on the same test.

    The membership criterion is irrecoverable loss, which is why launching a program with a
    URL and clearing a credential store were both declined: they are bad and recoverable.
    """
    assert scan_cmd(body) == expected


@pytest.mark.parametrize(
    "body",
    [
        "start https://example.com",  # the URL-launch class, deliberately not adopted
        "cmdkey /delete:server",  # the credential-store class, deliberately not adopted
        "format-table",  # a word that merely begins with `format`
        "echo format C:",  # the letters as an argument
    ],
)
def test_the_classes_that_were_declined_stay_out(body):
    """A decision not to adopt is a decision, and it has to be visible in the tests too."""
    reason = scan_cmd(body)
    assert reason is None or "opaque" in reason  # may be unreadable, never *dangerous*


def test_a_dangerous_word_inside_quotes_is_not_a_command():
    """The same position test the POSIX floor uses: quoted text is an argument, not a verb."""
    assert scan_cmd('echo "format C:"') is None


def test_the_dangerous_table_is_reported_before_unreadability():
    """A body refused for what it does should say so, not merely that it could not be read."""
    assert scan_cmd("format C: & goto end") == "hardline:format-volume"


def test_a_dynamic_target_is_unreadable_rather_than_dangerous():
    """`format %SYSTEMDRIVE%` is refused, but not as a format: nobody knows what it formats.

    Reporting the dangerous class here would name a specific irrecoverable act on evidence
    that does not exist — the argument is decided after this floor has finished looking.
    """
    assert scan_cmd("format %SYSTEMDRIVE%") == "hardline:cmd-opaque:TOK-02"


# ------------------------------------------------------------------ NAME-01


def test_the_internal_command_table_holds_the_keywords_that_are_commands():
    """NAME-01: a bare word resolves against this table before any PATH search.

    `if`, `for`, `goto` and `call` are commands in their own right, so a table missing one
    would make that word look like an external program to go and search PATH for. `do` and
    `else` are *not* — they are syntax inside `for` and `if`, never a command position — so
    they belong to the control set and not to this one. Asserting the whole control set were
    internal commands is the kind of tidy-looking claim that is simply untrue.
    """
    assert {"if", "for", "goto", "call"} <= CMD_INTERNAL
    assert not ({"do", "else"} & CMD_INTERNAL)
    for expected in ("echo", "set", "start", "del", "rd", "copy", "move", "type"):
        assert expected in CMD_INTERNAL


def test_quoting_an_argument_does_not_hide_it_from_the_table():
    """Quotes are how a person writes a path, not a way of meaning something else.

    `del /f /s /q "C:\\*"` is the same act as the unquoted spelling. A table that reads only
    the unquoted form is a table one keystroke away from silence.
    """
    assert scan_cmd('del /f /s /q "C:\\*"') == "hardline:delete-drive-root"
    assert scan_cmd('format "C:"') == "hardline:format-volume"


def test_quoting_a_separator_is_different_and_still_hides_it():
    """There the quotes change what the line does: this runs no format at all.

    Treating both kinds of quoting the same way would trade one silence for a false positive
    on text, which is the failure the command-position anchor already exists to avoid.
    """
    assert scan_cmd('echo "a & format C:"') is None
    assert scan_cmd('echo "format C:"') is None
