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
from agentao.transport import NonInteractiveTransport, SdkTransport
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


def test_deny_declines_without_cancelling_and_records_the_denial():
    token = CancellationToken()
    transport = NonInteractiveTransport(token=token, policy="deny")
    transport.queue_ask("write_file", "call_1", {"tool": "write_file"})

    with _confirmation.gated(None):
        assert transport.confirm_tool("write_file", "d", {}) is False
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
        assert transport.confirm_tool("write_file", "d", {}) is False
        reason = _confirmation.decline_reason()

    assert reason is None
    assert token.is_cancelled
    assert transport.rejection["type"] == "permission_required"
    assert transport.rejection["tool_call_id"] == "call_1"
    assert transport.denials == []


def test_the_call_past_the_cap_aborts_like_reject():
    token = CancellationToken()
    transport = NonInteractiveTransport(token=token, policy="deny", max_denials=2)
    for i in range(3):
        transport.queue_ask("run_shell_command", f"call_{i}")

    transport.confirm_tool("run_shell_command", "d", {})
    transport.confirm_tool("run_shell_command", "d", {})
    assert not token.is_cancelled

    transport.confirm_tool("run_shell_command", "d", {})
    assert token.is_cancelled
    assert transport.rejection["type"] == "permission_required"
    assert transport.rejection["tool_call_id"] == "call_2"
    assert "max_denials" in transport.rejection["message"]
    # The aborting call is the rejection, not a denial.
    assert [d["tool_call_id"] for d in transport.denials] == ["call_0", "call_1"]


def test_max_denials_zero_aborts_on_the_first_ask():
    token = CancellationToken()
    transport = NonInteractiveTransport(token=token, policy="deny", max_denials=0)
    transport.confirm_tool("write_file", "d", {})
    assert token.is_cancelled
    assert transport.denials == []


def test_same_name_asks_keep_their_ids_in_order():
    transport = NonInteractiveTransport(token=CancellationToken(), policy="deny")
    transport.queue_ask("write_file", "call_a")
    transport.queue_ask("read_file", "call_b")
    transport.queue_ask("write_file", "call_c")

    transport.confirm_tool("write_file", "d", {})
    transport.confirm_tool("write_file", "d", {})

    assert [d["tool_call_id"] for d in transport.denials] == ["call_a", "call_c"]


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
