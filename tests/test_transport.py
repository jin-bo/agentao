"""Tests for the Transport abstraction layer."""

from agentao.transport import (
    AgentEvent,
    EventType,
    NullTransport,
    SdkTransport,
    Transport,
    build_compat_transport,
)


class TestAgentEvent:
    def test_event_with_data(self):
        e = AgentEvent(EventType.TOOL_START, {"tool": "shell", "args": {}})
        assert e.type == EventType.TOOL_START
        assert e.data["tool"] == "shell"

    def test_event_default_empty_data(self):
        e = AgentEvent(EventType.TURN_START)
        assert e.data == {}

    def test_event_type_is_string(self):
        # EventType values compare equal to plain strings (str Enum mixin)
        assert EventType.LLM_TEXT == "llm_text"
        assert EventType.TOOL_START == "tool_start"
        assert EventType.TOOL_START.value == "tool_start"


class TestNullTransport:
    def test_emit_is_noop(self):
        t = NullTransport()
        t.emit(AgentEvent(EventType.TOOL_START, {"tool": "x"}))  # must not raise

    def test_confirm_tool_returns_true(self):
        assert NullTransport().confirm_tool("shell", "desc", {}) is True

    def test_ask_user_returns_sentinel(self):
        assert NullTransport().ask_user("what?") == "[ask_user: not available in non-interactive mode]"

    def test_on_max_iterations_returns_stop(self):
        result = NullTransport().on_max_iterations(10, [])
        assert result == {"action": "stop"}

    def test_satisfies_transport_protocol(self):
        assert isinstance(NullTransport(), Transport)


class TestSdkTransport:
    def test_emit_calls_on_event(self):
        received = []
        t = SdkTransport(on_event=received.append)
        e = AgentEvent(EventType.LLM_TEXT, {"chunk": "hi"})
        t.emit(e)
        assert received == [e]

    def test_emit_no_callback_is_noop(self):
        t = SdkTransport()
        t.emit(AgentEvent(EventType.TURN_START))  # must not raise

    def test_confirm_tool_uses_callback(self):
        t = SdkTransport(confirm_tool=lambda n, d, a: False)
        assert t.confirm_tool("shell", "run shell", {}) is False

    def test_confirm_tool_default_true(self):
        assert SdkTransport().confirm_tool("x", "y", {}) is True

    def test_ask_user_uses_callback(self):
        t = SdkTransport(ask_user=lambda q: "yes")
        assert t.ask_user("ok?") == "yes"

    def test_on_max_iterations_uses_callback(self):
        t = SdkTransport(on_max_iterations=lambda c, m: {"action": "continue"})
        assert t.on_max_iterations(5, []) == {"action": "continue"}

    def test_emit_callback_exception_is_swallowed(self):
        def boom(e):
            raise RuntimeError("oops")
        t = SdkTransport(on_event=boom)
        t.emit(AgentEvent(EventType.TURN_START))  # must not raise

    def test_satisfies_transport_protocol(self):
        assert isinstance(SdkTransport(), Transport)


class TestBuildCompatTransport:
    def test_turn_start_calls_step_callback_with_none(self):
        calls = []
        t = build_compat_transport(step_callback=lambda name, args: calls.append((name, args)))
        t.emit(AgentEvent(EventType.TURN_START))
        assert calls == [(None, {})]

    def test_tool_start_calls_step_callback(self):
        calls = []
        t = build_compat_transport(step_callback=lambda name, args: calls.append((name, args)))
        t.emit(AgentEvent(EventType.TOOL_START, {"tool": "shell", "args": {"cmd": "ls"}}))
        assert calls == [("shell", {"cmd": "ls"})]

    def test_tool_output_calls_output_callback(self):
        calls = []
        t = build_compat_transport(output_callback=lambda name, chunk: calls.append((name, chunk)))
        t.emit(AgentEvent(EventType.TOOL_OUTPUT, {"tool": "shell", "chunk": "hello\n"}))
        assert calls == [("shell", "hello\n")]

    def test_tool_complete_calls_complete_callback(self):
        calls = []
        t = build_compat_transport(tool_complete_callback=lambda name: calls.append(name))
        t.emit(AgentEvent(EventType.TOOL_COMPLETE, {"tool": "shell"}))
        assert calls == ["shell"]

    def test_tool_complete_forwards_call_id_when_accepted(self):
        # A callback declaring a ``call_id`` param receives the stable id so
        # a same-named parallel batch stays correlatable with its TOOL_START.
        calls = []

        def cb(name, call_id=None):
            calls.append((name, call_id))

        t = build_compat_transport(tool_complete_callback=cb)
        t.emit(AgentEvent(EventType.TOOL_COMPLETE, {"tool": "read_file", "call_id": "call_abc"}))
        assert calls == [("read_file", "call_abc")]

    def test_tool_complete_kwargs_only_callback_stays_legacy(self):
        # A callback that declares only ``**kwargs`` does NOT opt into
        # metadata forwarding: the bridge passes ``name`` positionally (the
        # legacy contract), which **kwargs cannot absorb, so green-lighting it
        # would only raise a swallowed TypeError. Opt-in is by naming the
        # parameter, not by declaring **kwargs.
        calls = []
        t = build_compat_transport(
            tool_complete_callback=lambda name, **kw: calls.append((name, kw))
        )
        t.emit(AgentEvent(EventType.TOOL_COMPLETE, {"tool": "read_file", "call_id": "call_xyz"}))
        assert calls == [("read_file", {})]

    def test_tool_complete_forwards_status_and_error_when_accepted(self):
        # A failed sub-agent tool must reach a metadata-aware callback as a
        # failure, not a hardcoded success.
        calls = []

        def cb(name, call_id=None, status=None, duration_ms=None, error=None):
            calls.append((name, call_id, status, duration_ms, error))

        t = build_compat_transport(tool_complete_callback=cb)
        t.emit(AgentEvent(EventType.TOOL_COMPLETE, {
            "tool": "shell", "call_id": "call_1", "status": "error",
            "duration_ms": 42, "error": "boom",
        }))
        assert calls == [("shell", "call_1", "error", 42, "boom")]

    def test_tool_output_forwards_call_id_when_accepted(self):
        calls = []

        def cb(name, chunk, call_id=None):
            calls.append((name, chunk, call_id))

        t = build_compat_transport(output_callback=cb)
        t.emit(AgentEvent(EventType.TOOL_OUTPUT, {"tool": "shell", "chunk": "hi\n", "call_id": "call_1"}))
        assert calls == [("shell", "hi\n", "call_1")]

    def test_same_name_parallel_calls_stay_correlated(self):
        # Regression: four concurrent ``read_file`` calls must each carry their
        # own id on TOOL_COMPLETE rather than collapsing onto the tool name.
        ids = ["call_a", "call_b", "call_c", "call_d"]
        completed = []
        t = build_compat_transport(
            tool_complete_callback=lambda name, call_id=None: completed.append(call_id)
        )
        for cid in ids:
            t.emit(AgentEvent(EventType.TOOL_COMPLETE, {"tool": "read_file", "call_id": cid}))
        assert completed == ids

    def test_thinking_calls_thinking_callback(self):
        calls = []
        t = build_compat_transport(thinking_callback=lambda text: calls.append(text))
        t.emit(AgentEvent(EventType.THINKING, {"text": "hmm"}))
        assert calls == ["hmm"]

    def test_llm_text_calls_llm_text_callback(self):
        calls = []
        t = build_compat_transport(llm_text_callback=lambda chunk: calls.append(chunk))
        t.emit(AgentEvent(EventType.LLM_TEXT, {"chunk": "hi"}))
        assert calls == ["hi"]

    def test_confirm_delegates(self):
        t = build_compat_transport(confirmation_callback=lambda n, d, a: False)
        assert t.confirm_tool("x", "y", {}) is False

    def test_ask_user_delegates(self):
        t = build_compat_transport(ask_user_callback=lambda q: "answer")
        assert t.ask_user("?") == "answer"

    def test_max_iterations_delegates(self):
        t = build_compat_transport(on_max_iterations_callback=lambda c, m: {"action": "continue"})
        assert t.on_max_iterations(3, []) == {"action": "continue"}

    def test_no_callbacks_still_works(self):
        t = build_compat_transport()
        t.emit(AgentEvent(EventType.TURN_START))  # no error
        assert t.confirm_tool("x", "y", {}) is True
        assert t.ask_user("?") == "[ask_user: not available in non-interactive mode]"
        assert t.on_max_iterations(1, []) == {"action": "stop"}
