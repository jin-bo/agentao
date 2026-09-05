"""The cmd floor: a regex dialect, refusing everything it cannot read statically.

PR-2 of the PowerShell ladder. Rules ``CMD-01``, ``TOK-02``, ``NAME-01`` and the Windows
half of the dangerous table are defined once each in
``docs/design/powershell-support-spec.zh.md`` §2.

**Why cmd gets its own floor rather than the POSIX one.** The existing floor is written for
POSIX shell syntax and, run against a cmd command line, matches almost nothing: its patterns
key on ``rm``, ``dd``, ``mkfs``, ``$(…)`` and ``&&`` in POSIX quoting. On Windows that does
not fail loudly — it returns clean. A floor that reports clean because it was reading the
wrong grammar is worse than no floor, because everything downstream treats the answer as
having been checked.

**Why it is deliberately blunt.** cmd expands variables at *read* time, not at execution
time, and control flow decides which line is read. So a body containing any variable form,
any control keyword, or any grouping parenthesis cannot be reasoned about statically at all,
and every one of those is refused outright rather than parsed. That refuses a great deal of
legitimate scripting. The alternative is a floor that believes it understands text whose
meaning is decided after it has finished looking.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from ..capabilities.shell_spec import ShellDialect
from ._windows import WINDOWS_DANGEROUS
from ._wrappers import classify

# NAME-01: cmd's internal commands. A bare word resolves here first, before any PATH search,
# so the table is part of deciding what a command word even means. Sourced from `help` on
# Windows 10/11; `keys` is included although it is undocumented in recent builds.
CMD_INTERNAL = frozenset(
    """assoc break call cd chdir cls color copy date del dir dpath echo endlocal erase exit
    for ftype goto if keys md mkdir mklink move path pause popd prompt pushd rd rem ren
    rename rmdir set setlocal shift start time title type ver verify vol""".split()
)

# CMD-01: control flow decides which lines are read, and read time is also expansion time,
# so a body containing any of these cannot be reasoned about by looking at it.
CMD_CONTROL = frozenset({"if", "else", "for", "do", "goto", "call"})

# TOK-02, cmd row: *any* dynamic token, in *any* position. Unlike PowerShell (where an
# expansion is one argument) and bash (where it is split by IFS), cmd substitutes into the
# line before it parses it, so a variable can introduce a separator, a redirect, or a whole
# second command. There is no position where knowing the token is dynamic is enough.
_DYNAMIC = re.compile(
    r"""
    %[A-Za-z_][A-Za-z0-9_()]*%      # %VAR%            environment, substituted at read time
  | %~?[0-9*]                       # %1..%9 %* %~1    batch parameters
  | %%?~?[A-Za-z]\b                 # %A / %%A         FOR iteration variable
  | ![A-Za-z_][A-Za-z0-9_]*!        # !VAR!            delayed expansion, under /v:on
    """,
    re.VERBOSE,
)

# A command starts the body, or follows a separator. Without this anchor `echo format C:`
# reads as a format — the same false positive the POSIX table's own command-position anchor
# exists to prevent, and the reason a floor that only searches for words is unusable.
_CMDPOS = r"(?:(?<=^)|(?<=[&|(\n])|(?<=[&|(\n] ))\s*"

# The table itself lives in ``_windows`` (PR-4): what a class refuses is a property of the
# platform, and reading it only here left every one of its entries unreachable from a
# PowerShell rung. The anchor stays, because command position is a question about *this*
# grammar's separators.
_CMD_DANGEROUS_COMPILED = [
    (re.compile(_CMDPOS + p, re.IGNORECASE | re.MULTILINE), d) for p, d in WINDOWS_DANGEROUS
]


def _unquoted_spans(body: str) -> List[Tuple[int, int]]:
    """Half-open ranges of ``body`` that are outside double quotes and not caret-escaped.

    cmd has one quoting character and one escape character, and both are needed here: a
    parenthesis inside quotes or after ``^`` is a literal, and refusing on it would reject
    ``echo (hello)``, which is not a grouping at all.
    """
    spans: List[Tuple[int, int]] = []
    start, i, in_quotes = 0, 0, False
    while i < len(body):
        ch = body[i]
        if ch == "^" and not in_quotes:
            # The caret makes the next character a literal, so it has to leave the span —
            # ending it here and resuming after. Merely advancing the index would keep the
            # escaped character in the unquoted text, which is how `echo ^(hello^)` came to
            # read as a grouping parenthesis.
            spans.append((start, i))
            i += 2
            start = i
            continue
        if ch == '"':
            if not in_quotes:
                spans.append((start, i))
            else:
                start = i + 1
            in_quotes = not in_quotes
        i += 1
    if not in_quotes:
        spans.append((start, len(body)))
    return spans


def _outside_quotes(body: str) -> str:
    """``body`` with quoted and escaped stretches blanked, offsets preserved."""
    out = [" "] * len(body)
    for a, b in _unquoted_spans(body):
        for i in range(a, min(b, len(body))):
            out[i] = body[i]
    return "".join(out)


# The characters quoting is allowed to hide. A quoted stretch containing one of these is
# hiding structure and stays blanked; a quoted stretch that is only an argument is not.
_STRUCTURAL = set("&|;()")


def _arguments_unquoted(body: str) -> str:
    """``body`` with argument quoting removed, and structural quoting still blanked.

    Quoting an argument must not hide it from the dangerous table: ``del /f /s /q "C:\\*"``
    is the same act as the unquoted spelling, and the quotes are how a person writes a path,
    not a way of meaning something else.

    Quoting a *separator* is different — there the quotes change what the line does, and
    ``echo "a & format C:"`` runs no format at all. So a quoted stretch is only unwrapped
    when it holds none of the characters that would otherwise be structure. Offsets are
    preserved throughout, which is what lets the command-position anchor keep working.
    """
    out = list(_outside_quotes(body))
    spans = _unquoted_spans(body)
    for (_, end), (start, _) in zip(spans, spans[1:]):
        # Between one unquoted span's end and the next one's start lies a quoted stretch,
        # bracketed by its two quote characters.
        inner = body[end + 1 : start - 1] if start - 1 > end else ""
        if inner and not (_STRUCTURAL & set(inner)):
            for i in range(end + 1, start - 1):
                out[i] = body[i]
    return "".join(out)


def has_dynamic_token(body: str) -> bool:
    """TOK-02: whether the body contains any token whose value is not known statically."""
    return _DYNAMIC.search(body) is not None


def _control_keyword(bare: str) -> Optional[str]:
    """CMD-01: a control keyword in command position, or ``None``.

    Command position is the start of the body or just after a separator. Matching the words
    anywhere would refuse ``echo if``, and the point is not to ban the letters.
    """
    for m in re.finditer(r"(?:^|[&|(\n])\s*([A-Za-z]+)", bare):
        if m.group(1).lower() in CMD_CONTROL:
            return m.group(1).lower()
    return None


def scan_cmd(body: str, depth: int = 0) -> Optional[str]:
    """The cmd floor. Returns a ``hardline:`` reason, or ``None`` if nothing refused it.

    Order matters and follows the rule numbering: the dangerous table first, so a body that
    is refused for *what it does* says so rather than being reported as merely unreadable,
    then the static-readability refusals.
    """
    if not body.strip():
        return None
    bare = _outside_quotes(body)

    # The dangerous table reads a view where argument quoting is removed but structural
    # quoting is not: `del /q "C:\\*"` must be caught, and `echo "a & format C:"` must not.
    readable = _arguments_unquoted(body)
    for pattern, description in _CMD_DANGEROUS_COMPILED:
        m = pattern.search(readable)
        if m is not None:
            return description

    if has_dynamic_token(body):
        return "hardline:cmd-opaque:TOK-02"

    keyword = _control_keyword(bare)
    if keyword is not None:
        return f"hardline:cmd-opaque:CMD-01:{keyword}"

    if "(" in bare or ")" in bare:
        # Any grouping parenthesis, including an unbalanced one. `)` alone is a syntax error
        # to cmd rather than a literal, so treating it as harmless would be a guess about
        # how the interpreter recovers.
        return "hardline:cmd-opaque:CMD-01:grouping"

    # WRAP-01 / WRAP-05, at every command position and on the *resolved* word: `echo hi &
    # start notepad` hands work to an unseen process and `"cmd" /c del C:\*` starts a second
    # interpreter, and reading only the first token of a body misses both. It runs last
    # because it needs the split, and the split is only sound on text every rule above has
    # accepted — a body with a dynamic token has no fixed words to classify.
    for words in commands_of(body):
        nested = classify(words[0], words[1:], ShellDialect.CMD, depth + 1)
        if nested is not None:
            return nested

    return None


def commands_of(body: str) -> List[List[str]]:
    """The command positions of a body :func:`scan_cmd` has already accepted, as literal words.

    Only ever called on text that gate has passed, which is what makes a tokenizer this small
    correct: every dynamic form, every control keyword and every grouping parenthesis has
    already been refused, so what is left is words, double quotes, caret escapes and the three
    separators. A quoted stretch contributes its content and not its quotes; a caret takes the
    next character literally. Both of those are how a person writes a path with a space or a
    literal ``&``, and reading them any other way would hand the effect table a word cmd will
    never see.
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

    index, in_quotes = 0, False
    while index < len(body):
        ch = body[index]
        if ch == "^" and not in_quotes:
            if index + 1 < len(body):
                current.append(body[index + 1])
                started = True
            index += 2
            continue
        if ch == '"':
            in_quotes = not in_quotes
            started = True  # `""` is an empty argument, not an absent one
            index += 1
            continue
        if not in_quotes and ch in "&|\n":
            end_command()
        elif not in_quotes and ch.isspace():
            end_word()
        else:
            current.append(ch)
            started = True
        index += 1
    end_command()
    return commands
