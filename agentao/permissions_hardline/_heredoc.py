"""Here-doc detection and length-preserving body masking.

Bash here-doc syntax (``cmd <<TAG\\nbody\\nTAG\\n``) feeds the body as
*data* to the host command — cat / dd / grep / a generator script reads
it on stdin, but the shell never executes it. The mask replaces body
bytes with spaces so downstream :mod:`._contexts` index maps still align
with the original command and the regular hardline scan doesn't
misinterpret an embedded ``\\n`` as a fresh command separator.

The exception is when the here-doc's launching line feeds the body to a
shell interpreter (``bash <<EOF``, ``cat <<EOF | bash``); in that case
the body IS executable shell, so masking would create a real bypass —
:func:`_heredoc_owner_is_shell` detects that case so masking is skipped
and the regular scan picks up a destructive command in the body.
"""

from __future__ import annotations

from functools import lru_cache
import re
from typing import List, Optional

from ._contexts import _position_contexts
from ._patterns import _HEREDOC_OPENER_RE, _SHELL_INTERP_LINE_RE


def _heredoc_owner_is_shell(cmd: str, heredoc_pos: int) -> bool:
    """Return True when the launching line of ``cmd[heredoc_pos:]`` is
    likely feeding its body to a shell interpreter.

    Looks at the entire line containing the ``<<TAG`` opener (the body
    starts at the next newline). If a shell-interpreter token appears
    anywhere on that line — at command position, on either side of a
    pipe, or as the target of a redirection — we conservatively treat
    the body as executable shell code and skip masking. That way
    ``bash <<EOF\\nrm -rf /\\nEOF``, ``cat <<EOF | bash\\n...\\nEOF``,
    and ``sudo bash <<EOF`` all expose their body to the regular
    hardline scan instead of being neutralized into spaces.
    """
    line_start = cmd.rfind("\n", 0, heredoc_pos) + 1
    line_end = cmd.find("\n", heredoc_pos)
    if line_end == -1:
        line_end = len(cmd)
    return _SHELL_INTERP_LINE_RE.search(cmd[line_start:line_end]) is not None


@lru_cache(maxsize=256)
def _heredoc_closer_re(is_dash: bool, tag: str) -> "re.Pattern[str]":
    """Compiled closer regex for ``\\nTAG`` (or ``\\n[\\t]*TAG`` with ``<<-``).

    Cached because the same shell command can repeat the same TAG and
    ``_mask_heredoc_bodies`` runs on every ``run_shell_command``
    permission check.
    """
    leading = r"[\t]*" if is_dash else r""
    return re.compile(rf"\n{leading}{re.escape(tag)}[ \t]*(?=\n|$)")


def _mask_heredoc_bodies(cmd: str) -> str:
    """Replace each here-doc body in ``cmd`` with spaces.

    Bash here-doc syntax (``cmd <<TAG\\nbody\\nTAG\\n``,
    ``cmd <<-TAG\\n\\tbody\\n\\tTAG\\n``, ``cmd <<'TAG'\\n...\\nTAG``)
    feeds the BODY as DATA to the host command — cat / dd / grep /
    a generator script reads it through stdin, but the shell never
    executes it. Embedded ``\\n`` chars are line breaks in the data
    stream, not command separators, so the hardline scanner must not
    treat ``cat <<EOF\\nrm -rf /\\nEOF`` as if ``rm -rf /`` sat at a
    fresh command position after the inline newline.

    The mask is length-preserving: every body character (including
    body newlines) becomes a space so downstream
    :func:`_position_contexts` and :func:`_shell_word_normalize`
    index maps still align with the original ``cmd``. The launching
    ``cat <<EOF`` line and the closing ``EOF`` line are left intact
    so the regex can still see the surrounding shell command.

    Quoted or escaped ``<<`` text (``echo "<<EOF"``,
    ``echo \\<\\<EOF``) is *not* a here-doc opener — bash sees those
    as literal text. The detector consults
    :func:`_position_contexts` so only top-level / cmdsub openers
    are processed.
    """
    if "<<" not in cmd:
        return cmd
    contexts, escaped = _position_contexts(cmd)
    chars: Optional[List[str]] = None
    n = len(cmd)
    i = 0
    while i < n:
        if (
            i + 1 < n
            and cmd[i] == "<" and cmd[i + 1] == "<"
            and i not in escaped
            and contexts[i] in (None, "$(", "`")
        ):
            m = _HEREDOC_OPENER_RE.match(cmd, i)
            if m is None:
                i += 1
                continue
            is_dash = bool(m.group(1))
            tag = m.group(3)
            if not tag:
                i += 1
                continue
            opener_end = m.end()
            # Find end of the opener line. Bash allows other
            # redirections / commands on the same line as ``<<TAG``
            # (``cat <<EOF | wc -l``), and the body starts at the
            # next newline regardless.
            nl = cmd.find("\n", opener_end)
            if nl == -1:
                # Declared but no body — leave cmd alone, advance past
                # the opener.
                i = opener_end
                continue
            body_start = nl + 1
            # Closer line: ``\nTAG`` (plain ``<<``) or
            # ``\n[\t]*TAG`` (``<<-``), optionally followed by
            # trailing whitespace, ending at ``\n`` or end-of-string.
            closer_m = _heredoc_closer_re(is_dash, tag).search(cmd, body_start - 1)
            if closer_m is None:
                # Unterminated here-doc — body runs to end of cmd.
                body_end = n
            else:
                # ``closer_m.start()`` is the ``\n`` *before* TAG.
                # Mask the body up to (but not including) that
                # newline, leaving the closer line visible.
                body_end = closer_m.start()
            if _heredoc_owner_is_shell(cmd, i):
                # Launching line feeds the body to a shell interpreter
                # (``bash <<EOF``, ``cat <<EOF | bash``, ...). The body
                # is executable shell code, not data — leave it visible
                # so the regular hardline scan picks up a destructive
                # ``rm -rf /`` after the body's ``\\n`` separators.
                # Otherwise masking would create a real bypass: the
                # destructive command would be replaced with spaces and
                # the floor would never see it.
                i = body_end
                continue
            if chars is None:
                # Allocate the mutable buffer lazily — when ``<<`` only
                # appears as text (escaped, quoted, or no opener
                # follows) we never enter this branch and the original
                # ``cmd`` is returned unchanged.
                chars = list(cmd)
            for k in range(body_start, body_end):
                chars[k] = " "
            i = body_end
            continue
        i += 1
    return cmd if chars is None else "".join(chars)
