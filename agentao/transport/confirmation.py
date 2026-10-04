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
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator, Optional

_local = threading.local()


@contextmanager
def gated(note: Optional[str]) -> Iterator[None]:
    """Mark the confirmation made inside this block as gated by ``note``."""
    previous = getattr(_local, "note", None)
    _local.note = note
    try:
        yield
    finally:
        _local.note = previous


def gate_note() -> Optional[str]:
    """The note of the gated confirmation in progress on this thread, or ``None``.

    Non-``None`` means: ask the user now, show this note, and remember
    nothing — no standing grant answers it, and the answer grants nothing
    beyond this call.
    """
    return getattr(_local, "note", None)
