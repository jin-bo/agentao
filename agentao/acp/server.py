"""ACP stdio JSON-RPC server.

This module owns the low-level JSON-RPC 2.0 framing, the request/response
loop over stdio, method dispatch, and standard error mapping. It is method-
agnostic on purpose — callers register handlers via :meth:`AcpServer.register`.
Later issues will register ``initialize``, ``session/new``, etc.

Framing is newline-delimited JSON (one compact JSON object per line), which
matches zed's ACP convention and is simpler than LSP-style ``Content-Length``
headers.

Stdout hygiene (critical): when constructed without an explicit ``stdout``
argument, the server captures the real ``sys.stdout`` into a private handle
then reassigns ``sys.stdout = sys.stderr`` so any stray ``print`` anywhere in
the process goes to stderr. All JSON-RPC responses are written through the
captured handle under a :class:`threading.Lock` so later issues can safely
emit notifications from worker threads concurrently with dispatch.

Concurrency model
-----------------

Issue 08 (``session/request_permission``) forces the server to support
blocking *server → client* requests: when a tool needs user confirmation,
:meth:`ACPTransport.confirm_tool` calls :meth:`call` which writes a request
and waits for a matching response. That wait must not block the read loop,
because the response itself arrives *on* the read loop. To keep the read
loop responsive, handlers are dispatched on a :class:`ThreadPoolExecutor`:

  - **Read thread** (``run``): parses lines, classifies requests vs.
    responses, and either *submits* the request to the executor or *routes*
    the response to the pending-request registry.
  - **Worker threads** (``_executor``): run handlers via :meth:`_dispatch`,
    then write the response under the shared write lock.
  - **Pending-request registry** (``_pending_requests``): map of outgoing
    request id → :class:`_PendingRequest`. ``call()`` inserts an entry,
    waits on its ``event``; ``_route_response`` pops the entry and sets it.

The only visible consequence is that multiple concurrent requests may see
their responses serialized in a non-deterministic order under the write
lock. Clients are expected to match responses by id, which is how
JSON-RPC is specified anyway; the old "responses are FIFO" behavior was
an accident of the synchronous dispatcher, not a protocol guarantee.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import traceback
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Dict, IO, List, Optional

from .models import AcpConnectionState, JsonRpcError, JsonRpcRequest, JsonRpcResponse
from .protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
)
from .session_manager import AcpSessionManager

logger = logging.getLogger(__name__)


Handler = Callable[[Any], Any]
"""A request handler takes the JSON-RPC ``params`` and returns the ``result``.

Error mapping:

- Raise :class:`TypeError` for bad params → surfaces as ``-32602``
  (INVALID_PARAMS). This is the common case.
- Raise :class:`JsonRpcHandlerError` for any other specific JSON-RPC error
  code (e.g. ``SERVER_NOT_INITIALIZED``). The dispatcher honors the ``code``
  and ``message`` you supply verbatim.
- Any other exception surfaces as ``-32603`` (INTERNAL_ERROR).
"""


class JsonRpcHandlerError(Exception):
    """Raise from a handler to return an arbitrary JSON-RPC error code.

    Lets a handler signal non-``INVALID_PARAMS`` failures (e.g. "server not
    initialized", "session not found") without the dispatcher lumping every
    non-``TypeError`` into ``-32603`` INTERNAL_ERROR.
    """

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class PendingRequestCancelled(Exception):
    """Raised when a pending server → client request is cancelled or the
    connection is torn down before a response arrives.

    Callers of :meth:`AcpServer.call` that block on the returned
    :class:`_PendingRequest` see this exception when the wait completes
    without a real response — e.g. because :meth:`AcpServer.run` exited
    (EOF, stdin disconnect) or because the pending slot was explicitly
    cancelled. Handlers map it to deterministic "reject tool" / "end turn
    cancelled" behavior per ACP's timeout/disconnect guarantees.
    """


class _PendingRequest:
    """Tracks a single outbound server → client request awaiting a response.

    Stored in :attr:`AcpServer._pending_requests` keyed by the outgoing
    request id. A handler calling :meth:`AcpServer.call` gets one of these
    back and blocks on :meth:`wait` until the matching response lands on
    the read loop, the connection drops, or the slot is cancelled.

    Three terminal states (mutually exclusive):
      - ``result`` is populated → success, ``error`` and ``cancelled`` are not
      - ``error`` is populated → JSON-RPC error response from the client
      - ``cancelled`` is True → disconnect / shutdown / explicit cancel
    """

    __slots__ = ("request_id", "event", "result", "error", "cancelled")

    def __init__(self, request_id: str) -> None:
        self.request_id = request_id
        self.event = threading.Event()
        self.result: Any = None
        self.error: Optional[JsonRpcError] = None
        self.cancelled: bool = False

    def wait(self, timeout: Optional[float] = None) -> Any:
        """Block until a response arrives, the request is cancelled, or
        ``timeout`` elapses.

        Returns the response ``result`` on success. Raises
        :class:`PendingRequestCancelled` on cancel/disconnect, or
        :class:`TimeoutError` on timeout. For JSON-RPC error responses the
        caller can inspect :attr:`error` directly via a re-raise variant
        that surfaces the code/message rather than swallowing them.
        """
        if not self.event.wait(timeout):
            raise TimeoutError(
                f"ACP call timed out waiting for response to {self.request_id}"
            )
        if self.cancelled:
            raise PendingRequestCancelled(
                f"ACP call {self.request_id} cancelled before response"
            )
        if self.error is not None:
            raise JsonRpcHandlerError(
                code=self.error.code,
                message=self.error.message,
                data=self.error.data,
            )
        return self.result


class AcpServer:
    """Stdio JSON-RPC 2.0 server with per-method dispatch.

    Usage::

        server = AcpServer()
        server.register("initialize", my_initialize_handler)
        server.run()  # blocks until stdin EOF

    For testing, pass in-memory streams::

        server = AcpServer(stdin=io.StringIO(...), stdout=io.StringIO())
        server.run()
    """

    def __init__(
        self,
        stdin: Optional[IO[str]] = None,
        stdout: Optional[IO[str]] = None,
        *,
        max_workers: int = 8,
    ) -> None:
        # Capture the *real* stdout BEFORE any swap so responses keep flowing.
        self._in: IO[str] = stdin if stdin is not None else sys.stdin
        self._out: IO[str] = stdout if stdout is not None else sys.stdout
        self._write_lock = threading.Lock()
        self._handlers: Dict[str, Handler] = {}

        # Connection-scoped state populated by the `initialize` handshake
        # (Issue 02). Handlers read/write this via ``server.state``.
        self.state = AcpConnectionState()

        # Session registry (Issue 03). Empty at construction; populated by
        # ``session/new`` in Issue 04. Shutdown is wired in ``run``'s
        # finally block so stdin EOF tears sessions down cleanly.
        self.sessions = AcpSessionManager()

        # Concurrent dispatch (Issue 08). See the module docstring for the
        # full rationale. Created lazily the first time ``run`` is called
        # so tests that construct a server and never call ``run`` do not
        # leak a thread pool — and so we can recreate the pool if ``run``
        # is called twice on the same server (unusual but not illegal).
        self._executor: Optional[ThreadPoolExecutor] = None
        self._max_workers = max_workers
        # Keep Futures in flight so the drain in ``run()``'s finally block
        # can wait for them. We don't strictly need this — ``executor
        # .shutdown(wait=True)`` handles it — but holding references
        # prevents the GC from surprising us during debugging.
        self._in_flight: List[Future] = []
        self._in_flight_lock = threading.Lock()

        # Pending outbound requests (Issue 08): server → client calls
        # blocked on a response. Keyed by outgoing request id.
        self._pending_requests: Dict[str, _PendingRequest] = {}
        self._pending_lock = threading.Lock()

        # Only install process-wide guards when we are attached to the real
        # stdio (the module-entry case). In tests we pass explicit streams
        # and don't want to mutate global state.
        if stdin is None and stdout is None:
            self._install_stdout_guard()
            self._install_log_guard()

    # ------------------------------------------------------------------
    # Guards
    # ------------------------------------------------------------------

    def _install_stdout_guard(self) -> None:
        """Redirect ``sys.stdout`` to ``sys.stderr`` to prevent pollution.

        Any ``print()`` call anywhere in the process after this point will
        land on stderr. JSON-RPC responses are written through the captured
        ``self._out`` handle and are unaffected.
        """
        sys.stdout = sys.stderr

    def _install_log_guard(self) -> None:
        """Attach a stderr ``StreamHandler`` to the ``agentao`` package logger
        if it has none yet.

        The project's main logging setup lives in ``agentao/llm/client.py``
        and only runs when ``LLMClient`` is instantiated. If any Agentao
        submodule logs before that happens, Python's default "last resort"
        handler would write to stderr — which is fine for us, but
        :class:`logging.lastResort` has level ``WARNING``. To capture INFO
        and DEBUG messages cleanly on stderr until ``LLMClient`` takes over,
        we install an explicit handler here.

        This is idempotent: if any handler already exists on the ``agentao``
        logger, we leave it alone.
        """
        pkg_logger = logging.getLogger("agentao")
        if pkg_logger.handlers:
            return
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        pkg_logger.addHandler(handler)
        pkg_logger.setLevel(logging.INFO)

    # ------------------------------------------------------------------
    # Handler registration
    # ------------------------------------------------------------------

    def register(self, method: str, handler: Handler) -> None:
        """Register a handler for a JSON-RPC method name.

        Registering the same method twice replaces the previous handler.
        """
        self._handlers[method] = handler

    # ------------------------------------------------------------------
    # Read loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Block reading newline-delimited JSON-RPC requests from stdin.

        Returns when stdin reaches EOF (clean shutdown). Any exception during
        per-line processing is caught and logged; the loop continues so a
        single malformed message cannot crash the server.

        Dispatch is concurrent: request handlers run on the
        :attr:`_executor` thread pool while the read thread keeps pumping
        stdin. This is what lets :meth:`call` (server → client blocking
        requests, Issue 08) work — a worker thread can sit inside
        ``transport.confirm_tool`` waiting for a response to land on the
        read loop.

        On any exit path — EOF, read-side exception, external termination —
        the ``finally`` block runs in this order:

          1. Cancels all pending outbound requests, unblocking any worker
             still inside :meth:`call`. This turns a client disconnect
             into deterministic "tool rejected" / "turn cancelled"
             behavior rather than an indefinite hang.
          2. Trips every active session's cancel token via
             :meth:`AcpSessionManager.cancel_all_active_turns`. Without
             this, a worker that is mid-turn (inside an LLM call or tool
             execution) but NOT blocked on a server→client request has no
             stop signal, and step 3 would hang indefinitely until the
             turn finished naturally.
          3. Drains the executor with ``shutdown(wait=True)``. We wait so
             ``run()``'s return is a happens-before barrier for all
             handler writes — tests that inspect ``stdout`` after
             ``run()`` see every response. Steps 1 + 2 guarantee every
             worker has a stop signal, so this drain is bounded.
          4. Calls :meth:`AcpSessionManager.close_all` so every
             session-owned Agentao runtime gets its MCP connections
             disconnected. Done last so handlers running during shutdown
             can still look up their session state.
        """
        # Recreate the executor each call so ``run()`` can be invoked more
        # than once on a server instance (useful for some test patterns).
        self._executor = ThreadPoolExecutor(
            max_workers=self._max_workers,
            thread_name_prefix="acp-handler",
        )
        try:
            while True:
                try:
                    line = self._in.readline()
                except Exception:  # pragma: no cover — defensive
                    logger.exception("acp: fatal error reading from stdin")
                    return
                if not line:
                    return  # EOF
                line = line.strip()
                if not line:
                    continue  # ignore blank lines
                try:
                    self._handle_line(line)
                except Exception:  # pragma: no cover — defensive
                    logger.exception("acp: unhandled error while processing line")
        finally:
            # Step 1: unblock any worker waiting on a server → client
            # response (e.g. one stuck in ``transport.confirm_tool``).
            # The worker sees ``PendingRequestCancelled`` and unwinds to
            # a deterministic "tool rejected" return.
            self._cancel_all_pending_requests("connection-closed")
            # Step 2: trip every active session's cancel token. This is
            # the stop signal for workers that are mid-turn but NOT
            # blocked on an outbound request — without it, an in-flight
            # LLM call or tool execution would keep running and the
            # ``shutdown(wait=True)`` below would block until it finished
            # naturally, hanging the process for the duration of the
            # remaining turn. We deliberately do NOT call
            # ``sessions.close_all()`` here because that would also
            # disconnect MCP servers while the worker is still using
            # them; ``cancel_all_active_turns`` only flips the cancel
            # bit and leaves runtime state intact.
            self.sessions.cancel_all_active_turns("connection-closed")
            # Step 3: drain executor. ``shutdown(wait=True)`` blocks
            # until every submitted handler finishes. Steps 1 + 2 mean
            # every worker now has a stop signal, so this drain is
            # bounded.
            executor = self._executor
            self._executor = None
            if executor is not None:
                executor.shutdown(wait=True)
            with self._in_flight_lock:
                self._in_flight.clear()
            # Step 4: tear down sessions last so handlers that ran during
            # shutdown can still look up their session state, and so MCP
            # disconnect happens after every worker has unwound.
            self.sessions.close_all()

    # ------------------------------------------------------------------
    # Per-line processing
    # ------------------------------------------------------------------

    def _handle_line(self, line: str) -> None:
        # 1) Parse JSON
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as e:
            # Per JSON-RPC 2.0, parse errors use id=null.
            self._write_error(None, PARSE_ERROR, f"Parse error: {e.msg}")
            return

        # 2) Validate shape before building a request object so we can return
        #    a proper INVALID_REQUEST with the original id if we can recover it.
        if not isinstance(raw, dict):
            self._write_error(None, INVALID_REQUEST, "Invalid Request: expected JSON object")
            return

        raw_id = raw.get("id")
        if raw.get("jsonrpc") != "2.0":
            self._write_error(raw_id, INVALID_REQUEST, "Invalid Request: jsonrpc must be '2.0'")
            return

        # 3) Classify: server → client *response* vs client → server *request*.
        #    A JSON-RPC response has ``result`` or ``error`` (and an id); a
        #    request/notification carries a ``method`` string. Anything else
        #    is malformed. Routing responses here — on the read thread —
        #    lets a worker blocked in :meth:`call` receive its answer
        #    without spinning up a second stdin reader.
        if "method" not in raw:
            if "result" in raw or "error" in raw:
                if raw_id is None:
                    logger.warning(
                        "acp: dropping response envelope with null id"
                    )
                    return
                self._route_response(raw)
                return
            # No method and no result/error → invalid request. Surface the
            # standard error with the recoverable id so the client can tell
            # what we rejected.
            self._write_error(raw_id, INVALID_REQUEST, "Invalid Request: missing method")
            return

        method = raw.get("method")
        if not isinstance(method, str) or not method:
            self._write_error(raw_id, INVALID_REQUEST, "Invalid Request: missing method")
            return

        req = JsonRpcRequest.from_dict(raw)

        # 4) Dispatch on a worker so the read loop stays responsive to
        #    server → client *response* messages arriving on stdin while
        #    a handler is blocked inside :meth:`call`. If :attr:`_executor`
        #    is None (tests that bypass ``run()``), fall back to synchronous
        #    dispatch so direct ``_handle_line`` calls still work.
        if self._executor is None:
            self._run_handler(req)
        else:
            future = self._executor.submit(self._run_handler, req)
            with self._in_flight_lock:
                self._in_flight.append(future)
                # Opportunistic cleanup of completed futures so the list
                # doesn't grow unbounded across a long session.
                self._in_flight = [f for f in self._in_flight if not f.done()]

    def _run_handler(self, req: JsonRpcRequest) -> None:
        """Execute a single request on the current thread and write the response.

        Extracted from :meth:`_handle_line` so the same code path serves
        both the synchronous fallback (``_executor is None``) and the
        concurrent executor case. Exceptions are mapped to JSON-RPC errors
        by :meth:`_dispatch`; anything else is defensive.
        """
        try:
            response = self._dispatch(req)
        except Exception:  # pragma: no cover — _dispatch traps everything
            logger.exception("acp: unexpected error in _dispatch for %s", req.method)
            return
        if req.is_notification():
            return
        try:
            self._write(response.to_dict())
        except Exception:  # pragma: no cover — defensive
            logger.exception("acp: error writing response for %s", req.method)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, req: JsonRpcRequest) -> JsonRpcResponse:
        handler = self._handlers.get(req.method)
        if handler is None:
            return JsonRpcResponse(
                id=req.id,
                error=JsonRpcError(code=METHOD_NOT_FOUND, message="Method not found"),
            )

        try:
            result = handler(req.params)
        except TypeError as e:
            # Treat TypeError as a parameter mismatch. Handlers that want to
            # signal invalid params explicitly should raise TypeError.
            return JsonRpcResponse(
                id=req.id,
                error=JsonRpcError(code=INVALID_PARAMS, message=f"Invalid params: {e}"),
            )
        except JsonRpcHandlerError as e:
            # Handler is asking for a specific JSON-RPC error code (e.g.
            # SERVER_NOT_INITIALIZED). Honor it verbatim.
            return JsonRpcResponse(
                id=req.id,
                error=JsonRpcError(code=e.code, message=e.message, data=e.data),
            )
        except Exception as e:
            logger.exception("acp: handler for %s raised", req.method)
            data: Optional[Any] = None
            if logger.isEnabledFor(logging.DEBUG):
                data = {"traceback": traceback.format_exc()}
            return JsonRpcResponse(
                id=req.id,
                error=JsonRpcError(code=INTERNAL_ERROR, message=f"Internal error: {e}", data=data),
            )

        return JsonRpcResponse(id=req.id, result=result)

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def _write(self, payload: dict) -> None:
        """Thread-safe single-line write of a JSON payload to stdout.

        Uses compact separators to guarantee one line per message (no
        embedded newlines from pretty-printing).
        """
        line = json.dumps(payload, separators=(",", ":")) + "\n"
        with self._write_lock:
            self._out.write(line)
            self._out.flush()

    def _write_error(self, id_: Any, code: int, message: str) -> None:
        """Convenience: build and write an error response."""
        resp = JsonRpcResponse(
            id=id_,
            error=JsonRpcError(code=code, message=message),
        )
        self._write(resp.to_dict())

    def write_notification(self, method: str, params: dict) -> None:
        """Send a JSON-RPC notification from the server to the client.

        Used by :class:`ACPTransport` to emit ``session/update``
        notifications. Exposed now so the shared write lock is the single
        point of stdout serialization.
        """
        self._write(
            {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
            }
        )

    # ------------------------------------------------------------------
    # Server → client requests (Issue 08)
    # ------------------------------------------------------------------

    def call(self, method: str, params: dict) -> _PendingRequest:
        """Send a server → client JSON-RPC request and return a pending handle.

        The caller blocks on the returned :class:`_PendingRequest` via
        :meth:`_PendingRequest.wait`. When the client's matching response
        lands on the read loop, :meth:`_route_response` fills the
        pending and sets its event.

        The id format is ``srv_<16hex>``, distinguishable from
        client-originated ids (which are typically sequential integers
        in ACP clients). Using a string id keeps us safe even if a future
        client sends integer ids that collide with a counter.

        Deterministic failure modes:
          - Client disconnects (stdin EOF) → :meth:`run`'s finally clause
            cancels every pending slot, :meth:`_PendingRequest.wait`
            raises :class:`PendingRequestCancelled`.
          - Client returns a JSON-RPC error response → :meth:`wait`
            raises :class:`JsonRpcHandlerError` with the error details.
          - Caller supplies a timeout → :meth:`wait` raises
            :class:`TimeoutError`.
        """
        req_id = f"srv_{uuid.uuid4().hex[:16]}"
        pending = _PendingRequest(req_id)
        with self._pending_lock:
            self._pending_requests[req_id] = pending
        try:
            self._write(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "method": method,
                    "params": params,
                }
            )
        except Exception:
            # Write failed — drop the pending slot so we don't leak it.
            with self._pending_lock:
                self._pending_requests.pop(req_id, None)
            raise
        return pending

    def _route_response(self, raw: dict) -> None:
        """Fill the pending request matching ``raw["id"]`` and wake its waiter.

        Unknown ids are logged and dropped: either the corresponding
        pending slot was already cancelled (shutdown race) or the client
        is responding to an id we never sent. Either way we can't act on
        it, and crashing here would break the read loop for well-formed
        traffic that follows.
        """
        req_id = raw.get("id")
        with self._pending_lock:
            pending = self._pending_requests.pop(req_id, None)
        if pending is None:
            logger.warning("acp: ignoring response for unknown id %r", req_id)
            return
        if "error" in raw and raw["error"] is not None:
            err = raw["error"]
            if not isinstance(err, dict):
                pending.error = JsonRpcError(
                    code=INTERNAL_ERROR,
                    message=f"malformed error object: {err!r}",
                )
            else:
                pending.error = JsonRpcError(
                    code=int(err.get("code", INTERNAL_ERROR)),
                    message=str(err.get("message", "")),
                    data=err.get("data"),
                )
        else:
            pending.result = raw.get("result")
        pending.event.set()

    def _cancel_all_pending_requests(self, reason: str) -> None:
        """Cancel every outstanding server → client request.

        Called from :meth:`run`'s finally clause so disconnects and
        shutdowns unblock every worker inside :meth:`call`.
        :attr:`_PendingRequest.wait` will raise
        :class:`PendingRequestCancelled` once its event fires.
        """
        with self._pending_lock:
            pendings = list(self._pending_requests.values())
            self._pending_requests.clear()
        for pending in pendings:
            pending.cancelled = True
            pending.event.set()
        if pendings:
            logger.info(
                "acp: cancelled %d pending server→client request(s): %s",
                len(pendings),
                reason,
            )
