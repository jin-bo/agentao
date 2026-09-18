"""Offline smoke: the example builds a working agent on the Anthropic wire."""

from __future__ import annotations

from src.wire import build_agent, main, usage_report


def test_a_turn_goes_out_as_a_messages_api_request(tmp_path, attach, answer):
    agent = build_agent(tmp_path, api_key="test-key", model="claude-test")
    socket = attach(agent, answer("hello there"))
    try:
        assert agent.chat("say hello") == "hello there"
    finally:
        agent.close()

    (body,) = socket.requests
    assert socket.urls == ["http://socket.test/v1/messages"]
    assert body["model"] == "claude-test"
    # The system prompt is a top-level field on this wire, not a message.
    assert body["system"] and all(m["role"] != "system" for m in body["messages"])
    assert body["messages"][0]["role"] == "user"
    # Never sent here, whatever the client's ``temperature`` is.
    assert "temperature" not in body


def test_effort_rides_output_config_not_reasoning_effort(tmp_path, attach, answer):
    agent = build_agent(tmp_path, api_key="test-key", model="claude-test", effort="high")
    socket = attach(agent, answer("ok"))
    try:
        agent.chat("think about it")
    finally:
        agent.close()
    (body,) = socket.requests
    assert body["output_config"] == {"effort": "high"}
    assert body["thinking"] == {"type": "adaptive"}
    assert "reasoning_effort" not in body


def test_the_usage_report_separates_what_was_cached(tmp_path, attach, answer):
    agent = build_agent(tmp_path, api_key="test-key", model="claude-test")
    attach(agent, answer("ok", input_tokens=900, cache_read_input_tokens=4000,
                         cache_creation_input_tokens=200))
    try:
        agent.chat("hi")
        report = usage_report(agent)
    finally:
        agent.close()
    # The whole input, and the parts of it billed at cache rates.
    assert report == {"prompt_tokens": 5100, "completion_tokens": 7,
                      "cache_read_tokens": 4000, "cache_creation_tokens": 200}


def test_a_host_logger_keeps_the_log_file_out_of_the_working_directory(tmp_path, attach, answer):
    """``main`` runs in a temporary directory; an open ``agentao.log`` inside
    one cannot be deleted on Windows."""
    import logging

    agent = build_agent(tmp_path, api_key="test-key", model="claude-test",
                        logger=logging.getLogger("anthropic_wire_example_test"))
    attach(agent, answer("ok"))
    try:
        agent.chat("hi")
        assert not (tmp_path / "agentao.log").exists()
    finally:
        agent.close()


def test_without_a_key_the_script_says_what_to_do(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert main([]) == 2
    assert "ANTHROPIC_API_KEY is not set" in capsys.readouterr().out
