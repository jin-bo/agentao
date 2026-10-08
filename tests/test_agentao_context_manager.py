"""``with Agentao(...)`` / ``async with Agentao(...)`` close the agent (F4 in
``docs/design/host-api-ergonomics-review.md``).

``__exit__`` calls ``close()``; ``__aexit__`` awaits ``aclose()``, which runs
``close()`` on a worker thread so the host's loop is not blocked by the MCP
disconnect. Neither suppresses an exception from the block.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path

import pytest

from agentao import Agentao


def _bare(tmp_path: Path) -> Agentao:
    return Agentao(
        working_directory=tmp_path,
        api_key="k",
        base_url="https://test.local/v1",
        model="m",
        logger=logging.getLogger("test_agentao_context_manager"),
    )


class _CloseSpy:
    """Records each ``close()`` and the thread it ran on, then really closes."""

    def __init__(self, agent: Agentao) -> None:
        self.threads: list[int] = []
        real = agent.close

        def _close() -> None:
            self.threads.append(threading.get_ident())
            real()

        agent.close = _close  # type: ignore[method-assign]


def _give_built_mcp(agent: Agentao, mcp: "_FakeMcp") -> None:
    """Stand ``mcp`` in for a manager the agent built itself, the only kind
    ``close()`` disconnects."""
    agent.mcp_manager = mcp  # type: ignore[assignment]
    agent._built_mcp_manager = mcp  # type: ignore[assignment]


class _FakeMcp:
    def __init__(self) -> None:
        self.disconnects = 0

    def disconnect_all(self) -> None:
        self.disconnects += 1


def test_with_returns_the_agent_and_closes_it(tmp_path):
    agent = _bare(tmp_path)
    spy = _CloseSpy(agent)
    with agent as entered:
        assert entered is agent
        assert spy.threads == []
    assert spy.threads == [threading.get_ident()]


def test_with_closes_and_reraises_on_error(tmp_path):
    agent = _bare(tmp_path)
    spy = _CloseSpy(agent)
    with pytest.raises(RuntimeError, match="boom"):
        with agent:
            raise RuntimeError("boom")
    assert len(spy.threads) == 1


def test_async_with_closes_off_the_loop_thread(tmp_path):
    agent = _bare(tmp_path)
    spy = _CloseSpy(agent)

    async def main() -> int:
        async with agent as entered:
            assert entered is agent
            assert spy.threads == []
        return threading.get_ident()

    loop_thread = asyncio.run(main())
    assert len(spy.threads) == 1
    assert spy.threads[0] != loop_thread


def test_async_with_closes_and_reraises_on_error(tmp_path):
    agent = _bare(tmp_path)
    spy = _CloseSpy(agent)

    async def main() -> None:
        async with agent:
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(main())
    assert len(spy.threads) == 1


def test_aclose_runs_close(tmp_path):
    agent = _bare(tmp_path)
    mcp = _FakeMcp()
    _give_built_mcp(agent, mcp)
    asyncio.run(agent.aclose())
    assert mcp.disconnects == 1
    assert agent.mcp_manager is None


def test_explicit_close_inside_with_is_safe(tmp_path, caplog):
    """A host that still calls ``close()`` inside the block is not hurt by
    the second close at the exit: MCP is disconnected once, and the second
    replay end and memory-store close log nothing."""
    from agentao.replay import ReplayConfig

    agent = Agentao(
        working_directory=tmp_path,
        api_key="k",
        base_url="https://test.local/v1",
        model="m",
        replay_config=ReplayConfig(enabled=True),
        logger=logging.getLogger("test_agentao_context_manager"),
    )
    mcp = _FakeMcp()
    _give_built_mcp(agent, mcp)
    assert agent.start_replay() is not None
    caplog.set_level(logging.WARNING)
    with agent:
        agent.close()
    assert mcp.disconnects == 1
    assert agent.replay_manager is not None
    assert agent.replay_manager.recorder is None
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_async_with_closes_when_the_block_is_cancelled(tmp_path):
    """The documented ``async with`` around a cancelled ``arun()`` path: a
    ``CancelledError`` out of the block still closes the agent, and is
    re-raised."""
    agent = _bare(tmp_path)
    spy = _CloseSpy(agent)

    async def main() -> None:
        async def body() -> None:
            async with agent:
                await asyncio.sleep(3600)

        task = asyncio.create_task(body())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())
    assert len(spy.threads) == 1


def test_concurrent_closes_run_the_teardown_once(tmp_path):
    """A cancelled ``aclose()`` leaves ``close()`` running on a worker
    thread; a second ``close()`` from the host must wait for it, not run
    the teardown beside it."""
    agent = _bare(tmp_path)
    entered = threading.Event()
    release = threading.Event()

    class _SlowMcp(_FakeMcp):
        def disconnect_all(self) -> None:
            super().disconnect_all()
            entered.set()
            release.wait(5)

    mcp = _SlowMcp()
    _give_built_mcp(agent, mcp)
    first = threading.Thread(target=agent.close)
    first.start()
    assert entered.wait(5)
    second = threading.Thread(target=agent.close)
    second.start()
    second.join(0.2)
    assert second.is_alive()  # waiting on the first close
    release.set()
    first.join(5)
    second.join(5)
    assert mcp.disconnects == 1


def test_cancelled_aclose_still_closes_behind_a_busy_default_executor(tmp_path):
    """``asyncio.to_thread`` cancelled while its work is still queued never
    runs it; ``aclose()`` must not depend on the default executor, so a
    cancel that lands before any worker is free still closes the agent."""
    import concurrent.futures

    agent = _bare(tmp_path)
    mcp = _FakeMcp()
    _give_built_mcp(agent, mcp)
    closed = threading.Event()
    real = agent.close

    def _close() -> None:
        real()
        closed.set()

    agent.close = _close  # type: ignore[method-assign]
    release = threading.Event()

    async def main() -> None:
        loop = asyncio.get_running_loop()
        loop.set_default_executor(concurrent.futures.ThreadPoolExecutor(1))
        blocker = loop.run_in_executor(None, release.wait, 5)
        task = asyncio.create_task(agent.aclose())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        await blocker

    asyncio.run(main())
    assert closed.wait(5)
    assert mcp.disconnects == 1


def test_close_failure_after_cancelled_aclose_is_logged(tmp_path, caplog):
    """Once the awaiter is gone nobody reads ``close()``'s exception, so
    ``aclose()`` logs it as a warning."""
    agent = _bare(tmp_path)
    started = threading.Event()
    release = threading.Event()

    def _close() -> None:
        started.set()
        release.wait(5)
        raise RuntimeError("teardown broke")

    agent.close = _close  # type: ignore[method-assign]
    caplog.set_level(logging.WARNING, logger="agentao.agent")

    async def main() -> None:
        task = asyncio.create_task(agent.aclose())
        while not started.is_set():
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())
    release.set()
    for _ in range(100):
        if any("close() failed" in r.getMessage() for r in caplog.records):
            break
        threading.Event().wait(0.02)
    failures = [r for r in caplog.records if "close() failed" in r.getMessage()]
    assert len(failures) == 1
    assert failures[0].exc_info[1].args == ("teardown broke",)


def test_aclose_closes_inline_when_no_thread_can_start(tmp_path, monkeypatch):
    """At interpreter shutdown (or the thread limit) ``Thread.start`` raises;
    ``aclose()`` then closes on the calling thread rather than leaving the
    agent open."""
    agent = _bare(tmp_path)
    spy = _CloseSpy(agent)

    real_start = threading.Thread.start

    def _refuse(self: threading.Thread) -> None:
        # Only the close thread is refused; any other thread starts as usual.
        if self.name == "agentao-aclose":
            raise RuntimeError("can't create new thread at interpreter shutdown")
        real_start(self)

    monkeypatch.setattr(threading.Thread, "start", _refuse)

    async def main() -> int:
        await agent.aclose()
        return threading.get_ident()

    loop_thread = asyncio.run(main())
    assert spy.threads == [loop_thread]


def test_close_reentered_on_the_same_thread_returns_at_once(tmp_path):
    """A ``close()`` reached again on the closing thread (a signal handler
    during the teardown) must not run the teardown inside itself."""
    agent = _bare(tmp_path)

    class _ReenteringMcp(_FakeMcp):
        def disconnect_all(self) -> None:
            super().disconnect_all()
            agent.close()  # what a SIGTERM handler would do mid-teardown

    mcp = _ReenteringMcp()
    _give_built_mcp(agent, mcp)
    agent.close()
    assert mcp.disconnects == 1
    assert agent.mcp_manager is None


def test_aclose_thread_is_not_a_daemon_under_a_daemon_loop(tmp_path):
    """A new thread inherits its creator's daemon flag; the close thread is
    non-daemon even when the host's loop runs on a daemon thread, so
    interpreter exit waits for the teardown."""
    agent = _bare(tmp_path)
    seen: list[bool] = []
    real_start = threading.Thread.start

    def _record(self: threading.Thread) -> None:
        if self.name == "agentao-aclose":
            seen.append(self.daemon)
        real_start(self)

    import unittest.mock as mock

    with mock.patch.object(threading.Thread, "start", _record):
        loop_thread = threading.Thread(
            target=lambda: asyncio.run(agent.aclose()), daemon=True
        )
        loop_thread.start()
        loop_thread.join(5)
    assert seen == [False]
