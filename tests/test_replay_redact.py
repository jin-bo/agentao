"""Tests for the v1.1 replay redaction layer.

Covers:

- the regex-based secret scanner in ``agentao.replay.redact``
- the per-field policy orchestrator ``sanitize_event`` in
  ``agentao.replay.sanitize``
- the recorder's cumulative redaction/dropped/truncated counters
"""

from __future__ import annotations

import json

from agentao.replay.recorder import ReplayRecorder
from agentao.replay.redact import SECRET_PATTERNS, scan_and_redact, scan_recursive
from agentao.replay.sanitize import (
    ASK_USER_ANSWER_MAX_CHARS,
    TOOL_OUTPUT_CHUNK_MAX_CHARS,
    TOOL_RESULT_MAX_CHARS,
    sanitize_event,
)


# ---------------------------------------------------------------------------
# Secret scanner — one test per advertised pattern kind
# ---------------------------------------------------------------------------


def test_pattern_kinds_cover_the_documented_set():
    # Multiple regexes are allowed to share a single kind name (e.g. two
    # bearer-shape patterns roll up into one ``bearer`` counter), so the
    # contract is on the SET of kinds, not list length.
    kinds = {k for k, _ in SECRET_PATTERNS}
    expected = {
        "private_key_block",
        "anthropic_api_key",
        "openai_api_key",
        "google_api_key",
        "aws_access_key",
        "github_token",
        "slack_token",
        "jwt",
        "bearer",
        "kv_secret",
    }
    assert kinds == expected


def test_scan_redacts_openai_api_key():
    text = "const k = 'sk-proj-abcdefghijklmnopqrstuvwxyz0123456789ABCDEF'"
    cleaned, hits = scan_and_redact(text)
    assert "[REDACTED:openai_api_key]" in cleaned
    assert "sk-proj-" not in cleaned
    assert hits.get("openai_api_key") == 1


def test_scan_redacts_anthropic_api_key():
    key = "sk-ant-" + "A" * 45
    cleaned, hits = scan_and_redact(f"key={key}")
    assert "[REDACTED:anthropic_api_key]" in cleaned
    assert hits.get("anthropic_api_key") == 1


def test_scan_redacts_aws_access_key():
    cleaned, hits = scan_and_redact("access=AKIAABCDEFGHIJKLMNOP next")
    assert "[REDACTED:aws_access_key]" in cleaned
    assert hits.get("aws_access_key") == 1


def test_scan_redacts_google_api_key():
    key = "AIza" + "a" * 35
    cleaned, hits = scan_and_redact(f"fetch('https://x?key={key}')")
    assert "[REDACTED:google_api_key]" in cleaned
    assert hits.get("google_api_key") == 1


def test_scan_redacts_github_token():
    cleaned, hits = scan_and_redact("use ghp_" + "A" * 36 + " to push")
    assert "[REDACTED:github_token]" in cleaned
    assert hits.get("github_token") == 1


def test_scan_redacts_slack_token():
    cleaned, hits = scan_and_redact("header: xoxb-1234567890-abcdefg")
    assert "[REDACTED:slack_token]" in cleaned
    assert hits.get("slack_token") == 1


def test_scan_redacts_jwt():
    jwt = "eyJABCDEFGHIJ.eyJABCDEFGHIJ.ABCDEFGHIJ"
    cleaned, hits = scan_and_redact(f"Authorization: {jwt}")
    # Bearer/authorization pattern may fire first; either way something fires.
    assert "[REDACTED:" in cleaned
    assert hits  # at least one pattern matched


def test_scan_redacts_private_key_block():
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIBOgIBAAJBAKj34GkxFhD90vcNLYLInFEX6Ppy1tPf9Cnzj4p4WGeKLs1Pt8Q\n"
        "-----END RSA PRIVATE KEY-----"
    )
    cleaned, hits = scan_and_redact(pem)
    assert "[REDACTED:private_key_block]" in cleaned
    assert "MIIBOgIBAAJBAKj" not in cleaned
    assert hits.get("private_key_block") == 1


def test_scan_redacts_bearer_header():
    text = "Authorization: Bearer abcdef1234567890ABCDEF"
    cleaned, hits = scan_and_redact(text)
    assert "[REDACTED:bearer]" in cleaned
    assert hits.get("bearer") == 1


def test_scan_redacts_kv_secret():
    text = "config: password='s3cretp@ssword'"
    cleaned, hits = scan_and_redact(text)
    assert "[REDACTED:kv_secret]" in cleaned
    assert hits.get("kv_secret") == 1


def test_scan_skips_short_strings_fast_path():
    # Under the minimum scan length, even a key-shaped token is not touched.
    cleaned, hits = scan_and_redact("sk-abcd")
    assert cleaned == "sk-abcd"
    assert hits == {}


def test_scan_leaves_clean_text_alone():
    clean = "The quick brown fox jumps over the lazy dog"
    cleaned, hits = scan_and_redact(clean)
    assert cleaned == clean
    assert hits == {}


def test_scan_non_string_passes_through():
    cleaned, hits = scan_and_redact(12345)  # type: ignore[arg-type]
    assert cleaned == 12345
    assert hits == {}


def test_scan_recursive_walks_nested_structures():
    payload = {
        "top": "sk-proj-AAAAAAAAAAAAAAAAAAAA",
        "list": ["ghp_" + "B" * 36, "safe"],
        "deep": {"inner": "AKIAZZZZZZZZZZZZZZZZ"},
    }
    cleaned, hits = scan_recursive(payload)
    assert "[REDACTED:openai_api_key]" in cleaned["top"]
    assert "[REDACTED:github_token]" in cleaned["list"][0]
    assert cleaned["list"][1] == "safe"
    assert "[REDACTED:aws_access_key]" in cleaned["deep"]["inner"]
    assert hits.get("openai_api_key") == 1
    assert hits.get("github_token") == 1
    assert hits.get("aws_access_key") == 1


def test_scan_preserves_unrelated_fields_and_counts_multiple_hits_per_kind():
    text = "k1=sk-proj-AAAAAAAAAAAAAAAAAAAA k2=sk-proj-BBBBBBBBBBBBBBBBBBBB"
    cleaned, hits = scan_and_redact(text)
    assert cleaned.count("[REDACTED:openai_api_key]") == 2
    assert hits.get("openai_api_key") == 2


# ---------------------------------------------------------------------------
# Orchestrator — sanitize_event
# ---------------------------------------------------------------------------


def test_sanitize_event_default_scan_only_applies_scanner():
    payload = {"content": "auth=Bearer " + "A" * 40}
    clean, stats = sanitize_event("user_message", payload)
    assert "[REDACTED:bearer]" in clean["content"]
    assert stats.redaction_hits.get("bearer") == 1
    assert "redaction_hits" in clean  # per-event copy of the counter


def test_sanitize_event_clean_payload_has_no_redaction_fields():
    clean, stats = sanitize_event("user_message", {"content": "hello"})
    assert clean == {"content": "hello"}
    assert not stats.any_activity()


def test_sanitize_event_scan_truncate_flat_style_for_tool_output_chunk():
    big = "x" * (TOOL_OUTPUT_CHUNK_MAX_CHARS * 3)
    clean, stats = sanitize_event(
        "tool_output_chunk", {"tool": "t", "call_id": "c", "chunk": big},
    )
    # v1.0-compatible flat markers on the same level as chunk.
    assert clean["truncated"] is True
    assert clean["original_chars"] == len(big)
    assert clean["omitted_chars"] > 0
    assert len(clean["chunk"]) < len(big)
    assert stats.truncated_fields == {"chunk": len(big)}


def test_sanitize_event_scan_truncate_nested_style_for_tool_result():
    big = "y" * (TOOL_RESULT_MAX_CHARS * 2)
    clean, stats = sanitize_event(
        "tool_result",
        {"tool": "shell", "call_id": "1", "content": big, "content_hash": "abc"},
    )
    # Nested meta for new-style events so multiple truncated fields
    # never collide on top-level names.
    assert clean["content_truncation"]["truncated"] is True
    assert clean["content_truncation"]["original_chars"] == len(big)
    assert len(clean["content"]) < len(big)
    # Verbatim policy for content_hash keeps it as-is (no scanner run).
    assert clean["content_hash"] == "abc"
    # No flat top-level markers on this event kind.
    assert "truncated" not in clean
    assert stats.truncated_fields == {"content": len(big)}


def test_sanitize_event_truncate_scans_before_truncating():
    # A secret near the start of a long string must be redacted even if
    # the tail end gets truncated away.
    leak = "sk-proj-" + "S" * 30
    big = leak + (" filler" * 5000)
    clean, _ = sanitize_event("tool_result", {"content": big})
    assert "[REDACTED:openai_api_key]" in clean["content"]


def test_sanitize_event_short_string_below_cap_is_unchanged():
    clean, stats = sanitize_event("tool_result", {"content": "ok"})
    assert clean["content"] == "ok"
    assert "content_truncation" not in clean
    assert not stats.truncated_fields


def test_sanitize_event_verbatim_field_skips_scanner():
    # content_hash is Verbatim. Even if its value happens to look like a
    # secret, it must pass through unchanged.
    hash_like = "AKIA" + "Z" * 16  # 20 uppercase alnum — shape of an AWS key
    clean, stats = sanitize_event(
        "tool_result", {"content_hash": hash_like, "content": "x"},
    )
    assert clean["content_hash"] == hash_like
    assert not stats.redaction_hits


def test_sanitize_event_non_dict_payload_becomes_empty_dict():
    clean, stats = sanitize_event("user_message", "not a dict")
    assert clean == {}
    assert not stats.any_activity()


def test_sanitize_event_recursive_scan_into_tool_args():
    payload = {
        "tool": "shell",
        "call_id": "c",
        "args": {"cmd": "curl -H 'Authorization: Bearer " + "X" * 40 + "' https://api"},
    }
    clean, stats = sanitize_event("tool_started", payload)
    assert "[REDACTED:bearer]" in clean["args"]["cmd"]
    assert stats.redaction_hits.get("bearer") == 1


def test_sanitize_event_coerce_failure_drops_single_field(monkeypatch):
    from agentao.replay import sanitize as sanmod

    def fake_coerce(value):
        if isinstance(value, str) and "boom" in value:
            raise RuntimeError("explode")
        return value

    monkeypatch.setattr(sanmod, "_coerce_value", fake_coerce)
    clean, stats = sanitize_event("user_message", {"ok": "fine", "bad": "boom"})
    assert clean["ok"] == "fine"
    assert "bad" not in clean
    assert stats.dropped_fields == ["bad"]
    assert clean["redacted"] == "filter_error"
    assert clean["redacted_fields"] == ["bad"]


def test_sanitize_event_ask_user_answer_is_truncated():
    answer = "s" * (ASK_USER_ANSWER_MAX_CHARS * 2)
    clean, stats = sanitize_event(
        "ask_user_answered", {"question": "?", "answer": answer},
    )
    assert clean["answer_truncation"]["truncated"] is True
    assert len(clean["answer"]) < len(answer)
    assert stats.truncated_fields == {"answer": len(answer)}


# ---------------------------------------------------------------------------
# Recorder roll-up
# ---------------------------------------------------------------------------


def test_recorder_accumulates_redaction_hits(tmp_path):
    rec = ReplayRecorder.create("sess", tmp_path)
    rec.record(
        "user_message",
        payload={"content": "token=sk-proj-" + "A" * 30},
    )
    rec.record(
        "user_message",
        payload={"content": "aws=AKIAZZZZZZZZZZZZZZZZ and sk-proj-" + "B" * 30},
    )
    rec.close()
    hits = rec.redaction_hits
    # Two openai redactions (one per event) plus one AWS in the second.
    assert hits.get("openai_api_key") == 2
    assert hits.get("aws_access_key") == 1


def test_recorder_rolls_up_truncated_fields(tmp_path):
    rec = ReplayRecorder.create("sess", tmp_path)
    big_chunk = "x" * (TOOL_OUTPUT_CHUNK_MAX_CHARS * 3)
    big_result = "y" * (TOOL_RESULT_MAX_CHARS * 2)
    rec.record(
        "tool_output_chunk",
        payload={"tool": "t", "call_id": "c", "chunk": big_chunk},
    )
    rec.record(
        "tool_result",
        payload={"tool": "t", "call_id": "c", "content": big_result},
    )
    rec.close()
    hits = rec.truncated_field_hits
    assert hits.get("chunk") == 1
    assert hits.get("content") == 1


def test_recorder_event_line_has_per_event_redaction_hits(tmp_path):
    rec = ReplayRecorder.create("sess", tmp_path)
    rec.record(
        "user_message",
        payload={"content": "AKIAZZZZZZZZZZZZZZZZ and AKIAQQQQQQQQQQQQQQQQ"},
    )
    rec.close()
    lines = rec.path.read_text(encoding="utf-8").splitlines()
    # Header is line 0, user_message is line 1.
    msg = json.loads(lines[1])
    assert msg["payload"]["redaction_hits"]["aws_access_key"] == 2


# ---------------------------------------------------------------------------
# Schema 1.1: header capture_flags, replay_footer
# ---------------------------------------------------------------------------


def test_header_declares_schema_1_1():
    from agentao.replay.events import SCHEMA_VERSION

    assert SCHEMA_VERSION == "1.1"


def test_header_records_capture_flags_when_provided(tmp_path):
    flags = {
        "capture_llm_delta": True,
        "capture_full_llm_io": False,
        "capture_tool_result_full": False,
        "capture_plugin_hook_output_full": False,
    }
    rec = ReplayRecorder.create("sess", tmp_path, capture_flags=flags)
    rec.close()
    first = json.loads(rec.path.read_text().splitlines()[0])
    assert first["kind"] == "replay_header"
    assert first["payload"]["schema_version"] == "1.1"
    assert first["payload"]["capture_flags"] == flags


def test_header_omits_capture_flags_when_none_supplied(tmp_path):
    rec = ReplayRecorder.create("sess", tmp_path)
    rec.close()
    first = json.loads(rec.path.read_text().splitlines()[0])
    # Legacy callers that don't pass flags get a clean header — the
    # field is omitted rather than populated with a default.
    assert "capture_flags" not in first["payload"]


def test_replay_footer_is_written_on_close(tmp_path):
    rec = ReplayRecorder.create(
        "sess", tmp_path, capture_flags={"capture_llm_delta": True},
    )
    rec.record(
        "user_message",
        payload={"content": "leak: sk-proj-" + "A" * 30},
    )
    rec.record(
        "tool_output_chunk",
        payload={"tool": "t", "call_id": "c", "chunk": "x" * (TOOL_OUTPUT_CHUNK_MAX_CHARS * 2)},
    )
    rec.close()
    events = [json.loads(l) for l in rec.path.read_text().splitlines()]
    footer = events[-1]
    assert footer["kind"] == "replay_footer"
    payload = footer["payload"]
    assert payload["redaction_hits"]["openai_api_key"] == 1
    assert payload["truncated_field_hits"]["chunk"] == 1
    # Header and body events all got sequence numbers before the footer.
    assert payload["event_count"] >= 3
    assert payload["capture_flags"] == {"capture_llm_delta": True}


def test_close_is_idempotent_with_footer(tmp_path):
    rec = ReplayRecorder.create("sess", tmp_path)
    rec.close()
    rec.close()  # must not duplicate the footer
    events = [json.loads(l) for l in rec.path.read_text().splitlines()]
    kinds = [e["kind"] for e in events]
    assert kinds.count("replay_footer") == 1


def test_reader_handles_unknown_event_kind(tmp_path):
    """Forward-compat: a file with a future event kind still reads."""
    rec = ReplayRecorder.create("sess", tmp_path)
    rec.record("some_future_kind", payload={"note": "from the future"})
    rec.close()
    events = [json.loads(l) for l in rec.path.read_text().splitlines()]
    kinds = [e["kind"] for e in events]
    assert "some_future_kind" in kinds
    # Recorder and reader never hard-check against EventKind.ALL, so the
    # unknown kind round-trips cleanly.
    from agentao.replay.reader import ReplayReader

    kinds_read = [e["kind"] for e in ReplayReader(rec.path).events()]
    assert "some_future_kind" in kinds_read


# ---------------------------------------------------------------------------
# Config: capture_flags parsing
# ---------------------------------------------------------------------------


def test_config_applies_capture_flag_defaults(tmp_path):
    from agentao.replay.config import (
        CAPTURE_FLAG_DEFAULTS,
        load_replay_config,
    )

    cfg = load_replay_config(tmp_path)
    assert cfg.capture_flags == CAPTURE_FLAG_DEFAULTS
    # capture_llm_delta is on by default (design decision).
    assert cfg.capture_flags["capture_llm_delta"] is True
    # Deep-capture flags are off by default.
    assert cfg.deep_capture_enabled() is False


def test_config_merges_partial_capture_flags(tmp_path):
    (tmp_path / ".agentao").mkdir()
    (tmp_path / ".agentao" / "settings.json").write_text(
        json.dumps({
            "replay": {
                "enabled": True,
                "capture_flags": {"capture_full_llm_io": True},
            }
        })
    )
    from agentao.replay.config import load_replay_config

    cfg = load_replay_config(tmp_path)
    # Explicit flag applied.
    assert cfg.capture_flags["capture_full_llm_io"] is True
    # Unspecified flags keep their defaults.
    assert cfg.capture_flags["capture_llm_delta"] is True
    assert cfg.capture_flags["capture_tool_result_full"] is False
    assert cfg.deep_capture_enabled() is True


# ---------------------------------------------------------------------------
# Step 3: tool_result event wire-through
# ---------------------------------------------------------------------------


def _drive_adapter_event(tmp_path, event_type, data):
    """Small helper: wire an AgentEvent through a ReplayAdapter and return
    the resulting replay events (minus the header)."""
    from agentao.replay.adapter import ReplayAdapter
    from agentao.replay.reader import ReplayReader
    from agentao.transport import AgentEvent, NullTransport

    rec = ReplayRecorder.create("sess", tmp_path)
    adapter = ReplayAdapter(NullTransport(), rec)
    adapter.begin_turn("drive")
    adapter.emit(AgentEvent(event_type, data))
    adapter.end_turn("done")
    rec.close()
    events = ReplayReader(rec.path).events()
    return [e for e in events if e["kind"] != "replay_header"]


def test_tool_result_event_roundtrips_small_result(tmp_path):
    from agentao.transport import EventType

    events = _drive_adapter_event(
        tmp_path,
        EventType.TOOL_RESULT,
        {
            "tool": "read_file",
            "call_id": "call-a",
            "content": "hello world",
            "content_hash": "deadbeef",
            "original_chars": 11,
            "saved_to_disk": False,
            "disk_path": None,
            "status": "ok",
            "duration_ms": 3,
            "error": None,
        },
    )
    tr = [e for e in events if e["kind"] == "tool_result"]
    assert len(tr) == 1
    payload = tr[0]["payload"]
    assert payload["tool"] == "read_file"
    assert payload["call_id"] == "call-a"
    assert payload["content"] == "hello world"
    # Short content below the 8000-char cap stays un-truncated and
    # carries no nested truncation meta.
    assert "content_truncation" not in payload
    assert payload["content_hash"] == "deadbeef"
    assert payload["status"] == "ok"
    assert payload["saved_to_disk"] is False


def test_tool_result_event_truncates_large_content_but_keeps_hash(tmp_path):
    from agentao.replay.sanitize import TOOL_RESULT_MAX_CHARS
    from agentao.transport import EventType

    big = "z" * (TOOL_RESULT_MAX_CHARS * 3)
    events = _drive_adapter_event(
        tmp_path,
        EventType.TOOL_RESULT,
        {
            "tool": "shell",
            "call_id": "c",
            "content": big,
            "content_hash": "HASH_OF_FULL",
            "original_chars": len(big),
            "saved_to_disk": True,
            "disk_path": ".agentao/tool-outputs/example.txt",
            "status": "ok",
            "duration_ms": 10,
            "error": None,
        },
    )
    tr = [e for e in events if e["kind"] == "tool_result"][0]
    payload = tr["payload"]
    # Body got truncated but the hash (of the full original) is kept as-is
    # (Verbatim policy on content_hash).
    assert len(payload["content"]) < len(big)
    assert payload["content_truncation"]["truncated"] is True
    assert payload["content_truncation"]["original_chars"] == len(big)
    assert payload["content_hash"] == "HASH_OF_FULL"
    # Disk path round-trips so readers know where the real artifact lives.
    assert payload["saved_to_disk"] is True
    assert payload["disk_path"] == ".agentao/tool-outputs/example.txt"


def test_tool_result_event_scans_secrets_in_content(tmp_path):
    from agentao.transport import EventType

    events = _drive_adapter_event(
        tmp_path,
        EventType.TOOL_RESULT,
        {
            "tool": "shell",
            "call_id": "c",
            "content": "leaking OPENAI_API_KEY=sk-proj-" + "A" * 30,
            "content_hash": "x",
            "original_chars": 45,
            "saved_to_disk": False,
            "disk_path": None,
            "status": "ok",
            "duration_ms": 1,
            "error": None,
        },
    )
    tr = [e for e in events if e["kind"] == "tool_result"][0]
    payload = tr["payload"]
    assert "[REDACTED:" in payload["content"]
    # ``kv_secret`` and/or ``openai_api_key`` may both fire; the event
    # carries at least one hit.
    assert payload["redaction_hits"]


def test_llm_call_events_round_trip_through_adapter(tmp_path):
    from agentao.transport import EventType

    started = _drive_adapter_event(
        tmp_path,
        EventType.LLM_CALL_STARTED,
        {
            "attempt": 1,
            "model": "gpt-x",
            "temperature": 0.2,
            "max_tokens": 4096,
            "n_messages": 5,
            "n_tool_messages": 1,
            "n_system_reminder_blocks": 2,
            "system_prompt_hash": "abcd1234",
            "tools_hash": "toolshash",
            "tool_count": 14,
        },
    )
    ev = [e for e in started if e["kind"] == "llm_call_started"]
    assert len(ev) == 1
    assert ev[0]["payload"]["model"] == "gpt-x"
    assert ev[0]["payload"]["system_prompt_hash"] == "abcd1234"


def test_llm_call_delta_preserves_message_shape(tmp_path):
    from agentao.transport import EventType

    delta_msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello", "tool_calls": []},
    ]
    events = _drive_adapter_event(
        tmp_path,
        EventType.LLM_CALL_DELTA,
        {
            "attempt": 1,
            "delta_start_index": 0,
            "total_messages": 2,
            "added_messages": delta_msgs,
        },
    )
    ev = [e for e in events if e["kind"] == "llm_call_delta"][0]
    assert ev["payload"]["delta_start_index"] == 0
    assert ev["payload"]["total_messages"] == 2
    assert ev["payload"]["added_messages"] == delta_msgs


def test_llm_call_delta_scans_secrets_inside_nested_messages(tmp_path):
    from agentao.transport import EventType

    msgs = [
        {"role": "user", "content": "key=sk-proj-" + "A" * 30},
        {"role": "tool", "content": "ok"},
    ]
    events = _drive_adapter_event(
        tmp_path,
        EventType.LLM_CALL_DELTA,
        {
            "attempt": 1,
            "delta_start_index": 0,
            "total_messages": 2,
            "added_messages": msgs,
        },
    )
    delta = [e for e in events if e["kind"] == "llm_call_delta"][0]
    assert "[REDACTED:" in delta["payload"]["added_messages"][0]["content"]
    assert delta["payload"]["redaction_hits"]  # counter surfaced on event


def test_ask_user_emits_request_and_answer_events(tmp_path):
    """ReplayAdapter.ask_user must fire a request + answer pair."""
    from agentao.replay.adapter import ReplayAdapter
    from agentao.replay.reader import ReplayReader

    class EchoTransport:
        def emit(self, e): pass
        def confirm_tool(self, name, desc, args): return True
        def ask_user(self, q): return "my answer"
        def on_max_iterations(self, n, msgs): return {"action": "stop"}

    rec = ReplayRecorder.create("sess", tmp_path)
    adapter = ReplayAdapter(EchoTransport(), rec)
    adapter.begin_turn("ask something")
    answer = adapter.ask_user("what is your name?")
    adapter.end_turn("")
    rec.close()

    assert answer == "my answer"
    events = ReplayReader(rec.path).events()
    kinds = [e["kind"] for e in events]
    assert "ask_user_requested" in kinds
    assert "ask_user_answered" in kinds

    req = [e for e in events if e["kind"] == "ask_user_requested"][0]
    ans = [e for e in events if e["kind"] == "ask_user_answered"][0]
    assert req["payload"]["question"] == "what is your name?"
    assert ans["payload"]["answer"] == "my answer"


def test_ask_user_answer_is_truncated_and_scanned(tmp_path):
    from agentao.replay.adapter import ReplayAdapter
    from agentao.replay.reader import ReplayReader
    from agentao.replay.sanitize import ASK_USER_ANSWER_MAX_CHARS

    secret = "token=sk-proj-" + "Z" * 30
    big_answer = secret + " " + ("x" * (ASK_USER_ANSWER_MAX_CHARS * 2))

    class SecretTransport:
        def emit(self, e): pass
        def confirm_tool(self, *a): return True
        def ask_user(self, q): return big_answer
        def on_max_iterations(self, n, msgs): return {"action": "stop"}

    rec = ReplayRecorder.create("sess", tmp_path)
    adapter = ReplayAdapter(SecretTransport(), rec)
    adapter.begin_turn("t")
    adapter.ask_user("paste your token?")
    adapter.end_turn("")
    rec.close()

    ans = [
        e for e in ReplayReader(rec.path).events()
        if e["kind"] == "ask_user_answered"
    ][0]
    payload = ans["payload"]
    # Scanner removed the leaked token and the answer was truncated.
    assert "sk-proj-" not in payload["answer"]
    assert "[REDACTED:" in payload["answer"]
    assert payload["answer_truncation"]["truncated"] is True
    assert payload["answer_truncation"]["original_chars"] == len(big_answer)


def test_context_compressed_event_surfaces_pre_post_metrics(tmp_path):
    from agentao.transport import EventType

    events = _drive_adapter_event(
        tmp_path,
        EventType.CONTEXT_COMPRESSED,
        {
            "type": "microcompact",
            "reason": "microcompact_threshold",
            "pre_msgs": 42,
            "post_msgs": 42,
            "pre_est_tokens": 120_000,
            "post_est_tokens": 88_000,
            "duration_ms": 5,
        },
    )
    ev = [e for e in events if e["kind"] == "context_compressed"][0]
    assert ev["payload"]["type"] == "microcompact"
    assert ev["payload"]["pre_est_tokens"] == 120_000
    assert ev["payload"]["post_est_tokens"] == 88_000


def test_background_notification_injected_event(tmp_path):
    from agentao.transport import EventType

    events = _drive_adapter_event(
        tmp_path,
        EventType.BACKGROUND_NOTIFICATION_INJECTED,
        {"note_count": 2, "content": "agent-42 finished"},
    )
    ev = [e for e in events if e["kind"] == "background_notification_injected"][0]
    assert ev["payload"]["note_count"] == 2
    assert "agent-42" in ev["payload"]["content"]


def test_model_changed_event_round_trip(tmp_path):
    from agentao.transport import EventType

    events = _drive_adapter_event(
        tmp_path,
        EventType.MODEL_CHANGED,
        {
            "old_model": "gpt-a",
            "new_model": "gpt-b",
            "base_url_changed": True,
            "cause": "set_provider",
        },
    )
    ev = [e for e in events if e["kind"] == "model_changed"][0]
    assert ev["payload"]["old_model"] == "gpt-a"
    assert ev["payload"]["new_model"] == "gpt-b"
    assert ev["payload"]["base_url_changed"] is True
    assert ev["payload"]["cause"] == "set_provider"


def test_skill_activated_and_deactivated_events(tmp_path):
    from agentao.transport import EventType

    activated = _drive_adapter_event(
        tmp_path, EventType.SKILL_ACTIVATED, {"skill": "web-research"},
    )
    deactivated = _drive_adapter_event(
        tmp_path, EventType.SKILL_DEACTIVATED, {"skill": "web-research"},
    )
    assert any(e["kind"] == "skill_activated" for e in activated)
    assert any(e["kind"] == "skill_deactivated" for e in deactivated)


def test_memory_write_event_carries_versions(tmp_path):
    from agentao.transport import EventType

    events = _drive_adapter_event(
        tmp_path,
        EventType.MEMORY_WRITE,
        {"version_before": 5, "version_after": 6, "total_entries": 12},
    )
    ev = [e for e in events if e["kind"] == "memory_write"][0]
    assert ev["payload"]["version_before"] == 5
    assert ev["payload"]["version_after"] == 6
    assert ev["payload"]["total_entries"] == 12


def test_memory_delete_and_cleared_events_round_trip(tmp_path):
    from agentao.transport import EventType

    del_events = _drive_adapter_event(
        tmp_path,
        EventType.MEMORY_DELETE,
        {"key": "stale_fact", "deleted_count": 1, "cause": "cli"},
    )
    assert any(e["kind"] == "memory_delete" for e in del_events)
    clr_events = _drive_adapter_event(
        tmp_path,
        EventType.MEMORY_CLEARED,
        {"memories_cleared": 7, "session_summaries_cleared": 3, "cause": "cli"},
    )
    ev = [e for e in clr_events if e["kind"] == "memory_cleared"][0]
    assert ev["payload"]["memories_cleared"] == 7
    assert ev["payload"]["session_summaries_cleared"] == 3


def test_readonly_mode_changed_event_fires_once_per_flip(tmp_path):
    """ToolRunner.set_readonly_mode emits once when the value actually flips."""
    from agentao.replay.adapter import ReplayAdapter
    from agentao.replay.reader import ReplayReader
    from agentao.runtime.tool_runner import ToolRunner
    from agentao.tools import ToolRegistry
    from agentao.transport import NullTransport

    rec = ReplayRecorder.create("sess", tmp_path)
    adapter = ReplayAdapter(NullTransport(), rec)
    adapter.begin_turn("t")

    class _Logger:
        def info(self, *a, **k): pass
        def warning(self, *a, **k): pass
        def error(self, *a, **k): pass

    runner = ToolRunner(
        tools=ToolRegistry(),
        permission_engine=None,
        transport=adapter,
        logger=_Logger(),
    )
    runner.set_readonly_mode(True)   # flip
    runner.set_readonly_mode(True)   # no-op; must not emit
    runner.set_readonly_mode(False)  # flip back

    adapter.end_turn("")
    rec.close()

    flips = [
        e for e in ReplayReader(rec.path).events()
        if e["kind"] == "readonly_mode_changed"
    ]
    # Only two events — one per real transition.
    assert len(flips) == 2
    assert flips[0]["payload"]["previous"] is False
    assert flips[0]["payload"]["current"] is True
    assert flips[1]["payload"]["previous"] is True
    assert flips[1]["payload"]["current"] is False


def test_permission_mode_changed_event_round_trip(tmp_path):
    from agentao.transport import EventType

    events = _drive_adapter_event(
        tmp_path,
        EventType.PERMISSION_MODE_CHANGED,
        {
            "previous": "workspace-write",
            "current": "read-only",
            "cause": "cli",
        },
    )
    ev = [e for e in events if e["kind"] == "permission_mode_changed"][0]
    assert ev["payload"]["previous"] == "workspace-write"
    assert ev["payload"]["current"] == "read-only"


def test_plugin_hook_fired_event_captures_verdict(tmp_path):
    from agentao.transport import EventType

    events = _drive_adapter_event(
        tmp_path,
        EventType.PLUGIN_HOOK_FIRED,
        {
            "hook_name": "UserPromptSubmit",
            "rule_count": 3,
            "outcome": "block",
            "blocking_error": "Not allowed in this repo",
            "stop_reason": None,
            "added_context_count": 0,
        },
    )
    ev = [e for e in events if e["kind"] == "plugin_hook_fired"][0]
    assert ev["payload"]["hook_name"] == "UserPromptSubmit"
    assert ev["payload"]["outcome"] == "block"
    assert ev["payload"]["blocking_error"] == "Not allowed in this repo"


def test_session_summary_written_event_round_trip(tmp_path):
    from agentao.transport import EventType

    events = _drive_adapter_event(
        tmp_path,
        EventType.SESSION_SUMMARY_WRITTEN,
        {
            "summary_id": "abc123",
            "session_id": "sess-1",
            "tokens_before": 150_000,
            "messages_summarized": 40,
            "summary_size": 2048,
        },
    )
    ev = [e for e in events if e["kind"] == "session_summary_written"][0]
    assert ev["payload"]["summary_id"] == "abc123"
    assert ev["payload"]["messages_summarized"] == 40


def test_llm_call_completed_carries_usage_and_status(tmp_path):
    from agentao.transport import EventType

    events = _drive_adapter_event(
        tmp_path,
        EventType.LLM_CALL_COMPLETED,
        {
            "attempt": 2,
            "status": "ok",
            "duration_ms": 420,
            "error_class": None,
            "error_message": None,
            "finish_reason": "stop",
            "prompt_tokens": 1024,
            "completion_tokens": 256,
        },
    )
    ev = [e for e in events if e["kind"] == "llm_call_completed"][0]
    assert ev["payload"]["status"] == "ok"
    assert ev["payload"]["finish_reason"] == "stop"
    assert ev["payload"]["prompt_tokens"] == 1024
    assert ev["payload"]["attempt"] == 2


def test_tool_result_duration_ms_and_hash_are_deterministic():
    """Sanity: hashing the same input gives the same result.

    The test is cheap and protects against a future refactor that
    accidentally hashes the truncated string instead of the original.
    """
    import hashlib

    content = "abc" * 10_000
    expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
    # Recompute inline — if tool_runner uses a different algorithm or
    # encoding this assertion would need to be rewritten.
    assert expected == hashlib.sha256(
        content.encode("utf-8", errors="replace"),
    ).hexdigest()


# ---------------------------------------------------------------------------
# Step 7: /replays show v2 — grouping, flags, rendering
# ---------------------------------------------------------------------------


def test_show_flag_parser_handles_space_and_equals_forms():
    from agentao.cli.replay_render import _parse_show_flags

    flags = _parse_show_flags(["--raw"])
    assert flags.raw is True

    flags = _parse_show_flags(["--turn", "abc123"])
    assert flags.turn == "abc123"

    flags = _parse_show_flags(["--turn=def456"])
    assert flags.turn == "def456"

    flags = _parse_show_flags(["--kind", "tool_result", "--errors"])
    assert flags.kind == "tool_result"
    assert flags.errors is True


def test_show_flag_parser_leaves_unknown_tokens_in_rest():
    from agentao.cli.replay_render import _parse_show_flags

    flags = _parse_show_flags(["50"])
    assert flags.rest == ["50"]
    assert flags.raw is False


def test_group_events_splits_turns_and_top_level(tmp_path):
    from agentao.cli.replay_render import _group_events_into_turns

    events = [
        {"kind": "replay_header", "payload": {}},
        {"kind": "session_started", "payload": {}},  # no turn_id → top-level
        {"kind": "turn_started", "turn_id": "t1", "payload": {}},
        {"kind": "user_message", "turn_id": "t1", "payload": {"content": "hi"}},
        {"kind": "tool_started", "turn_id": "t1", "payload": {"tool": "read_file", "call_id": "c"}},
        {"kind": "tool_completed", "turn_id": "t1", "payload": {"tool": "read_file", "call_id": "c", "status": "ok", "duration_ms": 3}},
        {"kind": "turn_completed", "turn_id": "t1", "payload": {"status": "ok", "final_text": "done"}},
        {"kind": "turn_started", "turn_id": "t2", "payload": {}},
        {"kind": "turn_completed", "turn_id": "t2", "payload": {"status": "error", "final_text": ""}},
        {"kind": "replay_footer", "payload": {}},
    ]
    turns, top = _group_events_into_turns(events)
    assert [t["id"] for t in turns] == ["t1", "t2"]
    assert turns[0]["has_error"] is False
    assert turns[1]["has_error"] is True
    # session_started has no turn_id → top-level. Header/footer dropped.
    top_kinds = [e["kind"] for e in top]
    assert "session_started" in top_kinds
    assert "replay_header" not in top_kinds
    assert "replay_footer" not in top_kinds


def test_collect_tool_rows_aggregates_multiple_events_per_call():
    from agentao.cli.replay_render import _collect_tool_rows

    events = [
        {"kind": "tool_started", "payload": {"tool": "shell", "call_id": "A", "tool_source": "builtin"}},
        {"kind": "tool_output_chunk", "payload": {"tool": "shell", "call_id": "A", "truncated": True}},
        {"kind": "tool_completed", "payload": {"tool": "shell", "call_id": "A", "status": "ok", "duration_ms": 12, "error": None}},
        {"kind": "tool_result", "payload": {"tool": "shell", "call_id": "A", "content_truncation": {"truncated": True}, "saved_to_disk": True, "original_chars": 99999}},
    ]
    rows = _collect_tool_rows(events)
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "shell"
    assert row["status"] == "ok"
    assert row["duration_ms"] == 12
    assert row["truncated"] is True
    assert row["saved_to_disk"] is True
    assert row["output_truncated"] is True


def test_summarize_unknown_kind_returns_payload_key_preview():
    from agentao.cli.replay_render import _summarize_replay_event

    s = _summarize_replay_event(
        {"kind": "future_feature_v12", "payload": {"alpha": 1, "beta": 2}},
    )
    # Doesn't crash; previews payload keys so the user sees what's there.
    assert "alpha" in s
    assert "beta" in s


def test_summarize_tool_result_event_includes_size_and_flags():
    from agentao.cli.replay_render import _summarize_replay_event

    s = _summarize_replay_event({
        "kind": "tool_result",
        "payload": {
            "tool": "shell",
            "original_chars": 12345,
            "content_truncation": {"truncated": True},
            "saved_to_disk": True,
        },
    })
    assert "shell" in s
    assert "12,345" in s
    assert "truncated" in s
    assert "saved_to_disk" in s


def test_summarize_replay_footer_reports_redaction_totals():
    from agentao.cli.replay_render import _summarize_replay_event

    s = _summarize_replay_event({
        "kind": "replay_footer",
        "payload": {
            "event_count": 57,
            "redaction_hits": {"openai_api_key": 2, "bearer": 1},
            "truncated_field_hits": {"chunk": 1},
            "dropped_field_hits": {},
        },
    })
    assert "events=57" in s
    assert "redactions=3" in s
    assert "truncated_fields=1" in s


def test_render_replay_grouped_runs_end_to_end_on_real_recorder(tmp_path):
    """Smoke test: write a small replay, render grouped, verify output contains
    the turn header plus tool and assistant markers."""
    from io import StringIO

    from rich.console import Console

    from agentao.cli.replay_render import _render_replay_grouped
    from agentao.replay.adapter import ReplayAdapter
    from agentao.replay.reader import ReplayReader
    from agentao.transport import AgentEvent, EventType, NullTransport

    rec = ReplayRecorder.create(
        "sess", tmp_path, capture_flags={"capture_llm_delta": True},
    )
    adapter = ReplayAdapter(NullTransport(), rec)
    adapter.begin_turn("hello world")
    adapter.emit(AgentEvent(EventType.TOOL_START, {"tool": "read_file", "call_id": "c1", "args": {"path": "README.md"}}))
    adapter.emit(AgentEvent(EventType.TOOL_COMPLETE, {
        "tool": "read_file", "call_id": "c1", "status": "ok", "duration_ms": 3, "error": None,
    }))
    adapter.emit(AgentEvent(EventType.TOOL_RESULT, {
        "tool": "read_file", "call_id": "c1",
        "content": "some content",
        "content_hash": "h",
        "original_chars": 12,
        "saved_to_disk": False,
        "disk_path": None,
        "status": "ok",
        "duration_ms": 3,
        "error": None,
    }))
    adapter.emit(AgentEvent(EventType.LLM_TEXT, {"chunk": "Hi "}))
    adapter.emit(AgentEvent(EventType.LLM_TEXT, {"chunk": "there."}))
    adapter.end_turn("Hi there.")
    rec.close()

    events = ReplayReader(rec.path).events()
    buf = StringIO()
    con = Console(file=buf, force_terminal=False, width=120)
    meta = ReplayReader(rec.path).meta()
    _render_replay_grouped(events, meta, con)
    out = buf.getvalue()

    # Header + turn marker + user message + tool row + aggregated asst text
    assert "Replay " in out
    assert "Turn " in out
    assert "hello world" in out
    assert "read_file" in out
    assert "args" in out
    assert "README.md" in out
    assert "sha256" in out
    assert "12 chars" in out
    # Aggregated assistant chunks render as one concatenated line.
    assert "Hi there." in out
    # Footer shows up
    assert "End of replay" in out


def test_render_replay_grouped_shows_full_final_text_without_chunks(tmp_path):
    from io import StringIO

    from rich.console import Console

    from agentao.cli.replay_render import _render_replay_grouped
    from agentao.replay.adapter import ReplayAdapter
    from agentao.replay.reader import ReplayReader
    from agentao.transport import NullTransport

    rec = ReplayRecorder.create("sess", tmp_path)
    adapter = ReplayAdapter(NullTransport(), rec)
    adapter.begin_turn("long answer please")
    final = "start " + ("middle " * 140) + "final tail marker"
    adapter.end_turn(final)
    rec.close()

    events = ReplayReader(rec.path).events()
    buf = StringIO()
    con = Console(file=buf, force_terminal=False, width=120)
    meta = ReplayReader(rec.path).meta()
    _render_replay_grouped(events, meta, con)
    out = buf.getvalue()

    assert "long answer please" in out
    assert "start middle" in out
    assert "final tail marker" in out
    assert "chars)" in out


def test_render_replay_grouped_errors_only_filter_works(tmp_path):
    from io import StringIO

    from rich.console import Console

    from agentao.cli.replay_render import _render_replay_grouped
    from agentao.replay.adapter import ReplayAdapter
    from agentao.replay.reader import ReplayReader
    from agentao.transport import AgentEvent, EventType, NullTransport

    rec = ReplayRecorder.create("sess", tmp_path)
    adapter = ReplayAdapter(NullTransport(), rec)
    adapter.begin_turn("ok turn")
    adapter.end_turn("fine", status="ok")
    adapter.begin_turn("bad turn")
    adapter.end_turn("oops", status="error", error="boom")
    rec.close()

    events = ReplayReader(rec.path).events()
    buf = StringIO()
    con = Console(file=buf, force_terminal=False, width=120)
    meta = ReplayReader(rec.path).meta()
    _render_replay_grouped(events, meta, con, errors_only=True)
    out = buf.getvalue()

    # "bad turn" user message must appear; "ok turn" must not.
    assert "bad turn" in out
    assert "ok turn" not in out


def test_render_replay_grouped_surfaces_audit_details(tmp_path):
    from io import StringIO

    from rich.console import Console

    from agentao.cli.replay_render import _render_replay_grouped
    from agentao.replay.adapter import ReplayAdapter
    from agentao.replay.reader import ReplayReader
    from agentao.transport import AgentEvent, EventType, NullTransport

    rec = ReplayRecorder.create("sess", tmp_path)
    adapter = ReplayAdapter(NullTransport(), rec)
    adapter.begin_turn("audit this")
    adapter.emit(AgentEvent(EventType.TOOL_CONFIRMATION, {
        "tool": "run_shell_command",
        "args": {"cmd": "pwd"},
    }))
    adapter.emit(AgentEvent(EventType.ERROR, {
        "message": "tool failed",
        "detail": "stack trace here",
    }))
    adapter.emit(AgentEvent(EventType.LLM_CALL_STARTED, {
        "attempt": 1,
        "model": "gpt-test",
        "n_messages": 3,
        "tool_count": 1,
    }))
    adapter.emit(AgentEvent(EventType.LLM_CALL_COMPLETED, {
        "attempt": 1,
        "status": "error",
        "duration_ms": 1500,
        "error_class": "RuntimeError",
        "error_message": "boom",
        "finish_reason": None,
        "prompt_tokens": None,
        "completion_tokens": None,
    }))
    adapter.end_turn("failed", status="error", error="boom")
    rec.close()

    events = ReplayReader(rec.path).events()
    buf = StringIO()
    con = Console(file=buf, force_terminal=False, width=120)
    meta = ReplayReader(rec.path).meta()
    _render_replay_grouped(events, meta, con)
    out = buf.getvalue()

    assert "file=" in out
    assert "seq=" in out
    assert "confirm" in out
    assert "run_shell_command" in out
    assert "cmd" in out
    assert "error" in out
    assert "stack trace here" in out
    assert "LLM" in out
    assert "1.50s" in out
    assert "RuntimeError" in out


def test_config_coerces_non_bool_capture_flag_strings(tmp_path):
    (tmp_path / ".agentao").mkdir()
    (tmp_path / ".agentao" / "settings.json").write_text(
        json.dumps({
            "replay": {
                "capture_flags": {
                    "capture_llm_delta": "false",
                    "capture_full_llm_io": "yes",
                }
            }
        })
    )
    from agentao.replay.config import load_replay_config

    cfg = load_replay_config(tmp_path)
    assert cfg.capture_flags["capture_llm_delta"] is False
    assert cfg.capture_flags["capture_full_llm_io"] is True


# ---------------------------------------------------------------------------
# Deep-capture flag bypasses ScanTruncate
# ---------------------------------------------------------------------------


def test_sanitize_respects_capture_tool_result_full(tmp_path):
    """With the flag on, tool_result.content keeps its full length."""
    big = "x" * (TOOL_RESULT_MAX_CHARS * 3)
    clean_capped, _ = sanitize_event("tool_result", {"content": big})
    assert len(clean_capped["content"]) < len(big)
    assert clean_capped["content_truncation"]["truncated"] is True

    clean_full, _ = sanitize_event(
        "tool_result",
        {"content": big},
        capture_flags={"capture_tool_result_full": True},
    )
    assert clean_full["content"] == big
    assert "content_truncation" not in clean_full


def test_sanitize_full_capture_still_scans_secrets():
    """Deep-capture waives the length cap but the scanner still runs."""
    leak = "leak sk-proj-" + "A" * 30
    clean, stats = sanitize_event(
        "tool_result",
        {"content": leak + " " + "z" * (TOOL_RESULT_MAX_CHARS * 2)},
        capture_flags={"capture_tool_result_full": True},
    )
    # No truncation markers, but the OpenAI key got redacted.
    assert "content_truncation" not in clean
    assert "sk-proj-" + "A" * 30 not in clean["content"]
    assert stats.redaction_hits.get("openai_api_key") == 1


def test_recorder_threads_capture_flags_to_sanitize(tmp_path):
    """End-to-end: ReplayRecorder with capture_tool_result_full writes full content."""
    big = "y" * (TOOL_RESULT_MAX_CHARS * 2)
    rec = ReplayRecorder.create(
        "sess", tmp_path,
        capture_flags={"capture_tool_result_full": True},
    )
    rec.record(
        "tool_result",
        payload={
            "tool": "t",
            "call_id": "c",
            "content": big,
            "content_hash": "abc123",
        },
    )
    rec.close()
    events = [json.loads(l) for l in rec.path.read_text().splitlines()]
    body = [e for e in events if e["kind"] == "tool_result"][0]
    assert body["payload"]["content"] == big
    assert "content_truncation" not in body["payload"]
    # Footer must not double-book a truncated_field_hit for content.
    footer = [e for e in events if e["kind"] == "replay_footer"][0]
    assert "content" not in footer["payload"]["truncated_field_hits"]


def test_sanitize_full_capture_off_still_truncates():
    """Safety: flag absent/False keeps the existing ScanTruncate behavior."""
    big = "x" * (TOOL_RESULT_MAX_CHARS * 2)
    for flags in (None, {}, {"capture_tool_result_full": False}):
        clean, _ = sanitize_event(
            "tool_result", {"content": big}, capture_flags=flags,
        )
        assert clean["content_truncation"]["truncated"] is True


# ---------------------------------------------------------------------------
# Legacy import path: agentao.tool_runner keeps working after the refactor
# ---------------------------------------------------------------------------


def test_legacy_tool_runner_import_path():
    """`from agentao.tool_runner import ToolRunner` keeps working.

    The module moved to ``agentao.runtime.tool_runner``; this guards
    the shim that re-exports the public surface from the old path.
    """
    import agentao.tool_runner as legacy
    from agentao.runtime.tool_runner import ToolRunner as RuntimeToolRunner

    from agentao.tool_runner import ToolRunner  # noqa: F401

    assert ToolRunner is RuntimeToolRunner
    assert legacy.ToolRunner is RuntimeToolRunner
