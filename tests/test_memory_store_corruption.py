"""A memory store that cannot be read must not stop the session (codex #49701 / #49710).

A ``memory.db`` with a valid header and corrupt pages opens fine and then
fails every query. The stable-memory block reads it while building the system
prompt, so every ``chat()`` raised ``DatabaseError`` before the model was
called. A file that cannot be opened at all fell back to a transient store with
only a log line. Both now run without the store and give the host one notice.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentao.agent import Agentao
from agentao.memory import MemoryManager
from agentao.memory.models import SaveMemoryRequest
from agentao.memory.storage import SQLiteMemoryStore


def _corrupt_db(path: Path) -> None:
    """A database whose header and schema survive and whose data pages do not."""
    store = SQLiteMemoryStore.open(path)
    manager = MemoryManager(project_store=store)
    for i in range(300):
        manager.upsert(SaveMemoryRequest(key=f"k{i}", value="value " * 50, scope="project"))
    manager.close()
    data = bytearray(path.read_bytes())
    page = int.from_bytes(data[16:18], "big")
    for off in range(len(data) // 2 // page * page, len(data), page):
        data[off:off + page] = b"\xde\xad" * (page // 2)
    path.write_bytes(bytes(data))


def _reply(messages, tools, token):
    message = SimpleNamespace(content="ok", tool_calls=None, reasoning_content=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="stop")],
        usage=None, model="m",
    )


def _agent(wd: Path, manager: MemoryManager) -> Agentao:
    agent = Agentao(
        api_key="k", base_url="https://api.example.test", model="m",
        working_directory=wd, logger=logging.getLogger("t"), memory_manager=manager,
    )
    agent._llm_call = _reply
    return agent


@pytest.fixture
def corrupt_store(tmp_path):
    db = tmp_path / ".agentao" / "memory.db"
    db.parent.mkdir(parents=True)
    _corrupt_db(db)
    store = SQLiteMemoryStore.open_or_memory(db)
    assert store.fallback_reason is None   # it opened: the damage is past the header
    return store


def test_a_store_with_corrupt_pages_does_not_fail_the_turn(tmp_path, corrupt_store, caplog):
    manager = MemoryManager(project_store=corrupt_store)
    agent = _agent(tmp_path, manager)
    try:
        with caplog.at_level(logging.WARNING, logger="agentao.memory.manager"):
            assert agent.chat("hi") == "ok"
            assert agent.chat("again") == "ok"
    finally:
        agent.close()
    warnings = [r for r in caplog.records if "cannot be read" in r.getMessage()]
    assert len(warnings) == 1                       # once, not once per read
    problems = manager.take_store_problems()
    assert len(problems) == 1 and "project memory store" in problems[0]
    assert manager.take_store_problems() == []      # each notice is taken once


def test_a_store_that_cannot_be_opened_gives_a_notice(tmp_path):
    db = tmp_path / "memory.db"
    db.write_bytes(b"this is not a database" * 100)
    store = SQLiteMemoryStore.open_or_memory(db)
    assert store.db_path == ":memory:"
    manager = MemoryManager(project_store=store)
    problems = manager.take_store_problems()
    assert len(problems) == 1 and str(db) in problems[0]


def test_an_unopenable_user_store_gives_a_notice(tmp_path, monkeypatch):
    from agentao.embedding import build_from_environment

    home = tmp_path / "home"
    (home / "memory.db").mkdir(parents=True)        # a directory: open() raises
    monkeypatch.setattr("agentao.embedding.factory.safe_load_dotenv", lambda *a, **kw: None)
    monkeypatch.setattr("agentao.paths.user_root", lambda: home)
    monkeypatch.setenv("LLM_PROVIDER", "OPENAI")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://test.local/v1")
    monkeypatch.setenv("OPENAI_MODEL", "m")
    agent = build_from_environment(working_directory=tmp_path)
    try:
        problems = agent.memory_manager.take_store_problems()
    finally:
        agent.close()
    assert len(problems) == 1 and "user memory store" in problems[0]


def test_a_non_database_error_still_raises(tmp_path):
    """Only a database error means "this file is unusable". A bug must not hide."""
    store = MagicMock()
    store.list_memories.side_effect = TypeError("bug")
    store.fallback_reason = None
    manager = MemoryManager(project_store=store)
    with pytest.raises(TypeError):
        manager.get_all_entries()


def test_the_cli_prints_each_notice_once(tmp_path, corrupt_store, capsys):
    from agentao.cli.input_loop import _report_memory_problems

    manager = MemoryManager(project_store=corrupt_store)
    manager.get_all_entries()
    cli = SimpleNamespace(agent=SimpleNamespace(memory_manager=manager))
    _report_memory_problems(cli)
    _report_memory_problems(cli)
    out = " ".join(capsys.readouterr().out.split())
    assert out.count("cannot be read") == 1


def test_the_cli_ignores_a_mock_agent(capsys):
    from agentao.cli.input_loop import _report_memory_problems

    _report_memory_problems(SimpleNamespace(agent=MagicMock()))
    assert capsys.readouterr().out == ""


def test_a_locked_store_is_not_called_corrupt():
    """A lock says nothing about the file: moving a healthy shared store aside loses it."""
    import sqlite3

    store = MagicMock()
    store.db_path = "/x/memory.db"
    store.fallback_reason = None
    store.list_memories.side_effect = sqlite3.OperationalError("database is locked")
    manager = MemoryManager(project_store=store)
    assert manager.get_all_entries() == []
    (problem,) = manager.take_store_problems()
    assert "locked" in problem and "move it aside to start" not in problem


def test_recall_does_not_cache_an_index_built_from_a_failed_read(tmp_path):
    """A lock that clears must not leave recall empty until the next write."""
    import sqlite3

    from agentao.memory.retriever import MemoryRetriever

    store = SQLiteMemoryStore.open(tmp_path / "memory.db")
    manager = MemoryManager(project_store=store)
    manager.upsert(SaveMemoryRequest(key="deploy_target", value="staging cluster", scope="project"))
    retriever = MemoryRetriever(manager)

    real = store.list_memories
    store.list_memories = lambda *a, **k: (_ for _ in ()).throw(
        sqlite3.OperationalError("database is locked"))
    retriever._rebuild_index_if_stale()
    assert retriever._records_by_id == {}
    assert retriever._index_built is False

    store.list_memories = real                      # the lock clears; nothing writes
    retriever._rebuild_index_if_stale()
    assert len(retriever._records_by_id) == 1
    manager.close()
