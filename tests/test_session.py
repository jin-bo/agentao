"""Regression tests for session save/restore (agentao/session.py)."""

import json
from pathlib import Path

import pytest

import agentao.session as session_module
from agentao.session import (
    delete_all_sessions,
    delete_session,
    list_sessions,
    load_session,
    save_session,
)

_MESSAGES = [
    {"role": "user", "content": "hello"},
    {"role": "assistant", "content": "hi there"},
]
_MODEL = "gpt-5.4"
_SKILLS = ["my-skill"]


@pytest.fixture(autouse=True)
def isolated_session_dir(tmp_path, monkeypatch):
    """Redirect _session_dir() to a temp directory for every test.

    Issue 05 added an optional ``project_root`` parameter to ``_session_dir``;
    the mock accepts but ignores it so the tests keep working unchanged.
    """
    monkeypatch.setattr(
        session_module, "_session_dir", lambda project_root=None: tmp_path / "sessions"
    )


def test_save_and_load_roundtrip():
    _, sid = save_session(_MESSAGES, _MODEL, _SKILLS)
    assert "-" in sid  # UUID format
    messages, model, skills = load_session()
    assert messages == _MESSAGES
    assert model == _MODEL
    assert skills == _SKILLS


def test_load_latest_when_no_id(tmp_path):
    # Write two files with distinct second-precision timestamps directly, so the
    # "latest" is unambiguous regardless of how fast the test runs.
    session_dir = tmp_path / "sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    for ts, model, content in [
        ("20260101_000001", "gpt-4", "first"),
        ("20260101_000002", "gpt-5.4", "second"),
    ]:
        data = {
            "timestamp": ts,
            "model": model,
            "active_skills": [],
            "messages": [{"role": "user", "content": content}],
        }
        (session_dir / f"{ts}.json").write_text(json.dumps(data), encoding="utf-8")
    messages, model, _ = load_session()
    assert messages[0]["content"] == "second"
    assert model == "gpt-5.4"


def test_load_by_id_prefix(tmp_path):
    # Create two files with distinct timestamps; load the first one by its full stem prefix.
    session_dir = tmp_path / "sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    ts1, ts2 = "20260101_000001", "20260101_000002"
    for ts, content in [(ts1, "alpha"), (ts2, "beta")]:
        data = {"timestamp": ts, "model": "gpt-4", "active_skills": [],
                "messages": [{"role": "user", "content": content}]}
        (session_dir / f"{ts}.json").write_text(json.dumps(data), encoding="utf-8")
    messages, _, _ = load_session(session_id=ts1)
    assert messages[0]["content"] == "alpha"


def test_list_sessions_metadata():
    save_session(_MESSAGES, _MODEL, _SKILLS)
    sessions = list_sessions()
    assert len(sessions) == 1
    s = sessions[0]
    assert s["model"] == _MODEL
    assert s["message_count"] == len(_MESSAGES)
    assert s["active_skills"] == _SKILLS
    assert s["first_user_msg"] == "hello"
    assert "id" in s
    assert "timestamp" in s
    assert "-" in (s.get("session_id") or "")  # stable UUID present
    assert s.get("title") == "hello"            # title derived from first user msg
    assert s.get("created_at") is not None
    assert s.get("updated_at") is not None


def test_list_first_user_msg_truncation():
    long_msg = "x" * 100
    save_session([{"role": "user", "content": long_msg}], _MODEL, [])
    sessions = list_sessions()
    assert sessions[0]["first_user_msg"].endswith("...")
    assert len(sessions[0]["first_user_msg"]) == 80


def test_rotation_keeps_max_10(tmp_path):
    # save_session uses second-precision timestamps that collide in rapid loops;
    # create session files directly and invoke _rotate_sessions explicitly.
    from agentao.session import _rotate_sessions
    session_dir = tmp_path / "sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    for i in range(12):
        ts = f"20260101_{i:06d}"
        data = {"timestamp": ts, "model": _MODEL, "active_skills": [], "messages": []}
        (session_dir / f"{ts}.json").write_text(json.dumps(data), encoding="utf-8")
    _rotate_sessions(session_dir)
    remaining = list(session_dir.glob("*.json"))
    assert len(remaining) == 10


def test_delete_session_by_uuid():
    _, sid = save_session(_MESSAGES, _MODEL, [])
    result = delete_session(sid)  # delete by full UUID
    assert result is True
    assert list_sessions() == []


def test_delete_session_by_timestamp_prefix():
    path, _ = save_session(_MESSAGES, _MODEL, [])
    result = delete_session(path.stem)  # delete by timestamp stem (backward compat)
    assert result is True
    assert list_sessions() == []


def test_delete_session_returns_false_for_missing():
    result = delete_session("nonexistent_id")
    assert result is False


def test_delete_all_sessions(tmp_path):
    # Create two session files with distinct timestamps directly.
    session_dir = tmp_path / "sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    for ts in ["20260101_000001", "20260101_000002"]:
        data = {"timestamp": ts, "model": _MODEL, "active_skills": [], "messages": _MESSAGES}
        (session_dir / f"{ts}.json").write_text(json.dumps(data), encoding="utf-8")
    count = delete_all_sessions()
    assert count == 2
    assert list_sessions() == []


def test_load_by_uuid():
    _, sid = save_session(_MESSAGES, _MODEL, _SKILLS)
    messages, model, skills = load_session(session_id=sid)
    assert messages == _MESSAGES
    assert model == _MODEL


def test_load_by_uuid_short_prefix():
    """8-char prefix (no hyphens) as shown by /sessions list must work."""
    _, sid = save_session(_MESSAGES, _MODEL, _SKILLS)
    short = sid[:8]
    assert "-" not in short
    messages, _, _ = load_session(session_id=short)
    assert messages == _MESSAGES


def test_delete_session_by_short_uuid_prefix():
    """8-char UUID prefix (no hyphens) must delete the correct session."""
    _, sid = save_session(_MESSAGES, _MODEL, [])
    short = sid[:8]
    assert "-" not in short
    result = delete_session(short)
    assert result is True
    assert list_sessions() == []


def test_save_continuation_preserves_created_at():
    # First save — establishes created_at.
    _, sid = save_session(_MESSAGES, _MODEL, [])
    sessions_before = list_sessions()
    created_at_original = sessions_before[0]["created_at"]

    # Second save with same session_id — created_at must be preserved.
    extended = _MESSAGES + [{"role": "user", "content": "more"}]
    _, sid2 = save_session(extended, _MODEL, [], session_id=sid)
    assert sid2 == sid  # same UUID returned
    sessions_after = list_sessions()
    # Pick the entry with matching session_id
    match = next(s for s in sessions_after if s.get("session_id") == sid)
    assert match["created_at"] == created_at_original


def test_delete_removes_all_checkpoints_with_same_uuid(tmp_path):
    """Deleting a session UUID must remove every checkpoint file sharing that UUID."""
    import uuid as _uuid
    session_dir = tmp_path / "sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    sid = str(_uuid.uuid4())
    # Write two checkpoint files with distinct timestamps but the same session_id.
    for ts in ["20260101_000001", "20260101_000002"]:
        data = {
            "session_id": sid,
            "title": "test",
            "created_at": "2026-01-01T00:00:01",
            "updated_at": ts,
            "model": _MODEL,
            "active_skills": [],
            "messages": _MESSAGES,
        }
        (session_dir / f"{ts}.json").write_text(json.dumps(data), encoding="utf-8")
    assert len(list_sessions()) == 2
    result = delete_session(sid)
    assert result is True
    assert list_sessions() == []


def test_load_nonexistent_raises_file_not_found():
    with pytest.raises(FileNotFoundError):
        load_session()


def test_load_with_bad_id_raises_file_not_found():
    save_session(_MESSAGES, _MODEL, [])
    with pytest.raises(FileNotFoundError):
        load_session(session_id="no_such_id")
