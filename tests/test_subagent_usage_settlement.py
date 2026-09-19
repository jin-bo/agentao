"""A sub-agent's usage is settled before anyone is told the run is over.

0.5.1 added a sub-agent's requests to its parent's totals, from the ``finally``
that closes it. On the background path that ``finally`` runs *after* the record
is updated and the terminal ``SubagentLifecycleEvent`` is published, so both
readers were told "completed" while the parent's totals still left the run
out: a host reading them from its event handler, and anything reading them on
the completion notice. The event said nothing about usage either, so a host
could not attribute a cost to the sub-agent that incurred it.

Observers fire inline on the producer's thread, which is what lets these tests
read the totals *at* the event rather than some time after it.
"""

import threading
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentao import Agentao
from agentao.agents.bg_store import BackgroundTaskStore
from agentao.agents.tools._wrapper import AgentToolWrapper
from agentao.host import SubagentLifecycleEvent, SubagentUsage
from agentao.llm.client import LLMClient
from agentao.host.replay_projection import (
    host_event_to_replay_kind, host_event_to_replay_payload, replay_payload_to_host_event,
)
from tests.support.anthropic_wire import (
    Wire, attach, message_end, message_start, stream_of, text_block,
)

# Agentao here writes to the process cwd: see ``isolated_cwd`` in conftest.py.
pytestmark = pytest.mark.usefixtures("isolated_cwd")

USAGE = {"prompt_tokens": 4900, "completion_tokens": 40,
         "cache_read_tokens": 4000, "cache_creation_tokens": 0}


def _answer() -> bytes:
    return stream_of(
        message_start(input_tokens=900, cache_read_input_tokens=4000),
        text_block(0, "found it"),
        message_end("end_turn", output_tokens=40),
    )


@pytest.fixture
def child_wire(monkeypatch):
    real_build = AgentToolWrapper._build_sub_agent

    def build(self, suppress_output):
        sub_agent, setup = real_build(self, suppress_output)
        attach(sub_agent.llm, Wire(_answer()))
        return sub_agent, setup

    monkeypatch.setattr(AgentToolWrapper, "_build_sub_agent", build)


@pytest.fixture
def parent(child_wire):
    store = BackgroundTaskStore(persistence_dir=None)
    agent = Agentao(
        api_key="test-key", base_url="https://api.example.test", model="claude-test",
        api_format="anthropic-messages", working_directory=Path.cwd(),
        enable_builtin_agents=True, bg_store=store,
    )
    # (event, the parent's prompt total as the handler saw it)
    agent.seen = []
    agent.add_host_event_observer(lambda event: agent.seen.append(
        (event, agent.llm.total_prompt_tokens)
    ) if isinstance(event, SubagentLifecycleEvent) else None)
    try:
        yield agent
    finally:
        agent.close()


def _run(parent, *, background: bool):
    parent.tools.tools["agent_generalist"].execute(task="look", run_in_background=background)
    deadline = time.monotonic() + 10
    while len(parent.seen) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    (spawned, _), (terminal, total_at_terminal) = parent.seen
    assert spawned.phase == "spawned"
    return spawned, terminal, total_at_terminal


@pytest.mark.parametrize("background", [False, True], ids=["foreground", "background"])
def test_the_parents_totals_include_the_run_when_the_terminal_event_fires(parent, background):
    _, terminal, total_at_terminal = _run(parent, background=background)
    assert terminal.phase == "completed"
    assert total_at_terminal == 4900


@pytest.mark.parametrize("background", [False, True], ids=["foreground", "background"])
def test_the_terminal_event_says_what_the_sub_agent_cost(parent, background):
    spawned, terminal, _ = _run(parent, background=background)
    assert spawned.usage is None  # nothing has been spent yet
    assert terminal.usage == SubagentUsage(**USAGE)


def test_it_is_added_once_though_four_paths_can_settle_it(parent):
    _run(parent, background=True)
    # The thread's ``finally`` settles again after the event; give it time to.
    time.sleep(0.2)
    assert parent.llm.total_prompt_tokens == 4900
    assert parent.llm.total_cache_read_tokens == 4000


def test_the_background_record_carries_the_reported_usage(parent):
    """``tokens`` on the record is a local estimate of the final history's
    size — not what the requests cost."""
    _run(parent, background=True)
    (record,) = parent.bg_store.list()
    assert record["usage"] == USAGE
    assert record["tokens"] != USAGE["prompt_tokens"]


def test_the_record_is_not_updated_before_the_totals_are(parent, monkeypatch):
    """``update`` queues the completion notice, so it is a publish too."""
    real_update, seen = parent.bg_store.update, []

    def update(agent_id, **kwargs):
        seen.append(parent.llm.total_prompt_tokens)
        return real_update(agent_id, **kwargs)

    monkeypatch.setattr(parent.bg_store, "update", update)
    _run(parent, background=True)
    assert seen == [4900]


def test_a_background_run_that_raised_reports_the_requests_it_made(parent, monkeypatch):
    real_drive = AgentToolWrapper._drive_sub_agent

    def drive_then_raise(self, sub_agent, **kwargs):
        real_drive(self, sub_agent, **kwargs)
        raise RuntimeError("fell over after its request")

    monkeypatch.setattr(AgentToolWrapper, "_drive_sub_agent", drive_then_raise)
    _, terminal, total_at_terminal = _run(parent, background=True)
    assert (terminal.phase, terminal.error_type) == ("failed", "RuntimeError")
    assert terminal.usage == SubagentUsage(**USAGE)
    assert total_at_terminal == 4900
    (record,) = parent.bg_store.list()
    assert record["usage"] == USAGE


def test_a_count_that_is_not_a_count_cannot_take_the_terminal_event_with_it(parent, monkeypatch):
    """The usage dict becomes a pydantic model inside the emitter, whose
    caller swallows every exception: a refused value would silently drop the
    event and leave ``spawned`` without its pair."""
    real_drive = AgentToolWrapper._drive_sub_agent

    def drive(self, sub_agent, **kwargs):
        out = real_drive(self, sub_agent, **kwargs)
        sub_agent.llm.total_completion_tokens = "forty"
        return out

    monkeypatch.setattr(AgentToolWrapper, "_drive_sub_agent", drive)
    _, terminal, _ = _run(parent, background=True)
    assert terminal.phase == "completed"
    assert terminal.usage == SubagentUsage(**{**USAGE, "completion_tokens": 0})


def test_usage_survives_the_replay_round_trip(parent):
    _, terminal, _ = _run(parent, background=False)
    payload = host_event_to_replay_payload(terminal)
    assert payload["usage"] == USAGE
    kind = host_event_to_replay_kind(terminal)
    assert replay_payload_to_host_event(kind, payload) == terminal


@pytest.mark.parametrize("version", ["1.2", "1.3"])
def test_a_recorded_terminal_event_validates_against_the_whole_replay_schema(parent, version):
    """Against the *document*, not the payload subschema lifted out of it: a
    ``#/$defs/…`` ref resolves from the document root, so a ``$defs`` left
    under ``payload`` validates in isolation and points at nothing in place."""
    jsonschema = pytest.importorskip("jsonschema", reason="jsonschema not installed")
    from agentao.replay.schema import build_event_schema

    spawned, terminal, _ = _run(parent, background=False)
    for event in (spawned, terminal):  # ``usage: null`` and a filled one
        jsonschema.validate(
            instance={
                "event_id": "e", "session_id": "s", "instance_id": "i", "seq": 1,
                "ts": "2026-09-19T00:00:00.000Z",
                "kind": host_event_to_replay_kind(event),
                "payload": host_event_to_replay_payload(event),
            },
            schema=build_event_schema(version),
        )


# -- the read itself -----------------------------------------------------------


def test_a_negative_count_is_not_a_usage():
    """The producer never writes one; the *contract* should not accept one
    either, or a host validating against the schema learns nothing from it."""
    with pytest.raises(ValidationError):
        SubagentUsage(prompt_tokens=-5)


def test_the_four_totals_are_read_under_the_lock_the_adds_take():
    """Read one attribute at a time, an ``add_usage`` on another thread lands
    between two reads. Holding the lock here stands in for that add being
    half-way: the snapshot must wait for it rather than read around it."""
    llm = LLMClient(api_key="k", base_url="https://api.example.test", model="m")
    llm.add_usage(10, 1)
    got = []
    with llm._usage_lock:
        reader = threading.Thread(target=lambda: got.append(llm.usage_snapshot()))
        reader.start()
        reader.join(0.2)
        assert reader.is_alive() and not got
        # What an in-flight ``add_usage`` would have done by the time it lets go.
        llm.total_prompt_tokens += 5
        llm.total_completion_tokens += 2
    reader.join(5)
    assert got == [{"prompt_tokens": 15, "completion_tokens": 3,
                    "cache_read_tokens": 0, "cache_creation_tokens": 0}]


def test_the_settle_reads_through_that_snapshot(parent, monkeypatch):
    calls = []
    real = LLMClient.usage_snapshot

    def snapshot(self):
        calls.append(self)
        return real(self)

    monkeypatch.setattr(LLMClient, "usage_snapshot", snapshot)
    _, terminal, _ = _run(parent, background=True)
    assert len(calls) == 1 and calls[0] is not parent.llm
    assert terminal.usage == SubagentUsage(**USAGE)


@pytest.mark.parametrize("answer", [None, "4900", 4900, ["prompt_tokens"]])
def test_a_snapshot_that_is_not_a_dict_falls_back_to_the_attributes(parent, monkeypatch, answer):
    """``llm_client=`` may be a host's object: that it *answers*
    ``usage_snapshot`` says nothing about what comes back."""
    monkeypatch.setattr(LLMClient, "usage_snapshot", lambda self: answer)
    _, terminal, total_at_terminal = _run(parent, background=True)
    assert terminal.usage == SubagentUsage(**USAGE)
    assert total_at_terminal == 4900
