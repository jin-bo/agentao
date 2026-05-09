"""Shell-context analysis used by the hardline scanner.

Walks raw shell text and reports, for each byte position, which lexical
context bash will treat that byte under (top-level / single-quote /
double-quote / ``$(...)`` cmdsub / backtick cmdsub), plus a separate
"shell-word-unquoted" view used to detect quote-split bypasses such as
``r"m" -rf /`` collapsing to ``rm -rf /`` at execution time.

The :func:`_position_contexts` and :func:`_shell_word_normalize`
walkers are intentionally near-mirrors of one another: same state
machine, same nesting rules, same backslash-escape semantics. Only the
emit policy differs — one records *where* every char lives, the other
decides *whether* to keep each char in the unquoted view.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from ._decode import _decode_ansi_c


def _position_contexts(
    cmd: str,
) -> Tuple[List[Optional[str]], set]:
    """For each position in ``cmd``, return ``(contexts, escaped)``.

    ``contexts[i]`` is the innermost shell context at offset ``i`` and
    takes one of these values:

    - ``None``     — top-level shell text
    - ``"'"``      — inside a single-quoted string (fully literal)
    - ``'"'``      — inside a double-quoted string (literal text, but
                     ``$(...)`` and `` `...` `` substitutions are still
                     evaluated)
    - ``"$("``     — inside a ``$(...)`` command substitution
    - ``"`"``      — inside a backtick ``` `...` ``` command substitution

    ``escaped`` is the set of positions whose preceding ``\\`` made the
    character at that position a literal — ``\\$`` and ``\\``` inside
    a double-quoted string don't actually open a command substitution,
    so the hardline filter must reject matches whose start position is
    in this set (otherwise ``echo "\\$(rm -rf /)"`` is a false positive
    even though it only prints text).

    The hardline post-filter uses both maps to decide whether a matched
    danger position is real shell syntax or literal text:

    - top-level / ``$(`` / ``` ` ``` → real shell, accept the match
    - single quote → fully literal, reject
    - double quote → literal text, but accept the match if its first
      character is ``$`` or ``` ` ``` *and* it isn't in ``escaped`` (it
      opens a *new* substitution)

    The state machine handles arbitrary nesting (``echo "$(echo ok;
    rm -rf /)"`` — the ``;`` inside ``$(...)`` is shell context even
    though the surrounding ``"..."`` is a double-quoted literal). Any
    unclosed construct extends to end-of-string so a malformed input
    never falls back to "treat as top-level shell" by accident.
    """
    n = len(cmd)
    contexts: List[Optional[str]] = [None] * n
    escaped: set = set()
    stack: List[str] = []
    i = 0
    while i < n:
        c = cmd[i]
        cur = stack[-1] if stack else None
        contexts[i] = cur

        # Inside a single quote: only the closing ``'`` matters; nothing
        # else is processed (no escapes, no expansions).
        if cur == "'":
            if c == "'":
                stack.pop()
            i += 1
            continue

        # Backslash escape — consume next char with current context and
        # record it as ``escaped`` so the filter can reject false
        # positives like ``echo "\$(rm -rf /)"``. Single-quote context
        # is already handled above, so the escape rule only fires in
        # top-level / double-quote / cmdsub regions.
        if c == "\\" and i + 1 < n:
            escaped.add(i + 1)
            contexts[i + 1] = cur
            i += 2
            continue

        # Inside a double quote: closing ``"`` ends it; ``$(...)`` and
        # `` `...` `` are still active.
        if cur == '"':
            if c == '"':
                stack.pop()
                i += 1
                continue
            if c == "$" and i + 1 < n and cmd[i + 1] == "(":
                stack.append("$(")
                if i + 1 < n:
                    contexts[i + 1] = "$("
                i += 2
                continue
            if c == "`":
                stack.append("`")
                i += 1
                continue
            i += 1
            continue

        # Inside ``$(...)`` cmdsub — like top-level shell, but ``)``
        # closes it.
        if cur == "$(":
            if c == ")":
                stack.pop()
                i += 1
                continue
            if c == "'":
                stack.append("'")
                i += 1
                continue
            if c == '"':
                stack.append('"')
                i += 1
                continue
            if c == "$" and i + 1 < n and cmd[i + 1] == "(":
                stack.append("$(")
                if i + 1 < n:
                    contexts[i + 1] = "$("
                i += 2
                continue
            if c == "`":
                stack.append("`")
                i += 1
                continue
            i += 1
            continue

        # Inside backtick cmdsub — closing backtick ends it.
        if cur == "`":
            if c == "`":
                stack.pop()
                i += 1
                continue
            if c == "'":
                stack.append("'")
                i += 1
                continue
            if c == '"':
                stack.append('"')
                i += 1
                continue
            if c == "$" and i + 1 < n and cmd[i + 1] == "(":
                stack.append("$(")
                if i + 1 < n:
                    contexts[i + 1] = "$("
                i += 2
                continue
            i += 1
            continue

        # Top-level: open new context as needed.
        if c == "'":
            stack.append("'")
            i += 1
            continue
        if c == '"':
            stack.append('"')
            i += 1
            continue
        if c == "$" and i + 1 < n and cmd[i + 1] == "(":
            stack.append("$(")
            if i + 1 < n:
                contexts[i + 1] = "$("
            i += 2
            continue
        if c == "`":
            stack.append("`")
            i += 1
            continue
        i += 1
    return contexts, escaped


def _try_consume_dollar_quote(
    cmd: str, i: int, n: int,
) -> Optional[Tuple[int, str]]:
    """Try to consume a ``$'...'`` or ``$"..."`` opener at ``cmd[i]``.

    ``cmd[i]`` must be ``$``. Returns ``(new_i, decoded_body)`` when
    ``cmd[i+1]`` is ``'`` (ANSI-C quoting — body is decoded by
    :func:`_decode_ansi_c`) or ``"`` (locale string — body is emitted
    literally; bash performs gettext lookup at runtime, but the source
    string is what determines the destructive intent we scan for).
    Returns ``None`` for any other follower (the caller falls through
    to the existing ``$(`` / bare-``$`` handling).

    Bash treats ``$'...'`` and ``$"..."`` as a single shell word whose
    decoded value is what gets parsed as argv. From a destructiveness
    standpoint the decoded chars are *executed text* — the same as if
    they appeared at top-level — even though they live inside a
    syntactic quote span. The closing quote is found while skipping
    backslash-escaped quote chars (``\\'`` inside ``$'...'``,
    ``\\"`` inside ``$"..."``) so attacker forms like
    ``$'rm \\'-rf\\' /'`` are walked correctly.
    """
    if i + 1 >= n:
        return None
    nxt = cmd[i + 1]
    if nxt == "'":
        j = i + 2
        while j < n:
            if cmd[j] == "\\" and j + 1 < n:
                j += 2
            elif cmd[j] == "'":
                break
            else:
                j += 1
        body = cmd[i + 2:j]
        return (j + 1 if j < n else j), _decode_ansi_c(body)
    if nxt == '"':
        j = i + 2
        while j < n:
            if cmd[j] == "\\" and j + 1 < n:
                j += 2
            elif cmd[j] == '"':
                break
            else:
                j += 1
        body = cmd[i + 2:j]
        return (j + 1 if j < n else j), body
    return None


def _shell_word_normalize(cmd: str) -> Tuple[str, List[int]]:
    """Return a shell-word-unquoted view of ``cmd`` plus an index map.

    Walks ``cmd`` and removes quote boundary characters (``"`` and
    ``'``) and the leading backslash of any ``\\X`` escape, while
    preserving the *content* of those quoted/escaped regions. The
    resulting string is what the shell would see *as far as command-
    word concatenation goes*: forms like ``r"m"``, ``r\\m``,
    ``'r''m'``, ``m"k"fs.ext4``, and ``\\rm`` all collapse to the
    single shell word ``rm`` / ``mkfs.ext4`` so the hardline command-
    name patterns can detect quote-split bypasses.

    Returns ``(normalized, idx_map)`` where ``idx_map[i]`` is the
    *original* offset in ``cmd`` of ``normalized[i]``. The caller uses
    this map to query the per-position quote/escape contexts of the
    original string, so literal-quoted data (``echo "rm -rf /"``) is
    still rejected by :func:`_hardline_match` — its normalized text
    looks dangerous, but the mapped start position lives inside a
    literal double-quoted region where bash never executes it.

    The walker mirrors :func:`_position_contexts`: same state machine,
    same handling of nested ``$(...)`` and `` `...` ``, same
    backslash-escape semantics (no escapes inside single quotes). The
    only divergence is the emit policy — ``_position_contexts``
    records *where* every char lives, while this function decides
    *whether* to keep each char in the unquoted view.

    Quote boundary chars (``"`` ``'``) are dropped because they are
    syntactic, not data. The opening ``$`` and ``(`` of a command
    substitution ARE kept (they're shell syntax executed by bash) so
    a separator like ``$(`` survives in normalized form. The same
    applies to backticks. Inside ``$(...)`` and `` `...` `` the
    handling is identical to top-level — those are real shell
    contexts, so quotes there get the same treatment.
    """
    n = len(cmd)
    norm: List[str] = []
    idx_map: List[int] = []
    stack: List[str] = []
    i = 0
    while i < n:
        c = cmd[i]
        cur = stack[-1] if stack else None

        if cur == "'":
            # Single-quoted: only the closing ``'`` has meaning. The
            # quote char itself is dropped from the view; everything
            # else is emitted as literal content.
            if c == "'":
                stack.pop()
                i += 1
                continue
            norm.append(c)
            idx_map.append(i)
            i += 1
            continue

        # Backslash escape (in any context except single-quote, which
        # is handled above). The backslash is dropped; the escaped
        # character is emitted at its original offset so the index map
        # points the filter at the actual literal char.
        if c == "\\" and i + 1 < n:
            norm.append(cmd[i + 1])
            idx_map.append(i + 1)
            i += 2
            continue

        if cur == '"':
            if c == '"':
                # Closing of double-quote: drop from view.
                stack.pop()
                i += 1
                continue
            if c == "$" and i + 1 < n and cmd[i + 1] == "(":
                # ``$(...)`` opens a command substitution even inside
                # double quotes. Keep the opener so separator regexes
                # still see ``$(``.
                stack.append("$(")
                norm.append("$")
                idx_map.append(i)
                norm.append("(")
                idx_map.append(i + 1)
                i += 2
                continue
            if c == "`":
                stack.append("`")
                norm.append("`")
                idx_map.append(i)
                i += 1
                continue
            norm.append(c)
            idx_map.append(i)
            i += 1
            continue

        # Inside ``$(...)`` cmdsub — like top-level, but ``)`` closes.
        # Keep the closer so separator regexes still see ``)``.
        if cur == "$(" and c == ")":
            stack.pop()
            norm.append(c)
            idx_map.append(i)
            i += 1
            continue
        # Inside backtick cmdsub — closing backtick ends it. Keep it.
        if cur == "`" and c == "`":
            stack.pop()
            norm.append(c)
            idx_map.append(i)
            i += 1
            continue

        # Top-level / inside cmdsub: open new contexts, drop quote
        # boundary chars, otherwise emit literally.
        if c == "'":
            stack.append("'")
            i += 1
            continue
        if c == '"':
            stack.append('"')
            i += 1
            continue
        if c == "$":
            # ``$'...'`` (ANSI-C) and ``$"..."`` (locale string) are
            # bash-specific quote forms whose decoded body is the
            # *executed* shell word. Decode the body and emit the
            # decoded chars mapped to the offset of the leading ``$``
            # — that offset lives at top-level shell context in
            # :func:`_position_contexts`, so the hardline filter sees
            # the decoded text as real shell, not as quoted literal.
            # Without this, attacker forms like ``$'rm' -rf /`` and
            # ``rm -rf $'/etc'`` slip through because the body chars
            # carry the inner ``'``/``"`` context. Bash treats
            # ``$'...'`` / ``$"..."`` inside another quote as literal,
            # so this branch only fires in real shell contexts
            # (top-level / cmdsub / backtick — same set the rest of
            # this code path covers).
            dq = _try_consume_dollar_quote(cmd, i, n)
            if dq is not None:
                new_i, decoded = dq
                for ch in decoded:
                    norm.append(ch)
                    idx_map.append(i)
                i = new_i
                continue
            if i + 1 < n and cmd[i + 1] == "(":
                stack.append("$(")
                norm.append("$")
                idx_map.append(i)
                norm.append("(")
                idx_map.append(i + 1)
                i += 2
                continue
            # Bare ``$`` (e.g., ``$VAR``) — emit literally.
            norm.append(c)
            idx_map.append(i)
            i += 1
            continue
        if c == "`":
            stack.append("`")
            norm.append("`")
            idx_map.append(i)
            i += 1
            continue

        norm.append(c)
        idx_map.append(i)
        i += 1

    return "".join(norm), idx_map

def _is_real_shell_pos(
    text: str,
    contexts: List[Optional[str]],
    escaped: set,
    start: int,
) -> bool:
    """True when ``text[start]`` is at executable shell context.

    Mirrors the in-line filter the ``_SHELL_SCRIPT_WRAPPER`` loop has
    used since this module was written: skip matches whose start
    position was backslash-escaped (``\\$(...)``), or that sit in a
    fully-literal quoted region with no substitution opener as the
    first character. Centralized so the new ``<<<`` / pipe / process-
    substitution extractors apply the same context rules.
    """
    if start in escaped:
        return False
    ctx = contexts[start] if 0 <= start < len(contexts) else None
    head = text[start:start + 1]
    if ctx in ("'", '"') and head not in ("$", "`"):
        return False
    return True


def _normalize_indirect_body(text: str) -> str:
    """Return the shell-word-normalized view of ``text``.

    Used by the indirect-execution extractors — ``echo ARG | sh``,
    ``bash <<< 'ARG'``, ``source <(echo ARG)`` — to turn the captured
    arg region (which still contains the shell quoting that surrounded
    it) into the literal byte sequence the downstream interpreter sees
    at runtime. Without this step a body of ``"rm -rf /"`` would
    recurse with its outer double quotes still attached, and the
    inner positions would still be marked as quote-context by
    ``_position_contexts`` — leaving the bypass open.
    """
    norm, _ = _shell_word_normalize(text)
    return norm
