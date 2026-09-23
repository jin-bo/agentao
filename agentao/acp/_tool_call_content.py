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
output size (a 500 KB build log arriving in 4 KB chunks is 125 updates
whose sizes sum to ~31 MB — 63× the output that was produced, and the
ratio grows with the log), so the buffer has:

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

import threading
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

    **Guarded by its own re-entrant lock, and it needs one.** The single
    tool that streams does not stream from a single thread:
    ``capabilities/shell.py::LocalShellExecutor.run`` reads the child's
    stdout and stderr in two daemon threads and calls ``on_chunk`` from
    both — so two ``TOOL_OUTPUT`` events for the *same* ``call_id`` can
    enter :meth:`append` concurrently. Every mutation here is a
    read-modify-write (``_unflushed += …``, ``_head += …``,
    ``_tail = self._tail[dropped:]``), so unguarded interleaving drops one
    of the two writes: output disappears from the client's copy, the
    elided count stops matching what was dropped, and ``_head`` can pass
    :data:`HEAD_CHARS`. Re-entrant because :meth:`append` calls
    :meth:`mark_sent` and :meth:`entries` calls :meth:`text`.

    Do not try to justify removing it with a "hammer it from two threads
    and count the characters" test: on a GIL build that test passes
    against the unguarded buffer, because CPython checks the eval breaker
    at call and jump boundaries rather than between the ``LOAD_ATTR`` and
    ``STORE_ATTR`` of a ``+=``. It proves the window is narrow, not that
    it is closed — and it is not narrow at all on a free-threaded build.
    ``tests/test_acp_tool_call_content.py`` pins the lock with a hand-off
    that fails every time instead.

    Different calls get different buffers, keyed by ``call_id`` in the
    transport.
    """

    __slots__ = (
        "_lock", "_leading", "_head", "_tail", "_elided", "_unflushed",
        "_sent", "_seen_chunk",
    )

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # Entries that precede the streamed text and never change — the
        # ``diff`` a file-editing call opens with, for instance. Kept
        # apart from the text so a flush cannot drop them.
        self._leading: List[Dict[str, Any]] = []
        self._head = ""
        self._tail = ""
        self._elided = 0
        self._unflushed = 0
        # Whether the collection has ever been handed to the client. Distinct
        # from ``_seen_chunk``: a file-editing call is opened with a ``diff``
        # entry already sent on its ``tool_call``, and its first *chunk* still
        # has to carry the pending → in_progress transition.
        self._sent = False
        self._seen_chunk = False

    # -- writing -----------------------------------------------------------

    def add_leading(self, entry: Dict[str, Any]) -> None:
        """Pin a content entry ahead of the streamed text."""
        with self._lock:
            self._leading.append(entry)

    def mark_sent(self) -> None:
        """Record that the caller just emitted :meth:`entries` itself.

        Used by the ``tool_call`` that opens a file-editing call: it carries
        the ``diff`` entry, so the terminal update must not restate it as if
        it were news.
        """
        with self._lock:
            self._sent = True
            self._unflushed = 0

    def append(self, chunk: str) -> bool:
        """Accumulate one streamed chunk; answer whether to send an update.

        ``True`` means the caller should emit a ``tool_call_update``
        carrying :meth:`entries`. ``False`` means the chunk is held —
        it is already in the buffer and goes out with the next flush or
        with the terminal update, so holding it loses nothing.
        """
        with self._lock:
            if chunk:
                self._append_text(chunk)
            first = not self._seen_chunk
            self._seen_chunk = True
            if first or self._unflushed >= FLUSH_CHARS:
                # The first chunk is also the pending → in_progress
                # transition, so it always goes out, empty or not.
                self.mark_sent()
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
    def streamed(self) -> bool:
        """True once any ``TOOL_OUTPUT`` chunk has reached this buffer.

        The transport restates the collection at completion for every call
        that streamed, whether or not it is :attr:`dirty`: the snapshot an
        update carries is taken under this lock, but the write to the client
        happens after it is released, so two reader threads that both flush
        can deliver their snapshots in the opposite order — the older, shorter
        one last. Only a terminal restatement is guaranteed to come after
        both.
        """
        with self._lock:
            return self._seen_chunk

    @property
    def dirty(self) -> bool:
        """True when :meth:`entries` would differ from what was last sent."""
        with self._lock:
            return self._unflushed > 0 or not self._sent

    def entries(self) -> List[Dict[str, Any]]:
        """The whole collection to put on the next ``tool_call_update``."""
        with self._lock:
            entries = list(self._leading)
            text = self.text()
            if text:
                entries.append(_tool_content_text(text))
            return entries

    def text(self) -> str:
        """The streamed output as the client should see it."""
        with self._lock:
            if self._elided:
                return self._head + _ELISION.format(count=self._elided) + self._tail
            return self._head + self._tail
