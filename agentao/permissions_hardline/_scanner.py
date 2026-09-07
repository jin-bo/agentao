"""The command floor: pattern execution, and the ``hardline_check`` entry point.

This is the layer the rest of agentao actually calls into. Everything below
here is plumbing — patterns, shell-context analysis, ANSI-C decoding, here-doc
masking — composed to decide whether a single ``run_shell_command`` invocation
is on the unrecoverable-ops floor.

Two functions, and the split is the point. :func:`generic_floor` is what has
always run: a BFS over the command and every nested interpreter body it
reaches, matching a table written for POSIX shell syntax. :func:`hardline_check`
is the entry, and it runs that on **every** dialect before adding whatever the
dialect contributes — so a body refused today is refused on the PowerShell path
too, by construction rather than by two tables agreeing.
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


def _spec_refusal(shell_spec: Any) -> Optional[str]:
    """Refuse before a single pattern is matched, when the spec itself cannot be used.

    What will interpret this text is asked first, because every pattern below is written for
    one grammar. Scanning cmd syntax with POSIX patterns does not fail loudly; it returns a
    clean result, which is the worst answer a floor can give.

    ``None`` means the caller named no spec — every call site outside the shell tool's own
    planner, which is why it is not itself a refusal.
    """
    if shell_spec is None:
        return None
    from ..capabilities.shell_spec import Exhausted, ShellSpec, validate

    if isinstance(shell_spec, Exhausted):
        # Not "no interpreter has been chosen yet" but "none could be established" — a
        # configured dialect this platform cannot run, or a PowerShell nobody installed. The
        # tool stays registered so the model is told this call is denied, not that shells do
        # not exist.
        return f"hardline:no-shell-opaque:{shell_spec.reason}"
    if isinstance(shell_spec, ShellSpec):
        return validate(shell_spec)  # a spec built elsewhere is re-checked here, not trusted
    return None


def _dialect(shell_spec: Any) -> Optional[str]:
    """Which dialect's own floor to add, or ``None`` when there is only the general one."""
    from ..capabilities.shell_spec import ShellSpec

    if not isinstance(shell_spec, ShellSpec):
        return None
    return shell_spec.dialect.value


def _decided_reason(decided: Any) -> Optional[str]:
    """The reason on a frozen record, or ``None`` when it allows the call.

    A record whose verdict is a refusal refuses at the launch too, so reading it here and
    reading it there give the same answer by construction rather than by agreement.
    """
    from ..capabilities.shell_spec import Deny

    verdict = getattr(decided, "verdict", None)
    return verdict.reason if isinstance(verdict, Deny) else None


def generic_floor(cmd: str) -> Optional[str]:
    """The dialect-independent floor: today's patterns over today's shell grammar.

    This is what has always run, extracted so it can have two callers instead of
    one. It is written for POSIX shell syntax, and it keeps running on every
    dialect — including PowerShell, where it is by construction an imperfect
    reader of the grammar. That is deliberate and it is the compatibility
    property the PowerShell path rests on: **a body this refuses today is a body
    the PowerShell path refuses too.** Its false positives come along with that,
    and a pass from it is not evidence about PowerShell semantics.

    Each pattern is searched with ``finditer``; matches whose start position is
    in a *literal* shell context are suppressed — that protects benign commands
    like ``echo "(reboot required)"`` or ``printf "backup > /dev/disk0"`` from
    being denied. The shell context is computed by :func:`_position_contexts`,
    which handles nested ``$(...)`` and `` `...` `` correctly: a destructive
    command inside command substitution is real shell, even when the outer layer
    is a double-quoted string (``echo "$(echo ok; rm -rf /)"``).

    After the direct check it descends into ``sh -c '...'`` / ``bash -c "..."``
    bodies and reruns itself against each. The body is *literal* to the outer
    shell but *executed as shell* by the nested interpreter, so a destructive
    command anywhere inside it counts — ``sh -c 'echo ok; rm -rf /'`` is denied
    even though the ``;`` and ``rm`` sit inside an outer single-quoted region.
    """
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


def hardline_check(
    tool_name: str, tool_args: Dict[str, Any], *, shell_spec: Any = None, decided: Any = None,
) -> Optional[str]:
    """Return a ``"hardline:<desc>"`` reason when ``tool_args`` is unrecoverable.

    ``decided`` is this call's frozen record when the caller has one. The planner builds it,
    because it is the only place that has the tool's own working-directory resolution, the
    spec and the arguments at once. Given one, this function reports *its* verdict rather
    than computing a second: one value, one source.

    Only inspects shell commands: the floor is about preventing unrecoverable operations, and
    ``run_shell_command`` is the single surface that can express them. File-write tools have
    their own PathPolicy; other tools have narrow, named effects.

    **The general floor runs first and runs always.** That is the compatibility property, and
    it is what makes it safe to state without qualification: a body refused today is refused
    on the PowerShell path, same input, same settings. Short-circuiting on its refusal costs
    nothing — the call is already denied, and a second table could only change which reason
    is reported.

    The dialect's own table then covers what the general one lets through, which on Windows
    is most of what matters. Two shapes it deliberately is *not*: running the PowerShell
    table **instead** would trade one set of blind spots for another, and running it **only
    when lowering failed** — tempting, because a parse failure feels like the risky case —
    would make the added coverage vanish on exactly the bodies it can actually read.

    Returns ``None`` when nothing matched. That is the floor declining to refuse, not an
    allow: the caller (typically :class:`PermissionEngine`) goes on to the permission rules.
    """
    if tool_name != "run_shell_command":
        return None
    if decided is not None:
        return _decided_reason(decided)
    spec_reason = _spec_refusal(shell_spec)
    if spec_reason is not None:
        return spec_reason
    cmd = str(tool_args.get("command", ""))
    if not cmd:
        return None
    general = generic_floor(cmd)
    if general is not None:
        return general
    if _dialect(shell_spec) == "powershell":
        from ._powershell import scan_powershell

        return scan_powershell(cmd)
    return None
