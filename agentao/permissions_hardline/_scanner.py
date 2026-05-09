"""Hardline pattern execution + the ``hardline_check`` BFS entry point.

This is the layer the rest of agentao actually calls into. Everything
below here is plumbing — patterns, shell-context analysis, ANSI-C
decoding, and here-doc masking — composed by :func:`hardline_check` to
decide whether a single ``run_shell_command`` invocation is on the
unrecoverable-ops floor.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Dict, List, Optional

from ._contexts import (
    _is_real_shell_pos,
    _normalize_indirect_body,
    _position_contexts,
    _shell_word_normalize,
)
from ._decode import _decode_ansi_c
from ._heredoc import _mask_heredoc_bodies
from ._patterns import (
    REASON_HARDLINE,
    _CMDPOS_SEP_FIRST_CHARS,
    _CMDSUB_ECHO_AS_BODY,
    _CMDSUB_ECHO_AT_CMDPOS,
    _ECHO_PIPE_TO_SHELL,
    _HARDLINE_PATTERNS_COMPILED,
    _HERESTRING_TO_SHELL,
    _PROCSUBST_TO_SHELL,
    _SHELL_SCRIPT_WRAPPER,
)


def _hardline_match(
    cmd: str,
    contexts: Optional[List[Optional[str]]] = None,
    escaped: Optional[set] = None,
) -> Optional[str]:
    """Run hardline patterns against ``cmd`` and return a deny reason
    for the first valid match (filtered by shell context).

    A "valid" match is one whose start position is in real shell
    context — top-level, inside a command substitution, or starting a
    new substitution inside a double-quoted string — *and* its first
    character isn't backslash-escaped. Matches whose start sits in a
    fully-literal region (single quotes, or arbitrary text inside
    double quotes), or whose opening character was preceded by ``\\``,
    are skipped. The caller can pass in pre-computed ``contexts`` and
    ``escaped`` so the BFS in :func:`hardline_check` doesn't pay to
    recompute them twice per iteration.

    Patterns are matched against a *shell-word-unquoted* view of
    ``cmd`` (see :func:`_shell_word_normalize`), not the raw text.
    That way command-name forms split by per-character shell quoting
    (``r"m" -rf /``, ``r\\m -rf /``, ``m"k"fs.ext4 /dev/sda1``,
    ``'r''m' -rf /``) — which bash unquotes to ``rm`` / ``mkfs.ext4``
    before execution — still hit the floor. Each match's start in the
    normalized view is mapped back to its origin offset in ``cmd``,
    and the original ``contexts`` / ``escaped`` of that origin govern
    the literal-vs-executed decision. Quoted-data cases like
    ``echo "rm -rf /"`` therefore stay benign: their normalized
    string looks dangerous, but the mapped start lives inside a
    literal double-quoted region with no substitution opener, so the
    filter rejects them.

    Returns the ``"hardline:<description>"`` reason string for the
    first surviving match, or ``None`` when no match remains.
    """
    if contexts is None or escaped is None:
        contexts, escaped = _position_contexts(cmd)
    norm, idx_map = _shell_word_normalize(cmd)
    norm_n = len(norm)
    for compiled, desc in _HARDLINE_PATTERNS_COMPILED:
        for m in compiled.finditer(norm):
            start = m.start()
            if start >= norm_n:
                # Defensive — ``finditer`` should never return out-of-
                # bounds offsets, but guard against future refactors.
                continue
            if start == 0 and norm[0] not in _CMDPOS_SEP_FIRST_CHARS:
                # ``^``-anchored match where the first emitted char of
                # the normalized view is part of the command name
                # itself (or leading whitespace before it), not a
                # ``$(`` / ``;`` / ``` ` ``` / keyword separator. The
                # command begins at the very start of the input, which
                # is always at top-level shell, so the literal-quote
                # and escape filters don't apply — the shell-word view
                # has already resolved any quote/escape splits in the
                # command name (``'rm' -rf /``, ``\rm -rf /``,
                # ``'r''m' -rf /``). Block.
                return f"{REASON_HARDLINE}:{desc}"
            orig_start = idx_map[start]
            if orig_start in escaped:
                # The mapped origin char was backslash-escaped at the
                # outer layer (``\$``, ``\`` ``, ``\(``, ``\;``, ...).
                # Bash treats the next char literally there, so this
                # isn't actual shell syntax. Note: backslashes that
                # _shell_word_normalize already consumed (escapes of
                # plain word chars, ``\rm``) don't show up here —
                # ``escaped`` records the *original* offset of the
                # escaped char, and idx_map points the regex at that
                # same offset, so those positions are top-level and
                # accepted.
                continue
            ctx = contexts[orig_start] if 0 <= orig_start < len(contexts) else None
            if ctx is None or ctx in ("$(", "`"):
                # Top-level shell text or already inside a command
                # substitution — bash will execute the matched syntax.
                return f"{REASON_HARDLINE}:{desc}"
            if ctx == "'":
                # Single-quoted: fully literal, never executed.
                continue
            # ctx == '"': inside a double-quoted string. Bash still
            # evaluates ``$(...)`` and `` `...` `` here, so the match is
            # real iff its first character opens a new substitution.
            head = cmd[orig_start:orig_start + 1]
            if head in ("$", "`"):
                return f"{REASON_HARDLINE}:{desc}"
    return None


def hardline_check(
    tool_name: str, tool_args: Dict[str, Any],
) -> Optional[str]:
    """Return a ``"hardline:<desc>"`` reason when ``tool_args`` is unrecoverable.

    Only inspects shell commands today: the floor is about preventing
    unrecoverable operations, and ``run_shell_command`` is the single
    surface that can express them. File-write tools have their own
    PathPolicy; other tools have narrow, named effects.

    Each pattern is searched with ``finditer``; matches whose start
    position is in a *literal* shell context are suppressed — that
    protects benign commands like ``echo "(reboot required)"`` or
    ``printf "backup > /dev/disk0"`` from being denied. The shell
    context is computed by :func:`_position_contexts`, which handles
    nested ``$(...)`` and `` `...` `` correctly: a destructive command
    inside command substitution is real shell, even when the outer
    layer is a double-quoted string (``echo "$(echo ok; rm -rf /)"``).

    After the direct check, the function recursively descends into
    ``sh -c '...'`` / ``bash -c "..."`` / similar shell-interpreter
    bodies and reruns the floor against each. The body is *literal* to
    the outer shell but *executed as shell* by the nested interpreter,
    so a destructive command anywhere inside it counts —
    ``sh -c 'echo ok; rm -rf /'`` is denied even though the ``;`` and
    ``rm`` sit inside an outer single-quoted region.

    Returns ``None`` when no surviving match remains. The caller
    (typically :class:`PermissionEngine`) wraps a non-``None`` return
    into a deny decision.
    """
    if tool_name != "run_shell_command":
        return None
    cmd = str(tool_args.get("command", ""))
    if not cmd:
        return None

    # BFS through the original command and all reachable sh -c bodies.
    # Each iteration runs the same matcher on a separate piece of
    # text. Bodies are bounded by the outer command's length, so the
    # queue can't grow unboundedly; the explicit cap is a defense in
    # depth against pathological inputs.
    queue: deque[str] = deque([cmd])
    inspected = 0
    while queue and inspected < 16:
        text = queue.popleft()
        inspected += 1
        # ``$(echo SCRIPT)`` as a whole-body — when a queued text is
        # exactly the cmdsub of an echo / printf, the runtime script is
        # the inner args, not the textual cmdsub itself. Surface it
        # before pattern matching so the recursive scan sees what bash
        # actually executes. This covers ``bash -c "$(echo rm -rf /)"``
        # (body queued by the ``-c`` extractor), ``echo "$(echo rm -rf
        # /)" | bash`` (body queued by the pipe extractor), and any
        # other indirect path that funnels a cmdsub-as-script.
        inner = _CMDSUB_ECHO_AS_BODY.match(text)
        if inner is not None:
            queue.append(_normalize_indirect_body(inner.group(1)))
        # Mask here-doc bodies BEFORE computing contexts or running
        # patterns: ``cat <<'EOF'\nrm -rf /\nEOF`` is data being read
        # by ``cat``, not commands. The mask replaces body chars with
        # spaces so downstream offset-based maps still align with
        # the original.
        text = _mask_heredoc_bodies(text)
        contexts, escaped = _position_contexts(text)
        hit = _hardline_match(text, contexts, escaped)
        if hit is not None:
            return hit
        # Descend into ``sh -c '...'`` bodies that live in real shell
        # context. A literal ``echo "sh -c 'rm -rf /'"`` only *prints*
        # the nested-shell example — its body is never executed, so
        # treating it as a script body would create a false positive.
        # Inside double quotes, however, ``$(...)`` and `` `...` `` ARE
        # executed: ``echo "$(sh -c 'echo ok; rm -rf /')"`` runs the
        # wrapped script, so when the match opens a substitution
        # (head char is ``$`` or `` ` ``) we still descend.
        for m in _SHELL_SCRIPT_WRAPPER.finditer(text):
            if not _is_real_shell_pos(text, contexts, escaped, m.start()):
                continue
            sq_dollar = m.group(1)
            sq_body = m.group(2)
            dq_dollar = m.group(3)
            dq_body = m.group(4)
            if sq_body is not None:
                body = sq_body
                if sq_dollar == "$":
                    # ``bash -c $'...'`` — ANSI-C-quoted body. Decode
                    # ``\n`` / ``\t`` / etc. so embedded separators
                    # become real whitespace before the recursive
                    # check sees them.
                    body = _decode_ansi_c(body)
            elif dq_body is not None:
                # ``bash -c $"..."`` (locale string): runtime gettext
                # translation can't introduce destructive intent that
                # wasn't already in the source, so we treat it
                # identically to ``"..."`` here. ``dq_dollar`` is captured
                # by the regex but unused intentionally.
                body = dq_body
            else:
                body = None
            if body:
                queue.append(body)
        # ``bash <<< 'rm -rf /'`` — here-string feeds the body to the
        # interpreter on stdin. The body is recursive shell, so we
        # treat it identically to ``-c <body>``.
        for m in _HERESTRING_TO_SHELL.finditer(text):
            if not _is_real_shell_pos(text, contexts, escaped, m.start()):
                continue
            sq_dollar = m.group(1)
            sq_body = m.group(2)
            dq_body = m.group(4)
            if sq_body is not None:
                body = _decode_ansi_c(sq_body) if sq_dollar == "$" else sq_body
            elif dq_body is not None:
                body = dq_body
            else:
                body = None
            if body:
                queue.append(body)
        # ``echo ARGS | sh`` / ``printf ARGS | bash`` — echo writes
        # ARGS to stdout, the right-hand shell reads stdin as a
        # script. The captured ARGS still carries its outer quoting,
        # so normalize before recursion: ``"rm -rf /"`` → ``rm -rf /``,
        # which then matches the rm pattern as if at top level.
        for m in _ECHO_PIPE_TO_SHELL.finditer(text):
            if not _is_real_shell_pos(text, contexts, escaped, m.start()):
                continue
            args = m.group(1)
            if args:
                queue.append(_normalize_indirect_body(args))
        # ``source <(echo SCRIPT)`` / ``bash <(echo SCRIPT)`` — process
        # substitution feeds a fifo containing SCRIPT to the shell
        # loader. Same recursion shape as the pipe form.
        for m in _PROCSUBST_TO_SHELL.finditer(text):
            if not _is_real_shell_pos(text, contexts, escaped, m.start()):
                continue
            args = m.group(1)
            if args:
                queue.append(_normalize_indirect_body(args))
        # Cmdsub-of-echo at command position: ``$(echo rm -rf /)`` or
        # ``` `echo rm -rf /` ``` typed directly. Bash captures the
        # echo output and re-parses it as a command — the args ARE the
        # script. ``_CMDSUB_ECHO_AS_BODY`` (whole-text match) covers
        # the case where this is queued from a wrapper extraction; this
        # ``finditer`` covers the standalone form sitting at top level
        # of an outer command.
        for m in _CMDSUB_ECHO_AT_CMDPOS.finditer(text):
            if not _is_real_shell_pos(text, contexts, escaped, m.start()):
                continue
            args = m.group(1)
            if args:
                queue.append(_normalize_indirect_body(args))
    return None
