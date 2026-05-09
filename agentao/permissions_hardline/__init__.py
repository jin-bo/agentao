"""Hardline shell-safety scanner: pre-permission floor for unrecoverable ops.

This package is the opt-out *floor* that
:class:`agentao.permissions.PermissionEngine` consults before any rule
is evaluated. It detects shell commands whose damage is *unrecoverable*
(disk wipe, host poweroff, fork bomb) and returns a stable, source-
tagged reason string the engine surfaces as the matched-rule reason of
a DENY decision.

The floor exists so a CLI user — or an embedded host that hasn't
thought through threat modeling — is protected from prompt-injected
disk wipes by default. A host that takes the policy responsibility
itself (typically because Agentao is sandboxed in a container) can
disable the floor with ``enable_hardline=False`` on the
:class:`PermissionEngine`.

Recoverable-but-costly operations (``git reset --hard``, ``pip
install``, ``chmod -R 777``, ``curl | sh``) deliberately stay outside
the floor so they remain host-policy decisions, not library-baked
invariants.

Public API:

- :func:`hardline_check` — top-level entry: ``(tool_name, tool_args)``
  → ``Optional[str]`` (a ``"hardline:<description>"`` reason string,
  or ``None`` when the call is not on the floor).
- :data:`REASON_HARDLINE` — the stable ``"hardline"`` source tag.
  Hosts and audit displays may pattern-match this prefix in
  ``PermissionDecisionEvent.reason``; it is part of the public event
  contract.

Layout (each row only depends on rows above):
    _decode    ← _decode_ansi_c
    _patterns  ← regex constants + REASON_HARDLINE + compiled table
    _contexts  ← _position_contexts / _shell_word_normalize / etc.
    _heredoc   ← here-doc masking
    _scanner   ← _hardline_match + hardline_check (entry)
"""

from __future__ import annotations

from ._patterns import REASON_HARDLINE
from ._scanner import hardline_check

__all__ = ["hardline_check", "REASON_HARDLINE"]
