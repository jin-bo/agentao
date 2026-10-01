"""``temperature`` is unset by default, and unset means not sent.

It used to default to 0.2, which every request carried: a reasoning model
answered the first one with a 400 the repair latch had to recognise, and a
gateway whose wording the detector missed ended the turn. Now the provider's
own default applies unless a value is set — and a set value, ``0.0``
included, is sent exactly as before.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from agentao import Agentao
from agentao.cli.commands import provider as provider_cmd

# Agentao here writes to the process cwd: see ``isolated_cwd`` in conftest.py.
pytestmark = pytest.mark.usefixtures("isolated_cwd")

MESSAGES = [{"role": "user", "content": "hi"}]


def _agent(**kwargs) -> Agentao:
    # Pinned: a stray ``*_API_FORMAT`` in the environment must not move the wire.
    kwargs.setdefault("api_format", "openai-completions")
    return Agentao(
        api_key="test-key", base_url="https://api.example.test/v1", model="gpt-test",
        working_directory=Path.cwd(), **kwargs,
    )


@pytest.fixture
def printed(monkeypatch):
    lines = []
    sink = lambda *a, **k: lines.append(str(a[0]) if a else "")  # noqa: E731
    monkeypatch.setattr(provider_cmd.console, "print", sink)
    return lines


@pytest.mark.parametrize("api_format", ["openai-completions", "openai-responses"])
def test_an_unset_temperature_is_not_sent(api_format):
    agent = _agent(api_format=api_format)
    try:
        assert agent.llm.temperature is None
        kwargs = agent.llm._build_request_kwargs(MESSAGES, None, 100, stream=False)
        assert "temperature" not in kwargs
    finally:
        agent.close()


@pytest.mark.parametrize("api_format", ["openai-completions", "openai-responses"])
@pytest.mark.parametrize("value", [0.0, 0.7])
def test_a_set_temperature_is_sent_zero_included(api_format, value):
    agent = _agent(api_format=api_format, temperature=value)
    try:
        kwargs = agent.llm._build_request_kwargs(MESSAGES, None, 100, stream=False)
        assert kwargs["temperature"] == value
    finally:
        agent.close()


def test_a_sub_agent_inherits_unset():
    """The live config carries ``None``, and ``Agentao`` treats a ``None``
    temperature as "not given" — so the sub-agent sends nothing either."""
    parent = _agent()
    try:
        assert parent._llm_config["temperature"] is None
        child = _agent(temperature=parent._llm_config["temperature"])
        try:
            assert child.llm.temperature is None
        finally:
            child.close()
    finally:
        parent.close()


def test_status_says_provider_default():
    agent = _agent()
    try:
        assert "Temperature: provider default (not sent)" in agent.get_conversation_summary()
        agent.llm.temperature = 0.3
        assert "Temperature: 0.3" in agent.get_conversation_summary()
    finally:
        agent.close()


def test_the_temperature_command_on_an_unset_value(printed):
    agent = _agent()
    cli = SimpleNamespace(agent=agent)
    try:
        provider_cmd.handle_temperature_command(cli, "")
        assert any("provider default" in line and "not sent" in line for line in printed)

        # ``on`` has no value to send, and says so rather than "sending None".
        printed.clear()
        provider_cmd.handle_temperature_command(cli, "on")
        assert agent.llm.temperature is None
        assert any("No temperature is set" in line for line in printed)
        assert not any("sending" in line for line in printed)

        printed.clear()
        provider_cmd.handle_temperature_command(cli, "0.3")
        assert agent.llm.temperature == 0.3
        assert any("from provider default to 0.3" in line for line in printed)
        kwargs = agent.llm._build_request_kwargs(MESSAGES, None, 100, stream=False)
        assert kwargs["temperature"] == 0.3
    finally:
        agent.close()


@pytest.mark.parametrize("api_format", ["openai-completions", "openai-responses"])
def test_an_unset_request_is_not_repaired_for_a_field_it_never_sent(api_format):
    """A rejection naming ``temperature`` cannot be about a request without
    one: resending it unchanged would spend a call, and latching would
    silently override a value the user sets later."""
    agent = _agent(api_format=api_format)
    try:
        kwargs = agent.llm._build_request_kwargs(MESSAGES, None, 100, stream=False)
        err = "Unsupported value: 'temperature' does not support 0.2 with this model."
        assert agent.llm._adapter.repair_request(err, kwargs, stream=False) is False
        assert agent.llm.omit_temperature is False
    finally:
        agent.close()


def test_the_request_log_says_not_sent_rather_than_none():
    import logging

    agent = _agent()
    records = []
    handler = logging.Handler()
    handler.emit = records.append
    agent.llm.logger.addHandler(handler)
    try:
        kwargs = agent.llm._build_request_kwargs(MESSAGES, None, 100, stream=False)
        agent.llm._log_request("r1", kwargs)
        agent.llm.temperature = 0.0
        kwargs = agent.llm._build_request_kwargs(MESSAGES, None, 100, stream=False)
        agent.llm._log_request("r2", kwargs)
    finally:
        agent.llm.logger.removeHandler(handler)
        agent.close()
    lines = [r.getMessage() for r in records if r.getMessage().startswith("Temperature:")]
    assert lines == ["Temperature: (not sent)", "Temperature: 0.0"]
