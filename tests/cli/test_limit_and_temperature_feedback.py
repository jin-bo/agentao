"""Two CLI answers that said something untrue after the second wire landed.

``/context`` showed its "Effective" line only when an overflow had been
*observed*, so a window narrowed by the Models API — which needs no overflow —
was invisible: the user saw the configured number while every budget ran on a
smaller one. ``/temperature`` answered "sending 0.7" on the
``anthropic-messages`` wire, which never sends it.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from agentao import Agentao
from agentao.cli.commands import context as context_cmd
from agentao.cli.commands import provider as provider_cmd
from tests.support.anthropic_wire import (
    Wire,
    attach,
    message_end,
    message_start,
    stream_of,
    text_block,
)

# Agentao here writes to the process cwd: see ``isolated_cwd`` in conftest.py.
pytestmark = pytest.mark.usefixtures("isolated_cwd")


def _agent(**kwargs) -> Agentao:
    kwargs.setdefault("api_format", "anthropic-messages")
    return Agentao(
        api_key="test-key", base_url="https://api.example.test", model="claude-test",
        working_directory=Path.cwd(), **kwargs,
    )


@pytest.fixture
def printed(monkeypatch):
    lines = []
    sink = lambda *a, **k: lines.append(str(a[0]) if a else "")  # noqa: E731
    monkeypatch.setattr(context_cmd.console, "print", sink)
    monkeypatch.setattr(provider_cmd.console, "print", sink)
    return lines


def _effective(lines):
    return [line for line in lines if "Effective:" in line]


# -- /context ---------------------------------------------------------------


def test_context_names_a_window_narrowed_by_the_models_api_alone(printed):
    agent = _agent(max_context_tokens=200_000)
    try:
        agent.llm.model_input_limit = 150_000
        assert agent.context_manager.observed_limit is None
        context_cmd.handle_context_command(SimpleNamespace(agent=agent), "")
    finally:
        agent.close()
    (line,) = _effective(printed)
    assert "150,000" in line and "Models API reports 150,000" in line
    assert "yellow" in line  # narrower than configured: the mismatch is the news


def test_context_says_so_when_the_reported_window_is_not_the_binding_one(printed):
    agent = _agent(max_context_tokens=100_000)
    try:
        agent.llm.model_input_limit = 150_000
        context_cmd.handle_context_command(SimpleNamespace(agent=agent), "")
    finally:
        agent.close()
    (line,) = _effective(printed)
    assert "100,000" in line and "at or above configured" in line


def test_context_names_both_inputs_when_both_have_spoken(printed):
    agent = _agent(max_context_tokens=200_000)
    try:
        agent.llm.model_input_limit = 150_000
        cm = agent.context_manager
        cm._observed_limit, cm._observed_limit_provenance = 120_000, "test-pattern"
        context_cmd.handle_context_command(SimpleNamespace(agent=agent), "")
    finally:
        agent.close()
    (line,) = _effective(printed)
    assert "[yellow]120,000" in line
    assert "provider asserted 120,000 — test-pattern" in line
    assert "Models API reports 150,000" in line


def test_context_has_no_effective_line_when_nothing_narrowed(printed):
    agent = _agent(max_context_tokens=200_000)
    try:
        context_cmd.handle_context_command(SimpleNamespace(agent=agent), "")
    finally:
        agent.close()
    assert _effective(printed) == []


# -- /temperature -----------------------------------------------------------


def test_temperature_on_the_anthropic_wire_says_it_is_not_sent(printed):
    agent = _agent()
    cli = SimpleNamespace(agent=agent)
    try:
        provider_cmd.handle_temperature_command(cli, "0.3")
        provider_cmd.handle_temperature_command(cli, "on")
        provider_cmd.handle_temperature_command(cli, "")
        # Stored all the same: a /provider switch back to Chat Completions
        # sends the client's value.
        assert agent.llm.temperature == 0.3

        # And the claim is true — read off the socket, not off the adapter.
        wire = attach(agent.llm, Wire(stream_of(
            message_start(input_tokens=5), text_block(0, "ok"), message_end("end_turn"))))
        agent.chat("hi")
        assert "temperature" not in wire.requests[0]
    finally:
        agent.close()
    answers = [line for line in printed if "emperature" in line and "Usage" not in line]
    assert len(answers) == 3
    assert all("not sent" in line for line in answers)
    assert not any("sending" in line for line in answers)


def test_temperature_on_chat_completions_answers_as_before(printed):
    agent = _agent(api_format="openai-completions")
    cli = SimpleNamespace(agent=agent)
    try:
        provider_cmd.handle_temperature_command(cli, "0.3")
        provider_cmd.handle_temperature_command(cli, "on")
    finally:
        agent.close()
    assert any("Temperature changed from" in line and "to 0.3" in line for line in printed)
    assert any("Temperature on — sending 0.3" in line for line in printed)
    assert not any("not sent" in line for line in printed)
