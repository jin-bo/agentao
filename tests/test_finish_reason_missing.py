"""``TurnOutcome.finish_reason_missing`` — "the provider never said why it stopped".

A stream can end without the provider ever sending ``finish_reason``: a gateway
closing the SSE body after its own upstream timeout, or a partially-compatible
local server that never emits the field. agentao falls back to ``"stop"``, so
such a turn is otherwise indistinguishable from a clean completion — a truncated
fragment is reported as a finished answer, and ``is_answer`` says True.

This flag reports that and only that. It is deliberately **not** a member of
``INCOMPLETE_ANSWER_REASONS``: every value in that closed set becomes a CLI error
envelope, so joining it would make each turn a hard failure on every provider
that omits the field. It rides its own axis, like ``max_iterations``.

Streaming responses here come from the **real** producers —
``client.chat_stream`` and ``_StreamAccumulator.build()`` — so a regression that
stops propagating the flag through ``build()`` fails these tests instead of
being restated by them.

``_plain()`` is a ``SimpleNamespace``, matching the established fixture style in
this repo's runner tests. Be aware of what that does *not* prove: the
non-streaming half of the detector rests on a provider-omitted ``finish_reason``
surfacing as ``None`` on a real ``ChatCompletion``, which is a property of
openai-python's lenient ``construct_type`` rather than of the model signature.
If a future SDK surfaced a sentinel instead, these tests would stay green while
every Gemini-bypass turn went unflagged — the failure mode CLAUDE.md's MCP
section records. ``test_sdk_reports_none_for_an_omitted_finish_reason`` pins
that assumption against the installed SDK so the drift is caught at the source
rather than here.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agentao import Agentao
from agentao.cancellation import CancellationToken
from agentao.llm._stream_response import _StreamAccumulator


# ---------------------------------------------------------------------------
# Fixtures — chunk shapes as the OpenAI SDK yields them
# ---------------------------------------------------------------------------


def _chunk(content=None, finish=None):
    delta = SimpleNamespace(content=content, tool_calls=None, reasoning_content=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason=finish)],
        usage=None,
        model="test-model",
    )


def _make_client():
    with patch("agentao.llm.client.OpenAI") as openai_cls:
        openai_cls.return_value = MagicMock()
        from agentao.llm.client import LLMClient

        return LLMClient(
            api_key="test-key",
            base_url="https://example.test/v1",
            model="test-model",
        )


def _make_agent() -> Agentao:
    return Agentao(
        api_key="test-key",
        base_url="https://example.test/v1",
        model="test-model",
        working_directory=Path.cwd(),
    )


def _streamed(content, *, finish):
    """A real ``_StreamResponse``, built the way the streaming path builds it.

    ``finish=None`` models a stream that ended without the provider ever
    sending a finish_reason.
    """
    acc = _StreamAccumulator("test-model")
    acc.content_parts.append(content)
    if finish is not None:
        acc.finish_reason = finish
        acc.finish_reason_reported = True
    return acc.build()


def _plain(content, *, finish_reason, tool_calls=None):
    """A non-streaming ChatCompletion-shaped response (no flag attribute)."""
    message = SimpleNamespace(
        content=content, tool_calls=tool_calls, reasoning_content=None
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
        usage=None,
        model="test-model",
    )


def _tool_call(call_id, name, arguments):
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _scripted(responses):
    """An ``_llm_call`` stub yielding ``responses`` in order."""
    it = iter(responses)

    def _call(messages, tools, token):
        return next(it)

    return _call


# ---------------------------------------------------------------------------
# Producer: chat_stream must record whether the provider reported one
# ---------------------------------------------------------------------------


class TestStreamProducer:
    def test_reported_when_provider_sends_finish_reason(self):
        """Uses ``"length"``, NOT ``"stop"``.

        ``"stop"`` is the accumulator's own fallback, so asserting it here
        would be satisfied whether or not the chunk's value was ever read —
        deleting ``acc.finish_reason = choice.finish_reason`` would keep this
        green while silently bypassing ``_is_length_truncation`` in production.
        A value the fallback cannot produce makes both assertions load-bearing.
        """
        client = _make_client()
        client.client.chat.completions.create = MagicMock(
            return_value=iter([_chunk(content="hi"), _chunk(content=" there", finish="length")])
        )

        response = client.chat_stream(
            messages=[{"role": "user", "content": "hi"}], tools=None, max_tokens=64
        )

        assert response.finish_reason_reported is True
        assert response.choices[0].finish_reason == "length"

    def test_empty_string_finish_reason_counts_as_unreported(self):
        """A gateway sending ``""`` must not read as a provider-confirmed stop.

        The streaming recorder gates on truthiness, so the detector's
        non-streaming arm has to test falsiness too — otherwise the same ``""``
        answers differently depending on which transport the turn took.
        """
        client = _make_client()
        client.client.chat.completions.create = MagicMock(
            return_value=iter([_chunk(content="hi", finish="")])
        )

        response = client.chat_stream(
            messages=[{"role": "user", "content": "hi"}], tools=None, max_tokens=64
        )

        assert response.finish_reason_reported is False

    def test_not_reported_when_stream_just_ends(self):
        """The gateway-timeout shape: content, then the body simply closes."""
        client = _make_client()
        client.client.chat.completions.create = MagicMock(
            return_value=iter([_chunk(content="a partial answ")])
        )

        response = client.chat_stream(
            messages=[{"role": "user", "content": "hi"}], tools=None, max_tokens=64
        )

        assert response.finish_reason_reported is False
        # The wire value keeps its "stop" fallback on purpose: flipping it to
        # None would change LLM_CALL_COMPLETED payloads and replay renders for
        # every provider that omits the field. The fact rides the flag instead.
        assert response.choices[0].finish_reason == "stop"


# ---------------------------------------------------------------------------
# Turn level: the flag reaches TurnOutcome without changing the verdict
# ---------------------------------------------------------------------------


class TestTurnOutcomeFlag:
    def test_normal_turn_is_not_flagged(self):
        agent = _make_agent()
        agent._llm_call = lambda messages, tools, token: _streamed("42", finish="stop")

        agent.chat("what is 6*7?")

        assert agent.last_turn.finish_reason_missing is False
        assert agent.last_turn.is_answer is True

    def test_streamed_turn_without_finish_reason_is_flagged(self):
        agent = _make_agent()
        agent._llm_call = lambda messages, tools, token: _streamed("42", finish=None)

        agent.chat("what is 6*7?")

        assert agent.last_turn.finish_reason_missing is True

    def test_flag_does_not_make_the_turn_a_non_answer(self):
        """The whole point of choosing a marker over the strict default.

        A provider that never sends the field would otherwise fail every
        single turn.
        """
        agent = _make_agent()
        agent._llm_call = lambda messages, tools, token: _streamed("42", finish=None)

        agent.chat("what is 6*7?")

        assert agent.last_turn.is_answer is True
        assert agent.last_turn.incomplete_reason is None
        assert agent.last_turn.status == "ok"

    def test_non_streaming_none_finish_reason_is_flagged(self):
        """A real ChatCompletion has no flag attribute; ``None`` is the signal."""
        agent = _make_agent()
        agent._llm_call = lambda messages, tools, token: _plain("42", finish_reason=None)

        agent.chat("what is 6*7?")

        assert agent.last_turn.finish_reason_missing is True
        assert agent.last_turn.is_answer is True

    def test_non_streaming_empty_string_is_flagged(self):
        """The two producers must agree on a falsy-but-not-None value.

        The streaming recorder gates on truthiness, so a gateway sending ``""``
        leaves ``finish_reason_reported`` False there. If the detector's
        non-streaming arm tested ``is None``, the identical ``""`` would read
        as reported, and the flag would flip depending on whether the turn took
        the streaming or the Gemini/fallback bypass — a transport detail no
        host can see, making the failures look nondeterministic.
        """
        agent = _make_agent()
        agent._llm_call = lambda messages, tools, token: _plain("42", finish_reason="")

        agent.chat("what is 6*7?")

        assert agent.last_turn.finish_reason_missing is True

    def test_non_streaming_with_finish_reason_is_not_flagged(self):
        agent = _make_agent()
        agent._llm_call = lambda messages, tools, token: _plain("42", finish_reason="stop")

        agent.chat("what is 6*7?")

        assert agent.last_turn.finish_reason_missing is False

    def test_flag_resets_between_turns(self):
        agent = _make_agent()
        agent._llm_call = lambda messages, tools, token: _streamed("42", finish=None)
        agent.chat("first")
        assert agent.last_turn.finish_reason_missing is True

        agent._llm_call = lambda messages, tools, token: _streamed("43", finish="stop")
        agent.chat("second")
        assert agent.last_turn.finish_reason_missing is False

    def test_sticky_across_calls_within_one_turn(self, tmp_path):
        """Set by *any* call in the turn, not just the last one.

        An intermediate call that ends without a finish_reason may have had its
        tool-call arguments cut off with nothing to detect it — the length
        guard never fires, so those arguments get executed. Inspecting only the
        final call would miss exactly that, which is why the flag is sticky.

        Driven through the real loop: call 1 is a tool call with no reported
        finish_reason, call 2 is a clean final answer. The turn therefore *ends*
        on a well-behaved call.
        """
        target = tmp_path / "note.txt"
        target.write_text("hello\n")

        agent = _make_agent()
        agent._llm_call = _scripted([
            _plain(
                "let me look",
                finish_reason=None,  # unreported
                tool_calls=[_tool_call("call_1", "read_file", f'{{"file_path": "{target}"}}')],
            ),
            _plain("the file says hello", finish_reason="stop"),  # clean, and last
        ])

        agent.chat("what is in that file?")

        assert agent.last_turn.finish_reason_missing is True
        # Evidence the intermediate call really executed: a tool *result*
        # message carrying the file's content. ``tool_count`` would not do —
        # it counts calls the model made, not calls that ran, so it stays 1
        # even if the read is denied or the file never existed.
        tool_msgs = [m for m in agent.messages if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert "hello" in tool_msgs[0]["content"]
        # Still an answer — the flag reports, it does not classify.
        assert agent.last_turn.is_answer is True


    def test_errored_turn_keeps_the_flag(self):
        """Errors are NOT suppressed, unlike cancellation.

        A stream truncated mid tool-call arguments can get them repaired into
        something parseable-but-wrong, run the tool, and raise. Zeroing the
        flag there would tell a triager the provider confirmed a clean stop for
        the very turn it truncated.

        Targets `run_turn`'s gate directly — the flag is set, then the inner
        loop raises — rather than staging a whole tool-failure scenario. The
        gate is the line this test exists to pin.
        """
        agent = _make_agent()
        _orig_inner = agent._chat_inner

        def _inner(user_message, max_iterations, token, **kwargs):
            agent._turn_finish_reason_missing = True
            raise RuntimeError("tool blew up on repaired arguments")

        agent._chat_inner = _inner

        with pytest.raises(RuntimeError):
            agent.chat("hi")

        assert agent.last_turn.status == "error"
        assert agent.last_turn.finish_reason_missing is True

    def test_cancelled_turn_is_not_flagged(self):
        """Gated on ``status == "ok"``, same as ``incomplete_reason``.

        Cancellation breaks out of the chunk loop before any finish_reason can
        arrive, so an ungated flag would fire on every single cancellation and
        say nothing the status does not already say.
        """
        agent = _make_agent()
        token = CancellationToken()

        def _llm(messages, tools, cancellation_token):
            token.cancel("sigint")
            return _streamed("", finish=None)

        agent._llm_call = _llm

        agent.chat("hi", cancellation_token=token)

        assert agent.last_turn.status == "cancelled"
        assert agent.last_turn.finish_reason_missing is False


class TestSdkAssumption:
    def test_sdk_reports_none_for_an_omitted_finish_reason(self):
        """The non-streaming arm's premise, pinned against the installed SDK.

        `_plain()` fixtures cannot prove this — they *are* the assumption. If a
        future openai-python surfaces a sentinel instead of ``None`` for an
        omitted field, every Gemini-bypass turn would silently stop being
        flagged; this fails at the source instead.
        """
        from openai.types.chat import ChatCompletion

        parsed = ChatCompletion.construct(
            **{
                "id": "cmpl-1",
                "object": "chat.completion",
                "created": 0,
                "model": "test-model",
                # finish_reason deliberately absent, as a lenient gateway sends it
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}}],
            }
        )
        assert getattr(parsed.choices[0], "finish_reason", None) is None


# ---------------------------------------------------------------------------
# Coverage the observation must reach beyond TurnOutcome
# ---------------------------------------------------------------------------


class TestDownstreamCoverage:
    def test_compaction_summary_without_finish_reason_flags_the_turn(self):
        """The summarization call bypasses the chat loop's detector.

        It is also the one call whose output permanently replaces history, so a
        truncated summary that went unflagged would be inherited by every later
        turn.
        """
        agent = _make_agent()
        agent._llm_call = lambda messages, tools, token: _streamed("done", finish="stop")

        cm = agent.context_manager

        def _compress(msgs, is_auto=False):
            # What `_summarize_messages` does when its own provider call ends
            # without a finish_reason. Set *inside* the turn on purpose:
            # `run_turn` clears this attribute at turn start, so seeding it
            # beforehand would prove nothing (and would have hidden the reset).
            cm.last_summary_finish_reason_missing = True
            return msgs

        cm.compress_messages = _compress
        cm.needs_compression = lambda *a, **k: True
        cm.needs_microcompaction = lambda *a, **k: False

        agent.chat("summarize and answer")

        # The turn's own LLM call reported "stop"; only the summarizer did not.
        assert agent.last_turn.finish_reason_missing is True

    def test_stale_summary_flag_does_not_leak_into_a_later_turn(self):
        """`run_turn` clears the context manager's flag at turn start.

        The leak needs all three conditions, so the test has to stage all
        three: turn 1 summarizes badly, turn 2 *also* compacts (otherwise the
        fold-in never runs and any assertion passes vacuously), and turn 2's
        `compress_messages` returns without re-summarizing — the early-return
        path, where `_summarize_messages`' own per-call reset never fires.
        Without `run_turn`'s reset, turn 2 inherits turn 1's verdict.
        """
        agent = _make_agent()
        agent._llm_call = lambda messages, tools, token: _streamed("done", finish="stop")
        cm = agent.context_manager
        cm.needs_compression = lambda *a, **k: True
        cm.needs_microcompaction = lambda *a, **k: False

        def _compress_and_flag(msgs, is_auto=False):
            cm.last_summary_finish_reason_missing = True
            return msgs

        cm.compress_messages = _compress_and_flag
        agent.chat("turn one, summary came back truncated")
        assert agent.last_turn.finish_reason_missing is True

        # Turn 2 compacts again, but nothing re-summarizes this time.
        cm.compress_messages = lambda msgs, is_auto=False: msgs
        agent.chat("turn two, nothing new to summarize")

        assert agent.last_turn.finish_reason_missing is False

    def test_replay_turn_completed_records_the_flag(self):

        recorded = []
        adapter_mod = __import__(
            "agentao.replay.adapter", fromlist=["ReplayAdapter"]
        )
        adapter = adapter_mod.ReplayAdapter.__new__(adapter_mod.ReplayAdapter)
        adapter._turn_id = "turn-1"
        adapter._recorder = SimpleNamespace(
            record=lambda kind, turn_id, payload: recorded.append(payload)
        )

        adapter.end_turn(
            "a truncated answer",
            status="ok",
            error=None,
            tool_count=0,
            incomplete_reason=None,
            finish_reason_missing=True,
        )

        assert recorded[0]["finish_reason_missing"] is True

    def test_replay_omits_the_key_on_an_ordinary_turn(self):
        recorded = []
        adapter_mod = __import__(
            "agentao.replay.adapter", fromlist=["ReplayAdapter"]
        )
        adapter = adapter_mod.ReplayAdapter.__new__(adapter_mod.ReplayAdapter)
        adapter._turn_id = "turn-2"
        adapter._recorder = SimpleNamespace(
            record=lambda kind, turn_id, payload: recorded.append(payload)
        )

        adapter.end_turn("fine", status="ok", error=None, tool_count=0)

        assert "finish_reason_missing" not in recorded[0]


# ---------------------------------------------------------------------------
# The flag must also reach the TURN_END wire payload
# ---------------------------------------------------------------------------


def test_turn_end_event_carries_the_flag():
    """``TurnOutcome`` is documented as mirroring TURN_END field-for-field."""
    from agentao.transport import EventType

    seen = []
    agent = _make_agent()
    agent.transport.subscribe(
        lambda e: seen.append(e) if e.type == EventType.TURN_END else None
    )
    agent._llm_call = lambda messages, tools, token: _streamed("42", finish=None)

    agent.chat("what is 6*7?")

    assert len(seen) == 1
    assert seen[0].data["finish_reason_missing"] is True


# ---------------------------------------------------------------------------
# It must stay off the closed incomplete_reason vocabulary
# ---------------------------------------------------------------------------


def test_flagged_turn_still_exits_zero_from_agentao_run():
    """The exit-code half of the product decision, driven through the real
    classifier rather than asserted against a frozenset.

    A membership assertion (``"finish_reason_missing" not in
    INCOMPLETE_ANSWER_REASONS``) cannot fail for the risk it names: if
    ``_classify_outcome`` were later changed to consult the flag, `agentao run`
    would start exiting non-zero on every turn against a provider that never
    sends finish_reason — the hard failure this design rejected — and a
    frozenset check would stay green. So drive the classifier itself.
    """
    from agentao.cli.run import _classify_outcome
    from agentao.cancellation import CancellationToken

    transport = SimpleNamespace(rejection=None, max_iterations_hit=False)

    error, exit_code, status = _classify_outcome(
        transport=transport,
        token=CancellationToken(),
        runtime_error=None,
        max_iterations=100,
        incomplete_reason=None,   # the flag is NOT one of these
        final_text="a possibly-truncated answer",
    )

    assert exit_code == 0
    assert status == "ok"
    assert error is None
