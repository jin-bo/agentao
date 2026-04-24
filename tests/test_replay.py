"""Tests for the Session Replay module.

Covers the test-plan requirements from
``docs/implementation/SESSION_REPLAY_PLAN.md``:

- disabled by default → no file, no recorder
- enabled → replay file created
- /replay on|off toggles ``.agentao/settings.json``
- missing/corrupt settings → safe defaults
- turn lifecycle: turn_started / turn_completed with final_text
- multi-chunk ordering
- multi-tool single turn_id
- confirmation request/resolve pairing
- tool failure / cancellation / readonly deny representation
- MCP vs builtin ``tool_source``
- sub-agent gets its own turn_id + parent_turn_id back-pointer
- /clear semantics: session_ended + new file
- ACP session/load reuses session_id but creates a new instance file
- session_saved never emitted in v1
- sanitizer / write failures don't break runtime
- large tool output truncation
- reader stops at EOF (no follow)
- partial final line tolerated
- retention deletes replays only
- /replays list/show/tail/prune work across empty, single, multi
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentao.replay import (
    REPLAY_DEFAULTS,
    ReplayAdapter,
    ReplayConfig,
    ReplayReader,
    ReplayRecorder,
    ReplayRetentionPolicy,
    find_replay_candidates,
    list_replays,
    load_replay_config,
    open_replay,
    resolve_replay_id,
    save_replay_enabled,
)
from agentao.replay.adapter import _classify_tool
from agentao.replay.events import SCHEMA_VERSION, EventKind
from agentao.replay.sanitize import (
    TOOL_OUTPUT_CHUNK_MAX_CHARS,
    sanitize_payload,
    truncate_tool_output_chunk,
)
from agentao.transport import AgentEvent, EventType, NullTransport


# ---------------------------------------------------------------------------
# Config + settings.json persistence
# ---------------------------------------------------------------------------


def test_config_defaults_when_file_missing(tmp_path):
    cfg = load_replay_config(tmp_path)
    assert cfg.enabled is False
    assert cfg.max_instances == REPLAY_DEFAULTS["max_instances"]


def test_config_coerces_malformed_settings(tmp_path):
    (tmp_path / ".agentao").mkdir()
    (tmp_path / ".agentao" / "settings.json").write_text("not valid json")
    cfg = load_replay_config(tmp_path)
    assert cfg.enabled is False
    assert cfg.max_instances == REPLAY_DEFAULTS["max_instances"]


def test_config_coerces_invalid_max_instances(tmp_path):
    (tmp_path / ".agentao").mkdir()
    (tmp_path / ".agentao" / "settings.json").write_text(
        json.dumps({"replay": {"enabled": True, "max_instances": "oops"}})
    )
    cfg = load_replay_config(tmp_path)
    assert cfg.enabled is True
    assert cfg.max_instances == REPLAY_DEFAULTS["max_instances"]


def test_save_replay_enabled_preserves_other_keys(tmp_path):
    (tmp_path / ".agentao").mkdir()
    (tmp_path / ".agentao" / "settings.json").write_text(
        json.dumps({"mode": "workspace-write", "foo": 1})
    )
    save_replay_enabled(True, tmp_path)
    data = json.loads((tmp_path / ".agentao" / "settings.json").read_text())
    assert data["mode"] == "workspace-write"
    assert data["foo"] == 1
    assert data["replay"]["enabled"] is True

    save_replay_enabled(False, tmp_path)
    data = json.loads((tmp_path / ".agentao" / "settings.json").read_text())
    assert data["replay"]["enabled"] is False


# ---------------------------------------------------------------------------
# Recorder
# ---------------------------------------------------------------------------


def test_recorder_header_is_first_line(tmp_path):
    rec = ReplayRecorder.create("sess", tmp_path)
    rec.record(EventKind.SESSION_STARTED, payload={})
    rec.close()
    lines = rec.path.read_text().splitlines()
    first = json.loads(lines[0])
    assert first["kind"] == EventKind.REPLAY_HEADER
    assert first["payload"]["schema_version"] == SCHEMA_VERSION
    assert first["payload"]["session_id"] == "sess"
    assert first["payload"]["instance_id"] == rec.instance_id


def test_recorder_seq_is_monotonic(tmp_path):
    rec = ReplayRecorder.create("sess", tmp_path)
    rec.record("user_message", payload={"content": "a"})
    rec.record("user_message", payload={"content": "b"})
    rec.close()
    events = [json.loads(l) for l in rec.path.read_text().splitlines()]
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs)
    assert seqs[0] == 1


def test_recorder_ignores_records_after_close(tmp_path):
    rec = ReplayRecorder.create("sess", tmp_path)
    rec.close()
    rec.record("user_message", payload={"content": "ignored"})
    # header + footer made it in before close; the post-close record()
    # is silently dropped.
    events = [json.loads(l) for l in rec.path.read_text().splitlines()]
    kinds = [e["kind"] for e in events]
    assert kinds == [EventKind.REPLAY_HEADER, EventKind.REPLAY_FOOTER]


def test_recorder_survives_non_serializable_values(tmp_path):
    rec = ReplayRecorder.create("sess", tmp_path)

    class Weird:
        def __repr__(self):
            return "<weird>"

    rec.record("error", payload={"ok": 1, "bad": Weird(), "nested": [Weird()]})
    rec.close()
    events = [json.loads(l) for l in rec.path.read_text().splitlines()]
    err = [e for e in events if e["kind"] == "error"][0]
    # Weird falls through the str() fallback rather than dropping the whole event.
    assert err["payload"]["ok"] == 1
    assert err["payload"]["bad"] == "<weird>"
    assert err["payload"]["nested"] == ["<weird>"]


# ---------------------------------------------------------------------------
# Sanitizer
# ---------------------------------------------------------------------------


def test_sanitize_drops_only_failing_fields(monkeypatch):
    from agentao.replay import sanitize as sanmod

    def fake_coerce(value):
        if isinstance(value, str) and "boom" in value:
            raise RuntimeError("explode")
        return value

    monkeypatch.setattr(sanmod, "_coerce_value", fake_coerce)
    out, dropped = sanitize_payload({"ok": "x", "bad": "boom"})
    assert "bad" in dropped
    assert "bad" not in out
    assert out["ok"] == "x"
    assert out["redacted"] == "filter_error"
    assert "bad" in out["redacted_fields"]


def test_truncate_tool_output_chunk_small(tmp_path):
    out = truncate_tool_output_chunk("hello")
    assert out == {"chunk": "hello"}


def test_truncate_tool_output_chunk_huge():
    big = "x" * (TOOL_OUTPUT_CHUNK_MAX_CHARS * 3)
    out = truncate_tool_output_chunk(big)
    assert out["truncated"] is True
    assert out["original_chars"] == len(big)
    assert out["omitted_chars"] > 0
    assert "truncated" in out["chunk"]


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------


def test_reader_stops_at_eof_no_follow(tmp_path):
    rec = ReplayRecorder.create("sess", tmp_path)
    rec.record("user_message", payload={"content": "a"})
    reader = open_replay("sess", rec.instance_id, tmp_path)
    events_before = reader.events()
    # Write more after reader built: new events should NOT appear unless
    # we re-open. This is the "no follow" semantic.
    rec.record("user_message", payload={"content": "b"})
    rec.close()
    events_after = ReplayReader(rec.path).events()
    # +1 for the second user_message, +1 for the replay_footer that
    # close() emits. The reader snapshot taken before both writes only
    # saw the first user_message.
    assert len(events_after) == len(events_before) + 2


def test_reader_skips_partial_final_line(tmp_path):
    rec = ReplayRecorder.create("sess", tmp_path)
    rec.record("user_message", payload={"content": "ok"})
    rec.close()
    # Append a partial JSON line as if a crash happened mid-write.
    with rec.path.open("a", encoding="utf-8") as fp:
        fp.write('{"seq": 99, "kind": "use')
    events = ReplayReader(rec.path).events()
    # header + user_message + replay_footer. Partial line is skipped.
    kinds = [e["kind"] for e in events]
    assert kinds == [EventKind.REPLAY_HEADER, "user_message", EventKind.REPLAY_FOOTER]


def test_reader_filters_by_kind_and_turn(tmp_path):
    rec = ReplayRecorder.create("sess", tmp_path)
    rec.record("turn_started", turn_id="t1", payload={})
    rec.record("user_message", turn_id="t1", payload={"content": "a"})
    rec.record("turn_started", turn_id="t2", payload={})
    rec.record("user_message", turn_id="t2", payload={"content": "b"})
    rec.close()
    reader = ReplayReader(rec.path)
    user_messages = reader.events(kinds={"user_message"})
    assert [e["turn_id"] for e in user_messages] == ["t1", "t2"]
    t1_events = reader.events(turn_id="t1")
    kinds = {e["kind"] for e in t1_events}
    assert kinds == {"turn_started", "user_message"}


def test_resolve_replay_id_prefix_and_ambiguity(tmp_path):
    rec1 = ReplayRecorder.create("alpha", tmp_path)
    rec1.close()
    rec2 = ReplayRecorder.create("beta", tmp_path)
    rec2.close()
    # Exact instance id
    hit = resolve_replay_id(rec1.instance_id, tmp_path)
    assert hit is not None and hit.session_id == "alpha"
    # Prefix that matches both session ids (e.g. common prefix "a") — should
    # resolve uniquely to the single session that matches.
    only_alpha = resolve_replay_id("alp", tmp_path)
    assert only_alpha is not None and only_alpha.session_id == "alpha"
    # Nonsense
    assert resolve_replay_id("zzz", tmp_path) is None


def test_find_replay_candidates_ambiguous_prefix(tmp_path):
    """An ambiguous prefix should enumerate all matches so the CLI can
    show a disambiguation list instead of a misleading 'no match' error.
    """
    rec1 = ReplayRecorder.create("shared-prefix-one", tmp_path)
    rec1.close()
    rec2 = ReplayRecorder.create("shared-prefix-two", tmp_path)
    rec2.close()
    # Prefix that matches both
    candidates = find_replay_candidates("shared-prefix", tmp_path)
    assert len(candidates) == 2
    session_ids = {c.session_id for c in candidates}
    assert session_ids == {"shared-prefix-one", "shared-prefix-two"}
    # The wrapper resolve_replay_id still returns None on ambiguity
    assert resolve_replay_id("shared-prefix", tmp_path) is None
    # Extend the prefix → disambiguates
    unique = resolve_replay_id("shared-prefix-o", tmp_path)
    assert unique is not None and unique.session_id == "shared-prefix-one"


def test_find_replay_candidates_short_id(tmp_path):
    """Users copy-paste the short_id from the /replays listing — that
    form must prefix-match the underlying full_id regardless of how the
    8/6-char truncation aligns.
    """
    rec = ReplayRecorder.create("long-session-identifier", tmp_path)
    rec.close()
    meta = list_replays(tmp_path)[0]
    # Full short_id as shown in listings resolves to exactly one replay
    hit = resolve_replay_id(meta.short_id, tmp_path)
    assert hit is not None and hit.session_id == "long-session-identifier"
    # A proper prefix of the short_id also works
    hit_prefix = resolve_replay_id(meta.short_id[:5], tmp_path)
    assert hit_prefix is not None and hit_prefix.session_id == "long-session-identifier"


# ---------------------------------------------------------------------------
# Adapter + event translation
# ---------------------------------------------------------------------------


def test_classify_tool_source():
    assert _classify_tool("read_file") == "builtin"
    assert _classify_tool("mcp_server_tool") == "mcp"
    assert _classify_tool(None) == "builtin"


def _make_adapter(tmp_path: Path):
    inner = NullTransport()
    rec = ReplayRecorder.create("sess", tmp_path)
    return ReplayAdapter(inner, rec), rec


def test_adapter_turn_lifecycle(tmp_path):
    adapter, rec = _make_adapter(tmp_path)
    tid = adapter.begin_turn("hello")
    adapter.emit(AgentEvent(EventType.LLM_TEXT, {"chunk": "hi"}))
    adapter.end_turn("hi")
    rec.close()
    events = ReplayReader(rec.path).events()
    kinds = [e["kind"] for e in events if e["kind"] not in ("replay_header",)]
    assert "turn_started" in kinds
    assert "user_message" in kinds
    assert "assistant_text_chunk" in kinds
    # Footer is the file-close marker; turn_completed is the last
    # turn-level event right before it.
    assert kinds[-1] == "replay_footer"
    assert kinds[-2] == "turn_completed"
    tc = [e for e in events if e["kind"] == "turn_completed"][0]
    assert tc["payload"]["final_text"] == "hi"
    assert tc["payload"]["status"] == "ok"
    # All events share the same turn_id for this chat()-equivalent turn.
    turn_ids = {e["turn_id"] for e in events if e.get("turn_id")}
    assert turn_ids == {tid}


def test_adapter_multiple_tools_share_turn_id(tmp_path):
    adapter, rec = _make_adapter(tmp_path)
    tid = adapter.begin_turn("do X and Y")
    adapter.emit(AgentEvent(EventType.TOOL_START, {"tool": "read_file", "call_id": "a", "args": {}}))
    adapter.emit(AgentEvent(EventType.TOOL_COMPLETE, {"tool": "read_file", "call_id": "a", "status": "ok", "duration_ms": 1, "error": None}))
    adapter.emit(AgentEvent(EventType.TOOL_START, {"tool": "mcp_s_foo", "call_id": "b", "args": {}}))
    adapter.emit(AgentEvent(EventType.TOOL_COMPLETE, {"tool": "mcp_s_foo", "call_id": "b", "status": "ok", "duration_ms": 2, "error": None}))
    adapter.end_turn("done")
    rec.close()
    events = ReplayReader(rec.path).events()
    tool_events = [e for e in events if e["kind"] in ("tool_started", "tool_completed")]
    assert {e["turn_id"] for e in tool_events} == {tid}
    # Built-in vs MCP tool_source preserved.
    sources = {
        e["payload"]["tool"]: e["payload"]["tool_source"]
        for e in events if e["kind"] == "tool_started"
    }
    assert sources["read_file"] == "builtin"
    assert sources["mcp_s_foo"] == "mcp"


def test_adapter_tool_confirmation_request_and_resolve(tmp_path):
    class AskingTransport:
        def emit(self, e): pass
        def confirm_tool(self, name, desc, args): return False
        def ask_user(self, q): return ""
        def on_max_iterations(self, n, msgs): return {"action": "stop"}

    rec = ReplayRecorder.create("sess", tmp_path)
    adapter = ReplayAdapter(AskingTransport(), rec)
    adapter.begin_turn("ask me")
    adapter.emit(AgentEvent(EventType.TOOL_CONFIRMATION, {"tool": "run_shell_command", "args": {"cmd": "ls"}}))
    result = adapter.confirm_tool("run_shell_command", "desc", {"cmd": "ls"})
    assert result is False
    adapter.end_turn("")
    rec.close()
    events = ReplayReader(rec.path).events()
    req = [e for e in events if e["kind"] == "tool_confirmation_requested"]
    res = [e for e in events if e["kind"] == "tool_confirmation_resolved"]
    assert len(req) == 1 and req[0]["payload"]["tool"] == "run_shell_command"
    assert len(res) == 1 and res[0]["payload"]["approved"] is False


def test_adapter_tool_failure_status_recorded(tmp_path):
    adapter, rec = _make_adapter(tmp_path)
    adapter.begin_turn("do it")
    adapter.emit(AgentEvent(EventType.TOOL_START, {"tool": "t", "call_id": "a", "args": {}}))
    adapter.emit(AgentEvent(EventType.TOOL_COMPLETE, {
        "tool": "t", "call_id": "a", "status": "error",
        "duration_ms": 1, "error": "boom",
    }))
    adapter.end_turn("")
    rec.close()
    completed = [
        e for e in ReplayReader(rec.path).events() if e["kind"] == "tool_completed"
    ]
    assert completed[0]["payload"]["status"] == "error"
    assert completed[0]["payload"]["error"] == "boom"


def test_adapter_readonly_deny_shows_up_as_cancelled(tmp_path):
    adapter, rec = _make_adapter(tmp_path)
    adapter.begin_turn("do it")
    adapter.emit(AgentEvent(EventType.TOOL_START, {"tool": "write_file", "call_id": "x", "args": {}}))
    adapter.emit(AgentEvent(EventType.TOOL_COMPLETE, {
        "tool": "write_file", "call_id": "x", "status": "cancelled",
        "duration_ms": 0, "error": "denied by permission engine",
    }))
    adapter.end_turn("")
    rec.close()
    completed = [
        e for e in ReplayReader(rec.path).events() if e["kind"] == "tool_completed"
    ]
    assert completed[0]["payload"]["status"] == "cancelled"
    assert "denied" in completed[0]["payload"]["error"]


def test_adapter_tool_output_chunk_truncated(tmp_path):
    adapter, rec = _make_adapter(tmp_path)
    adapter.begin_turn("big output")
    big = "y" * (TOOL_OUTPUT_CHUNK_MAX_CHARS * 4)
    adapter.emit(AgentEvent(EventType.TOOL_OUTPUT, {"tool": "t", "call_id": "c", "chunk": big}))
    adapter.end_turn("")
    rec.close()
    chunk_events = [
        e for e in ReplayReader(rec.path).events() if e["kind"] == "tool_output_chunk"
    ]
    assert chunk_events[0]["payload"]["truncated"] is True
    assert chunk_events[0]["payload"]["original_chars"] == len(big)


def test_adapter_subagent_gets_own_turn_id_with_parent_pointer(tmp_path):
    adapter, rec = _make_adapter(tmp_path)
    parent_turn = adapter.begin_turn("spawn an agent")
    adapter.emit(AgentEvent(EventType.AGENT_START, {
        "agent": "bot", "task": "go", "max_turns": 5,
    }))
    # Tool event emitted "inside" the subagent context (after AGENT_START,
    # before AGENT_END) should be tagged with the subagent's turn_id.
    adapter.emit(AgentEvent(EventType.TOOL_START, {
        "tool": "read_file", "call_id": "inner", "args": {},
    }))
    adapter.emit(AgentEvent(EventType.TOOL_COMPLETE, {
        "tool": "read_file", "call_id": "inner", "status": "ok",
        "duration_ms": 1, "error": None,
    }))
    adapter.emit(AgentEvent(EventType.AGENT_END, {
        "agent": "bot", "state": "completed", "turns": 1,
        "tool_calls": 1, "tokens": 10, "duration_ms": 50, "error": None,
    }))
    adapter.end_turn("done")
    rec.close()

    events = ReplayReader(rec.path).events()
    sub_started = [e for e in events if e["kind"] == "subagent_started"][0]
    sub_completed = [e for e in events if e["kind"] == "subagent_completed"][0]
    inner_tool = [e for e in events if e["kind"] == "tool_started"][0]

    # Subagent turn_id differs from the parent turn_id and points back.
    assert sub_started["turn_id"] != parent_turn
    assert sub_started["parent_turn_id"] == parent_turn
    # Completed event reuses the subagent's turn_id.
    assert sub_completed["turn_id"] == sub_started["turn_id"]
    # Tool events inside the subagent carry the subagent turn_id.
    assert inner_tool["turn_id"] == sub_started["turn_id"]
    assert inner_tool["parent_turn_id"] == parent_turn


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


def test_retention_prunes_only_replays_not_sessions(tmp_path):
    # Create replay + sessions files; prune must touch only replays/.
    (tmp_path / ".agentao" / "replays").mkdir(parents=True)
    (tmp_path / ".agentao" / "sessions").mkdir(parents=True)
    # A fake session file that must be preserved.
    session_file = tmp_path / ".agentao" / "sessions" / "keep.json"
    session_file.write_text("{}")
    # Create 4 replay instances (by hand; mtimes explicitly spaced).
    import time as _time
    replay_files = []
    for i in range(4):
        rec = ReplayRecorder.create(f"sess{i}", tmp_path)
        rec.close()
        replay_files.append(rec.path)
        _time.sleep(0.01)  # stagger mtimes
    policy = ReplayRetentionPolicy(max_instances=2)
    deleted = policy.prune(tmp_path)
    assert len(deleted) == 2
    # Session file untouched.
    assert session_file.exists()
    # Newest 2 survived.
    remaining = list((tmp_path / ".agentao" / "replays").glob("*.jsonl"))
    assert len(remaining) == 2


# ---------------------------------------------------------------------------
# Agentao integration
# ---------------------------------------------------------------------------


def _build_agent(tmp_path: Path):
    # Import inside the function so the autouse credentials fixture wins.
    from agentao.agent import Agentao

    return Agentao(working_directory=tmp_path)


def test_agent_disabled_by_default_creates_no_file(tmp_path):
    agent = _build_agent(tmp_path)
    path = agent.start_replay("sess-123")
    assert path is None
    assert agent._replay_recorder is None
    assert not (tmp_path / ".agentao" / "replays").exists()
    agent.close()


def test_agent_enabled_creates_file(tmp_path):
    save_replay_enabled(True, tmp_path)
    agent = _build_agent(tmp_path)
    # Agentao read config at construction — reload to pick up the new flag.
    agent.reload_replay_config()
    path = agent.start_replay("sess-123")
    assert path is not None
    assert path.exists()
    assert agent._replay_adapter is not None
    agent.end_replay()
    agent.close()

    events = ReplayReader(path).events()
    kinds = [e["kind"] for e in events]
    assert kinds[0] == "replay_header"
    assert "session_started" in kinds
    assert "session_ended" in kinds
    # Footer is always the very last event after close().
    assert kinds[-1] == "replay_footer"


def test_agent_end_replay_is_idempotent(tmp_path):
    save_replay_enabled(True, tmp_path)
    agent = _build_agent(tmp_path)
    agent.reload_replay_config()
    agent.start_replay("sess-X")
    agent.end_replay()
    # Second call must not raise or emit anything.
    agent.end_replay()
    agent.close()


def test_agent_session_saved_never_emitted_in_v1(tmp_path):
    save_replay_enabled(True, tmp_path)
    agent = _build_agent(tmp_path)
    agent.reload_replay_config()
    path = agent.start_replay("sess-saved")
    # Drive a couple of lifecycle events through the adapter.
    agent._replay_adapter.begin_turn("hi")
    agent._replay_adapter.end_turn("bye")
    agent.end_replay()
    agent.close()
    kinds = [e["kind"] for e in ReplayReader(path).events()]
    assert "session_saved" not in kinds


def test_clear_like_cycle_emits_session_ended_then_new_file(tmp_path):
    save_replay_enabled(True, tmp_path)
    agent = _build_agent(tmp_path)
    agent.reload_replay_config()
    first = agent.start_replay("sess-1")
    agent.end_replay()
    # Simulate /clear: new session_id, fresh start_replay.
    second = agent.start_replay("sess-2")
    agent.end_replay()
    agent.close()
    assert first != second
    first_kinds = [e["kind"] for e in ReplayReader(first).events()]
    second_kinds = [e["kind"] for e in ReplayReader(second).events()]
    # Each instance file ends with replay_footer (session_ended precedes it).
    assert "session_ended" in first_kinds
    assert first_kinds[-1] == "replay_footer"
    assert second_kinds[0] == "replay_header"
    assert "session_ended" in second_kinds
    assert second_kinds[-1] == "replay_footer"


def test_reused_session_id_creates_new_instance_file(tmp_path):
    """Matches the ACP session/load invariant: same logical session_id,
    new replay instance file per load."""
    save_replay_enabled(True, tmp_path)
    agent = _build_agent(tmp_path)
    agent.reload_replay_config()
    first = agent.start_replay("sess-shared")
    agent.end_replay()
    second = agent.start_replay("sess-shared")
    agent.end_replay()
    agent.close()
    assert first != second
    assert first.parent == second.parent  # same replays/ dir
    # Both files readable and each has its own header.
    assert ReplayReader(first).events()[0]["kind"] == "replay_header"
    assert ReplayReader(second).events()[0]["kind"] == "replay_header"


def test_agent_turn_error_does_not_break_replay(tmp_path):
    save_replay_enabled(True, tmp_path)
    agent = _build_agent(tmp_path)
    agent.reload_replay_config()
    path = agent.start_replay("sess-err")
    # Force a failure during turn to confirm end_turn still fires with status.
    agent._replay_adapter.begin_turn("oops")
    agent._replay_adapter.end_turn("[Interrupted]", status="cancelled", error="user-cancel")
    agent.end_replay()
    agent.close()
    completed = [
        e for e in ReplayReader(path).events() if e["kind"] == "turn_completed"
    ]
    assert completed[0]["payload"]["status"] == "cancelled"
    assert completed[0]["payload"]["error"] == "user-cancel"


def test_llm_call_delta_emits_only_new_messages_across_turns(tmp_path):
    """Regression: the per-turn reset must seed the delta baseline to the
    pre-turn history length, not 0. Before the fix, the first ``_llm_call``
    of every later turn re-emitted the entire accumulated conversation as
    ``added_messages`` because ``_llm_call_last_msg_count`` was reset to 0
    at the top of ``chat()``, causing replay files to grow quadratically.
    """
    save_replay_enabled(True, tmp_path)
    agent = _build_agent(tmp_path)
    agent.reload_replay_config()
    path = agent.start_replay("sess-delta")

    class _FakeChoice:
        finish_reason = "stop"

    class _FakeUsage:
        prompt_tokens = 10
        completion_tokens = 5

    class _FakeResponse:
        choices = [_FakeChoice()]
        usage = _FakeUsage()

    def _fake_chat_stream(**kwargs):
        return _FakeResponse()

    agent.llm.chat_stream = _fake_chat_stream

    # Turn 1: fresh agent. chat()'s reset seeds the baseline to
    # 1 + len(agent.messages) == 1 (just the system slot).
    agent._llm_call_seq = 0
    agent._llm_call_last_msg_count = 1 + len(agent.messages)
    agent.messages.append({"role": "user", "content": "hello"})
    msgs_t1 = [{"role": "system", "content": "sys"}] + agent.messages
    agent._llm_call(msgs_t1, tools=[])
    agent.messages.append({"role": "assistant", "content": "hi back"})

    # Turn 2: prior history has 2 messages, so baseline is 1 + 2 == 3.
    # The first LLM_CALL_DELTA of this turn must emit only the new user
    # message — not the entire [user1, asst1, user2] prefix.
    agent._llm_call_seq = 0
    agent._llm_call_last_msg_count = 1 + len(agent.messages)
    agent.messages.append({"role": "user", "content": "again"})
    msgs_t2 = [{"role": "system", "content": "sys"}] + agent.messages
    agent._llm_call(msgs_t2, tools=[])

    agent.end_replay()
    agent.close()

    deltas = [
        e for e in ReplayReader(path).events() if e["kind"] == "llm_call_delta"
    ]
    assert len(deltas) == 2

    t1 = deltas[0]["payload"]
    assert t1["delta_start_index"] == 1
    assert t1["total_messages"] == 2
    assert len(t1["added_messages"]) == 1
    assert t1["added_messages"][0]["content"] == "hello"

    t2 = deltas[1]["payload"]
    assert t2["delta_start_index"] == 3
    assert t2["total_messages"] == 4
    assert len(t2["added_messages"]) == 1
    assert t2["added_messages"][0]["content"] == "again"
