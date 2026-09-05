"""Nested launches and process spawners: parsed so they can be refused precisely.

PR-2 of the PowerShell ladder. ``WRAP-01`` through ``WRAP-06`` are defined once each in
``docs/design/powershell-support-spec.zh.md`` §2. (``WRAP-07``, the prefix runners, lives in
the trusted table instead, because those are not wrappers — they run their argument tail
without starting a second interpreter.)

**Re-entry buys a refusal, not an approval.** An interpreter started by the child process
carries none of the guarantees this design puts on the one agentao starts: not the pinned
environment, not the filtered search path, not the attested image. So a nested interpreter
launch is opaque no matter how harmless its body looks. Parsing the body anyway is worth
doing for one reason — a dangerous nested body should be refused by its *own* reason, which
is what a person reading the denial needs.

**Spawners are opaque for the same reason, one step further out.** ``Start-Process`` and its
family hand the work to a process this floor never sees, launched by a mechanism that does
not route through the request agentao built.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from ..capabilities.shell_spec import ShellDialect

# WRAP-01: the command words that start another interpreter and hand it a body. The value is
# the dialect the body will be read in, which is what makes a re-entry meaningful rather than
# a second pass with the wrong grammar.
WRAPPER_DIALECTS = {
    "sh": ShellDialect.POSIX,
    "bash": ShellDialect.POSIX,
    "zsh": ShellDialect.POSIX,
    "dash": ShellDialect.POSIX,
    "ash": ShellDialect.POSIX,
    "ksh": ShellDialect.POSIX,
    "csh": ShellDialect.POSIX,
    "tcsh": ShellDialect.POSIX,
    "busybox": ShellDialect.POSIX,
    "pwsh": ShellDialect.POWERSHELL,
    "powershell": ShellDialect.POWERSHELL,
    "cmd": ShellDialect.CMD,
}

# WRAP-05: commands whose whole purpose is to start a process this floor will never see.
# `Invoke-Command`'s remote parameter sets are listed separately because the local form is an
# ordinary cmdlet — the parameter is what moves the work out of reach.
SPAWNERS = {
    ShellDialect.POWERSHELL: frozenset(
        {"start-process", "saps", "start", "invoke-item", "ii", "start-job", "sajb"}
    ),
    ShellDialect.CMD: frozenset({"start"}),
    ShellDialect.POSIX: frozenset(),
}

INVOKE_COMMAND_REMOTE_PARAMETERS = frozenset(
    {
        "-computername",
        "-session",
        "-connectionuri",
        "-vmid",
        "-vmname",
        "-containerid",
        "-hostname",
        "-sshconnection",
    }
)


@dataclass(frozen=True)
class NestedLaunch:
    """A wrapper that was recognised, and the body it hands on."""

    callee: ShellDialect
    body: Optional[str]  # None when the body is not statically known (a file, or a pipe)
    reason: str


# ------------------------------------------------------------------ WRAP-02

# PowerShell's launcher matches switches by prefix, so every one of these is reachable by any
# unambiguous abbreviation. The documented short forms are listed explicitly because a bare
# `-c` is ambiguous by pure prefix rules and PowerShell resolves it to `-Command`.
_PS_SWITCHES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("command", ("c",)),
    ("commandwithargs", ("cwa",)),
    ("encodedcommand", ("e", "ec")),
    ("file", ("f",)),
    ("noprofile", ("nop",)),
    ("nologo", ("nol",)),
    ("noninteractive", ("noni",)),
    ("noexit", ("noe",)),
    ("executionpolicy", ("ex",)),
    ("windowstyle", ("w",)),
)

# Switches that are noise for this purpose: they change how the interpreter behaves, not what
# it runs. Two of them take a value, which has to be consumed or it reads as the body.
_CONSUMED = {"noprofile", "nologo", "noninteractive", "noexit"}
_CONSUMED_WITH_VALUE = {"executionpolicy", "windowstyle"}


def resolve_powershell_switch(token: str) -> Optional[str]:
    """WRAP-02: the canonical switch a token names, or ``None`` if it names none.

    Exact documented aliases win first, then unambiguous prefix matching. An abbreviation that
    could mean two switches returns ``None``, which the caller turns into a refusal — guessing
    which one the launcher would have picked is exactly the kind of assumption that makes the
    parsed argv differ from the real one.
    """
    if not token or token[0] not in "-/":
        return None
    name = token[1:].lower()
    if not name:
        return None
    for canonical, aliases in _PS_SWITCHES:
        if name in aliases or name == canonical:
            return canonical
    matches = [canonical for canonical, _ in _PS_SWITCHES if canonical.startswith(name)]
    return matches[0] if len(matches) == 1 else None


def parse_powershell_launch(args: Sequence[str]) -> NestedLaunch:
    """WRAP-02: read a PowerShell launcher's argv and say what body it will run.

    Anything not on the switch list refuses. The launcher accepts far more than this, and the
    ones left out are left out because nobody has worked out what they do to the body — which
    is the same standard the trusted table holds its own entries to.
    """
    index = 0
    while index < len(args):
        token = args[index]
        switch = resolve_powershell_switch(token)
        if switch is None:
            if token.startswith("-") or token.startswith("/"):
                return NestedLaunch(ShellDialect.POWERSHELL, None, "WRAP-02:unknown-switch")
            # A bare word here is the script-file positional form, which is `-File` by another
            # spelling and opaque for the same reason: the bytes are not in the command line.
            return NestedLaunch(ShellDialect.POWERSHELL, None, "WRAP-02:file")
        if switch in _CONSUMED:
            index += 1
            continue
        if switch in _CONSUMED_WITH_VALUE:
            index += 2
            continue
        if switch == "file":
            return NestedLaunch(ShellDialect.POWERSHELL, None, "WRAP-02:file")
        rest = list(args[index + 1 :])
        if not rest:
            return NestedLaunch(ShellDialect.POWERSHELL, None, "WRAP-02:no-body")
        if switch == "encodedcommand":
            try:
                body = base64.b64decode(rest[0]).decode("utf-16-le")
            except Exception:  # noqa: BLE001 - any decode failure is an unreadable body
                return NestedLaunch(ShellDialect.POWERSHELL, None, "WRAP-02:undecodable")
            return NestedLaunch(ShellDialect.POWERSHELL, body, "WRAP-02:encodedcommand")
        # `-Command` and `-CommandWithArgs` both take the rest of the line as the body.
        return NestedLaunch(ShellDialect.POWERSHELL, " ".join(rest), f"WRAP-02:{switch}")
    return NestedLaunch(ShellDialect.POWERSHELL, None, "WRAP-02:no-body")


# ------------------------------------------------------------------ WRAP-01 / WRAP-05


def _basename(word: str) -> str:
    """The command word without a path or a Windows executable suffix."""
    tail = word.replace("\\", "/").rsplit("/", 1)[-1].lower()
    for suffix in (".exe", ".cmd", ".bat", ".com"):
        if tail.endswith(suffix):
            return tail[: -len(suffix)]
    return tail


def wrapper_for(word: str) -> Optional[ShellDialect]:
    """WRAP-01: the dialect this word's body would be read in, if it is a wrapper at all."""
    return WRAPPER_DIALECTS.get(_basename(word))


def is_spawner(word: str, args: Sequence[str], dialect: ShellDialect) -> bool:
    """WRAP-05: whether this command hands the work to a process the floor will not see."""
    name = _basename(word)
    if name in SPAWNERS.get(dialect, frozenset()):
        return True
    if name in {"invoke-command", "icm"}:
        # The local form is an ordinary cmdlet. A remote parameter set is what moves the work
        # somewhere none of this design's guarantees reach.
        return any(a.lower() in INVOKE_COMMAND_REMOTE_PARAMETERS for a in args)
    return False


def nested_launch(word: str, args: Sequence[str]) -> Optional[NestedLaunch]:
    """WRAP-01 / WRAP-02 / WRAP-03: what this wrapper hands on, or ``None`` if it is not one.

    The body is returned so a caller can refuse by the nested body's own reason (WRAP-06).
    It is never a reason to allow: the launch itself is opaque either way.
    """
    callee = wrapper_for(word)
    if callee is None:
        return None
    if callee is ShellDialect.POWERSHELL:
        return parse_powershell_launch(list(args))
    if callee is ShellDialect.CMD:
        # WRAP-03: cmd is analysed, not skipped. `/c` and `/k` both take the rest of the line.
        for index, token in enumerate(args):
            if token.lower() in ("/c", "/k", "-c"):
                rest = list(args[index + 1 :])
                return NestedLaunch(callee, " ".join(rest) if rest else None, "WRAP-03:cmd")
        return NestedLaunch(callee, None, "WRAP-03:cmd")
    for index, token in enumerate(args):
        if token == "-c" or (token.startswith("-") and token.endswith("c") and len(token) <= 4):
            rest = list(args[index + 1 :])
            return NestedLaunch(callee, rest[0] if rest else None, "WRAP-01:posix")
    return NestedLaunch(callee, None, "WRAP-01:posix")


MAX_NESTED_DEPTH = 8
"""WRAP-06: a bound on reading nested bodies. ``bash -c "bash -c '…'"`` nests as deep as the
model cares to type, and the body is untrusted input — a stack overflow is not a refusal."""


def _nested_refusal(nested: NestedLaunch, depth: int) -> Optional[str]:
    """WRAP-06: the callee dialect's own refusal for the body it was handed, if any.

    This is the whole reason :func:`nested_launch` recovers the body. A launch is opaque
    either way (WRAP-01 rule 2), so nothing here can allow anything — but
    ``pwsh -Command "Remove-Item -Recurse -Force C:\"`` refused as "starts a second
    interpreter" tells a reader far less than the same call refused as a drive-root delete,
    and the second is what they need to act on.
    """
    if nested.body is None or depth >= MAX_NESTED_DEPTH:
        return None
    if nested.callee is ShellDialect.CMD:
        from ._cmd import scan_cmd

        return scan_cmd(nested.body)
    if nested.callee is ShellDialect.POSIX:
        from ._bash import scan_bash
        from ._scanner import _hardline_match

        # The dangerous table first, then the syntax gate. On this dialect the two live in
        # different places — the classes are the scanner's, the grammar is BASH-01's — and a
        # reader needs "this deletes the root" ahead of "this starts a second interpreter".
        return _hardline_match(nested.body) or scan_bash(nested.body, depth + 1)
    if nested.callee is ShellDialect.POWERSHELL:
        from ._powershell import scan_powershell

        return scan_powershell(nested.body)
    return None


def classify(
    word: str, args: Sequence[str], dialect: ShellDialect, depth: int = 0
) -> Optional[str]:
    """The refusal a wrapper or spawner earns, or ``None`` when this is an ordinary command.

    Both are opaque, and the reason says which and why, because "this was refused" without
    "because it starts a second interpreter" is not something a person can act on. When the
    nested body is statically known and its own dialect refuses it, that refusal is the reason
    reported instead — a dangerous nested body is refused for being dangerous (WRAP-06).
    """
    if is_spawner(word, args, dialect):
        return f"hardline:{dialect.value}-opaque:WRAP-05:{_basename(word)}"
    nested = nested_launch(word, args)
    if nested is not None:
        inner = _nested_refusal(nested, depth)
        return inner or f"hardline:{dialect.value}-opaque:{nested.reason}"
    return None


def split_words(body: str) -> List[str]:
    """A whitespace split adequate for recognising a wrapper's own command word.

    Deliberately not a shell parser. Every dialect's real gate has already refused the bodies
    where the split could be wrong — quoting, expansions, control structures — so this only
    ever runs on text those gates have already agreed is plain.
    """
    return body.split()


# ``classify_body`` lived here until PR-4 and is gone on purpose. It read command words out
# of the *quote-blanked* view, which is safe for finding boundaries and wrong for reading
# text: a quoted argument became whitespace, so the nested body it recovered was not the one
# the child would run — and WRAP-06 needs that body verbatim to refuse it by its own reason.
# Each dialect now splits with its own tokenizer and calls :func:`classify` per command, which
# also resolves the two words the old view could only report as unreadable: ``"cmd"`` and
# ``c^md`` are ``cmd``.
