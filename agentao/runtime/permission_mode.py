"""Permission-mode switching.

One function, so every entry path that changes posture moves *both* of
read-only's switches and records the transition. Same shape and the same
reason as :mod:`agentao.runtime.model`: a module-level function over an
``Agentao`` handle rather than a subsystem, emitting the event so CLI,
replay and ACP observers all see the switch.

Before this existed each caller did it by hand, and ACP
``session/set_mode`` did only half of it — it set the engine's preset and
emitted nothing, so an ACP replay showed read-only denials with no record
of when the session became read-only.

:class:`~agentao.permissions.PermissionEngine` cannot do this itself: it
holds no transport and does no I/O, deliberately.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from ..permissions import PermissionMode
from ..transport import AgentEvent, EventType

if TYPE_CHECKING:
    from ..agent import Agentao


def _mode_name(mode: Any) -> str:
    """Render a mode for an event payload, tolerating a plain string."""
    return getattr(mode, "value", str(mode))


def apply_permission_mode(
    agent: "Agentao",
    mode: PermissionMode,
    *,
    cause: str,
) -> Optional[PermissionMode]:
    """Switch ``agent`` to ``mode``, and record the transition.

    Moves **both** of read-only's switches, because
    :meth:`~agentao.runtime.tool_runner.ToolRunner.readonly_active` honours
    either: setting only the engine's preset leaves the runner's flag
    behind (so a session that entered read-only by the flag cannot be
    talked out of it), and setting only the flag leaves the engine
    evaluating the previous preset's rules.

    Emits, in the order ``cli/app.py::_apply_mode`` has always emitted
    them: ``READONLY_MODE_CHANGED`` (from
    :meth:`ToolRunner.set_readonly_mode`, which fires only on a real flip)
    then ``PERMISSION_MODE_CHANGED`` — only when the preset actually moved,
    so re-setting the current mode does not pollute a replay timeline.
    ``cause`` names the entry path — ``"cli"`` (``/mode``),
    ``"cli-allow-all"`` (answering "2" at the confirmation prompt),
    ``"cli-plan-implement"``, ``"acp"``, ``"run"``, ``"host"`` — and is the
    payload's only free-form field.

    Returns the mode that was active before, for a caller that wants to
    report the transition itself.

    Raises ``ValueError`` when ``agent`` has no permission engine: there is
    then nothing holding the posture, and applying half of the switch while
    answering "done" is the fail-open reading. ACP checks for the engine
    first so it can answer with a JSON-RPC error naming the session.
    """
    engine = getattr(agent, "permission_engine", None)
    if engine is None:
        raise ValueError(
            f"cannot apply permission mode {_mode_name(mode)!r}: this runtime "
            "has no permission engine"
        )
    previous = getattr(engine, "active_mode", None)
    engine.set_mode(mode)
    # Absent on a duck-typed handle (ACP's handler tests, an embedder's
    # stub). Skipping it costs the replay record of the flag, not the gate:
    # ``readonly_active`` reads the engine's mode too.
    runner = getattr(agent, "tool_runner", None)
    if runner is not None:
        runner.set_readonly_mode(mode == PermissionMode.READ_ONLY)
    if previous != mode:
        try:
            agent.transport.emit(AgentEvent(EventType.PERMISSION_MODE_CHANGED, {
                "previous": _mode_name(previous),
                "current": _mode_name(mode),
                "cause": cause,
            }))
        except Exception:
            pass
    return previous
