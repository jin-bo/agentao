"""Tests for the non-interactive ``agentao run`` subcommand pipeline.

Covers the M0 test matrix from
``docs/history/implementation/non-interactive-run-plan.md``: spec loading,
merge rules, output formats, exit codes, permission injection
ordering, observer-only emit gate, replay override, ASK
``tool_call_id`` correlation, and read-only enforcement.

These tests exercise the pipeline directly via
``agentao.cli.run._execute_with_args``; we patch ``agent.chat`` so no
real LLM calls happen.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_args(**overrides: Any) -> argparse.Namespace:
    """Build a default ``args`` namespace; tests override a few fields."""
    return argparse.Namespace(
        spec_path=overrides.get("spec_path"),
        prompt=overrides.get("prompt"),
        output_format=overrides.get("output_format"),
        model=overrides.get("model"),
        base_url=overrides.get("base_url"),
        permission_mode=overrides.get("permission_mode"),
        interaction_policy=overrides.get("interaction_policy"),
        max_iterations=overrides.get("max_iterations"),
        skills=overrides.get("skills"),
        replay=overrides.get("replay"),
    )


class _TtyStdin(io.StringIO):
    """``io.StringIO`` that reports as a TTY — emulates a terminal."""

    def isatty(self) -> bool:  # pragma: no cover - trivial
        return True


class _PipedStdin(io.StringIO):
    """``io.StringIO`` that reports as a non-TTY (piped data)."""

    def isatty(self) -> bool:  # pragma: no cover - trivial
        return False


def _no_stdin(monkeypatch) -> None:
    """Install a TTY stdin so ``_load_spec`` skips the stdin read path."""
    monkeypatch.setattr(sys, "stdin", _TtyStdin(""))


@pytest.fixture
def stub_pipeline(monkeypatch, tmp_path):
    """Patch ``build_from_environment`` and the agent's chat / runtime
    enough that the pipeline can run without an LLM. Returns a
    ``StubAgent`` factory the test customizes.
    """
    from agentao.transport import NonInteractiveTransport

    captured: Dict[str, Any] = {}

    class StubAgent:
        def __init__(self, transport, replay_config, working_directory, **kw):
            self.working_directory = Path(working_directory).resolve()
            self.transport = transport
            self._session_id = "session-test"
            self._current_turn_id = "turn-test"
            self._plugin_hook_rules: list = []
            self.replay_manager = None
            self.permission_engine = None
            self.tool_runner = type(
                "TR", (),
                {"set_readonly_mode": lambda self_, enabled: None},
            )()
            self.skill_manager = type(
                "SM", (),
                {
                    "list_available_skills": lambda self_: [],
                    "activate_skill": lambda self_, name, task_description="": "ok",
                },
            )()

            class _Llm:
                model = "stub-model"
                total_prompt_tokens = 0
                total_completion_tokens = 0

            self.llm = _Llm()
            captured["agent"] = self
            captured["transport"] = transport
            captured["replay_config"] = replay_config

        def chat(self, prompt, max_iterations=100, cancellation_token=None):
            captured["chat_prompt"] = prompt
            captured["max_iterations"] = max_iterations
            captured["cancellation_token"] = cancellation_token
            return "stub final text"

        def add_event_observer(self, cb):
            captured.setdefault("observers", []).append(cb)
            return cb

        def remove_event_observer(self, cb):
            obs = captured.get("observers", [])
            if cb in obs:
                obs.remove(cb)
                return True
            return False

        def close(self):
            captured["closed"] = True

    def _factory(**kwargs):
        return StubAgent(**kwargs)

    # ``_run_pipeline`` imports ``build_from_environment`` lazily, so
    # the patch lands on the module the import resolves against.
    monkeypatch.setattr(
        "agentao.embedding.build_from_environment", _factory,
    )
    # No plugin loading in tests.
    monkeypatch.setattr(
        "agentao.cli.subcommands._load_and_register_plugins",
        lambda agent: None,
    )
    return captured, StubAgent


# ---------------------------------------------------------------------------
# Spec loading & merge rules
# ---------------------------------------------------------------------------


def test_yaml_spec_loads(monkeypatch, tmp_path, stub_pipeline, capsys):
    captured, _ = stub_pipeline
    spec_path = tmp_path / "task.yaml"
    spec_path.write_text(
        "prompt: hello world\npermission_mode: read-only\n",
        encoding="utf-8",
    )
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(spec_path=str(spec_path), output_format="text")
    rc = run._execute_with_args(args)
    assert rc == 0
    assert captured["chat_prompt"] == "hello world"
    out = capsys.readouterr().out
    assert "stub final text" in out


def test_json_spec_loads(monkeypatch, tmp_path, stub_pipeline, capsys):
    captured, _ = stub_pipeline
    spec_path = tmp_path / "task.json"
    spec_path.write_text(
        json.dumps({"prompt": "via json", "permission_mode": "workspace-write"}),
        encoding="utf-8",
    )
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(spec_path=str(spec_path), output_format="text")
    rc = run._execute_with_args(args)
    assert rc == 0
    assert captured["chat_prompt"] == "via json"


def test_invalid_yaml_exits_2(monkeypatch, tmp_path, stub_pipeline, capsys):
    spec_path = tmp_path / "task.yaml"
    spec_path.write_text("prompt: [unterminated\n", encoding="utf-8")
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(spec_path=str(spec_path), output_format="text")
    rc = run._execute_with_args(args)
    assert rc == run.EXIT_INVALID_USAGE


def test_unknown_field_exits_2(monkeypatch, tmp_path, stub_pipeline, capsys):
    spec_path = tmp_path / "task.yaml"
    spec_path.write_text(
        "prompt: hi\nunknown_field: 1\n",
        encoding="utf-8",
    )
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(spec_path=str(spec_path), output_format="text")
    rc = run._execute_with_args(args)
    assert rc == run.EXIT_INVALID_USAGE


def test_spec_and_stdin_together_exit_2(
    monkeypatch, tmp_path, stub_pipeline, capsys,
):
    spec_path = tmp_path / "task.yaml"
    spec_path.write_text("prompt: hi\n", encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", _PipedStdin("prompt: world"))
    from agentao.cli import run

    args = _build_args(spec_path=str(spec_path), output_format="text")
    rc = run._execute_with_args(args)
    assert rc == run.EXIT_INVALID_USAGE


def test_cli_flag_overrides_spec_scalar(
    monkeypatch, tmp_path, stub_pipeline, capsys,
):
    captured, _ = stub_pipeline
    spec_path = tmp_path / "task.yaml"
    spec_path.write_text(
        "prompt: from spec\nmax_iterations: 8\n",
        encoding="utf-8",
    )
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(spec_path=str(spec_path), max_iterations=42, output_format="text")
    rc = run._execute_with_args(args)
    assert rc == 0
    assert captured["max_iterations"] == 42


def test_repeated_skill_replaces_spec_skills():
    """Repeated ``--skill`` overrides ``skills:`` from the spec wholesale."""
    from agentao.cli.run import _apply_cli_overrides
    from agentao.cli.run_models import RunSpec

    spec = RunSpec.model_validate({
        "prompt": "hi", "skills": ["alpha", "beta"],
    })
    args = _build_args(prompt=None, skills=["gamma"])
    merged = _apply_cli_overrides(spec, args)
    assert merged.skills == ["gamma"]


# ---------------------------------------------------------------------------
# Output contract
# ---------------------------------------------------------------------------


def test_format_text_emits_only_final_text(
    monkeypatch, tmp_path, stub_pipeline, capsys,
):
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(prompt="hi", output_format="text")
    rc = run._execute_with_args(args)
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out == "stub final text"


def test_format_json_emits_structured_envelope(
    monkeypatch, tmp_path, stub_pipeline, capsys,
):
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(prompt="hi", output_format="json")
    rc = run._execute_with_args(args)
    assert rc == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["status"] == "ok"
    assert payload["final_text"] == "stub final text"
    assert payload["cwd"]
    assert payload["model"] == "stub-model"


# ---------------------------------------------------------------------------
# Empty model turn — must not read as success
# ---------------------------------------------------------------------------


def _emit_incomplete_turn(kind: str, final_text: str):
    """Build a ``chat`` stub that ends the turn the way the runtime does when
    the model returns no usable answer."""
    from agentao.transport import AgentEvent, EventType

    def chat(self, prompt, max_iterations=100, cancellation_token=None):
        self.transport.emit(AgentEvent(EventType.TURN_END, {
            "final_text": final_text,
            "status": "ok",
            "error": None,
            "tool_count": 0,
            "incomplete_reason": kind,
        }))
        return final_text

    return chat


def _real_turn_end_payload(content, *, reasoning=None) -> Dict[str, Any]:
    """Drive a real Agentao turn and return the TURN_END payload it emits.

    The other tests here hand-write that payload, which leaves the producer
    (``runtime/turn.py``) and this module's reader (``run.py``) agreeing only
    by coincidence of a string literal. This reaches for the real thing so the
    seam is covered.
    """
    from agentao import Agentao
    from agentao.transport import EventType

    agent = Agentao(
        api_key="test-key", base_url="https://example.test/v1",
        model="test-model", working_directory=Path.cwd(),
    )
    message = SimpleNamespace(
        content=content, tool_calls=None, reasoning_content=reasoning,
    )
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="stop")],
        usage=None, model="test-model",
    )
    agent._llm_call = lambda messages, tools, token: response

    seen: List[Dict[str, Any]] = []
    agent.transport.subscribe(
        lambda ev: seen.append(dict(getattr(ev, "data", None) or {}))
        if getattr(ev, "type", None) == EventType.TURN_END else None
    )
    agent.chat("hi")
    assert seen, "real turn emitted no TURN_END"
    return seen[-1]


def test_run_consumes_the_real_runtime_turn_end_payload(
    monkeypatch, tmp_path, stub_pipeline, capsys,
):
    """Bind the producer to the consumer.

    Feeding run.py the payload the *real* runtime emits means renaming the
    field in turn.py breaks this test, instead of silently disabling the guard
    while every hand-written-payload test stays green.
    """
    payload = _real_turn_end_payload("   \n  ")
    assert payload["incomplete_reason"] == "no_output", (
        "real runtime stopped classifying an empty turn"
    )

    captured, StubAgent = stub_pipeline
    from agentao.transport import AgentEvent, EventType

    def chat(self, prompt, max_iterations=100, cancellation_token=None):
        self.transport.emit(AgentEvent(EventType.TURN_END, payload))
        return payload["final_text"]

    monkeypatch.setattr(StubAgent, "chat", chat)
    _no_stdin(monkeypatch)
    from agentao.cli import run

    rc = run._execute_with_args(_build_args(prompt="hi", output_format="json"))

    assert rc == run.EXIT_RUNTIME_ERROR
    assert json.loads(capsys.readouterr().out)["error"]["type"] == "empty_response"


def test_empty_turn_exits_non_zero_with_envelope(
    monkeypatch, tmp_path, stub_pipeline, capsys,
):
    """The regression this guards: an empty model turn used to exit 0 with the
    bare placeholder as ``final_text``, indistinguishable from a real answer."""
    captured, StubAgent = stub_pipeline
    monkeypatch.setattr(
        StubAgent, "chat", _emit_incomplete_turn("no_output", "[No response]"),
    )
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(prompt="hi", output_format="json")
    rc = run._execute_with_args(args)

    assert rc == run.EXIT_RUNTIME_ERROR
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "error"
    assert payload["error"]["type"] == "empty_response"
    # The placeholder must never be served as the result.
    assert payload.get("final_text") is None


def test_reasoning_only_turn_reports_distinct_diagnostic(
    monkeypatch, tmp_path, stub_pipeline, capsys,
):
    captured, StubAgent = stub_pipeline
    monkeypatch.setattr(
        StubAgent, "chat",
        _emit_incomplete_turn("reasoning_only", "[No text response]"),
    )
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(prompt="hi", output_format="json")
    rc = run._execute_with_args(args)

    assert rc == run.EXIT_RUNTIME_ERROR
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["type"] == "empty_response"
    # Structural discriminator, not a substring of a human-facing sentence.
    assert payload["error"]["reason"] == "reasoning_only"


def test_empty_turn_text_mode_writes_stderr_diagnostic(
    monkeypatch, tmp_path, stub_pipeline, capsys,
):
    """Text mode suppresses ``final_text`` on error, so without the stderr
    diagnostic a non-zero exit would carry no explanation at all."""
    captured, StubAgent = stub_pipeline
    monkeypatch.setattr(
        StubAgent, "chat", _emit_incomplete_turn("no_output", "[No response]"),
    )
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(prompt="hi", output_format="text")
    rc = run._execute_with_args(args)

    assert rc == run.EXIT_RUNTIME_ERROR
    streams = capsys.readouterr()
    assert streams.out.strip() == ""
    assert "empty response" in streams.err


@pytest.mark.parametrize(
    "reason, expected_type, expected_phrase",
    [
        ("length_truncated", "length_truncated", "cut off at the token limit"),
        ("doom_loop", "doom_loop", "doom-loop detector"),
    ],
)
def test_harness_halted_turn_exits_non_zero_with_own_type(
    monkeypatch, tmp_path, stub_pipeline, capsys,
    reason, expected_type, expected_phrase,
):
    """A turn the harness halted must not exit 0 serving its canned string.

    These get their own ``type`` rather than ``empty_response``: the model did
    answer-ish things, the harness cut the turn short, and a pipeline may want
    to retry those differently from "the model said nothing".
    """
    captured, StubAgent = stub_pipeline
    monkeypatch.setattr(
        StubAgent, "chat", _emit_incomplete_turn(reason, "[halted]"),
    )
    _no_stdin(monkeypatch)
    from agentao.cli import run

    rc = run._execute_with_args(_build_args(prompt="hi", output_format="json"))

    assert rc == run.EXIT_RUNTIME_ERROR
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["type"] == expected_type
    assert payload["error"]["reason"] == reason
    assert expected_phrase in payload["error"]["message"]
    assert payload.get("final_text") is None


def test_llm_error_turn_exits_non_zero_and_surfaces_provider_message(
    monkeypatch, tmp_path, stub_pipeline, capsys,
):
    """An LLM call that failed outright must not exit 0 serving its own
    ``[LLM API error: …]`` notice as the model's answer. It exits 1 as
    ``runtime_error`` with ``reason: "llm_error"``, and the envelope surfaces
    the provider's actual message rather than a generic stem.
    """
    captured, StubAgent = stub_pipeline
    monkeypatch.setattr(
        StubAgent, "chat",
        _emit_incomplete_turn("llm_error", "[LLM API error: 502 Bad Gateway]"),
    )
    _no_stdin(monkeypatch)
    from agentao.cli import run

    rc = run._execute_with_args(_build_args(prompt="hi", output_format="json"))

    assert rc == run.EXIT_RUNTIME_ERROR
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["type"] == "runtime_error"
    assert payload["error"]["reason"] == "llm_error"
    assert "502 Bad Gateway" in payload["error"]["message"]
    assert payload.get("final_text") is None


def test_incomplete_outcomes_covers_every_runtime_reason():
    """The CLI outcome table must key on exactly the runtime's wire vocabulary.

    ``_INCOMPLETE_OUTCOMES`` hand-copies the ``INCOMPLETE_*`` values as literals
    to avoid importing runtime internals at module scope (see its comment). That
    duplication is safe only while the two vocabularies stay in lockstep: a
    runtime value with no table entry falls through to the generic
    ``empty_response`` stem instead of its own ``type``. Bind them here so a
    future rename/addition on either side fails loudly rather than silently
    misclassifying.
    """
    from agentao.cli.run import _INCOMPLETE_OUTCOMES
    from agentao.runtime.chat_loop import INCOMPLETE_ANSWER_REASONS

    assert set(_INCOMPLETE_OUTCOMES) == INCOMPLETE_ANSWER_REASONS


def test_normal_turn_still_exits_ok(monkeypatch, tmp_path, stub_pipeline, capsys):
    """A turn that emits TURN_END without a classification stays a success."""
    captured, StubAgent = stub_pipeline
    monkeypatch.setattr(
        StubAgent, "chat", _emit_incomplete_turn(None, "the answer is 42"),
    )
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(prompt="hi", output_format="json")
    rc = run._execute_with_args(args)

    assert rc == run.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["final_text"] == "the answer is 42"


# ``empty_response`` is checked last, so any outcome that explains the missing
# answer keeps its own more specific exit code.
def test_cancelled_empty_turn_reports_interrupted_not_empty():
    """A stream cancelled before its first token returns a normally-built empty
    response (``llm/client.py`` breaks out without raising), so both signals are
    live at once — cancellation must win."""
    from agentao.cli import run

    transport = SimpleNamespace(rejection=None, max_iterations_hit=False)
    token = SimpleNamespace(is_cancelled=True, reason="sigint")

    error, exit_code, status = run._classify_outcome(
        transport=transport, token=token, runtime_error=None,
        max_iterations=10, incomplete_reason="no_output",
    )

    assert exit_code == run.EXIT_INTERRUPTED
    assert error.type == "interrupted"


def test_max_iterations_empty_turn_reports_max_iterations():
    from agentao.cli import run

    transport = SimpleNamespace(rejection=None, max_iterations_hit=True)
    token = SimpleNamespace(is_cancelled=False, reason=None)

    error, exit_code, status = run._classify_outcome(
        transport=transport, token=token, runtime_error=None,
        max_iterations=10, incomplete_reason="no_output",
    )

    assert exit_code == run.EXIT_MAX_ITERATIONS
    assert error.type == "max_iterations"


def test_runtime_error_empty_turn_reports_runtime_error():
    from agentao.cli import run

    transport = SimpleNamespace(rejection=None, max_iterations_hit=False)
    token = SimpleNamespace(is_cancelled=False, reason=None)

    error, exit_code, status = run._classify_outcome(
        transport=transport, token=token, runtime_error=RuntimeError("boom"),
        max_iterations=10, incomplete_reason="no_output",
    )

    assert error.type == "runtime_error"
    assert "boom" in error.message


# ---------------------------------------------------------------------------
# Permission engine extension
# ---------------------------------------------------------------------------


def test_add_run_rules_pre_check_tier(tmp_path):
    """Spec deny pre-empts a user ``allow:*`` under workspace-write."""
    from agentao.permissions import PermissionEngine, PermissionMode

    engine = PermissionEngine(
        project_root=tmp_path,
        rules=[{"tool": "*", "action": "allow"}],
    )
    engine.set_mode(PermissionMode.WORKSPACE_WRITE)
    engine.add_run_rules(
        deny=[{"tool": "run_shell_command", "action": "deny"}],
    )
    detail = engine.decide_detail("run_shell_command", {"command": "rm -rf"})
    assert detail.decision.value == "deny"
    assert detail.reason == "injected:run-spec:run_shell_command"


def test_add_run_rules_full_access_still_blocks(tmp_path):
    """Spec deny pre-empts the ``full-access`` preset ``allow:*``."""
    from agentao.permissions import PermissionEngine, PermissionMode

    engine = PermissionEngine(project_root=tmp_path, rules=[])
    engine.set_mode(PermissionMode.FULL_ACCESS)
    engine.add_run_rules(
        deny=[{"tool": "run_shell_command", "action": "deny"}],
    )
    detail = engine.decide_detail("run_shell_command", {"command": "echo"})
    assert detail.decision.value == "deny"
    assert detail.reason.startswith("injected:run-spec:")


def test_add_run_rules_allow_does_not_override_user_deny(tmp_path):
    """Spec allow joins user list — a user deny earlier still wins."""
    from agentao.permissions import PermissionEngine, PermissionMode

    engine = PermissionEngine(
        project_root=tmp_path,
        rules=[{"tool": "write_file", "action": "deny"}],
    )
    engine.set_mode(PermissionMode.WORKSPACE_WRITE)
    engine.add_run_rules(
        allow=[{"tool": "write_file", "action": "allow"}],
    )
    detail = engine.decide_detail("write_file", {})
    assert detail.decision.value == "deny"
    assert detail.reason == "user-rule:write_file"


def test_add_run_rules_allow_grants_unaddressed_tool(tmp_path):
    """Spec allow grants a tool the user policy doesn't address."""
    from agentao.permissions import PermissionEngine, PermissionMode

    engine = PermissionEngine(project_root=tmp_path, rules=[])
    engine.set_mode(PermissionMode.WORKSPACE_WRITE)
    engine.add_run_rules(
        allow=[{"tool": "custom_tool", "action": "allow"}],
    )
    detail = engine.decide_detail("custom_tool", {})
    assert detail.decision.value == "allow"
    assert detail.reason == "user-rule:custom_tool"


def test_active_permissions_includes_injected_source(tmp_path):
    from agentao.permissions import PermissionEngine, PermissionMode

    engine = PermissionEngine(project_root=tmp_path, rules=[])
    engine.set_mode(PermissionMode.WORKSPACE_WRITE)
    engine.add_run_rules(
        deny=[{"tool": "run_shell_command", "action": "deny"}],
        source="run-spec",
    )
    snap = engine.active_permissions()
    assert "injected:run-spec" in snap.loaded_sources
    assert snap.rules[0] == {"tool": "run_shell_command", "action": "deny"}


# ---------------------------------------------------------------------------
# Read-only short-circuit reason prefix
# ---------------------------------------------------------------------------


def test_readonly_short_circuit_uses_mode_preset_prefix():
    """The readonly fallback reason must use the existing ``mode-preset:`` family."""
    from agentao.runtime.tool_planning import ToolCallPlanner

    class _ReadOnlyTool:
        is_read_only = True
        requires_confirmation = False

    class _WriteTool:
        is_read_only = False
        requires_confirmation = False

    class _Tools:
        tools = {"writer": _WriteTool()}

        def get(self, name):
            return self.tools[name]

    import logging
    planner = ToolCallPlanner(_Tools(), permission_engine=None, logger=logging.getLogger("t"))
    decision, detail = planner._decide(
        _WriteTool(), "writer", {}, readonly_mode=True,
    )
    assert decision.value == "deny"
    assert detail.reason == "mode-preset:read-only"


# ---------------------------------------------------------------------------
# Observer-only emit gate
# ---------------------------------------------------------------------------


def test_has_listeners_true_with_observer_only():
    """The new ``_has_listeners`` returns True when only an observer is attached."""
    from agentao.host.events import EventStream

    stream = EventStream()
    assert not stream._has_listeners()
    stream.add_observer(lambda e: None)
    assert stream._has_listeners() is True
    # ``_has_subscribers`` (the older introspection hook) MUST keep
    # its narrower semantics — observers do not count.
    assert stream._has_subscribers() is False


# ---------------------------------------------------------------------------
# NonInteractiveTransport
# ---------------------------------------------------------------------------


def test_non_interactive_confirm_records_rejection_and_cancels():
    from agentao.cancellation import CancellationToken
    from agentao.transport import NonInteractiveTransport

    token = CancellationToken()
    transport = NonInteractiveTransport(token=token)
    transport.queue_ask("write_file", "call_42")
    assert transport.confirm_tool("write_file", "desc", {}) is False
    assert transport.rejection["type"] == "permission_required"
    assert transport.rejection["tool_call_id"] == "call_42"
    assert token.is_cancelled
    assert "permission_required" in token.reason


def test_non_interactive_ask_user_records_interaction():
    from agentao.cancellation import CancellationToken
    from agentao.transport import NonInteractiveTransport

    token = CancellationToken()
    transport = NonInteractiveTransport(token=token)
    answer = transport.ask_user("anything?")
    assert answer == "[interaction_required]"
    assert transport.rejection["type"] == "interaction_required"
    assert transport.rejection["tool_name"] == "ask_user"
    assert token.is_cancelled
    assert "interaction_required" in token.reason


def test_non_interactive_ask_fifo_dedup_by_name():
    """Two ASK plans for the same tool name → FIFO returns first id, then second."""
    from agentao.cancellation import CancellationToken
    from agentao.transport import NonInteractiveTransport

    transport = NonInteractiveTransport(token=CancellationToken())
    transport.queue_ask("write_file", "call_1")
    transport.queue_ask("write_file", "call_2")
    transport.confirm_tool("write_file", "d", {})
    assert transport.rejection["tool_call_id"] == "call_1"
    transport.rejection = None
    transport.confirm_tool("write_file", "d", {})
    assert transport.rejection["tool_call_id"] == "call_2"


def test_non_interactive_max_iterations_flag():
    from agentao.transport import NonInteractiveTransport

    transport = NonInteractiveTransport()
    out = transport.on_max_iterations(100, [])
    assert out == {"action": "stop"}
    assert transport.max_iterations_hit is True


# ---------------------------------------------------------------------------
# Run model — extra="forbid" / to_engine_dict
# ---------------------------------------------------------------------------


def test_run_permission_rule_action_is_injected_not_authored():
    """``RunPermissionRule`` rejects ``action:`` written by hand."""
    from pydantic import ValidationError

    from agentao.cli.run_models import RunPermissionRule

    with pytest.raises(ValidationError):
        RunPermissionRule.model_validate({"tool": "write_file", "action": "allow"})


def test_run_permission_rule_to_engine_dict_shapes():
    from agentao.cli.run_models import RunPermissionRule

    r = RunPermissionRule.model_validate({
        "tool": "write_file",
        "args": {"path": "^/tmp"},
        "domain": {"allowlist": [".github.com"]},
    })
    out = r.to_engine_dict("deny")
    assert out["tool"] == "write_file"
    assert out["action"] == "deny"
    assert out["args"] == {"path": "^/tmp"}
    assert out["domain"] == {"allowlist": [".github.com"]}


# ---------------------------------------------------------------------------
# Pipeline classification: max-iterations
# ---------------------------------------------------------------------------


def test_max_iterations_exit_4(monkeypatch, tmp_path, stub_pipeline, capsys):
    captured, StubAgent = stub_pipeline

    # Customize stub so chat() flips the transport's max-iter flag.
    def chat(self, prompt, max_iterations=100, cancellation_token=None):
        # Simulate ``runtime`` reaching its iteration cap.
        self.transport.on_max_iterations(max_iterations, [])
        return "[partial]"

    monkeypatch.setattr(StubAgent, "chat", chat)
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(prompt="hi", output_format="json")
    rc = run._execute_with_args(args)
    assert rc == run.EXIT_MAX_ITERATIONS
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "error"
    assert payload["error"]["type"] == "max_iterations"


def test_runtime_error_exit_1(monkeypatch, tmp_path, stub_pipeline, capsys):
    captured, StubAgent = stub_pipeline

    def chat(self, prompt, max_iterations=100, cancellation_token=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(StubAgent, "chat", chat)
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(prompt="hi", output_format="json")
    rc = run._execute_with_args(args)
    assert rc == run.EXIT_RUNTIME_ERROR
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["type"] == "runtime_error"
    assert "boom" in payload["error"]["message"]


def test_sigint_exit_130(monkeypatch, tmp_path, stub_pipeline, capsys):
    captured, StubAgent = stub_pipeline

    def chat(self, prompt, max_iterations=100, cancellation_token=None):
        # Simulate the SIGINT path: the chat loop swallows
        # KeyboardInterrupt and turn.py returns sentinel text after
        # cancelling the token.
        if cancellation_token is not None:
            cancellation_token.cancel("sigint")
        return "[Interrupted by user]"

    monkeypatch.setattr(StubAgent, "chat", chat)
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(prompt="hi", output_format="json")
    rc = run._execute_with_args(args)
    assert rc == run.EXIT_INTERRUPTED
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["type"] == "interrupted"


def test_unknown_interaction_policy_exit_2(monkeypatch, tmp_path, stub_pipeline, capsys):
    spec_path = tmp_path / "task.yaml"
    spec_path.write_text(
        "prompt: hi\ninteraction_policy: approve_all\n",
        encoding="utf-8",
    )
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(spec_path=str(spec_path), output_format="json")
    rc = run._execute_with_args(args)
    assert rc == run.EXIT_INVALID_USAGE


def test_missing_skill_fails_before_chat(
    monkeypatch, tmp_path, stub_pipeline, capsys,
):
    captured, StubAgent = stub_pipeline
    # The stub agent's skill_manager has no skills available.
    spec_path = tmp_path / "task.yaml"
    spec_path.write_text(
        "prompt: hi\nskills:\n  - missing-skill\n",
        encoding="utf-8",
    )
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(spec_path=str(spec_path), output_format="json")
    rc = run._execute_with_args(args)
    assert rc == run.EXIT_INVALID_USAGE
    # chat must not have been called.
    assert "chat_prompt" not in captured


# ---------------------------------------------------------------------------
# -p shim shares the unified pipeline
# ---------------------------------------------------------------------------


def test_print_mode_shim_returns_4_on_max_iter(
    monkeypatch, tmp_path, stub_pipeline, capsys,
):
    captured, StubAgent = stub_pipeline

    def chat(self, prompt, max_iterations=100, cancellation_token=None):
        self.transport.on_max_iterations(max_iterations, [])
        return "[partial]"

    monkeypatch.setattr(StubAgent, "chat", chat)
    _no_stdin(monkeypatch)
    from agentao.cli.entrypoints import run_print_mode

    rc = run_print_mode("hello world")
    assert rc == 4
