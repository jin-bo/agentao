"""Minimal runnable headless consumer for the Agentao ACP runtime.

This sample is the authoritative Week 1 regression fixture referenced
throughout ``docs/features/headless-runtime.md`` and the
``HEADLESS_RUNTIME_PLAN``. It exercises the four behaviours every
headless embedder must rely on:

1. Initialize :class:`ACPManager` from an inline ``AcpClientConfig``
2. Run one non-interactive turn and print the typed
   :class:`ServerStatus` snapshot
3. Walk an error path — a non-interactive turn that the server tries
   to interrupt for permission and that therefore raises
   :class:`AcpInteractionRequiredError`
4. Walk a cancel path — a slow non-interactive turn aborted by
   ``cancel_turn``

Running from the repository root::

    uv run python examples/headless_worker.py

The example ships its own mock ACP server (a small Python script
written to ``tempfile.mkdtemp()`` on each run) so it never depends on
a user's ``.agentao/acp.json``. It is intended as a CI smoke job:
exit code ``0`` means every required path worked.
"""

from __future__ import annotations

import json
import sys
import tempfile
import textwrap
import threading
import time
from pathlib import Path
from typing import List

from agentao.acp_client import (
    ACPManager,
    AcpClientConfig,
    AcpClientError,
    AcpErrorCode,
    AcpInteractionRequiredError,
    AcpServerConfig,
    PromptResult,
    ServerStatus,
)


_MOCK_SERVER_SOURCE = textwrap.dedent("""\
    import json
    import queue
    import sys
    import threading
    import time

    stdin_raw = sys.stdin.buffer if hasattr(sys.stdin, 'buffer') else sys.stdin

    write_lock = threading.Lock()
    cancel_event = threading.Event()
    pending_responses = {}
    response_cv = threading.Condition()
    next_srv_id = [1000]


    def write(obj):
        with write_lock:
            sys.stdout.write(json.dumps(obj) + "\\n")
            sys.stdout.flush()


    def respond(rid, result):
        write({"jsonrpc": "2.0", "id": rid, "result": result})


    def server_request(method, params):
        sid = next_srv_id[0]
        next_srv_id[0] += 1
        write({"jsonrpc": "2.0", "id": sid, "method": method, "params": params})
        with response_cv:
            while sid not in pending_responses:
                response_cv.wait(timeout=5)
                if sid in pending_responses:
                    break
            return pending_responses.pop(sid)


    def reader(q):
        for raw in stdin_raw:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "method" not in req:
                rid = req.get("id")
                with response_cv:
                    pending_responses[rid] = req
                    response_cv.notify_all()
                continue
            if req.get("method") == "session/cancel":
                cancel_event.set()
                continue
            q.put(req)
        q.put(None)


    q = queue.Queue()
    threading.Thread(target=reader, args=(q,), daemon=True).start()

    while True:
        req = q.get()
        if req is None:
            break
        method = req.get("method", "")
        rid = req.get("id")
        params = req.get("params", {})
        if method == "initialize":
            respond(rid, {"protocolVersion": 1, "agentCapabilities": {},
                          "agentInfo": {"name": "mock-headless"}})
        elif method == "session/new":
            respond(rid, {"sessionId": "sess_headless_worker"})
        elif method == "session/prompt":
            text = ""
            for block in params.get("prompt", []):
                if block.get("type") == "text":
                    text += block.get("text", "")
            cancel_event.clear()
            if text == "permission":
                server_request("session/request_permission", {
                    "toolCall": {"title": "rm -rf /", "kind": "shell"},
                    "options": [
                        {"id": "allow_once", "label": "Allow"},
                        {"id": "reject_once", "label": "Reject"},
                    ],
                })
                respond(rid, {"stopReason": "end_turn"})
            elif text == "slow":
                for _ in range(50):
                    if cancel_event.is_set():
                        respond(rid, {"stopReason": "cancelled"})
                        break
                    time.sleep(0.1)
                else:
                    respond(rid, {"stopReason": "end_turn"})
            else:
                respond(rid, {"stopReason": "end_turn"})
        else:
            if rid is not None:
                write({"jsonrpc": "2.0", "id": rid,
                       "error": {"code": -32601, "message": "unknown"}})
""")


SERVER_NAME = "mock-worker"


def _write_mock_server(workdir: Path) -> Path:
    script = workdir / "mock_acp_server.py"
    script.write_text(_MOCK_SERVER_SOURCE, encoding="utf-8")
    return script


def _build_manager(workdir: Path) -> ACPManager:
    script = _write_mock_server(workdir)
    cfg = AcpClientConfig(servers={
        SERVER_NAME: AcpServerConfig(
            command=sys.executable,
            args=[str(script)],
            env={},
            cwd=str(workdir),
            request_timeout_ms=10_000,
            description="inline mock ACP server",
        ),
    })
    return ACPManager(cfg)


def _print_status(mgr: ACPManager, label: str) -> None:
    snapshot: List[ServerStatus] = mgr.get_status()
    print(f"\n[status:{label}]")
    for s in snapshot:
        ready = mgr.readiness(s.server)
        print(
            f"  server={s.server} state={s.state} pid={s.pid} "
            f"has_active_turn={s.has_active_turn} readiness={ready}"
        )
        print(
            f"    session={s.active_session_id!r} "
            f"inbox_pending={s.inbox_pending} "
            f"interaction_pending={s.interaction_pending}"
        )
        if s.last_error is not None:
            print(
                f"    last_error={s.last_error!r} at={s.last_error_at}"
            )
        if s.config_warnings:
            print(f"    config_warnings={s.config_warnings}")


def run_success_turn(mgr: ACPManager) -> PromptResult:
    print("\n>>> Running a non-interactive success turn")
    result = mgr.prompt_once(SERVER_NAME, "hello", timeout=5)
    print(f"    stop_reason={result.stop_reason!r} "
          f"session_id={result.session_id!r}")
    _print_status(mgr, "after-success")
    return result


def run_error_turn(mgr: ACPManager) -> None:
    """Walk the non-interactive error path.

    A non-interactive turn that the server tries to interrupt for
    permission must raise :class:`AcpInteractionRequiredError` with
    ``code == AcpErrorCode.INTERACTION_REQUIRED``. This is the only
    error path an embedder must handle to safely run unattended.
    """
    print("\n>>> Running a non-interactive error turn")
    try:
        mgr.prompt_once(SERVER_NAME, "permission", timeout=5)
    except AcpInteractionRequiredError as exc:
        assert exc.code == AcpErrorCode.INTERACTION_REQUIRED
        print(f"    caught AcpInteractionRequiredError: "
              f"server={exc.server!r} prompt={exc.prompt!r}")
    else:
        raise AssertionError(
            "expected AcpInteractionRequiredError on 'permission' prompt"
        )
    _print_status(mgr, "after-error")


def run_cancel_turn(mgr: ACPManager) -> None:
    """Walk the cancel path.

    Start a slow non-interactive turn on a background thread, let it
    reach the server, then call :meth:`ACPManager.cancel_turn`.
    ``prompt_once`` returns a :class:`PromptResult` with
    ``stop_reason == "cancelled"`` — no exception.
    """
    print("\n>>> Running a cancellable non-interactive turn")
    box: dict = {}

    def _target() -> None:
        try:
            box["result"] = mgr.prompt_once(SERVER_NAME, "slow", timeout=10)
        except BaseException as exc:
            box["error"] = exc

    t = threading.Thread(target=_target, daemon=True)
    t.start()

    # Give the turn a moment to register as active on the manager slot.
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        statuses = mgr.get_status()
        if any(s.has_active_turn for s in statuses):
            break
        time.sleep(0.05)
    else:
        raise AssertionError(
            "turn never reached has_active_turn=True within 3s"
        )
    _print_status(mgr, "during-slow-turn")

    mgr.cancel_turn(SERVER_NAME)
    t.join(timeout=5)
    if t.is_alive():
        raise AssertionError("cancel_turn did not finalize within 5s")

    if "error" in box:
        raise AssertionError(
            f"cancel turn raised unexpectedly: {box['error']!r}"
        )
    result = box["result"]
    assert isinstance(result, PromptResult)
    print(f"    stop_reason={result.stop_reason!r}")
    _print_status(mgr, "after-cancel")


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="agentao-headless-worker-"))
    print(f"[headless_worker] workdir={workdir}")
    mgr = _build_manager(workdir)
    try:
        _print_status(mgr, "initial")
        run_success_turn(mgr)
        run_error_turn(mgr)
        run_cancel_turn(mgr)
        print("\n[headless_worker] all paths completed successfully")
        return 0
    finally:
        mgr.stop_all()


if __name__ == "__main__":
    raise SystemExit(main())
