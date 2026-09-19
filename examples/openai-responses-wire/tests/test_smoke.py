"""Offline smoke: the example builds a working agent on the Responses wire."""

from __future__ import annotations

from src.wire import build_agent, main, usage_report


def test_a_turn_goes_out_as_a_stateless_responses_request(tmp_path, attach, answer):
    agent = build_agent(tmp_path, api_key="test-key", model="gpt-test")
    socket = attach(agent, answer("hello there"))
    try:
        assert agent.chat("say hello") == "hello there"
    finally:
        agent.close()

    (body,) = socket.requests
    assert socket.urls == ["http://socket.test/v1/responses"]
    assert body["model"] == "gpt-test"
    # History is Agentao's; the provider is asked to keep none of it.
    assert body["store"] is False and "previous_response_id" not in body
    assert "messages" not in body and body["input"][-1]["role"] == "user"


def test_effort_rides_the_reasoning_object_not_reasoning_effort(tmp_path, attach, answer):
    agent = build_agent(tmp_path, api_key="test-key", model="gpt-test", effort="high")
    socket = attach(agent, answer("ok"))
    try:
        agent.chat("think about it")
    finally:
        agent.close()
    (body,) = socket.requests
    assert body["reasoning"] == {"effort": "high", "summary": "auto"}
    assert "reasoning_effort" not in body


def test_reasoning_is_carried_to_the_next_request(tmp_path, attach, answer):
    """Stateless means the provider keeps nothing: the encrypted item goes back."""
    agent = build_agent(tmp_path, api_key="test-key", model="gpt-test")
    socket = attach(agent, answer("4", reasoning="ENCRYPTED"), answer("still 4"))
    try:
        agent.chat("what is 2 + 2?")
        agent.chat("are you sure?")
    finally:
        agent.close()
    first, second = socket.requests
    assert first["include"] == ["reasoning.encrypted_content"]
    (item,) = [i for i in second["input"] if i.get("type") == "reasoning"]
    assert item["id"] == "rs_1" and item["encrypted_content"] == "ENCRYPTED"


def test_the_usage_report_separates_what_was_cached(tmp_path, attach, answer):
    agent = build_agent(tmp_path, api_key="test-key", model="gpt-test")
    attach(agent, answer("ok", input_tokens=5100, cached=4000, cache_write=200))
    try:
        agent.chat("hi")
        report = usage_report(agent)
    finally:
        agent.close()
    # ``input_tokens`` already includes both cache counts on this wire.
    assert report == {"prompt_tokens": 5100, "completion_tokens": 7,
                      "cache_read_tokens": 4000, "cache_creation_tokens": 200}


def test_a_host_logger_keeps_the_log_file_out_of_the_working_directory(tmp_path, attach, answer):
    """``main`` runs in a temporary directory; an open ``agentao.log`` inside
    one cannot be deleted on Windows."""
    import logging

    agent = build_agent(tmp_path, api_key="test-key", model="gpt-test",
                        logger=logging.getLogger("openai_responses_wire_example_test"))
    attach(agent, answer("ok"))
    try:
        agent.chat("hi")
        assert not (tmp_path / "agentao.log").exists()
    finally:
        agent.close()


def test_without_a_key_the_script_says_what_to_do(monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert main([]) == 2
    assert "OPENAI_API_KEY is not set" in capsys.readouterr().out
