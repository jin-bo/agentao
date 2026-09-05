"""PowerShell lowering: parse first, and refuse everything the grammar does not close over.

PR-2 of the PowerShell ladder. ``LOWER-01`` through ``LOWER-04`` are defined once each in
``docs/design/powershell-support-spec.zh.md`` §2.

**What this does and does not do.** It lowers a script to literal argv, or refuses. It makes
no judgement about whether a command is safe — that is the trusted table's job, and it can
only do that job if the argv it is handed is the argv PowerShell will actually see. Every
refusal here is a case where those two could differ.

**Why an allowlist of node kinds rather than a denylist of dangerous ones.** ``$Function:git
= { … }`` forms no command node and passes no arguments, so a command-level rule never sees
it at all. The closure has to come from the shape of the tree, not from the commands in it.
A named node kind that is not on the list refuses the body, which also means a grammar
upgrade that renames a node fails closed rather than silently widening what is accepted.

Ported from codex's ``powershell_tree_sitter.rs`` so the 68-case corpus in
``tests/fixtures/powershell_lowering.json`` grades this implementation and not a paraphrase
of it. Where the two differ, that corpus is the arbiter.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import List, Optional, Sequence, Tuple

from ._windows import WINDOWS_DANGEROUS

# LOWER-02: exactly these named kinds are understood; every other one refuses the body.
# `comment` is here only because the `#requires` step has already run — a comment that could
# execute has been refused before this list is consulted.
ALLOWED_KINDS = frozenset(
    {
        "program",
        "statement_list",
        "pipeline",
        "pipeline_chain",
        "pipeline_chain_tail",
        "command",
        "command_name",
        "command_elements",
        "command_argument_sep",
        "command_parameter",
        "generic_token",
        "array_literal_expression",
        "unary_expression",
        "string_literal",
        "verbatim_string_characters",
        "expandable_string_literal",
        "integer_literal",
        "decimal_integer_literal",
        "comment",
        "empty_statement",
        # Negative numeric flags such as `git log -1` arrive under this wrapper.
        "expression_with_unary_operator",
    }
)

# LOWER-01 step 1. PowerShell treats these as syntax even where tree-sitter leaves them inside
# a generic token, so the whole spelling family stays opaque rather than being guessed at
# position by position.
UNICODE_SYNTAX_ALIASES = "‘’“”–—―"

# Characters that cannot appear in a bare word without changing what the word is.
_REJECTED_BARE = set("$@'\"(){}[];|&><,")


class LoweringError(Exception):
    """A refusal, carrying which of the ten steps produced it."""

    def __init__(self, step: int, detail: str) -> None:
        super().__init__(f"LOWER-01:{step}:{detail}")
        self.step = step
        self.detail = detail


@lru_cache(maxsize=1)
def _parser():
    """The tree-sitter parser, or ``None`` when the grammar is not installed.

    Absence is not an error here. The parser is a Windows-only runtime dependency because the
    PowerShell rungs only exist there; on every other platform this returns ``None`` and the
    caller refuses, which is the same answer it would give for an unreadable script.

    Built once. Loading the grammar and constructing a ``Parser`` on every call put a
    dynamic-library load on the permission path of every shell command on a PowerShell rung,
    and the object is stateless across ``parse`` calls.
    """
    try:
        import tree_sitter_powershell
        from tree_sitter import Language, Parser
    except ImportError:  # pragma: no cover - exercised by the platform without the wheel
        return None
    return Parser(Language(tree_sitter_powershell.language()))


def parser_available() -> bool:
    return _parser() is not None


# ------------------------------------------------------------------ step 2


def _token_separator(ch: str) -> bool:
    return ch.isspace() or ch in "|&;#><(){}"


def _inline_double_dash_equals(script: str) -> List[int]:
    """Character indices of the ``=`` in bare ``--flag=value`` tokens.

    The grammar rejects the native spelling, so exactly one byte is replaced with a space
    before parsing. One byte, and a space rather than a deletion, because step 8 compares the
    parsed node ranges against the *original* source — any edit that moved an offset would
    make that comparison meaningless, which is the check that catches everything the tree
    silently dropped.
    """
    found: List[int] = []
    start = 0

    def consider(begin: int, end: int) -> None:
        token = script[begin:end]
        if not token.startswith("--"):
            return
        if any(ch.isspace() or ch in _REJECTED_BARE or ch in "`#" for ch in token):
            return
        relative = token.find("=")
        if relative > 2 and relative + 1 < len(token):
            found.append(begin + relative)

    for index, ch in enumerate(script):
        if _token_separator(ch):
            consider(start, index)
            start = index + 1
    consider(start, len(script))
    return found


# ------------------------------------------------------------------ step 7


def _parse_single_quoted(chars: Sequence[str], start: int) -> Tuple[str, int]:
    value: List[str] = []
    index = start + 1
    while index < len(chars):
        if chars[index] == "'":
            if index + 1 < len(chars) and chars[index + 1] == "'":
                value.append("'")
                index += 2
                continue
            return "".join(value), index + 1
        value.append(chars[index])
        index += 1
    raise LoweringError(7, "unterminated single-quoted string")


_BACKTICK = {"0": "\0", "a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v"}


def _parse_double_quoted(chars: Sequence[str], start: int) -> Tuple[str, int]:
    value: List[str] = []
    index = start + 1
    while index < len(chars):
        ch = chars[index]
        if ch == '"':
            return "".join(value), index + 1
        if ch == "$":
            # An expandable string's runtime value is not knowable here, and the whole point
            # of this step is that the lowered argv equals the argv PowerShell will build.
            raise LoweringError(7, "expandable string contains variable syntax")
        if ch == "`":
            if index + 1 >= len(chars):
                raise LoweringError(7, "trailing PowerShell escape")
            escaped = chars[index + 1]
            if escaped == "e":
                # `e is ESC only from PowerShell 6 on, so it has no version-neutral value.
                raise LoweringError(7, "PowerShell-version-dependent escape")
            if escaped == "u" and index + 2 < len(chars) and chars[index + 2] == "{":
                raise LoweringError(7, "PowerShell Unicode escape")
            value.append(_BACKTICK.get(escaped, escaped))
            index += 2
            continue
        value.append(ch)
        index += 1
    raise LoweringError(7, "unterminated double-quoted string")


def _parse_bare_word(chars: Sequence[str], start: int) -> Tuple[str, int]:
    value: List[str] = []
    index = start
    while index < len(chars) and not chars[index].isspace():
        ch = chars[index]
        if ch == "`":
            if index + 1 >= len(chars):
                raise LoweringError(7, "trailing PowerShell escape")
            escaped = chars[index + 1]
            if escaped == "e":
                raise LoweringError(7, "PowerShell-version-dependent escape")
            value.append(_BACKTICK.get(escaped, escaped))
            index += 2
            continue
        if ch in _REJECTED_BARE:
            raise LoweringError(7, f"dynamic or structural bare character: {ch!r}")
        value.append(ch)
        index += 1
    if not value:
        raise LoweringError(7, "empty bare word")
    return "".join(value), index


def _reject_unsupported_bare_word(word: str) -> None:
    """Forms whose runtime value needs PowerShell's own conversion rules to predict."""
    if word.startswith("-") and not word.startswith("--") and ":" in word:
        raise LoweringError(7, "attached PowerShell parameter value")
    if word[:1].isdigit() and not (
        word == "0" or (not word.startswith("0") and word.isdigit())
    ):
        # Tree-sitter leaves several PowerShell numeric spellings as generic tokens, and their
        # runtime string is not the source string. Only the canonical decimal survives.
        raise LoweringError(7, "non-canonical numeric-leading bare word")


def _lower_command_text(command_text: str) -> List[str]:
    """Literal argv lowering for one command node. Not a safety judgement."""
    words: List[str] = []
    chars = list(command_text.strip())
    index = 0
    while index < len(chars):
        while index < len(chars) and chars[index].isspace():
            index += 1
        if index == len(chars) or chars[index] == "#":
            break
        if chars[index] == "'":
            word, nxt = _parse_single_quoted(chars, index)
            bare = False
        elif chars[index] == '"':
            word, nxt = _parse_double_quoted(chars, index)
            bare = False
        else:
            word, nxt = _parse_bare_word(chars, index)
            bare = True
        index = nxt
        if index < len(chars) and not chars[index].isspace() and chars[index] != "#":
            # `'a'b` is one PowerShell word built from two pieces, and its value depends on
            # rules this lowering does not reproduce.
            raise LoweringError(7, "adjacent/concatenated command elements")
        if not word:
            raise LoweringError(7, "empty word")
        if bare:
            _reject_unsupported_bare_word(word)
        words.append(word)
    if not words:
        raise LoweringError(7, "command lowered to no words")
    return words


# ------------------------------------------------------------------ step 8


def _char_at(script: str, index: int) -> str:
    return script[index]


def _source_is_covered(script: str, ranges: Sequence[Tuple[int, int]]) -> bool:
    """LOWER-03: a stateful walk proving every byte is either a command or an understood joiner.

    The command nodes alone are not enough. Anything the tree dropped — a construct the
    grammar recovered from, a separator in a position that means something else — lives in
    the gaps between them, and this is what refuses to ignore those gaps.

    Separators are allowed by *position*, not by membership in a character set: a stray ``)``
    belongs to any plausible permitted set and still has to be refused, which is why this is a
    walk carrying ``can_chain``, ``needs_command`` and ``paren_depth`` rather than a filter.
    """
    index = 0
    range_index = 0
    can_chain = False
    needs_command = False
    paren_depth = 0
    while index < len(script):
        if range_index < len(ranges) and index == ranges[range_index][0]:
            index = ranges[range_index][1]
            range_index += 1
            can_chain = True
            needs_command = False
            continue
        ch = _char_at(script, index)
        nxt = index + 1
        if ch in "\r\n":
            can_chain = False
            index = nxt
            continue
        if ch.isspace():
            index = nxt
            continue
        if ch == ";":
            if needs_command:
                return False
            can_chain = False
            index = nxt
            continue
        if ch == "(" and not can_chain:
            paren_depth += 1
            index = nxt
            continue
        if ch == ")" and paren_depth > 0 and not needs_command:
            paren_depth -= 1
            index = nxt
            continue
        if ch == "|" and can_chain:
            can_chain = False
            needs_command = True
            index = nxt + 1 if script[nxt : nxt + 1] == "|" else nxt
            continue
        if ch == "&" and can_chain and script[nxt : nxt + 1] == "&":
            can_chain = False
            needs_command = True
            index = nxt + 1
            continue
        if ch == "#" and not needs_command:
            # `#` opens a comment only at a token boundary. Tree-sitter can split an embedded
            # `#` out of a bare token, and accepting that would drop the rest of the line.
            if index > 0 and not (script[index - 1].isspace() or script[index - 1] == ";"):
                return False
            newline = min(
                (p for p in (script.find("\r", nxt), script.find("\n", nxt)) if p != -1),
                default=-1,
            )
            index = len(script) if newline == -1 else newline
            continue
        if script.startswith("<#", index) and not needs_command and (
            index == 0 or script[index - 1].isspace() or script[index - 1] == ";"
        ):
            end = script.find("#>", nxt)
            if end == -1:
                return False
            index = end + 2
            continue
        return False
    return range_index == len(ranges) and not needs_command and paren_depth == 0


# ------------------------------------------------------------------ LOWER-01


def lower_powershell(script: str) -> List[List[str]]:
    """The ten steps, in order. Raises :class:`LoweringError` naming the step that refused.

    Returns one literal argv per command node. A caller that gets a value back may rely on it
    being what PowerShell will run; a caller that gets an exception knows only that this
    script is not readable, never that it is dangerous.
    """
    # 1 — Unicode syntax aliases
    if any(ch in UNICODE_SYNTAX_ALIASES for ch in script):
        raise LoweringError(1, "PowerShell Unicode syntax alias")

    parser = _parser()
    if parser is None:
        raise LoweringError(0, "tree-sitter-powershell is not installed")

    # 2 — mask the `=` of `--flag=value`, one byte each, offsets preserved for step 8
    masked = list(script)
    for position in _inline_double_dash_equals(script):
        masked[position] = " "
    parse_source = "".join(masked)

    tree = parser.parse(parse_source.encode("utf-8"))
    root = tree.root_node

    # 3 — a recovered parse is a parse of something else
    if root.has_error:
        raise LoweringError(3, "tree contains ERROR or missing nodes")

    # 4 — `#requires` runs before the script body and can load modules or assemblies
    if _has_requires(root, script):
        raise LoweringError(4, "requires directive executes before the body")

    # 5 — the node-kind allowlist
    unknown = _first_unrecognized_kind(root)
    if unknown is not None:
        raise LoweringError(5, f"unrecognized named node: {unknown}")

    # 6 — a script with no command node has nothing for the command rules to read
    command_nodes = _collect_commands(root)
    if not command_nodes:
        raise LoweringError(6, "no literal command nodes")

    # 7 — literal argv, per command node
    data = script.encode("utf-8")
    commands: List[List[str]] = []
    ranges: List[Tuple[int, int]] = []
    for node in command_nodes:
        text = data[node.start_byte : node.end_byte].decode("utf-8")
        ranges.append((len(data[: node.start_byte].decode("utf-8")),
                       len(data[: node.end_byte].decode("utf-8"))))
        commands.append(_lower_command_text(text))

    # 8 — everything outside those ranges must be a joiner this walk understands
    if not _source_is_covered(script, ranges):
        raise LoweringError(8, "source outside literal command nodes")

    # 9 — `using` is resolved by the engine before the body runs
    if any(cmd and cmd[0].lower() == "using" for cmd in commands):
        raise LoweringError(9, "using declaration requires the PowerShell AST oracle")

    # 10 — an empty command or an empty word has no command word to look up
    if any(not cmd or any(not word for word in cmd) for cmd in commands):
        raise LoweringError(10, "empty lowered command or word")

    return commands


def _collect_commands(root) -> List[object]:
    """Every ``command`` node, without recursing into one. Script nesting is model-controlled,
    so the traversal stays off the call stack."""
    found: List[object] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "command":
            found.append(node)
            continue
        stack.extend(reversed(node.named_children))
    return found


def _has_requires(root, script: str) -> bool:
    data = script.encode("utf-8")
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "comment":
            text = data[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
            if text.lstrip().lower().startswith("#requires"):
                return True
        stack.extend(node.named_children)
    return False


def _first_unrecognized_kind(root) -> Optional[str]:
    stack = [root]
    while stack:
        node = stack.pop()
        if node.is_named and node.type not in ALLOWED_KINDS:
            return node.type
        stack.extend(node.named_children)
    return None


_WINDOWS_DANGEROUS_COMPILED = [
    (re.compile(p, re.IGNORECASE), d) for p, d in WINDOWS_DANGEROUS
]


def commands_of(body: str) -> List[List[str]]:
    """The lowered commands, one literal argv each. Raises :class:`LoweringError` like the
    pipeline it delegates to — this dialect's split failure has a step number, unlike the
    other two, and flattening that into ``None`` would throw away which step refused."""
    return lower_powershell(body)


def scan_powershell(body: str) -> Optional[str]:
    """The PowerShell floor: lower, then the dangerous table over each lowered command.

    Returning ``None`` means the script lowered cleanly and refuses no class, which is the
    point at which the trusted table takes over. It is not a verdict that the script is safe.

    The dangerous table runs against each command's own reconstructed line rather than the
    body text, so it needs no command-position anchor: lowering has already separated the
    commands, and a class matched inside one of them is matched in command position by
    construction. That is also why the table is shared with cmd rather than duplicated —
    ``Format-Volume`` destroys the same bytes whichever interpreter typed it (``_windows``).
    """
    if not body.strip():
        return None
    try:
        commands = lower_powershell(body)
    except LoweringError as exc:
        return f"hardline:powershell-opaque:{exc.step}:{exc.detail}"
    for argv in commands:
        line = " ".join(argv)
        for pattern, description in _WINDOWS_DANGEROUS_COMPILED:
            # ``match``, not ``search``: the class has to *start* the command. Searching the
            # whole line reads `Write-Output Format-Volume` as a format, which is the same
            # false positive cmd's command-position anchor exists to prevent — here the
            # anchor is free, because lowering has already cut the body into commands.
            if pattern.match(line) is not None:
                return description
    return None
