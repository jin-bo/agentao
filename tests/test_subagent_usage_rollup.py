"""A sub-agent's token usage reaches its parent's session totals.

A sub-agent is a separate ``Agentao`` with its own ``LLMClient``, so its
requests were in nobody's total: the parent's counters — and ``agentao run``'s
``RunUsage``, which is a delta of them — reported a delegating session as
costing only the parent's own calls. The ``~N tokens`` in a sub-agent's footer
was never usage; it is a local estimate of the size of its final message list.

Both agents here are real and speak through the real ``anthropic`` SDK over a
scripted socket, so the numbers added are the ones the SDK parsed, not ones a
fake handed over.
"""

import threading
import time
from pathlib import Path

import pytest

from agentao import Agentao
from agentao.agents.bg_store import BackgroundTaskStore
from agentao.agents.tools._wrapper import AgentToolWrapper
from agentao.llm.client import LLMClient
from agentao.tooling.agent_tools import _add_usage
from tests.support.anthropic_wire import (
    Wire, attach, message_end, message_start, stream_of, text_block, tool_use_block,
)

# Agentao here writes to the process cwd: see ``isolated_cwd`` in conftest.py.
pytestmark = pytest.mark.usefixtures("isolated_cwd")


def _answer(text: str, *, input_tokens: int, output_tokens: int, **cache: int) -> bytes:
    return stream_of(
        message_start(input_tokens=input_tokens, **cache),
        text_block(0, text),
        message_end("end_turn", output_tokens=output_tokens),
    )


def _parent(**kwargs) -> Agentao:
    return Agentao(
        api_key="test-key", base_url="https://api.example.test", model="claude-test",
        api_format="anthropic-messages", working_directory=Path.cwd(),
        enable_builtin_agents=True, **kwargs,
    )


@pytest.fixture
def child_wires(monkeypatch):
    """Script the socket of every sub-agent built, in build order."""
    scripts, built = [], []
    real_build = AgentToolWrapper._build_sub_agent

    def build(self, suppress_output):
        sub_agent, setup = real_build(self, suppress_output)
        attach(sub_agent.llm, scripts.pop(0))
        built.append(sub_agent)
        return sub_agent, setup

    monkeypatch.setattr(AgentToolWrapper, "_build_sub_agent", build)
    return scripts, built


def test_a_foreground_sub_agents_usage_is_added_to_the_parents(child_wires):
    scripts, built = child_wires
    # Cached input counts: ``prompt_tokens`` is the whole prompt on this wire.
    scripts.append(Wire(_answer("found it", input_tokens=900, output_tokens=40,
                                cache_read_input_tokens=4000)))
    parent = _parent()
    try:
        parent.llm.add_usage(100, 10)  # what the parent's own calls had cost
        parent.tools.tools["agent_generalist"].execute(task="look")
        (child,) = built
        assert (child.llm.total_prompt_tokens, child.llm.total_completion_tokens) == (4900, 40)
        assert (parent.llm.total_prompt_tokens, parent.llm.total_completion_tokens) == (5000, 50)
    finally:
        parent.close()


def test_it_arrives_inside_the_parents_turn_so_a_run_delta_sees_it(child_wires):
    """``agentao run`` reports ``RunUsage`` as the counters' delta over one
    ``chat()``. A foreground sub-agent finishes inside that call."""
    scripts, _ = child_wires
    scripts.append(Wire(_answer("the child's answer", input_tokens=700, output_tokens=30)))
    parent = _parent()
    attach(parent.llm, Wire(
        stream_of(message_start(input_tokens=200),
                  tool_use_block(0, "toolu_1", "agent_generalist", '{"task": "look"}'),
                  message_end("tool_use", output_tokens=20)),
        _answer("done", input_tokens=300, output_tokens=15),
    ))
    try:
        before = (parent.llm.total_prompt_tokens, parent.llm.total_completion_tokens)
        parent.chat("delegate it")
        after = (parent.llm.total_prompt_tokens, parent.llm.total_completion_tokens)
    finally:
        parent.close()
    assert before == (0, 0)
    assert after == (200 + 300 + 700, 20 + 15 + 30)


def test_a_sub_agent_whose_run_raised_is_still_counted(child_wires, monkeypatch):
    """The requests were made and paid for whatever came of the run, so the
    roll-up rides the ``finally``, not the success path."""
    scripts, _ = child_wires
    scripts.append(Wire(_answer("partial", input_tokens=500, output_tokens=25)))
    real_chat = Agentao.chat
    parent = _parent()

    def chat_then_raise(self, *args, **kwargs):
        if self is parent:
            return real_chat(self, *args, **kwargs)
        real_chat(self, *args, **kwargs)
        raise RuntimeError("the sub-agent fell over after its request")

    monkeypatch.setattr(Agentao, "chat", chat_then_raise)
    try:
        with pytest.raises(RuntimeError):
            parent.tools.tools["agent_generalist"]._run_sync("look")
        assert (parent.llm.total_prompt_tokens, parent.llm.total_completion_tokens) == (500, 25)
    finally:
        parent.close()


def test_a_background_sub_agents_usage_arrives_from_its_own_thread(child_wires):
    scripts, _ = child_wires
    scripts.append(Wire(_answer("found it", input_tokens=600, output_tokens=35)))
    store = BackgroundTaskStore(persistence_dir=None)
    parent = _parent(bg_store=store)
    try:
        parent.tools.tools["agent_generalist"].execute(task="look", run_in_background=True)
        deadline = time.monotonic() + 10
        while parent.llm.total_prompt_tokens == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert (parent.llm.total_prompt_tokens, parent.llm.total_completion_tokens) == (600, 35)
    finally:
        parent.close()


def test_concurrent_adds_lose_nothing():
    """A background sub-agent adds while the parent's own turn does. ``+=`` on
    an attribute is a read and a write, so the adds go through one lock.

    Made able to fail: the switch interval is dropped so a thread is preempted
    between the read and the write often enough that unlocked ``+=`` loses
    updates on every run here (checked by removing the lock)."""
    import sys

    llm = LLMClient(api_key="k", base_url="https://api.example.test", model="m")
    threads, per_thread = 8, 20_000
    start = threading.Barrier(threads)

    def work():
        start.wait()
        for _ in range(per_thread):
            llm.add_usage(1, 1)

    interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        pool = [threading.Thread(target=work) for _ in range(threads)]
        for t in pool:
            t.start()
        for t in pool:
            t.join()
    finally:
        sys.setswitchinterval(interval)
    assert llm.total_prompt_tokens == llm.total_completion_tokens == threads * per_thread


@pytest.mark.parametrize("bad", [None, True, -5, 1.5, "12", object()])
def test_only_a_positive_int_is_counted(bad):
    """A mocked response answers any attribute, and a bool is an ``int``."""
    llm = LLMClient(api_key="k", base_url="https://api.example.test", model="m")
    llm.add_usage(bad, bad)
    llm.add_usage(7, bad)
    assert (llm.total_prompt_tokens, llm.total_completion_tokens) == (7, 0)


def test_a_host_client_without_totals_is_left_alone():
    """``llm_client=`` may be the host's own object. Nothing to add to is not
    an error, and the sub-agent's outcome must not depend on it."""
    class HostClient:
        pass

    class Parent:
        llm = HostClient()

    _add_usage(Parent(), 10, 5)  # does not raise
    assert not hasattr(Parent.llm, "total_prompt_tokens")
