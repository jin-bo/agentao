"""An empty/whitespace-only final assistant turn must not enter history verbatim.

Byte-level reasoning backends (Kimi, GLM, Qwen-via-Ollama) occasionally end a
turn with no text and no tool calls. Persisting ``{"role": "assistant",
"content": ""}`` leaves a contentless message that strict API proxies reject on
the next turn. The chat loop substitutes a neutral placeholder instead.

The placeholder keeps history valid but is not an answer, so TURN_END also
classifies the turn via ``incomplete_reason`` — that is what lets ``agentao
run`` exit non-zero instead of handing a pipeline the placeholder as a result.
"""

from pathlib import Path
from types import SimpleNamespace

from agentao import Agentao
from agentao.transport import EventType


def _fake_response(content, *, reasoning=None):
    message = SimpleNamespace(content=content, tool_calls=None, reasoning_content=reasoning)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    return SimpleNamespace(choices=[choice], usage=None, model="test-model")


def _make_agent() -> Agentao:
    return Agentao(
        api_key="test-key",
        base_url="https://example.test/v1",
        model="test-model",
        working_directory=Path.cwd(),
    )


def _capture_incomplete_reason(agent: Agentao) -> list:
    """Collect the ``incomplete_reason`` field of every TURN_END the agent emits."""
    seen: list = []

    def _observer(event) -> None:
        if getattr(event, "type", None) == EventType.TURN_END:
            seen.append((getattr(event, "data", None) or {}).get("incomplete_reason"))

    agent.transport.subscribe(_observer)
    return seen


def _assert_no_contentless_assistant_message(agent: Agentao) -> None:
    assistant_msgs = [m for m in agent.messages if m.get("role") == "assistant"]
    assert assistant_msgs, "expected an assistant message in history"
    for m in assistant_msgs:
        assert (m.get("content") or "").strip(), "contentless assistant message leaked into history"


def test_empty_final_content_substituted_with_placeholder():
    agent = _make_agent()
    agent._llm_call = lambda messages, tools, token: _fake_response("   \n  ")

    response = agent.chat("hi")

    assert response == "[No response]"
    _assert_no_contentless_assistant_message(agent)


def test_none_final_content_treated_as_empty():
    agent = _make_agent()
    agent._llm_call = lambda messages, tools, token: _fake_response(None)

    response = agent.chat("hi")

    assert response == "[No response]"
    _assert_no_contentless_assistant_message(agent)


def test_empty_final_content_with_reasoning_gets_distinct_marker():
    agent = _make_agent()
    agent._llm_call = lambda messages, tools, token: _fake_response("", reasoning="thought hard")

    response = agent.chat("hi")

    assert response == "[No text response]"
    _assert_no_contentless_assistant_message(agent)


def test_nonempty_final_content_passes_through():
    agent = _make_agent()
    agent._llm_call = lambda messages, tools, token: _fake_response("the answer is 42")

    response = agent.chat("hi")

    assert response == "the answer is 42"


# ---------------------------------------------------------------------------
# TURN_END classification — the signal ``agentao run`` exits non-zero on
# ---------------------------------------------------------------------------


def test_turn_end_reports_no_output():
    agent = _make_agent()
    agent._llm_call = lambda messages, tools, token: _fake_response("   \n  ")
    seen = _capture_incomplete_reason(agent)

    agent.chat("hi")

    assert seen == ["no_output"]


def test_turn_end_distinguishes_reasoning_only_turn():
    agent = _make_agent()
    agent._llm_call = lambda messages, tools, token: _fake_response(
        "", reasoning="thought hard"
    )
    seen = _capture_incomplete_reason(agent)

    agent.chat("hi")

    assert seen == ["reasoning_only"]


def test_turn_end_reports_none_for_real_answer():
    """The guard against false failures: a normal turn must never classify.

    ``agentao run`` maps a non-None ``incomplete_reason`` to a non-zero exit, so
    a turn that actually answered leaking a classification here would fail runs
    that succeeded.
    """
    agent = _make_agent()
    agent._llm_call = lambda messages, tools, token: _fake_response("the answer is 42")
    seen = _capture_incomplete_reason(agent)

    agent.chat("hi")

    assert seen == [None]


def test_answer_matching_placeholder_prefix_is_not_classified_empty():
    """Only the exact placeholder counts — an answer that merely starts with it
    is real model output and must not be reported as empty."""
    agent = _make_agent()
    agent._llm_call = lambda messages, tools, token: _fake_response(
        "[No response] was received from the upstream service, so I retried."
    )
    seen = _capture_incomplete_reason(agent)

    agent.chat("hi")

    assert seen == [None]


# ---------------------------------------------------------------------------
# Harness-halted turns — the other family of "no complete answer". These end
# with a canned harness string, which without a classification `agentao run`
# would serve to a pipeline at exit 0 as if the model had said it.
# ---------------------------------------------------------------------------


def _tool_call_response(finish_reason="stop"):
    tc = SimpleNamespace(
        id="call_0", type="function",
        function=SimpleNamespace(name="read_file", arguments='{"path": "x"}'),
    )
    message = SimpleNamespace(content="", tool_calls=[tc], reasoning_content=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
        usage=None, model="test-model",
    )


def test_repeated_length_truncation_abort_is_classified():
    """The model keeps getting cut off mid-tool-call; the harness gives up.

    Nothing ran, and the returned text is the harness's own explanation — not
    a model answer — so the turn must not read as a success.
    """
    agent = _make_agent()
    agent._llm_call = lambda messages, tools, token: _tool_call_response("length")
    seen = _capture_incomplete_reason(agent)

    final = agent.chat("hi")

    assert "cut off at the output-token limit" in final
    assert seen == ["length_truncated"]


def test_doom_loop_halt_is_classified(monkeypatch):
    """The doom-loop detector halts the turn; the canned halt notice is not an
    answer the model gave."""
    agent = _make_agent()
    agent._llm_call = lambda messages, tools, token: _tool_call_response()
    # Report the loop as tripped on the first execute, which is what the real
    # detector does once an identical call repeats past its threshold.
    monkeypatch.setattr(
        agent.tool_runner, "execute",
        lambda calls, cancellation_token=None: (True, []),
    )
    seen = _capture_incomplete_reason(agent)

    final = agent.chat("hi")

    assert "doom-loop" in final
    assert seen == ["doom_loop"]


def _text_response(content, *, finish_reason="stop", reasoning=None):
    message = SimpleNamespace(content=content, tool_calls=None, reasoning_content=reasoning)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
        usage=None, model="test-model",
    )


def test_truncated_final_text_is_classified_length_truncated():
    """A final answer with no tool calls, cut off mid-sentence at the output-
    token limit (``finish_reason=length``), is a partial answer — it must not
    exit 0 as a complete one. The partial text is kept in history."""
    agent = _make_agent()
    agent._llm_call = lambda messages, tools, token: _text_response(
        "Here is the summary of the migra", finish_reason="length",
    )
    seen = _capture_incomplete_reason(agent)

    final = agent.chat("hi")

    assert final == "Here is the summary of the migra"  # partial text preserved
    assert seen == ["length_truncated"]


def test_truncated_final_text_survives_vendor_finish_reason_variants():
    """Provider-neutral: gateways spell output-limit truncation differently."""
    for fr in ("max_tokens", "model_length", "MAX_TOKENS"):
        agent = _make_agent()
        agent._llm_call = lambda messages, tools, token, _fr=fr: _text_response(
            "partial", finish_reason=_fr,
        )
        seen = _capture_incomplete_reason(agent)

        agent.chat("hi")

        assert seen == ["length_truncated"], f"finish_reason={fr!r}"


def test_empty_reasoning_truncation_prefers_length_over_reasoning_only():
    """A reasoning model that exhausts the token budget mid-reasoning returns
    empty content + reasoning + ``finish_reason=length``. The actionable fact is
    "ran out of tokens" (retry smaller), so it classifies length_truncated, not
    reasoning_only — otherwise the diff's own retry guidance is inverted."""
    agent = _make_agent()
    agent._llm_call = lambda messages, tools, token: _text_response(
        "", finish_reason="length", reasoning="thinking so hard I ran out of room",
    )
    seen = _capture_incomplete_reason(agent)

    response = agent.chat("hi")

    assert response == "[No text response]"  # placeholder still substituted
    assert seen == ["length_truncated"]


def test_complete_final_text_is_not_classified():
    """The false-failure guard for the truncation path: a normal ``stop`` finish
    on real content must never classify, even for a long answer."""
    agent = _make_agent()
    agent._llm_call = lambda messages, tools, token: _text_response(
        "a complete answer", finish_reason="stop",
    )
    seen = _capture_incomplete_reason(agent)

    assert agent.chat("hi") == "a complete answer"
    assert seen == [None]


def test_llm_api_error_is_classified_llm_error():
    """The LLM call failing outright (provider 5xx / rate limit / auth after
    retries) is swallowed into a ``[LLM API error: …]`` notice and returned.

    That notice is the harness's, not a model answer, so the turn must classify
    ``llm_error`` — otherwise ``agentao run`` serves the notice at exit 0. This
    return path bypasses ``_resolve_stop_hook``, so it is the one most likely to
    be forgotten; the classification is committed at the return site itself.
    """
    agent = _make_agent()

    def _boom(messages, tools, token):
        raise RuntimeError("502 Bad Gateway")

    agent._llm_call = _boom
    seen = _capture_incomplete_reason(agent)

    final = agent.chat("hi")

    assert final.startswith("[LLM API error:")
    assert "502 Bad Gateway" in final
    assert seen == ["llm_error"]


# ---------------------------------------------------------------------------
# Stop-hook interaction — the classification must survive decoration, and must
# not survive the hook actually supplying an answer.
# ---------------------------------------------------------------------------


def _stub_stop(monkeypatch, *results):
    """Patch the runner's Stop dispatch to yield ``results`` in order (the last
    repeats), so a test can drive block / force-continue / decorate."""
    from agentao.runtime.chat_loop._runner import ChatLoopRunner

    calls = {"n": 0}

    def _dispatch(self, *, turn_end_reason, last_assistant_message):
        i = min(calls["n"], len(results) - 1)
        calls["n"] += 1
        return results[i]

    monkeypatch.setattr(ChatLoopRunner, "_dispatch_stop", _dispatch)


def test_additional_contexts_decoration_stays_classified_empty(monkeypatch):
    """A Stop hook that decorates the placeholder has not answered the turn.

    The hook appends a ``<stop-hook>`` block to "[No response]", so the
    returned text is no longer the bare placeholder — but the model still
    never answered, and the run must still report the turn empty.
    """
    from agentao.plugins.models import StopHookResult

    _stub_stop(monkeypatch, StopHookResult(
        matched_rule_count=1, additional_contexts=["audit-note"],
    ))
    agent = _make_agent()
    agent._llm_call = lambda messages, tools, token: _fake_response("")
    seen = _capture_incomplete_reason(agent)

    final = agent.chat("hi")

    assert "audit-note" in final, "expected the hook to decorate the answer"
    assert final.startswith("[No response]")
    assert seen == ["no_output"], "decoration must not mask an empty turn"


def test_blocking_stop_hook_clears_empty_classification(monkeypatch):
    """A blocking hook replaces the text the caller receives, so the turn is
    no longer answerless and must not be reported empty."""
    from agentao.plugins.models import StopHookResult

    _stub_stop(monkeypatch, StopHookResult(
        matched_rule_count=1, blocking_error="not so fast",
    ))
    agent = _make_agent()
    agent._llm_call = lambda messages, tools, token: _fake_response("")
    seen = _capture_incomplete_reason(agent)

    final = agent.chat("hi")

    assert "[Blocked by Stop hook]" in final
    assert seen == [None]


def test_blocking_stop_hook_does_not_mask_a_doom_loop_halt(monkeypatch):
    """A blocking hook substitutes its own text, but a doom-looped turn still
    never converged — hook text is not a model answer, so the halt
    classification must survive and keep ``agentao run`` at exit 1."""
    from agentao.plugins.models import StopHookResult

    _stub_stop(monkeypatch, StopHookResult(
        matched_rule_count=1, blocking_error="policy gate",
    ))
    agent = _make_agent()
    agent._llm_call = lambda messages, tools, token: _tool_call_response()
    monkeypatch.setattr(
        agent.tool_runner, "execute",
        lambda calls, cancellation_token=None: (True, []),
    )
    seen = _capture_incomplete_reason(agent)

    final = agent.chat("hi")

    assert "[Blocked by Stop hook]" in final
    assert seen == ["doom_loop"], "a blocking hook must not mask a harness halt"


def test_blocking_stop_hook_does_not_mask_length_truncation(monkeypatch):
    """The length-truncation twin of the doom-loop block case."""
    from agentao.plugins.models import StopHookResult

    _stub_stop(monkeypatch, StopHookResult(
        matched_rule_count=1, blocking_error="policy gate",
    ))
    agent = _make_agent()
    agent._llm_call = lambda messages, tools, token: _tool_call_response("length")
    seen = _capture_incomplete_reason(agent)

    final = agent.chat("hi")

    assert "[Blocked by Stop hook]" in final
    assert seen == ["length_truncated"]


def test_force_continue_to_a_real_answer_is_not_reported_empty(monkeypatch):
    """An empty iteration that the hook forces past must not stick to a turn
    that goes on to answer."""
    from agentao.plugins.models import StopHookResult

    _stub_stop(
        monkeypatch,
        StopHookResult(matched_rule_count=1, force_continue=True,
                       follow_up_message="try again"),
        StopHookResult(matched_rule_count=1),
    )
    replies = iter([_fake_response(""), _fake_response("the answer is 42")])
    agent = _make_agent()
    agent._llm_call = lambda messages, tools, token: next(replies)
    seen = _capture_incomplete_reason(agent)

    final = agent.chat("hi")

    assert final == "the answer is 42"
    assert seen == [None], "an earlier empty iteration must not stick"


def test_cancelled_stream_is_not_reported_as_an_empty_answer():
    """A token cancelled mid-stream makes the LLM return a normally-built
    empty message without raising, so the turn reaches the ordinary ending
    site with status "ok" — it must be reported as interrupted, not empty."""
    from agentao.cancellation import CancellationToken

    agent = _make_agent()
    token = CancellationToken()

    def _llm(messages, tools, cancellation_token):
        token.cancel("sigint")
        return _fake_response("")

    agent._llm_call = _llm
    seen = _capture_incomplete_reason(agent)

    agent.chat("hi", cancellation_token=token)

    assert seen == [None], "a cancelled turn must not be reported empty"


if __name__ == "__main__":
    # Half the tests here take a ``monkeypatch`` fixture and cannot run outside
    # pytest, so a hand-rolled call list would silently skip them and still
    # print "ok". Delegate to pytest so direct invocation runs the whole file.
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-q"]))
