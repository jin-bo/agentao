"""A model request that a cancel can stop while it waits on the network.

The adapters look at the cancellation token between stream events. A request
that hangs before the first event (a provider that never answers) or between
two events (a stalled stream) blocks the thread in a socket read, so the
cancel is not seen until the request times out, which can take minutes. A
headless run then ignores its first SIGINT / SIGTERM, and an ACP
``session/cancel`` does nothing.

Here the blocking part (opening the stream and reading each event) runs on a
worker thread. The caller's thread waits on a queue and looks at the token
every :data:`POLL_S` seconds. Everything else stays on the caller's thread:
the adapter's event handling, ``on_text_chunk`` and the accumulator see the
same thread as before, and an exception from the request is raised there,
so retry classification does not change.

A thread hand-off cancels the awaiter, never the worker (root
``CLAUDE.md``, Common gotchas). On a cancel the caller stops waiting and
closes the stream if it is open. Closing a socket from another thread does
not wake a ``recv`` already blocked on it (macOS, Linux), so a worker
reading a stalled stream would stay until the SDK's read timeout (600 s by
default) and hold the socket. While the worker reads, :func:`_wake_reader`
only shuts the socket down, through httpcore's ``network_stream`` response
extension; that wakes the read, and the worker closes the stream itself. One
thread closes, so no thread closes an fd another one is polling (its number
can be reused at once by the next connection). A worker that is not reading
is closed by the caller. After a normal end the connection is back in the
pool and is never shut down. A worker
still inside ``create()`` has no stream to wake. It closes the stream itself
when ``create()`` returns, and this is logged.
``LLMClient.chat`` hands its token to the adapter too, so a streaming wire's
``chat()`` closes its stream the same way. A non-streaming request (Chat
Completions' ``chat()``) cannot be closed: its worker ends when the request
does, and its result is dropped. The workers are daemon threads, so they
never hold the process open.

The token is polled, never woken through ``add_done_callback``: a run's
signal handler cancels the token on the main thread, and a callback that
took the queue's lock there could deadlock against the ``get`` it
interrupted.
"""

from __future__ import annotations

import contextvars
import logging
import queue
import socket
import threading
from typing import Any, Callable, Iterator, Optional

_logger = logging.getLogger(__name__)

#: How often the waiting thread looks at the token.
POLL_S = 0.1

_DONE = object()
_NEXT = object()
_STOP = object()
_CANCELLED = object()


class _Raised:
    __slots__ = ("exc",)

    def __init__(self, exc: BaseException) -> None:
        self.exc = exc


class CancelledWhileWaiting(Exception):
    """A cancel stopped :func:`call_cancellable` before the call returned."""


def _close(stream: Any) -> None:
    close = getattr(stream, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _wake_reader(stream: Any) -> None:
    """Shut down the socket under ``stream``, so a blocked read returns.

    Uses httpcore's documented ``network_stream`` response extension, which
    both SDKs' streams expose through ``stream.response``. Anything that does
    not look like that is left alone: the close that follows still runs.
    """
    try:
        extensions = getattr(getattr(stream, "response", None), "extensions", None)
        network_stream = extensions.get("network_stream") if extensions else None
        get_extra_info = getattr(network_stream, "get_extra_info", None)
        sock = get_extra_info("socket") if callable(get_extra_info) else None
        if isinstance(sock, socket.socket):
            sock.shutdown(socket.SHUT_RDWR)
    except Exception:
        pass


class CancellableStream:
    """Iterate the stream ``open_stream()`` returns, stopping on a cancel.

    Without a token this is the stream itself, opened and read on the
    calling thread, as before. With one, iteration ends quietly once the
    token is cancelled, the same way an adapter's ``break`` on
    ``is_cancelled`` ends it. :meth:`close` closes the underlying stream.
    """

    def __init__(
        self, open_stream: Callable[[], Any], token: Optional[Any],
    ) -> None:
        self._open_stream = open_stream
        self._token = token
        self._stream: Any = None
        self._lock = threading.Lock()
        self._abandoned = False
        self._asks: Optional["queue.Queue[Any]"] = None
        # Where the worker is: opening the stream, or blocked in a read.
        self._opening = False
        self._reading = False

    def __iter__(self) -> Iterator[Any]:
        if self._token is None:
            self._stream = self._open_stream()
            yield from self._stream
            return
        if self._token.is_cancelled:
            return
        # Lock-step: the worker reads one event only when asked for it, so
        # nothing is read past what the adapter consumed, as with a direct
        # read. A stream must not be read on after a cancel.
        asks: "queue.Queue[Any]" = queue.Queue()
        self._asks = asks
        items: "queue.Queue[Any]" = queue.Queue()
        # In the caller's context, as the request ran before: a host's
        # tracing or an httpx hook may read a ``ContextVar``.
        ctx = contextvars.copy_context()
        worker = threading.Thread(
            target=ctx.run, args=(self._pump, asks, items),
            name="agentao-llm-stream", daemon=True,
        )
        worker.start()
        try:
            while True:
                asks.put(_NEXT)
                item = self._wait(items)
                if item is _CANCELLED or item is _DONE:
                    return
                if isinstance(item, _Raised):
                    raise item.exc
                yield item
        finally:
            # Normal end, a cancel, an exception or the consumer stopping
            # early (``break``, ``KeyboardInterrupt``): release the worker
            # and close the stream nobody consumes any more.
            asks.put(_STOP)
            self._abandon()

    def _wait(self, items: "queue.Queue[Any]") -> Any:
        while True:
            try:
                return items.get(timeout=POLL_S)
            except queue.Empty:
                if self._token.is_cancelled:
                    return _CANCELLED

    def _pump(self, asks: "queue.Queue[Any]", items: "queue.Queue[Any]") -> None:
        try:
            if asks.get() is _STOP:
                return
            self._opening = True
            try:
                stream = self._open_stream()
            finally:
                self._opening = False
            with self._lock:
                if self._abandoned:
                    _close(stream)
                    return
                self._stream = stream
            iterator = iter(stream)
            while True:
                # Under the lock with ``_abandon``: either it sees this read
                # and wakes it, or this sees the abandon and does not read.
                with self._lock:
                    if self._abandoned:
                        return
                    self._reading = True
                try:
                    item = next(iterator)
                except StopIteration:
                    item = _DONE
                finally:
                    with self._lock:
                        self._reading = False
                        abandoned = self._abandoned
                    # An abandon during the read only shut the socket down
                    # (``_abandon``); closing is this thread's job, so no
                    # other thread closes an fd this one is polling.
                    if abandoned:
                        _close(stream)
                if abandoned:
                    return
                items.put(item)
                if item is _DONE:
                    return
                if asks.get() is _STOP:
                    return
        except BaseException as exc:  # noqa: BLE001 — handed to the caller
            if not self._abandoned:
                items.put(_Raised(exc))

    def _abandon(self) -> None:
        with self._lock:
            self._abandoned = True
            stream = self._stream
            asks = self._asks
            reading = self._reading
            if stream is not None and reading:
                # Wake the read; the worker closes the stream when it returns.
                _wake_reader(stream)
        # Release a worker parked between events too: :meth:`close` may run
        # before the generator is finalized (or on a runtime that does not
        # finalize it at once), and the worker must not wait forever.
        if asks is not None:
            asks.put(_STOP)
        if stream is not None and not reading:
            _close(stream)
        elif stream is None and self._opening:
            # Nothing to wake yet: the worker closes the stream when
            # ``create()`` returns, at the latest at the SDK's timeout.
            _logger.info(
                "cancelled while the model request was being opened; its "
                "worker stops when the request returns",
            )

    def close(self) -> None:
        self._abandon()


def call_cancellable(call: Callable[[], Any], token: Optional[Any]) -> Any:
    """Return ``call()``, or raise :class:`CancelledWhileWaiting` on a cancel.

    Without a token, ``call()`` runs on the calling thread. With one, it runs
    on a worker thread and the caller stops waiting once the token is
    cancelled. The worker is not stopped: it finishes the request and its
    result is dropped.
    """
    if token is None:
        return call()
    if token.is_cancelled:
        raise CancelledWhileWaiting()
    result: "queue.Queue[Any]" = queue.Queue(maxsize=1)

    def _run() -> None:
        try:
            result.put(call())
        except BaseException as exc:  # noqa: BLE001 — handed to the caller
            result.put(_Raised(exc))

    ctx = contextvars.copy_context()
    threading.Thread(
        target=ctx.run, args=(_run,), name="agentao-llm-call", daemon=True,
    ).start()
    while True:
        try:
            item = result.get(timeout=POLL_S)
        except queue.Empty:
            if token.is_cancelled:
                raise CancelledWhileWaiting() from None
            continue
        if isinstance(item, _Raised):
            raise item.exc
        return item
