"""The Git Bash syntax gate: a closed grammar, refused before any command is looked at.

PR-2 of the PowerShell ladder. ``BASH-01`` and ``BASH-01a`` are defined once each in
``docs/design/powershell-support-spec.zh.md`` §2.

**Why a bash rung needs a gate this strong.** PowerShell gets its closedness from a node-kind
allowlist and cmd from refusing every control structure. Without an equivalent here, the
closed set is trivially bypassed by putting the real work somewhere the command word is not:
``echo $(curl … | sh)`` passes the trusted-table check on an inert ``echo``, and the code has
already run before ``echo`` is reached. Nothing about the command word was wrong; the gate
that should have refused the substitution was missing.

**It is blunt on purpose, including about keywords.** Bash recognises a keyword only in
command position, and knowing whether a token *is* in command position requires the split to
be correct, which requires the expansions to be known, which is what this gate exists to
refuse. So an unquoted keyword anywhere refuses the body. That rejects harmless bodies such
as ``echo if``. The alternative is a gate that decides position using assumptions the body
itself can invalidate.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

# BASH-01. Every one of these changes what runs, or changes which words the runner sees, in a
# way that cannot be read off the text.
BASH_KEYWORDS = frozenset(
    """if then elif else fi for while until do done case esac select function coproc
    time !""".split()
) | {"[["}

# BASH-01: the two commands that are gates in their own right, plus the two that also appear
# in the effect table. Listed here as well because this gate runs *before* any table lookup.
BASH_REFUSED_WORDS = frozenset({"trap", "exec", "eval"})

_UNQUOTED, _SINGLE, _DOUBLE = 0, 1, 2


def _quote_states(body: str) -> Optional[List[int]]:
    """One quote state per character, or ``None`` when the quoting never closes.

    Returning ``None`` is BASH-01's "split failed": a body whose quoting does not terminate
    has no reading at all, and guessing which of the possible readings was meant is exactly
    what a floor must not do.
    """
    states: List[int] = []
    state = _UNQUOTED
    i = 0
    while i < len(body):
        ch = body[i]
        if state == _UNQUOTED and ch == "\\":
            states.extend([_SINGLE, _SINGLE][: min(2, len(body) - i)])  # an escaped char is a literal
            i += 2
            continue
        if state == _DOUBLE and ch == "\\" and i + 1 < len(body):
            states.extend([_DOUBLE, _DOUBLE])
            i += 2
            continue
        if state == _UNQUOTED and ch == "'":
            state = _SINGLE
            states.append(_UNQUOTED)  # the quote character itself is punctuation, not content
        elif state == _SINGLE and ch == "'":
            state = _UNQUOTED
            states.append(_UNQUOTED)
        elif state == _UNQUOTED and ch == '"':
            state = _DOUBLE
            states.append(_UNQUOTED)
        elif state == _DOUBLE and ch == '"':
            state = _UNQUOTED
            states.append(_UNQUOTED)
        else:
            states.append(state)
        i += 1
    if state != _UNQUOTED:
        return None
    return states


def _at(states: List[int], index: int) -> int:
    return states[index] if 0 <= index < len(states) else _UNQUOTED


def _find(body: str, states: List[int], needle: str, allowed: Tuple[int, ...]) -> bool:
    """Whether ``needle`` occurs at a position whose quote state is in ``allowed``."""
    start = 0
    while True:
        index = body.find(needle, start)
        if index == -1:
            return False
        if _at(states, index) in allowed:
            return True
        start = index + 1


_WORD = re.compile(r"[^\s;&|<>()]+")

# BASH-01a: expansions that change how many argv entries a word becomes. A quoted "$VAR" is
# exactly one entry and stays a dynamic token for the token rule to handle; these do not.
_UNQUOTED_VAR = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*|[@*#?$!0-9])")
_BRACE_EXPANSION = re.compile(r"\{[^{}]*(?:,|\.\.)[^{}]*\}")
_GLOB = re.compile(r"[*?]|\[[^\]]*\]")


def scan_bash(body: str, depth: int = 0) -> Optional[str]:
    """The Git Bash gate. Returns a ``hardline:`` reason, or ``None`` if the body is readable.

    Runs before any command-level rule, because every one of those reads a command word, and
    the constructs refused here decide what the command words even are.
    """
    if not body.strip():
        return None

    states = _quote_states(body)
    if states is None:
        return "hardline:posix-opaque:BASH-01:unterminated-quote"

    def unquoted_or_double(needle: str) -> bool:
        # Inside single quotes these are literal text. Inside double quotes a substitution
        # still runs, which is the case that makes `echo "$(rm -rf /)"` dangerous.
        return _find(body, states, needle, (_UNQUOTED, _DOUBLE))

    def unquoted_only(needle: str) -> bool:
        return _find(body, states, needle, (_UNQUOTED,))

    # --- BASH-01: constructs that run something the command word does not name
    if unquoted_or_double("$(("):
        return "hardline:posix-opaque:BASH-01:arithmetic-expansion"
    if unquoted_or_double("$("):
        return "hardline:posix-opaque:BASH-01:command-substitution"
    if unquoted_or_double("`"):
        return "hardline:posix-opaque:BASH-01:command-substitution"
    if unquoted_or_double("${"):
        return "hardline:posix-opaque:BASH-01:parameter-expansion"
    if unquoted_only("<(") or unquoted_only(">("):
        return "hardline:posix-opaque:BASH-01:process-substitution"
    if unquoted_only("<<<"):
        return "hardline:posix-opaque:BASH-01:herestring"
    if unquoted_only("<<"):
        return "hardline:posix-opaque:BASH-01:heredoc"
    if unquoted_only("(") or unquoted_only(")"):
        return "hardline:posix-opaque:BASH-01:subshell"
    if unquoted_only("{") or unquoted_only("}"):
        # Grouping and function bodies share this character, and both are refused, so there
        # is nothing to tell apart. Brace expansion is caught below and names itself.
        if not _BRACE_EXPANSION.search(body):
            return "hardline:posix-opaque:BASH-01:grouping"

    for match in re.finditer(r"/dev/(?:tcp|udp)/", body):
        if _at(states, match.start()) != _SINGLE:
            return "hardline:posix-opaque:BASH-01:network-redirect"

    # Everything below asks about *unquoted* text, so work on a copy with the quoted stretches
    # blanked. Scanning words and then searching inside them reads the state of the word's
    # first character, which for `"$FLAGS"` is the quote — punctuation, and unquoted — so a
    # quoted variable was being reported as an unquoted one.
    bare = "".join(ch if st == _UNQUOTED else " " for ch, st in zip(body, states))

    for match in _WORD.finditer(bare):
        word = match.group(0)
        if word in BASH_KEYWORDS:
            return f"hardline:posix-opaque:BASH-01:keyword:{word}"
        if word in BASH_REFUSED_WORDS:
            return f"hardline:posix-opaque:BASH-01:{word}"
        if word.startswith("~"):
            return "hardline:posix-opaque:BASH-01a:tilde-expansion"

    # --- BASH-01a: expansions that change how many argv entries a word becomes
    if _BRACE_EXPANSION.search(bare):
        return "hardline:posix-opaque:BASH-01a:brace-expansion"
    if _UNQUOTED_VAR.search(bare):
        return "hardline:posix-opaque:BASH-01a:unquoted-variable"
    if _GLOB.search(bare):
        return "hardline:posix-opaque:BASH-01a:pathname-expansion"

    # WRAP-01: a second interpreter started by the child carries none of the guarantees this
    # design places on the one agentao starts, so the launch is opaque whatever its body says.
    # This runs last because everything above decides what the words even are.
    from ..capabilities.shell_spec import ShellDialect
    from ._wrappers import classify

    # Every command position, on the *resolved* word: reading only `body.split()[0]` misses
    # `echo hi; sh -c …` entirely and reads `'bash' -c …` as a command called `-c`. The split
    # is this module's own, so a quoted word arrives unquoted and a nested body arrives whole —
    # which is what lets WRAP-06 refuse a dangerous nested body by its own reason.
    for words in commands_of(body) or ():
        nested = classify(words[0], words[1:], ShellDialect.POSIX, depth + 1)
        if nested is not None:
            return nested
    return None


def commands_of(body: str) -> Optional[List[List[str]]]:
    r"""The command positions of a body :func:`scan_bash` has already accepted.

    Its own walk rather than a reuse of :func:`_quote_states`, because that array answers
    "what is this character's quote state" and a tokenizer needs "is this character content".
    The two differ exactly on the punctuation: a quote delimiter and an escaping backslash are
    both syntax, and ``git\ log`` is one word whose text has no backslash in it.

    ``None`` when the quoting never closes — the same answer the gate gives, for the same
    reason: a body with no reading has no command words either.
    """
    commands: List[List[str]] = []
    words: List[str] = []
    current: List[str] = []
    started = False

    def end_word() -> None:
        nonlocal current, started
        if started:
            words.append("".join(current))
        current, started = [], False

    def end_command() -> None:
        nonlocal words
        end_word()
        if words:
            commands.append(words)
        words = []

    index, state = 0, _UNQUOTED
    while index < len(body):
        ch = body[index]
        if state == _UNQUOTED:
            if ch == "\\":
                if index + 1 < len(body):
                    current.append(body[index + 1])
                    started = True
                index += 2
                continue
            if ch == "'":
                state, started = _SINGLE, True
                index += 1
                continue
            if ch == '"':
                state, started = _DOUBLE, True
                index += 1
                continue
            if ch in ";&|\n":
                end_command()
                index += 1
                continue
            if ch.isspace():
                end_word()
                index += 1
                continue
            current.append(ch)
            started = True
            index += 1
            continue
        if state == _SINGLE:
            if ch == "'":
                state = _UNQUOTED
            else:
                current.append(ch)
            index += 1
            continue
        if ch == "\\" and index + 1 < len(body) and body[index + 1] in '$`"\\':
            current.append(body[index + 1])  # inside double quotes only these four escape
            index += 2
            continue
        if ch == '"':
            state = _UNQUOTED
        else:
            current.append(ch)
        index += 1
    if state != _UNQUOTED:
        return None
    end_command()
    return commands
