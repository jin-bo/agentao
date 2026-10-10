"""``Agentao.astream()`` — one turn's text deltas, then its ``TurnOutcome``.

The fakes replace only ``agent.llm.chat_stream``, so every delta goes through
the real ``run_llm_call`` → ``LLM_TEXT`` emit → transport subscription path,
and the outcome through the real ``run_turn``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

import pytest

import agentao
from agentao import Agentao
from agentao.cancellation import CancellationToken
from agentao.host import TextDelta, TurnOutcome
from agentao.replay.adapter import ReplayAdapter
from agentao.runtime.turn import TurnInProgressError
from agentao.tools.base import Tool

# Agentao here writes to the process cwd: see ``isolated_cwd`` in conftest.py.
pytestmark = pytest.mark.usefixtures("isolated_cwd")

REPO_ROOT = Path(__file__).resolve().parents[1]


def _make_agent() -> Agentao:
    return Agentao(
        api_key="test-key",
        base_url="https://example.test/v1",
        model="test-model",
        working_directory=Path.cwd(),
    )


def _response(content: str, tool_calls: Optional[list] = None) -> Any:
    message = SimpleNamespace(content=content, tool_calls=tool_calls, reasoning_content=None)
    finish = "tool_calls" if tool_calls else "stop"
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish)],
        usage=None,
        model="test-model",
    )


def _tool_call(call_id: str, name: str, args: Dict[str, Any]) -> Any:
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )


Script = Callable[[Callable[[str], None], Optional[CancellationToken]], Any]


def _script(agent: Agentao, *calls: Script) -> List[int]:
    """Make ``agent.llm.chat_stream`` run ``calls`` in order, one per LLM call."""
    seen: List[int] = []

    def chat_stream(messages, tools=None, max_tokens=None, on_text_chunk=None,
                    cancellation_token=None, **_kw):
        seen.append(len(seen))
        return calls[len(seen) - 1](on_text_chunk, cancellation_token)

    agent.llm.chat_stream = chat_stream  # type: ignore[method-assign]
    return seen


def _say(*chunks: str, tool_calls: Optional[list] = None) -> Script:
    def call(emit: Callable[[str], None], _token: Optional[CancellationToken]) -> Any:
        for c in chunks:
            emit(c)
        return _response("".join(chunks), tool_calls)
    return call


async def _collect(stream: Any) -> List[Any]:
    async with contextlib.aclosing(stream) as s:
        return [item async for item in s]


def _listener_count(agent: Agentao) -> int:
    return len(agent.transport._broadcast._listeners)


class _EchoTool(Tool):
    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echo the input."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}

    @property
    def is_read_only(self) -> bool:
        return True

    def execute(self, text: str) -> str:
        return text


# -- exports -------------------------------------------------------------------


def test_turn_outcome_is_one_class_on_every_path() -> None:
    from agentao.outcome import TurnOutcome as from_leaf
    from agentao.runtime.outcome import TurnOutcome as from_runtime

    assert TurnOutcome is agentao.TurnOutcome is from_leaf is from_runtime


def test_top_level_turn_outcome_does_not_load_the_runtime() -> None:
    probe = (
        "import sys, json; from agentao import TurnOutcome; "
        "from agentao.host import TextDelta; "
        "print(json.dumps(sorted(m for m in sys.modules if m.startswith('agentao'))))"
    )
    proc = subprocess.run([sys.executable, "-c", probe], cwd=REPO_ROOT,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    loaded = json.loads(proc.stdout)
    assert not [m for m in loaded if m.startswith(("agentao.runtime", "agentao.llm", "agentao.agent"))]


# -- the stream ------------------------------------------------------------------


def test_deltas_then_the_turns_outcome() -> None:
    agent = _make_agent()
    _script(agent, _say("Hel", "lo"))
    before = _listener_count(agent)

    items = asyncio.run(_collect(agent.astream("hi")))

    assert items[:-1] == [TextDelta("Hel"), TextDelta("lo")]
    outcome = items[-1]
    assert isinstance(outcome, TurnOutcome)
    assert outcome is agent.last_turn
    assert outcome.text == "Hello" and outcome.is_answer
    assert _listener_count(agent) == before


def test_narration_before_a_tool_call_streams_but_is_not_the_answer() -> None:
    agent = _make_agent()
    agent.add_tool(_EchoTool())
    _script(
        agent,
        _say("Let me check. ", tool_calls=[_tool_call("c1", "echo", {"text": "x"})]),
        _say("Done."),
    )

    items = asyncio.run(_collect(agent.astream("hi")))

    deltas = "".join(i.text for i in items if isinstance(i, TextDelta))
    outcome = items[-1]
    assert deltas == "Let me check. Done."
    assert outcome.text == "Done."
    assert outcome.tool_count == 1


def test_a_transport_without_subscribe_is_refused_before_the_turn() -> None:
    class Bare:
        def emit(self, event: Any) -> None:
            pass

    agent = _make_agent()
    seen = _script(agent, _say("x"))
    agent.transport = Bare()
    with pytest.raises(TypeError, match="Bare"):
        agent.astream("hi")
    assert seen == []


def test_a_replay_adapter_over_a_subscribe_less_transport_is_refused() -> None:
    class Bare:
        def emit(self, event: Any) -> None:
            pass

    agent = _make_agent()
    agent.transport = ReplayAdapter(Bare(), recorder=SimpleNamespace())
    with pytest.raises(TypeError, match="Bare"):
        agent.astream("hi")


def test_a_replay_adapter_over_a_subscribing_transport_streams() -> None:
    agent = _make_agent()
    inner = agent.transport
    _script(agent, _say("a", "b"))
    agent.transport = ReplayAdapter(inner, recorder=SimpleNamespace(record=lambda *a, **k: None))

    items = asyncio.run(_collect(agent.astream("hi")))

    assert [i.text for i in items[:-1]] == ["a", "b"]


def test_a_refused_stream_sees_nothing_of_the_running_turn() -> None:
    """Turn A emits while stream B is subscribed and about to be refused.

    B's ``arun`` is held until A has emitted ``LEAK`` with B's listener
    attached, so only the token-identity filter keeps it out of B.
    """
    agent = _make_agent()
    go = threading.Event()
    leaked_out = threading.Event()
    release = threading.Event()

    def held(emit: Callable[[str], None], _t: Optional[CancellationToken]) -> Any:
        emit("A1")
        go.wait(5)
        emit("LEAK")
        leaked_out.set()
        release.wait(5)
        return _response("A1LEAK")

    _script(agent, held)
    real_arun = agent.arun
    calls = 0

    async def arun(*args: Any, **kwargs: Any) -> str:
        nonlocal calls
        calls += 1
        if calls == 2:  # stream B: subscribed by now, turn not yet asked for
            go.set()
            await asyncio.to_thread(leaked_out.wait, 5)
        return await real_arun(*args, **kwargs)

    agent.arun = arun  # type: ignore[method-assign]

    async def main() -> tuple:
        first: List[Any] = []

        async def run_first() -> None:
            async with contextlib.aclosing(agent.astream("one")) as s:
                async for item in s:
                    first.append(item)

        t = asyncio.create_task(run_first())
        while calls < 1 or not agent._turn_lock.locked():
            await asyncio.sleep(0.01)
        second: List[Any] = []
        error: Optional[BaseException] = None
        try:
            async with contextlib.aclosing(agent.astream("two")) as s:
                async for item in s:
                    second.append(item)
        except TurnInProgressError as e:
            error = e
        release.set()
        await t
        return first, second, error

    first, second, error = asyncio.run(asyncio.wait_for(main(), timeout=10))
    assert isinstance(error, TurnInProgressError)
    assert second == []
    assert [i.text for i in first[:-1]] == ["A1", "LEAK"]
    assert first[-1].text == "A1LEAK"


def test_the_outcome_is_this_turns_even_if_a_later_turn_ran() -> None:
    """``last_turn`` read after the turn could be a later turn's outcome."""
    agent = _make_agent()
    _script(agent, _say("a", "b"), _say("later"))

    async def main() -> tuple:
        async with contextlib.aclosing(agent.astream("one")) as s:
            items = [await s.__anext__(), await s.__anext__()]
            while agent._turn_lock.locked() or agent.last_turn is None:
                await asyncio.sleep(0.01)
            await asyncio.to_thread(agent.chat, "two")
            items.append(await s.__anext__())
        return items, agent.last_turn

    items, latest = asyncio.run(asyncio.wait_for(main(), timeout=10))
    assert latest.text == "later"
    assert items[-1].text == "ab"


def test_closing_early_cancels_the_turn_and_unsubscribes() -> None:
    """``aclosing`` + ``break`` with a producer blocked on a full queue."""
    agent = _make_agent()
    many = [f"c{i} " for i in range(500)]

    def flood(emit: Callable[[str], None], token: Optional[CancellationToken]) -> Any:
        assert token is not None
        for c in many:
            if token.is_cancelled:
                break
            emit(c)
        # Keep the fake turn open until the consumer closes it. Otherwise a
        # delayed consumer can see a normally completed turn before cancellation.
        while not token.is_cancelled:
            time.sleep(0.001)
        return _response("".join(many))

    _script(agent, flood)
    before = _listener_count(agent)

    async def main() -> List[Any]:
        got: List[Any] = []
        async with contextlib.aclosing(agent.astream("hi")) as s:
            async for item in s:
                got.append(item)
                # Let the producer fill the queue and block in a put.
                await asyncio.sleep(0.2)
                break
        return got

    t0 = time.monotonic()
    got = asyncio.run(asyncio.wait_for(main(), timeout=10))
    elapsed = time.monotonic() - t0

    assert got == [TextDelta("c0 ")]
    # Releasing the blocked write comes before waiting for the turn; the
    # other order waits out arun's whole cleanup budget (5s).
    assert elapsed < 3
    assert agent.last_turn is not None and agent.last_turn.status == "cancelled"
    assert _listener_count(agent) == before
    assert not agent._turn_lock.locked()


def test_closing_early_cancels_the_turn_before_releasing_its_writes(monkeypatch) -> None:
    """The order the test above depends on, recorded rather than timed (#473).

    Released first, a producer parked on a full queue ran on with an
    uncancelled token and could finish the turn as "ok". That took a loaded
    runner to show (9 in 2000 runs), so this records the two steps in the
    order they happen: the token's cancel, and a parked write's release.
    """
    order: List[str] = []
    lock = threading.Lock()

    class RecordingToken(CancellationToken):
        def cancel(self, reason: str = "user-cancel") -> None:
            with lock:
                if not self.is_cancelled:
                    order.append("cancel")
            super().cancel(reason)

    real_submit = asyncio.run_coroutine_threadsafe

    def submit(coro: Any, loop: asyncio.AbstractEventLoop) -> Any:
        fut = real_submit(coro, loop)
        real_cancel = fut.cancel

        def cancel() -> bool:
            with lock:
                order.append("release")
            return real_cancel()

        fut.cancel = cancel  # type: ignore[method-assign]
        return fut

    monkeypatch.setattr("agentao.runtime.astream.CancellationToken", RecordingToken)
    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", submit)

    agent = _make_agent()
    many = [f"c{i} " for i in range(500)]

    def flood(emit: Callable[[str], None], token: Optional[CancellationToken]) -> Any:
        for c in many:
            if token is not None and token.is_cancelled:
                break
            emit(c)
        return _response("".join(many))

    _script(agent, flood)

    async def main() -> None:
        async with contextlib.aclosing(agent.astream("hi")) as s:
            async for _item in s:
                await asyncio.sleep(0.2)  # the producer parks on the full queue
                break

    asyncio.run(asyncio.wait_for(main(), timeout=10))

    assert "release" in order
    assert order[0] == "cancel", order
    assert agent.last_turn is not None and agent.last_turn.status == "cancelled"
    # The reason ``arun``'s own cancel gives, which this one now pre-empts.
    assert agent.last_turn.error == "async-cancel"


def test_cancelling_the_consumer_task_cancels_the_turn() -> None:
    agent = _make_agent()
    started = threading.Event()

    def until_cancelled(emit: Callable[[str], None], token: Optional[CancellationToken]) -> Any:
        emit("x")
        started.set()
        assert token is not None
        while not token.is_cancelled:
            token._event.wait(0.05)
        return _response("x")

    _script(agent, until_cancelled)
    before = _listener_count(agent)

    async def main() -> None:
        async def consume() -> None:
            async with contextlib.aclosing(agent.astream("hi")) as s:
                async for _ in s:
                    pass

        t = asyncio.create_task(consume())
        await asyncio.to_thread(started.wait, 5)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t

    asyncio.run(asyncio.wait_for(main(), timeout=10))
    assert agent.last_turn.status == "cancelled"
    assert _listener_count(agent) == before


def test_a_caller_token_cancels_the_turn_and_is_unlinked_after() -> None:
    agent = _make_agent()
    caller = CancellationToken()

    def cancel_midway(emit: Callable[[str], None], token: Optional[CancellationToken]) -> Any:
        emit("x")
        caller.cancel("host-stop")
        return _response("x")

    _script(agent, cancel_midway)

    items = asyncio.run(_collect(agent.astream("hi", cancellation_token=caller)))

    outcome = items[-1]
    assert outcome.status == "cancelled"
    assert outcome.error == "host-stop"


def test_a_caller_token_ends_the_turn_while_the_queue_is_full() -> None:
    # The producer parks on a full queue, not at a token check: without
    # dropping its write on cancel, the caller's token could not end a turn
    # whose consumer had stopped reading.
    agent = _make_agent()
    caller = CancellationToken()

    def many(emit: Callable[[str], None], token: Optional[CancellationToken]) -> Any:
        for i in range(500):
            if token is not None and token.is_cancelled:
                break
            emit(f"c{i} ")
        return _response("done")

    _script(agent, many)

    async def main() -> bool:
        async with contextlib.aclosing(agent.astream("hi", cancellation_token=caller)) as stream:
            await stream.__anext__()
            await asyncio.sleep(0.2)  # the queue fills; the producer waits
            caller.cancel("host-stop")
            deadline = time.monotonic() + 5
            while agent._turn_lock.locked() and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
            return agent._turn_lock.locked()

    assert asyncio.run(main()) is False
    assert agent.last_turn.status == "cancelled"


def test_a_caller_token_is_unlinked_when_the_stream_ends() -> None:
    agent = _make_agent()
    caller = CancellationToken()
    _script(agent, _say("x"))

    asyncio.run(_collect(agent.astream("hi", cancellation_token=caller)))

    assert caller._callbacks == []


def test_a_turn_exception_is_raised_after_its_text() -> None:
    agent = _make_agent()
    _script(agent, _say("partial"))

    real_inner = agent._chat_inner

    def boom(*args: Any, **kwargs: Any) -> str:
        real_inner(*args, **kwargs)
        raise ValueError("tool phase broke")

    agent._chat_inner = boom  # type: ignore[method-assign]

    async def main() -> List[Any]:
        got: List[Any] = []
        with pytest.raises(ValueError, match="tool phase broke"):
            async with contextlib.aclosing(agent.astream("hi")) as s:
                async for item in s:
                    got.append(item)
        return got

    got = asyncio.run(main())
    assert got == [TextDelta("partial")]


def test_closing_after_a_failed_turn_retrieves_its_exception() -> None:
    # The turn has already raised when the consumer closes the stream on a
    # delta still being drained: the ``arun`` task is done, so the close path
    # must still read its exception, or asyncio logs "Task exception was never
    # retrieved" when the task is collected.
    agent = _make_agent()
    _script(agent, _say("a", "b"))
    real_inner = agent._chat_inner

    def boom(*args: Any, **kwargs: Any) -> str:
        real_inner(*args, **kwargs)
        raise ValueError("tool phase broke")

    agent._chat_inner = boom  # type: ignore[method-assign]
    unretrieved: List[Dict[str, Any]] = []

    async def main() -> None:
        asyncio.get_running_loop().set_exception_handler(
            lambda _loop, ctx: unretrieved.append(ctx)
        )
        async with contextlib.aclosing(agent.astream("hi")) as s:
            async for _item in s:
                deadline = time.monotonic() + 5
                while agent._turn_lock.locked() and time.monotonic() < deadline:
                    await asyncio.sleep(0.01)
                await asyncio.sleep(0.05)  # let the arun task settle
                break
        import gc
        gc.collect()
        await asyncio.sleep(0)

    asyncio.run(main())
    assert not [c for c in unretrieved if "never retrieved" in str(c.get("message"))]


def test_max_iterations_and_images_reach_arun() -> None:
    agent = _make_agent()
    _script(agent, _say("x"))
    real_arun = agent.arun
    seen: Dict[str, Any] = {}

    async def arun(*args: Any, **kwargs: Any) -> str:
        seen.update(kwargs)
        return await real_arun(*args, **kwargs)

    agent.arun = arun  # type: ignore[method-assign]
    images = [{"data": "aGk=", "mimeType": "image/png"}]

    asyncio.run(_collect(agent.astream("hi", max_iterations=7, images=images)))

    assert seen["max_iterations"] == 7
    assert seen["images"] == images


def test_another_agent_on_a_shared_transport_does_not_leak_into_the_stream() -> None:
    # Two agents on one transport: the other agent's text arrives while this
    # agent's ``_current_token`` is the stream's, so the token alone matched.
    from agentao.transport import SdkTransport

    shared = SdkTransport()
    a = Agentao(api_key="k", base_url="https://example.test/v1", model="m",
                working_directory=Path.cwd(), transport=shared)
    b = Agentao(api_key="k", base_url="https://example.test/v1", model="m",
                working_directory=Path.cwd(), transport=shared)
    b_spoke = threading.Event()

    def a_call(emit: Callable[[str], None], _token: Optional[CancellationToken]) -> Any:
        emit("A1")
        assert b_spoke.wait(5)
        emit("A2")
        return _response("A1A2")

    def b_call(emit: Callable[[str], None], _token: Optional[CancellationToken]) -> Any:
        emit("B")
        b_spoke.set()
        return _response("B")

    _script(a, a_call)
    _script(b, b_call)

    async def main() -> List[Any]:
        collect = asyncio.create_task(_collect(a.astream("hi")))
        await asyncio.sleep(0.05)
        await b.arun("other")
        return await collect

    items = asyncio.run(main())

    assert items[:-1] == [TextDelta("A1"), TextDelta("A2")]
    assert items[-1].text == "A1A2"


_ON_LOOP_EMIT_SCRIPT = r"""
import asyncio, contextlib, json, sys, tempfile
from pathlib import Path
from types import SimpleNamespace

from agentao import Agentao
from agentao.tools.base import AsyncToolBase
from agentao.transport.events import AgentEvent, EventType

emits, repeats = int(sys.argv[1]), int(sys.argv[2])


def build():
    agent = Agentao(api_key="k", base_url="https://example.test/v1", model="m",
                    working_directory=Path(tempfile.mkdtemp()))

    class Emitter(AsyncToolBase):
        name = "emitter"
        description = "Emit text from the host loop."
        parameters = {"type": "object", "properties": {}}
        is_read_only = True

        async def async_execute(self, **kwargs):
            for i in range(emits):
                agent.transport.emit(AgentEvent(EventType.LLM_TEXT, {"chunk": f"L{i}"}))
            return "ok"

    agent.add_tool(Emitter())
    calls = []

    def chat_stream(messages, tools=None, max_tokens=None, on_text_chunk=None,
                    cancellation_token=None, **kw):
        calls.append(1)
        if len(calls) == 1:
            call = SimpleNamespace(id="c1", type="function",
                                   function=SimpleNamespace(name="emitter", arguments="{}"))
            msg = SimpleNamespace(content="", tool_calls=[call], reasoning_content=None)
            finish = "tool_calls"
        else:
            on_text_chunk("done")
            msg = SimpleNamespace(content="done", tool_calls=None, reasoning_content=None)
            finish = "stop"
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=finish)],
                               usage=None, model="m")

    agent.llm.chat_stream = chat_stream
    return agent


async def run_once():
    agent = build()
    try:
        async with contextlib.aclosing(agent.astream("hi")) as s:
            return [item.text async for item in s]
    finally:
        agent.close()


# Repeated because the ordering hazard is a race: one run can pass by luck.
print(json.dumps([asyncio.run(run_once()) for _ in range(repeats)]))
"""


@pytest.mark.parametrize("emits,repeats", [(1, 1), (200, 10)])
def test_text_emitted_on_the_host_loop_does_not_deadlock(emits: int, repeats: int) -> None:
    """A host async tool runs on the host loop with the turn's context.

    If it emits text through the transport, the listener runs on the loop's
    own thread; a blocking queue write there would wait for the loop it is
    blocking. A subprocess with a timeout, because a deadlocked turn worker
    would otherwise keep this test process from ever exiting. 200 emits is
    more scheduled writes than the queue holds: the final drain has to take
    them one at a time, and the turn's own "done" must not overtake them.
    """
    try:
        proc = subprocess.run([sys.executable, "-c", _ON_LOOP_EMIT_SCRIPT,
                               str(emits), str(repeats)],
                              cwd=Path.cwd(),
                              capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        pytest.fail("astream deadlocked on a text emit from the host loop")
    assert proc.returncode == 0, proc.stderr[-2000:]
    runs = json.loads(proc.stdout.strip().splitlines()[-1])
    expected = [f"L{i}" for i in range(emits)] + ["done", "done"]
    assert runs == [expected] * repeats


def test_a_transport_inheriting_the_protocol_stub_subscribe_is_refused() -> None:
    """Subclassing the ``Transport`` Protocol inherits a ``subscribe`` that
    registers nothing and returns None."""
    from agentao.transport.base import Transport

    class Partial(Transport):
        def emit(self, event: Any) -> None:
            pass

    agent = _make_agent()
    agent.transport = Partial()
    with pytest.raises(TypeError, match="Partial"):
        agent.astream("hi")


def test_a_nested_turn_of_another_agent_sharing_the_token_does_not_leak() -> None:
    """A host passes the turn's token to a helper agent on the same transport."""
    outer = _make_agent()
    helper = _make_agent()
    helper.transport = outer.transport
    _script(helper, _say("NESTED"))

    def calls_helper(emit: Callable[[str], None], token: Optional[CancellationToken]) -> Any:
        emit("outer-1")
        helper.chat("help", cancellation_token=token)
        emit("outer-2")
        return _response("outer-1outer-2")

    _script(outer, calls_helper)

    items = asyncio.run(_collect(outer.astream("hi")))

    assert [i.text for i in items[:-1]] == ["outer-1", "outer-2"]
    assert items[-1].text == "outer-1outer-2"
