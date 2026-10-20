"""What the runner knows about the confirmation a transport is answering.

``Transport.confirm_tool(name, description, args)`` carries no flag, and some
asks must be answered **for this call only**: the MCP Skills gate
(docs/design/mcp-skills.md §5.5, §6.2) asks for consent to one skill's
content, or to one command while server-written instructions are loaded. A
transport that answers from a standing grant — the CLI's full-access or
"allow all", ACP's remembered "Always allow" — would turn that back into an
approval nobody gave.

The runner sets the gate's note here, on its own thread, for the duration of
the ``confirm_tool`` call; a transport reads it with :func:`gate_note`. A
thread-local rather than a transport attribute, because a foreground
sub-agent's confirmation runs on the sub-agent's thread through the
*parent's* transport, concurrently with others.

The channel also runs the other way. A transport that declines may say *why*
with :func:`record_decline`, and the runner reads it with :func:`decline_reason`
before it leaves the block. ``confirm_tool`` returns only a bool, and the
model's result text depends on the reason: "nobody can approve this here" (a
non-interactive run, #565) may point at an alternative the rules allow, while a
person's "no" must not.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator, Optional

_local = threading.local()

#: Decline reason: no one can answer the confirmation (``agentao run`` under
#: ``interaction_policy: deny``). The executor gives the model a result that
#: allows an alternative the current rules permit.
APPROVAL_UNAVAILABLE = "approval-unavailable"


@contextmanager
def gated(note: Optional[str]) -> Iterator[None]:
    """Mark the confirmation made inside this block as gated by ``note``."""
    previous = getattr(_local, "note", None)
    previous_decline = getattr(_local, "decline", None)
    previous_depth = getattr(_local, "depth", 0)
    _local.note = note
    _local.decline = None
    _local.depth = previous_depth + 1
    try:
        yield
    finally:
        _local.note = previous
        _local.decline = previous_decline
        _local.depth = previous_depth


def gate_note() -> Optional[str]:
    """The note of the gated confirmation in progress on this thread, or ``None``.

    Non-``None`` means: ask the user now, show this note, and remember
    nothing — no standing grant answers it, and the answer grants nothing
    beyond this call.
    """
    return getattr(_local, "note", None)


def record_decline(reason: str) -> None:
    """Record why the transport declines the confirmation in progress on this thread.

    A no-op outside a :func:`gated` block: with no runner to read it, the
    reason would linger on the thread and be read by the next confirmation.
    """
    if getattr(_local, "depth", 0) > 0:
        _local.decline = reason


def decline_reason() -> Optional[str]:
    """The reason recorded inside the current :func:`gated` block, or ``None``."""
    return getattr(_local, "decline", None)
