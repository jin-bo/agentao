"""``ChatLoopRunner`` — Agentao's inner chat loop.

Public surface (preserved from the pre-split single-file module):

- :class:`ChatLoopRunner` — the loop runner used by ``Agentao.chat()``
- :func:`_serialize_tool_call` — re-exposed because tests import it
  directly to verify outbound canonicalization

Layering (each row only depends on rows above):
    _serialize     ← _attach_reasoning + _serialize_tool_call (pure)
    _outcomes      ← _HookOutcome (UserPromptSubmit dispatch verdict)
    _hook_dispatch ← _HookDispatchMixin (UPS / Stop / PreCompact)
    _compaction    ← _CompactionMixin (microcompact / full compress)
    _runner        ← ChatLoopRunner (__init__ + run + 3 remaining helpers)

An internal-mixin variant of the ``acp_client.manager`` mixin pattern:
the latter exposes ``TurnsMixin`` / ``LifecycleMixin`` etc. as public
names because ``ACPClient`` composes them at the package boundary; here
``_CompactionMixin`` / ``_HookDispatchMixin`` are runner-private and
underscored to match. Each mix-in reads ``self._agent`` (and, for
``_HookDispatchMixin``, ``self._stop_reentries``) from the runner.
"""

from __future__ import annotations

from ._runner import ChatLoopRunner
from ._serialize import _serialize_tool_call

__all__ = ["ChatLoopRunner", "_serialize_tool_call"]
