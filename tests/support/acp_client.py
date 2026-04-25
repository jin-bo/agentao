"""Shared fake ACP subprocess servers and manager/handle builders.

Two fakes live here:

* ``JSONRPC_MOCK_SCRIPT`` / :func:`make_jsonrpc_mock_handle` — a minimal
  NDJSON server (echo / fail / slow / notify_me). Used by JSON-RPC-layer
  tests that only need a responding peer.
* ``INTERACTION_SERVER_SCRIPT`` / :func:`make_interaction_mock_manager` —
  a server that emits server-initiated requests (permission, ask_user)
  mid-turn. Used by headless / non-interactive policy tests.

Both scripts stay as textwrap'd source because they run in a subprocess
over NDJSON. The Python helpers in this module own the script-on-disk
setup and return the started :class:`ACPProcessHandle` / :class:`ACPManager`.

The scripts are intentionally **fakes, not reference implementations** —
they only handle the methods each test suite needs. Do not extend them to
mirror real server behaviour; add a new fake instead.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from typing import Optional

from agentao.acp_client.manager import ACPManager
from agentao.acp_client.models import (
    AcpClientConfig,
    AcpServerConfig,
    InteractionPolicy,
)
from agentao.acp_client.process import ACPProcessHandle


# ---------------------------------------------------------------------------
# JSON-RPC fake server (echo / fail / slow / notify_me / session/new)
# ---------------------------------------------------------------------------

JSONRPC_MOCK_SCRIPT = textwrap.dedent("""\
    import json
    import sys

    def respond(rid, result):
        msg = {"jsonrpc": "2.0", "id": rid, "result": result}
        sys.stdout.write(json.dumps(msg) + "\\n")
        sys.stdout.flush()

    def respond_error(rid, code, message):
        msg = {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}
        sys.stdout.write(json.dumps(msg) + "\\n")
        sys.stdout.flush()

    def send_notification(method, params=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        sys.stdout.write(json.dumps(msg) + "\\n")
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = req.get("method", "")
        rid = req.get("id")

        if method == "initialize":
            respond(rid, {
                "protocolVersion": 1,
                "agentCapabilities": {"loadSession": True},
                "agentInfo": {"name": "mock", "title": "Mock", "version": "0.1"},
            })
        elif method == "session/new":
            respond(rid, {"sessionId": "sess_test123"})
        elif method == "echo":
            respond(rid, req.get("params", {}))
        elif method == "fail":
            respond_error(rid, -32603, "intentional failure")
        elif method == "notify_me":
            send_notification("session/update", {"status": "hello"})
            respond(rid, {"ok": True})
        elif method == "slow":
            import time
            time.sleep(5)
            respond(rid, {"ok": True})
        else:
            respond_error(rid, -32601, f"method not found: {method}")
""")


def make_jsonrpc_mock_handle(tmp_path: Path) -> ACPProcessHandle:
    """Return a started-able handle that spawns :data:`JSONRPC_MOCK_SCRIPT`.

    The caller is responsible for ``handle.start()`` / ``handle.stop()``.
    """
    script = tmp_path / "mock_acp_server.py"
    script.write_text(JSONRPC_MOCK_SCRIPT, encoding="utf-8")

    config = AcpServerConfig(
        command=sys.executable,
        args=[str(script)],
        env={},
        cwd=str(tmp_path),
    )
    return ACPProcessHandle("mock", config)


# ---------------------------------------------------------------------------
# Interaction fake server (server-initiated permission / ask_user)
# ---------------------------------------------------------------------------

INTERACTION_SERVER_SCRIPT = textwrap.dedent("""\
    import json
    import sys
    import threading
    import queue
    import time

    stdin_raw = sys.stdin.buffer if hasattr(sys.stdin, 'buffer') else sys.stdin

    pending_responses = {}
    response_cv = threading.Condition()
    cancel_event = threading.Event()
    write_lock = threading.Lock()
    next_srv_id = [1000]

    def write(obj):
        line = json.dumps(obj) + "\\n"
        with write_lock:
            sys.stdout.write(line)
            sys.stdout.flush()

    def respond(rid, result):
        write({"jsonrpc": "2.0", "id": rid, "result": result})

    def respond_error(rid, code, message):
        write({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}})

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

    def reader_thread():
        for raw_line in stdin_raw:
            line = raw_line.decode("utf-8", errors="replace").strip()
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
            method = req.get("method", "")
            if method == "session/cancel":
                cancel_event.set()
                continue
            msg_queue.put(req)
        msg_queue.put(None)

    msg_queue = queue.Queue()
    t = threading.Thread(target=reader_thread, daemon=True)
    t.start()

    while True:
        req = msg_queue.get()
        if req is None:
            break
        method = req.get("method", "")
        rid = req.get("id")
        params = req.get("params", {})
        if method == "initialize":
            respond(rid, {"protocolVersion": 1, "agentCapabilities": {},
                          "agentInfo": {"name": "mock"}})
        elif method == "session/new":
            respond(rid, {"sessionId": "sess_mock"})
        elif method == "session/prompt":
            text = ""
            for block in params.get("prompt", []):
                if block.get("type") == "text":
                    text += block.get("text", "")
            cancel_event.clear()
            if text == "permission":
                reply = server_request("session/request_permission", {
                    "toolCall": {"title": "rm -rf /", "kind": "shell"},
                    "options": [
                        {"id": "allow_once", "label": "Allow"},
                        {"id": "reject_once", "label": "Reject"},
                    ],
                })
                respond(rid, {"stopReason": "end_turn",
                              "_interaction": reply.get("result")})
            elif text == "permission_alt":
                # Non-standard option ids: manager must pick the one
                # whose 'kind' signals reject, not a hardcoded id.
                reply = server_request("session/request_permission", {
                    "toolCall": {"title": "x", "kind": "shell"},
                    "options": [
                        {"optionId": "go_ahead", "kind": "allow_once",
                         "name": "Go ahead"},
                        {"optionId": "decline_now", "kind": "reject_once",
                         "name": "Decline"},
                    ],
                })
                respond(rid, {"stopReason": "end_turn",
                              "_interaction": reply.get("result")})
            elif text == "permission_no_options":
                # No options array: manager must send outcome=cancelled,
                # not a bogus optionId.
                reply = server_request("session/request_permission", {
                    "toolCall": {"title": "x"},
                })
                respond(rid, {"stopReason": "end_turn",
                              "_interaction": reply.get("result")})
            elif text == "ask_user":
                try:
                    reply = server_request("_agentao.cn/ask_user", {
                        "question": "what now?",
                    })
                    respond(rid, {"stopReason": "end_turn",
                                  "_interaction": reply.get("result")})
                except Exception:
                    respond(rid, {"stopReason": "end_turn", "_error": True})
            elif text == "permission_then_wait":
                # Auto-reject should happen fast; then server holds the prompt
                # RPC open so tests can observe BUSY before terminal state.
                server_request("session/request_permission", {
                    "toolCall": {"title": "x", "kind": "shell"},
                })
                # Hold until cancel or 3s.
                for _ in range(30):
                    if cancel_event.is_set():
                        respond(rid, {"stopReason": "cancelled"})
                        break
                    time.sleep(0.1)
                else:
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
                respond_error(rid, -32601, "unknown")
""")


def make_interaction_mock_manager(
    tmp_path: Path,
    name: str = "srv",
    *,
    non_interactive_policy: Optional[InteractionPolicy] = None,
    request_timeout_ms: int = 10_000,
) -> ACPManager:
    """Return an :class:`ACPManager` wired to the interaction fake.

    Covers both the "default (reject_all)" and "server-default policy set"
    call patterns so the same helper handles both legacy ``_make_mgr`` and
    ``_make_mgr_with_policy`` call sites. The caller is responsible for
    ``mgr.start_all()`` / ``mgr.stop_all()``.
    """
    script = tmp_path / "mock_interaction_server.py"
    script.write_text(INTERACTION_SERVER_SCRIPT, encoding="utf-8")
    # ``non_interactive_policy=None`` means "use AcpServerConfig's own
    # default" (``reject_all``); ``AcpServerConfig.__post_init__`` rejects
    # bare None, so only pass it through when the caller set it.
    kwargs = dict(
        command=sys.executable,
        args=[str(script)],
        env={},
        cwd=str(tmp_path),
        request_timeout_ms=request_timeout_ms,
    )
    if non_interactive_policy is not None:
        kwargs["non_interactive_policy"] = non_interactive_policy
    cfg = AcpServerConfig(**kwargs)
    return ACPManager(AcpClientConfig(servers={name: cfg}))
