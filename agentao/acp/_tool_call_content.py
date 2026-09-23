"""Per-tool-call accumulation of the ACP ``content`` collection.

Why this exists
---------------

ACP v1 says a ``tool_call_update``'s collections are **replaced**, not
extended. The normative schema source says so in three places
(``agentclientprotocol/agent-client-protocol@bf6d1ec``,
``agent-client-protocol-schema/src/v1/tool_call.rs``)::

    :167  Fields with collections of values are overwritten, not extended.
    :252  Collections (content, locations) are overwritten, not extended.
    :285  Replace the content collection.

agentao streams ``run_shell_command`` output one chunk at a time
(``LocalShellExecutor.run(on_chunk=…)`` → ``TOOL_OUTPUT``), and the ACP
transport used to map each chunk to its own ``tool_call_update`` carrying
that chunk alone as the whole collection. Under replace semantics a
conformant client therefore kept only the **last** chunk, and a failing
command replaced even that with the bare ``Error: …`` line. The full
output reached the model and the replay log; the human watching the
client did not see it.

The fix is to carry the whole collection on every update, which needs a
per-call buffer — this module.

Two bounds, both deliberate
---------------------------

Re-sending the whole collection per chunk would be quadratic in the
output size (a 500 KB build log arriving in 4 KB chunks would put ~30 GB
on the wire), so the buffer has:

- **A flush threshold** (:data:`FLUSH_CHARS`). The first chunk always
  flushes — that is the ``pending`` → ``in_progress`` transition, and it
  is how the client learns output has started — and after that an update
  goes out only once another :data:`FLUSH_CHARS` have accumulated.
  Whatever is still unflushed rides the terminal update, so nothing is
  lost by holding it back. Byte-based rather than time-based so the
  behaviour is deterministic and the tests need no clock.
- **A size cap** (:data:`MAX_CHARS`, kept as head + tail with an elision
  marker in between, the same shape ``replay/sanitize.py`` uses). Output
  past the cap is elided *in the client's copy only*: the model still
  receives the tool result through
  ``runtime/tool_result_formatter.py`` (80 000 chars, or a file under
  ``.agentao/tool-outputs/``) and replay still records it. The head is
  kept as well as the tail because the first lines of a command are
  often what identifies it, while the last lines are where failures land.

Not a streaming channel
-----------------------

The honest reading of ACP v1 is that ``tool_call.content`` is a
collection you restate, not a stream you append to; live command output
belongs in a ``terminal`` content entry backed by ``terminal/create``.
That is the G1 fs/terminal proxy, a documented non-goal
(``docs/design/acp-server-conformance-review.md``). Until an editor-class
client is a real target, restating a bounded collection is the
conformant way to show progress.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ._transport_helpers import _tool_content_text

#: Largest streamed-output excerpt carried to the client, in characters.
#: Past this the middle is elided; see the module docstring for why the
#: client's copy may be shorter than the model's.
MAX_CHARS = 16_000

#: How much of :data:`MAX_CHARS` is reserved for the *start* of the
#: output. The remainder is the rolling tail.
HEAD_CHARS = 4_000

#: Minimum number of newly accumulated characters between two updates.
#: The first chunk of a call ignores this (it carries the status change).
FLUSH_CHARS = 4_000

_ELISION = "\n\n[… {count:,} characters elided …]\n\n"


class ToolCallContentBuffer:
    """One tool call's ACP ``content`` collection, restated on demand.

    Not thread-safe, and does not need to be: every event for a given
    ``call_id`` (``TOOL_START`` → ``TOOL_OUTPUT``\\ * → ``TOOL_COMPLETE``)
    is emitted from the one executor worker running that tool. Different
    calls get different buffers, keyed by ``call_id`` in the transport.
    """

    __slots__ = ("_leading", "_head", "_tail", "_elided", "_unflushed", "_flushed_once")

    def __init__(self) -> None:
        # Entries that precede the streamed text and never change — the
        # ``diff`` a file-editing call opens with, for instance. Kept
        # apart from the text so a flush cannot drop them.
        self._leading: List[Dict[str, Any]] = []
        self._head = ""
        self._tail = ""
        self._elided = 0
        self._unflushed = 0
        self._flushed_once = False

    # -- writing -----------------------------------------------------------

    def add_leading(self, entry: Dict[str, Any]) -> None:
        """Pin a content entry ahead of the streamed text."""
        self._leading.append(entry)

    def append(self, chunk: str) -> bool:
        """Accumulate one streamed chunk; answer whether to send an update.

        ``True`` means the caller should emit a ``tool_call_update``
        carrying :meth:`entries`. ``False`` means the chunk is held —
        it is already in the buffer and goes out with the next flush or
        with the terminal update, so holding it loses nothing.
        """
        if chunk:
            self._append_text(chunk)
        if not self._flushed_once:
            # The first chunk is also the pending → in_progress
            # transition, so it always goes out, empty or not.
            self._flushed_once = True
            self._unflushed = 0
            return True
        if self._unflushed >= FLUSH_CHARS:
            self._unflushed = 0
            return True
        return False

    def _append_text(self, chunk: str) -> None:
        self._unflushed += len(chunk)
        room = MAX_CHARS - HEAD_CHARS
        if len(self._head) < HEAD_CHARS:
            take = HEAD_CHARS - len(self._head)
            self._head += chunk[:take]
            chunk = chunk[take:]
            if not chunk:
                return
        self._tail += chunk
        if len(self._tail) > room:
            dropped = len(self._tail) - room
            self._tail = self._tail[dropped:]
            self._elided += dropped

    # -- reading -----------------------------------------------------------

    @property
    def dirty(self) -> bool:
        """True when :meth:`entries` would differ from what was last sent."""
        return self._unflushed > 0 or not self._flushed_once

    def entries(self) -> List[Dict[str, Any]]:
        """The whole collection to put on the next ``tool_call_update``."""
        entries = list(self._leading)
        text = self.text()
        if text:
            entries.append(_tool_content_text(text))
        return entries

    def text(self) -> str:
        """The streamed output as the client should see it."""
        if self._elided:
            return self._head + _ELISION.format(count=self._elided) + self._tail
        return self._head + self._tail

    def __bool__(self) -> bool:
        return bool(self._leading or self._head or self._tail)
