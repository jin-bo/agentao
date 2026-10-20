"""``agentao run --interaction-policy deny`` (#565).

Under ``deny`` a tool call that needs approval is declined back to the model
instead of aborting the run; ``reject`` (the default) is unchanged. These pin
the transport, the runner's dedicated result text, the cap, and the envelope.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

import pytest

from agentao.cancellation import CancellationToken
from agentao.permissions import PermissionEngine
from agentao.runtime.tool_runner import ToolRunner
from agentao.tools.base import Tool, ToolRegistry
from agentao.transport import AgentEvent, EventType, NonInteractiveTransport, SdkTransport
from agentao.transport import confirmation as _confirmation
from tests.support.tool_calls import make_tool_call
from tests.test_run_subcommand import (  # noqa: F401 — pytest fixture injection
    _build_args,
    _no_stdin,
    stub_pipeline,
)


class _Tool(Tool):
    def __init__(self, name: str, *, requires_confirm: bool) -> None:
        super().__init__()
        self._name = name
        self._requires = requires_confirm
        self.calls = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"test tool {self._name}"

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    @property
    def requires_confirmation(self) -> bool:
        return self._requires

    def execute(self, **kwargs) -> str:
        self.calls += 1
        return f"{self._name} ran"


def _runner(tmp_path, transport):
    registry = ToolRegistry()
    asked = _Tool("asked_tool", requires_confirm=True)
    free = _Tool("free_tool", requires_confirm=False)
    registry.register(asked)
    registry.register(free)
    runner = ToolRunner(
        registry, PermissionEngine(project_root=tmp_path), transport,
        logging.getLogger("test.run_deny_policy"),
    )
    return runner, asked, free


def _contents(messages) -> Dict[str, str]:
    return {m["tool_call_id"]: m["content"] for m in messages}


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


def _announce(transport, tool_name: str, call_id: str) -> None:
    """What the runner emits just before ``confirm_tool``, on the same thread."""
    transport.emit(AgentEvent(EventType.TOOL_CONFIRMATION, {
        "tool": tool_name, "args": {}, "call_id": call_id,
    }))


def _confirm(transport, tool_name: str, call_id: str) -> bool:
    _announce(transport, tool_name, call_id)
    return transport.confirm_tool(tool_name, "d", {})


def test_deny_declines_without_cancelling_and_records_the_denial():
    token = CancellationToken()
    transport = NonInteractiveTransport(token=token, policy="deny")
    transport.queue_ask("write_file", "call_1", {"tool": "write_file"})

    with _confirmation.gated(None):
        assert _confirm(transport, "write_file", "call_1") is False
        reason = _confirmation.decline_reason()

    assert reason == _confirmation.APPROVAL_UNAVAILABLE
    assert not token.is_cancelled
    assert transport.rejection is None
    assert transport.denials == [{
        "tool_name": "write_file",
        "tool_call_id": "call_1",
        "matched_rule": {"tool": "write_file"},
    }]


def test_reject_is_unchanged_and_records_no_decline_reason():
    token = CancellationToken()
    transport = NonInteractiveTransport(token=token)
    transport.queue_ask("write_file", "call_1")

    with _confirmation.gated(None):
        assert _confirm(transport, "write_file", "call_1") is False
        reason = _confirmation.decline_reason()

    assert reason is None
    assert token.is_cancelled
    assert transport.rejection["type"] == "permission_required"
    assert transport.rejection["tool_call_id"] == "call_1"
    assert transport.denials == []


def test_the_call_past_the_cap_aborts_like_reject():
    token = CancellationToken()
    transport = NonInteractiveTransport(token=token, policy="deny", max_denials=2)

    _confirm(transport, "run_shell_command", "call_0")
    _confirm(transport, "run_shell_command", "call_1")
    assert not token.is_cancelled

    _confirm(transport, "run_shell_command", "call_2")
    assert token.is_cancelled
    assert transport.rejection["type"] == "permission_required"
    assert transport.rejection["tool_call_id"] == "call_2"
    assert "max_denials" in transport.rejection["message"]
    # The aborting call is the rejection, not a denial.
    assert [d["tool_call_id"] for d in transport.denials] == ["call_0", "call_1"]


def test_permission_required_carries_the_rule_that_asked():
    rule = {"tool": "run_shell_command", "action": "ask"}
    # Default ``reject``, and the call past the cap under ``deny``.
    for transport in (
        NonInteractiveTransport(token=CancellationToken()),
        NonInteractiveTransport(token=CancellationToken(), policy="deny", max_denials=0),
    ):
        transport.queue_ask("run_shell_command", "call_1", rule)
        _confirm(transport, "run_shell_command", "call_1")
        assert transport.rejection["matched_rule"] == rule


def test_permission_required_omits_the_rule_when_none_matched():
    transport = NonInteractiveTransport(token=CancellationToken())
    _confirm(transport, "write_file", "call_1")
    assert "matched_rule" not in transport.rejection


def test_max_denials_zero_aborts_on_the_first_ask():
    token = CancellationToken()
    transport = NonInteractiveTransport(token=token, policy="deny", max_denials=0)
    transport.confirm_tool("write_file", "d", {})
    assert token.is_cancelled
    assert transport.denials == []


# ---------------------------------------------------------------------------
# Pairing a confirmation with its call (#566)
# ---------------------------------------------------------------------------


def test_same_name_asks_pair_by_id_not_by_order():
    transport = NonInteractiveTransport(token=CancellationToken(), policy="deny")
    transport.queue_ask("write_file", "call_a", {"rule": "a"})
    transport.queue_ask("write_file", "call_c", {"rule": "c"})

    # Confirmed in the other order: a FIFO by name would swap the rules.
    _confirm(transport, "write_file", "call_c")
    _confirm(transport, "write_file", "call_a")

    assert [(d["tool_call_id"], d["matched_rule"]) for d in transport.denials] == [
        ("call_c", {"rule": "c"}),
        ("call_a", {"rule": "a"}),
    ]


def test_the_id_is_consumed_once():
    transport = NonInteractiveTransport(token=CancellationToken(), policy="deny")
    _confirm(transport, "write_file", "call_1")
    # A direct call with no TOOL_CONFIRMATION before it inherits nothing.
    transport.confirm_tool("write_file", "d", {})
    assert [d["tool_call_id"] for d in transport.denials] == ["call_1", None]


def test_the_cap_path_consumes_the_id_too():
    transport = NonInteractiveTransport(token=CancellationToken(), policy="deny", max_denials=0)
    _confirm(transport, "write_file", "call_1")
    assert transport.rejection["tool_call_id"] == "call_1"
    assert transport._take_call("write_file") == (None, None)


def test_an_unmatched_call_gets_no_rule_and_no_guess_by_name():
    transport = NonInteractiveTransport(token=CancellationToken(), policy="deny")
    transport.queue_ask("write_file", "call_1", {"rule": "1"})
    _confirm(transport, "write_file", "call_2")
    assert transport.denials == [
        {"tool_name": "write_file", "tool_call_id": "call_2", "matched_rule": None},
    ]


def test_a_reused_id_in_a_later_batch_gets_its_own_rule():
    """Some providers number calls ``call_0`` in every response."""
    transport = NonInteractiveTransport(token=CancellationToken(), policy="deny")
    transport.queue_ask("run_shell_command", "call_0", {"batch": 1})
    _confirm(transport, "run_shell_command", "call_0")
    transport.queue_ask("run_shell_command", "call_0", {"batch": 2})
    _confirm(transport, "run_shell_command", "call_0")
    assert [d["matched_rule"] for d in transport.denials] == [{"batch": 1}, {"batch": 2}]


def test_a_sub_agent_call_gets_its_id_but_never_a_parent_rule():
    transport = NonInteractiveTransport(token=CancellationToken(), policy="deny")
    # A parent entry whose id the child happens to reuse.
    transport.queue_ask("write_file", "call_0", {"rule": "parent"})
    # The child's forwarded event carries its label and its own id; its
    # confirmation goes through the parent's ``confirm_tool`` with its label.
    _announce(transport, "[worker 1/5] write_file", "call_0")
    transport.confirm_tool("[worker] write_file", "d", {})

    assert transport.denials == [{
        "tool_name": "[worker] write_file", "tool_call_id": "call_0", "matched_rule": None,
    }]
    # The parent's own confirmation still finds its rule.
    _confirm(transport, "write_file", "call_0")
    assert transport.denials[1]["matched_rule"] == {"rule": "parent"}


def test_two_threads_keep_their_own_ids():
    """Foreground sub-agents confirm on their own threads through this transport."""
    import threading

    transport = NonInteractiveTransport(token=CancellationToken(), policy="deny")
    announced = threading.Barrier(2)

    def child(call_id: str) -> None:
        _announce(transport, "[worker] write_file", call_id)
        # Both have announced before either confirms.
        announced.wait()
        transport.confirm_tool("[worker] write_file", "d", {})

    threads = [threading.Thread(target=child, args=(f"child_{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(d["tool_call_id"] for d in transport.denials) == ["child_0", "child_1"]


def test_ask_user_still_aborts_under_deny():
    token = CancellationToken()
    transport = NonInteractiveTransport(token=token, policy="deny")
    assert transport.ask_user("which one?") == "[interaction_required]"
    assert token.is_cancelled
    assert transport.rejection["type"] == "interaction_required"


@pytest.mark.parametrize("bad", [-1, True, 1.5, "3"])
def test_transport_refuses_a_bad_max_denials(bad):
    with pytest.raises(ValueError):
        NonInteractiveTransport(policy="deny", max_denials=bad)


def test_transport_refuses_an_unknown_policy():
    with pytest.raises(ValueError):
        NonInteractiveTransport(policy="approve_all")  # type: ignore[arg-type]


def test_gated_does_not_leak_a_decline_into_the_next_confirmation():
    # Outside a block (a host calling ``confirm_tool`` directly) it is dropped.
    _confirmation.record_decline(_confirmation.APPROVAL_UNAVAILABLE)
    assert _confirmation.decline_reason() is None
    with _confirmation.gated(None):
        _confirmation.record_decline(_confirmation.APPROVAL_UNAVAILABLE)
    assert _confirmation.decline_reason() is None
    with _confirmation.gated(None):
        assert _confirmation.decline_reason() is None


# ---------------------------------------------------------------------------
# Runner: what the model receives
# ---------------------------------------------------------------------------


def test_the_model_gets_the_dedicated_text_and_the_batch_runs_on(tmp_path):
    token = CancellationToken()
    transport = NonInteractiveTransport(token=token, policy="deny")
    runner, asked, free = _runner(tmp_path, transport)

    _, messages = runner.execute(
        [make_tool_call("c1", "asked_tool"), make_tool_call("c2", "free_tool")],
        cancellation_token=token,
    )

    content = _contents(messages)
    assert "approval is unavailable in non-interactive mode" in content["c1"]
    assert "You may use an alternative permitted by the current permission rules." in content["c1"]
    assert "or use a different tool" not in content["c1"]
    assert asked.calls == 0
    assert free.calls == 1
    assert content["c2"] == "free_tool ran"
    assert not token.is_cancelled
    assert [d["tool_name"] for d in transport.denials] == ["asked_tool"]


def test_hitting_the_cap_cancels_the_rest_of_the_batch(tmp_path):
    token = CancellationToken()
    transport = NonInteractiveTransport(token=token, policy="deny", max_denials=0)
    runner, asked, free = _runner(tmp_path, transport)

    runner.execute(
        [make_tool_call("c1", "asked_tool"), make_tool_call("c2", "free_tool")],
        cancellation_token=token,
    )

    assert token.is_cancelled
    assert transport.rejection["type"] == "permission_required"
    assert asked.calls == 0
    assert free.calls == 0


def test_a_gated_ask_keeps_the_plain_decline_under_deny(tmp_path):
    """The MCP Skills gate asks for a person's consent; no alternative is offered."""
    token = CancellationToken()
    transport = NonInteractiveTransport(token=token, policy="deny")
    runner, asked, free = _runner(tmp_path, transport)
    consented = []
    # ``free_tool`` needs no confirmation of its own; the gate makes it ask.
    runner.set_mcp_skill_gate(
        lambda name, args, tool: "skill instructions are loaded" if name == "free_tool" else None,
        on_confirmed=lambda *a: consented.append(a),
    )

    _, messages = runner.execute(
        [make_tool_call("c1", "free_tool"), make_tool_call("c2", "asked_tool")],
        cancellation_token=token,
    )

    content = _contents(messages)
    assert free.calls == 0
    assert consented == []
    assert "needed approval and was not approved" in content["c1"]
    assert "or use a different tool to achieve the same outcome." in content["c1"]
    assert "You may use an alternative" not in content["c1"]
    # An ordinary ASK in the same batch still gets the new text.
    assert "You may use an alternative permitted" in content["c2"]
    # The gated decline still counts toward the cap.
    assert [d["tool_name"] for d in transport.denials] == ["free_tool", "asked_tool"]
    assert not token.is_cancelled


def test_the_first_rejection_wins_across_every_writer():
    token = CancellationToken()
    transport = NonInteractiveTransport(token=token, policy="deny", max_denials=0)
    assert transport.record_rejection({"type": "permission_denied", "message": "rule"}) is True
    transport.confirm_tool("write_file", "d", {})
    transport.ask_user("q?")
    assert transport.rejection == {"type": "permission_denied", "message": "rule"}
    assert transport.record_rejection({"type": "permission_required", "message": "x"}) is False


def test_concurrent_confirmations_respect_the_cap():
    """Sub-agents confirm on their own threads: exactly N denials, one rejection."""
    import threading

    token = CancellationToken()
    transport = NonInteractiveTransport(token=token, policy="deny", max_denials=7)
    start = threading.Barrier(32)

    def worker(i: int) -> None:
        start.wait()
        transport.confirm_tool(f"tool_{i}", "d", {})

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(32)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(transport.denials) == 7
    assert len({d["tool_name"] for d in transport.denials}) == 7
    assert transport.rejection["type"] == "permission_required"
    assert transport.rejection["tool_name"] not in {d["tool_name"] for d in transport.denials}
    assert token.is_cancelled


def test_an_interactive_decline_keeps_its_text(tmp_path):
    transport = SdkTransport(confirm_tool=lambda *_: False)
    runner, _, _ = _runner(tmp_path, transport)

    _, messages = runner.execute([make_tool_call("c1", "asked_tool")])

    content = _contents(messages)["c1"]
    assert "needed approval and was not approved" in content
    assert "or use a different tool to achieve the same outcome." in content
    assert "non-interactive" not in content


# ---------------------------------------------------------------------------
# CLI: spec validation and the envelope
# ---------------------------------------------------------------------------


def test_deny_policy_is_passed_to_the_transport(monkeypatch, stub_pipeline):  # noqa: F811 — fixture
    captured, _ = stub_pipeline
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(prompt="hi", output_format="json", interaction_policy="deny")
    args.max_denials = 3
    assert run._execute_with_args(args) == run.EXIT_OK
    transport = captured["transport"]
    assert transport._policy == "deny"
    assert transport._max_denials == 3


def test_max_denials_without_deny_exits_2(monkeypatch, stub_pipeline, capsys):  # noqa: F811 — fixture
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(prompt="hi", output_format="json")
    args.max_denials = 3
    assert run._execute_with_args(args) == run.EXIT_INVALID_USAGE
    assert "max_denials applies only" in capsys.readouterr().out


def test_negative_max_denials_exits_2(monkeypatch, stub_pipeline, capsys):  # noqa: F811 — fixture
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(prompt="hi", output_format="json", interaction_policy="deny")
    args.max_denials = -1
    assert run._execute_with_args(args) == run.EXIT_INVALID_USAGE


def test_spec_file_accepts_deny_and_max_denials(monkeypatch, tmp_path, stub_pipeline):  # noqa: F811 — fixture
    captured, _ = stub_pipeline
    spec_path = tmp_path / "task.yaml"
    spec_path.write_text(
        "prompt: hi\ninteraction_policy: deny\nmax_denials: 1\n", encoding="utf-8",
    )
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(spec_path=str(spec_path), output_format="json")
    assert run._execute_with_args(args) == run.EXIT_OK
    assert captured["transport"]._max_denials == 1


def _chat_with_a_denial(self, prompt, max_iterations=100, cancellation_token=None):
    self.transport.queue_ask("run_shell_command", "call_9", None)
    _announce(self.transport, "run_shell_command", "call_9")
    self.transport.confirm_tool("run_shell_command", "d", {"command": "true"})
    return "done anyway"


def test_a_run_with_denials_exits_0_and_lists_them(monkeypatch, stub_pipeline, capsys):  # noqa: F811 — fixture
    _, StubAgent = stub_pipeline
    monkeypatch.setattr(StubAgent, "chat", _chat_with_a_denial)
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(prompt="hi", output_format="json", interaction_policy="deny")
    assert run._execute_with_args(args) == run.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["final_text"] == "done anyway"
    assert payload["denials"] == [
        {"tool_name": "run_shell_command", "tool_call_id": "call_9"},
    ]


def test_text_mode_reports_each_denial_on_stderr(monkeypatch, stub_pipeline, capsys):  # noqa: F811 — fixture
    _, StubAgent = stub_pipeline
    monkeypatch.setattr(StubAgent, "chat", _chat_with_a_denial)
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(prompt="hi", output_format="text", interaction_policy="deny")
    assert run._execute_with_args(args) == run.EXIT_OK
    out = capsys.readouterr()
    assert out.out == "done anyway\n"
    assert "denied run_shell_command" in out.err


def test_reject_run_has_an_empty_denials_list(monkeypatch, stub_pipeline, capsys):  # noqa: F811 — fixture
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(prompt="hi", output_format="json")
    assert run._execute_with_args(args) == run.EXIT_OK
    assert json.loads(capsys.readouterr().out)["denials"] == []


def test_the_flag_parses():
    import argparse

    from agentao.cli import run

    parser = argparse.ArgumentParser()
    run._attach_run_arguments(parser)
    args = parser.parse_args(["--interaction-policy", "deny", "--max-denials", "2"])
    assert args.interaction_policy == "deny"
    assert args.max_denials == 2


def test_spec_refuses_a_boolean_max_denials():
    from pydantic import ValidationError

    from agentao.cli.run_models import RunSpec

    with pytest.raises(ValidationError):
        RunSpec(interaction_policy="deny", max_denials=True)


# ---------------------------------------------------------------------------
# Pairing through the real runner, wired as ``agentao run`` wires it (#566)
# ---------------------------------------------------------------------------


def _wired_runner(tmp_path, transport, *, outer=None):
    """A runner whose ASK permission events reach ``transport.queue_ask``.

    ``outer`` is the transport the runner talks to (a ``ReplayAdapter``
    around ``transport``, say); the observer always feeds ``transport``,
    as the run pipeline's does.
    """
    from agentao.host.events import EventStream
    from agentao.host.models import PermissionDecisionEvent
    from agentao.host.projection import HostPermissionEmitter

    registry = ToolRegistry()
    # An engine ASK falls through to the tool's own ``requires_confirmation``
    # (``tool_planning.py::_decide``), so the tool must ask too.
    asked = _Tool("asked_tool", requires_confirm=True)
    registry.register(asked)
    engine = PermissionEngine(
        project_root=tmp_path, user_root=tmp_path,
        rules=[{"tool": "asked_tool", "action": "ask"}],
    )
    stream = EventStream()

    def observer(event):
        if isinstance(event, PermissionDecisionEvent) and event.outcome == "prompt":
            transport.queue_ask(event.tool_name, event.tool_call_id, event.matched_rule)

    stream.add_observer(observer)
    emitter = HostPermissionEmitter(
        stream,
        session_id_provider=lambda: "s-1",
        turn_id_provider=lambda: "t-1",
        active_permissions_provider=engine.active_permissions,
    )
    runner = ToolRunner(
        registry, engine, outer or transport,
        logging.getLogger("test.run_deny_policy.wired"),
        host_permission_emitter=emitter,
    )
    return runner, asked


def test_the_runner_pairs_same_name_calls_across_batches(tmp_path):
    token = CancellationToken()
    transport = NonInteractiveTransport(token=token, policy="deny")
    runner, asked = _wired_runner(tmp_path, transport)

    # Distinct arguments, so doom-loop detection stays out of it.
    runner.execute(
        [
            make_tool_call("b1_a", "asked_tool", json.dumps({"n": 1})),
            make_tool_call("b1_b", "asked_tool", json.dumps({"n": 2})),
        ],
        cancellation_token=token,
    )
    # A provider that numbers calls per response reuses an id next batch.
    runner.execute(
        [make_tool_call("b1_a", "asked_tool", json.dumps({"n": 3}))],
        cancellation_token=token,
    )

    assert asked.calls == 0
    assert [d["tool_call_id"] for d in transport.denials] == ["b1_a", "b1_b", "b1_a"]
    assert all(
        d["matched_rule"] is not None and d["matched_rule"]["tool"] == "asked_tool"
        for d in transport.denials
    )
    assert transport._asked_rules == {}


def test_the_pairing_survives_a_replay_adapter(tmp_path):
    from agentao.replay.adapter import ReplayAdapter
    from agentao.replay.recorder import ReplayRecorder

    token = CancellationToken()
    transport = NonInteractiveTransport(token=token, policy="deny")
    recorder = ReplayRecorder.create("sess", tmp_path)
    try:
        runner, _ = _wired_runner(
            tmp_path, transport, outer=ReplayAdapter(transport, recorder),
        )
        runner.execute([make_tool_call("c7", "asked_tool")], cancellation_token=token)
    finally:
        recorder.close()

    assert [d["tool_call_id"] for d in transport.denials] == ["c7"]
    assert transport.denials[0]["matched_rule"]["tool"] == "asked_tool"


def test_the_reject_envelope_names_the_rule_that_asked(monkeypatch, stub_pipeline, capsys):  # noqa: F811 — fixture
    _, StubAgent = stub_pipeline

    def chat(self, prompt, max_iterations=100, cancellation_token=None):
        self.transport.queue_ask("run_shell_command", "call_3", {"tool": "run_shell_command"})
        _announce(self.transport, "run_shell_command", "call_3")
        self.transport.confirm_tool("run_shell_command", "d", {})
        return ""

    monkeypatch.setattr(StubAgent, "chat", chat)
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(prompt="hi", output_format="json")
    assert run._execute_with_args(args) == run.EXIT_PERMISSION_OR_INTERACTION
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["type"] == "permission_required"
    assert error["tool_call_id"] == "call_3"
    assert error["matched_rule"] == {"tool": "run_shell_command"}
