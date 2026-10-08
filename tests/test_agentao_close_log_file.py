"""``close()`` releases the ``agentao.log`` file the agent opened.

Without a ``logger=``, ``Agentao`` builds an ``LLMClient`` that attaches a
file handler on the ``agentao`` package logger, holding
``<working_directory>/agentao.log`` open. Windows cannot delete an open file,
so a host removing a temporary working directory after ``close()`` failed
with ``WinError 32``. POSIX deletes open files, so these tests assert the
handle itself: detached from the logger and its stream closed.
"""

from __future__ import annotations

import asyncio
import gc
import logging
import shutil

import pytest

from agentao import Agentao
from agentao.llm import LLMClient
from agentao.llm import client as llm_client_module


def _tagged() -> list[logging.Handler]:
    return [
        h
        for h in logging.getLogger("agentao").handlers
        if getattr(h, "_agentao_llm_file_handler", False)
    ]


@pytest.fixture(autouse=True)
def _restore_package_logger(monkeypatch):
    pkg = logging.getLogger("agentao")
    handlers, level = list(pkg.handlers), pkg.level
    # A handler and clients left open by other tests are set aside: the
    # file is not handed to them here, and _tagged() sees only this test's.
    for h in handlers:
        if getattr(h, "_agentao_llm_file_handler", False):
            pkg.removeHandler(h)
    monkeypatch.setattr(llm_client_module, "_log_owners", [])
    yield
    for h in list(pkg.handlers):
        if h not in handlers:
            pkg.removeHandler(h)
            h.close()
    for h in handlers:
        if h not in pkg.handlers:
            pkg.addHandler(h)
    pkg.setLevel(level)


def _build(wd, **kw):
    return Agentao(
        working_directory=wd,
        api_key="k",
        base_url="https://test.local/v1",
        model="m",
        **kw,
    )


def _log_handler(agent: Agentao) -> logging.FileHandler:
    [handler] = _tagged()
    assert agent.llm._file_handler is handler
    assert handler.baseFilename == str(agent.working_directory / "agentao.log")
    return handler


def test_close_detaches_and_closes_the_log_file(tmp_path):
    agent = _build(tmp_path)
    handler = _log_handler(agent)
    assert handler.stream is not None
    agent.close()
    assert _tagged() == []
    assert handler.stream is None  # the file is no longer held open
    agent.close()  # idempotent


def test_a_record_already_in_flight_does_not_reopen_the_file(tmp_path):
    """An emission that got the handler before ``close()`` (a background
    thread) reaches ``handle()`` after it; the file must not come back."""
    agent = _build(tmp_path)
    handler = _log_handler(agent)
    agent.close()
    (tmp_path / "agentao.log").unlink()
    handler.handle(logging.makeLogRecord({"msg": "late"}))
    assert handler.stream is None
    assert not (tmp_path / "agentao.log").exists()


def test_async_with_closes_the_log_file(tmp_path):
    async def main() -> logging.FileHandler:
        async with _build(tmp_path) as agent:
            return _log_handler(agent)

    handler = asyncio.run(main())
    assert _tagged() == []
    assert handler.stream is None


def test_a_failed_construction_closes_the_log_file(tmp_path, monkeypatch):
    opened: list[logging.Handler] = []
    real = LLMClient._build_file_handler

    def _spy(log_file):
        opened.append(real(log_file))
        return opened[-1]

    monkeypatch.setattr(LLMClient, "_build_file_handler", staticmethod(_spy))
    with pytest.raises(ValueError, match="no_such_tool"):
        _build(tmp_path, enabled_tools={"no_such_tool"})
    assert _tagged() == []
    [handler] = opened
    assert handler.stream is None


def test_a_failed_llm_client_construction_closes_the_log_file(
    tmp_path, monkeypatch
):
    """The handler is attached before the SDK client is built; a raise there
    leaves no client for anyone to close."""
    opened: list[logging.Handler] = []
    real = LLMClient._build_file_handler

    def _spy(log_file):
        opened.append(real(log_file))
        return opened[-1]

    def _boom(self):
        raise RuntimeError("adapter broke")

    monkeypatch.setattr(LLMClient, "_build_file_handler", staticmethod(_spy))
    monkeypatch.setattr(LLMClient, "_make_adapter", _boom)
    with pytest.raises(RuntimeError, match="adapter broke"):
        _build(tmp_path)
    assert _tagged() == []
    [handler] = opened
    assert handler.stream is None


def test_an_injected_llm_client_is_left_to_the_caller(tmp_path):
    llm = LLMClient(
        api_key="k",
        base_url="https://test.local/v1",
        model="m",
        log_file=str(tmp_path / "host.log"),
    )
    [handler] = _tagged()
    agent = Agentao(working_directory=tmp_path, llm_client=llm)
    agent.close()
    assert _tagged() == [handler]
    assert handler.stream is not None
    llm.close()
    assert _tagged() == []
    assert handler.stream is None


def test_closing_an_older_agent_keeps_the_newer_agents_log(tmp_path):
    """A later client evicts the earlier one's handler and installs its own;
    closing the earlier agent must not take the later one's away."""
    older = _build(tmp_path / "a")
    newer = _build(tmp_path / "b")
    handler = _log_handler(newer)
    older.close()
    assert _tagged() == [handler]
    assert handler.stream is not None
    newer.close()
    assert _tagged() == []


def test_closing_the_newest_agent_hands_the_log_back(tmp_path):
    """The handler is process-wide; closing the agent that holds it must not
    end file logging for an agent still running (an ACP server's other
    sessions)."""
    older = _build(tmp_path / "a")
    newer = _build(tmp_path / "b")
    newer_handler = _log_handler(newer)
    newer.close()
    assert newer_handler.stream is None
    restored = _log_handler(older)
    assert restored is not newer_handler
    logging.getLogger("agentao.test").info("after the newer agent closed")
    assert "after the newer agent closed" in (
        tmp_path / "a" / "agentao.log"
    ).read_text(encoding="utf-8")
    older.close()
    assert _tagged() == []
    assert restored.stream is None


def test_the_log_goes_back_past_agents_already_closed(tmp_path):
    first = _build(tmp_path / "a")
    middle = _build(tmp_path / "b")
    last = _build(tmp_path / "c")
    middle.close()  # its handler was already evicted: nothing changes
    _log_handler(last)
    last.close()
    _log_handler(first)
    first.close()
    assert _tagged() == []


def test_a_failed_construction_hands_the_log_back(tmp_path):
    live = _build(tmp_path / "a")
    with pytest.raises(ValueError, match="no_such_tool"):
        _build(tmp_path / "b", enabled_tools={"no_such_tool"})
    _log_handler(live)
    live.close()
    assert _tagged() == []


def test_the_log_is_not_handed_to_an_agent_whose_directory_is_gone(tmp_path):
    older = _build(tmp_path / "a")
    newer = _build(tmp_path / "b")
    shutil.rmtree(tmp_path / "a")
    newer.close()
    assert _tagged() == []
    assert not (tmp_path / "a").exists()
    older.close()


def test_a_client_dropped_without_close_is_not_handed_the_log(tmp_path):
    dropped = LLMClient(
        api_key="k",
        base_url="https://test.local/v1",
        model="m",
        log_file=str(tmp_path / "dropped.log"),
    )
    newer = _build(tmp_path / "b")
    del dropped
    gc.collect()
    newer.close()
    assert _tagged() == []


def test_an_injected_logger_attaches_and_closes_nothing(tmp_path):
    agent = _build(tmp_path, logger=logging.getLogger("test_close_log_file"))
    assert _tagged() == []
    assert agent.llm._file_handler is None
    agent.close()
    assert not (tmp_path / "agentao.log").exists()
