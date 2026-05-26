"""Tests for ``agentao run`` spec parameters & instructions templating.

Covers the 15-case test plan from
``docs/design/run-spec-parameters.md``. Hooks into the same stub
pipeline pattern that ``tests/test_run_subcommand.py`` uses so no real
LLM calls happen.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path
from typing import Any, Dict

import pytest


# ---------------------------------------------------------------------------
# Stub pipeline (mirrors test_run_subcommand.stub_pipeline; can't import the
# fixture cleanly because it lives in another module's local scope)
# ---------------------------------------------------------------------------


def _build_args(**overrides: Any) -> argparse.Namespace:
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
        params=overrides.get("params"),
    )


class _TtyStdin(io.StringIO):
    def isatty(self) -> bool:  # pragma: no cover - trivial
        return True


def _no_stdin(monkeypatch) -> None:
    monkeypatch.setattr(sys, "stdin", _TtyStdin(""))


@pytest.fixture
def stub_pipeline(monkeypatch, tmp_path):
    """Patch ``build_from_environment`` so the pipeline runs without an LLM.

    Returns ``(captured, StubAgent)`` so tests can both inspect what
    the pipeline did and override stub behavior on demand.
    """
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
            captured["factory_kwargs"] = kw

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

    monkeypatch.setattr(
        "agentao.embedding.build_from_environment", _factory,
    )
    monkeypatch.setattr(
        "agentao.cli.subcommands._load_and_register_plugins",
        lambda agent: None,
    )
    return captured, StubAgent


# ---------------------------------------------------------------------------
# 1. Valid render
# ---------------------------------------------------------------------------


def test_valid_render_uses_param_in_prompt(
    monkeypatch, tmp_path, stub_pipeline,
):
    captured, _ = stub_pipeline
    spec_path = tmp_path / "task.yaml"
    spec_path.write_text(
        "parameters:\n  - name: pr_number\n    required: true\n"
        "prompt: 'Review PR #{{ pr_number }} now.'\n",
        encoding="utf-8",
    )
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(
        spec_path=str(spec_path),
        output_format="text",
        params=["pr_number=142"],
    )
    rc = run._execute_with_args(args)
    assert rc == 0
    assert captured["chat_prompt"] == "Review PR #142 now."


# ---------------------------------------------------------------------------
# 2. Required missing
# ---------------------------------------------------------------------------


def test_required_param_missing_exits_2(
    monkeypatch, tmp_path, stub_pipeline, capsys,
):
    spec_path = tmp_path / "task.yaml"
    spec_path.write_text(
        "parameters:\n  - name: pr_number\n    required: true\n"
        "prompt: 'Review PR #{{ pr_number }}.'\n",
        encoding="utf-8",
    )
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(spec_path=str(spec_path), output_format="json")
    rc = run._execute_with_args(args)
    assert rc == run.EXIT_INVALID_USAGE
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["type"] == "invalid_spec"
    assert "pr_number" in payload["error"]["message"]


# ---------------------------------------------------------------------------
# 3. Default applied
# ---------------------------------------------------------------------------


def test_default_applied_when_param_missing(
    monkeypatch, tmp_path, stub_pipeline,
):
    captured, _ = stub_pipeline
    spec_path = tmp_path / "task.yaml"
    spec_path.write_text(
        "parameters:\n"
        "  - name: depth\n    default: shallow\n    choices: [shallow, deep]\n"
        "prompt: 'Use {{ depth }} mode.'\n",
        encoding="utf-8",
    )
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(spec_path=str(spec_path), output_format="text")
    rc = run._execute_with_args(args)
    assert rc == 0
    assert captured["chat_prompt"] == "Use shallow mode."


# ---------------------------------------------------------------------------
# 4. Choices enforced
# ---------------------------------------------------------------------------


def test_choices_violation_exits_2(
    monkeypatch, tmp_path, stub_pipeline, capsys,
):
    spec_path = tmp_path / "task.yaml"
    spec_path.write_text(
        "parameters:\n"
        "  - name: depth\n    default: shallow\n    choices: [shallow, deep]\n"
        "prompt: 'Use {{ depth }} mode.'\n",
        encoding="utf-8",
    )
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(
        spec_path=str(spec_path),
        output_format="json",
        params=["depth=medium"],
    )
    rc = run._execute_with_args(args)
    assert rc == run.EXIT_INVALID_USAGE
    payload = json.loads(capsys.readouterr().out)
    msg = payload["error"]["message"]
    assert "depth" in msg
    assert "shallow" in msg and "deep" in msg


# ---------------------------------------------------------------------------
# 5. Unknown param (parameters declared)
# ---------------------------------------------------------------------------


def test_unknown_param_with_declared_params(
    monkeypatch, tmp_path, stub_pipeline, capsys,
):
    spec_path = tmp_path / "task.yaml"
    spec_path.write_text(
        "parameters:\n  - name: a\n"
        "prompt: '{{ a | default(\"x\") }}'\n",
        encoding="utf-8",
    )
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(
        spec_path=str(spec_path),
        output_format="json",
        params=["b=1"],
    )
    rc = run._execute_with_args(args)
    assert rc == run.EXIT_INVALID_USAGE
    payload = json.loads(capsys.readouterr().out)
    assert "'b'" in payload["error"]["message"]


# ---------------------------------------------------------------------------
# 6. Unknown param (no parameters block)
# ---------------------------------------------------------------------------


def test_unknown_param_when_no_parameters_block(
    monkeypatch, tmp_path, stub_pipeline, capsys,
):
    spec_path = tmp_path / "task.yaml"
    spec_path.write_text("prompt: hello\n", encoding="utf-8")
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(
        spec_path=str(spec_path),
        output_format="json",
        params=["x=1"],
    )
    rc = run._execute_with_args(args)
    assert rc == run.EXIT_INVALID_USAGE
    payload = json.loads(capsys.readouterr().out)
    assert "'x'" in payload["error"]["message"]


# ---------------------------------------------------------------------------
# 7. No params + no CLI params → no-op pass-through
# ---------------------------------------------------------------------------


def test_no_params_passthrough_literal_braces(
    monkeypatch, tmp_path, stub_pipeline,
):
    captured, _ = stub_pipeline
    spec_path = tmp_path / "task.yaml"
    spec_path.write_text(
        "prompt: 'Keep {{ literal }} untouched.'\n",
        encoding="utf-8",
    )
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(spec_path=str(spec_path), output_format="text")
    rc = run._execute_with_args(args)
    assert rc == 0
    # Renderer is not invoked; the literal `{{ }}` flows through.
    assert captured["chat_prompt"] == "Keep {{ literal }} untouched."


# ---------------------------------------------------------------------------
# 8. StrictUndefined
# ---------------------------------------------------------------------------


def test_strict_undefined_in_template(
    monkeypatch, tmp_path, stub_pipeline, capsys,
):
    spec_path = tmp_path / "task.yaml"
    spec_path.write_text(
        "parameters:\n  - name: a\n"
        "prompt: 'has {{ missing }} variable'\n",
        encoding="utf-8",
    )
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(
        spec_path=str(spec_path),
        output_format="json",
        params=["a=value"],
    )
    rc = run._execute_with_args(args)
    assert rc == run.EXIT_INVALID_USAGE
    payload = json.loads(capsys.readouterr().out)
    assert "missing" in payload["error"]["message"]


# ---------------------------------------------------------------------------
# 9. Duplicate parameter name in spec
# ---------------------------------------------------------------------------


def test_duplicate_parameter_names_in_spec(
    monkeypatch, tmp_path, stub_pipeline, capsys,
):
    spec_path = tmp_path / "task.yaml"
    spec_path.write_text(
        "parameters:\n  - name: depth\n  - name: depth\n"
        "prompt: hi\n",
        encoding="utf-8",
    )
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(spec_path=str(spec_path), output_format="json")
    rc = run._execute_with_args(args)
    assert rc == run.EXIT_INVALID_USAGE
    payload = json.loads(capsys.readouterr().out)
    assert "depth" in payload["error"]["message"]


# ---------------------------------------------------------------------------
# 10. --param malformed
# ---------------------------------------------------------------------------


def test_param_malformed_no_equals(
    monkeypatch, tmp_path, stub_pipeline, capsys,
):
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(
        prompt="hi",
        output_format="json",
        params=["foo"],
    )
    rc = run._execute_with_args(args)
    assert rc == run.EXIT_INVALID_USAGE
    payload = json.loads(capsys.readouterr().out)
    assert "KEY=VALUE" in payload["error"]["message"]


# ---------------------------------------------------------------------------
# 11. --param duplicate key
# ---------------------------------------------------------------------------


def test_param_duplicate_key(
    monkeypatch, tmp_path, stub_pipeline, capsys,
):
    spec_path = tmp_path / "task.yaml"
    spec_path.write_text(
        "parameters:\n  - name: x\nprompt: '{{ x }}'\n",
        encoding="utf-8",
    )
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(
        spec_path=str(spec_path),
        output_format="json",
        params=["x=1", "x=2"],
    )
    rc = run._execute_with_args(args)
    assert rc == run.EXIT_INVALID_USAGE
    payload = json.loads(capsys.readouterr().out)
    assert "supplied multiple times" in payload["error"]["message"]


# ---------------------------------------------------------------------------
# 12. instructions flows to project_instructions
# ---------------------------------------------------------------------------


def test_instructions_flows_to_project_instructions(
    monkeypatch, tmp_path, stub_pipeline,
):
    captured, _ = stub_pipeline
    spec_path = tmp_path / "task.yaml"
    spec_path.write_text(
        "parameters:\n  - name: who\n    required: true\n"
        "instructions: 'You greet {{ who }}.'\n"
        "prompt: 'Say hello to {{ who }}.'\n",
        encoding="utf-8",
    )
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(
        spec_path=str(spec_path),
        output_format="text",
        params=["who=Bo"],
    )
    rc = run._execute_with_args(args)
    assert rc == 0
    kwargs = captured["factory_kwargs"]
    assert kwargs.get("project_instructions") == "You greet Bo."


# ---------------------------------------------------------------------------
# 13. required + default mutually exclusive
# ---------------------------------------------------------------------------


def test_required_and_default_mutually_exclusive(
    monkeypatch, tmp_path, stub_pipeline, capsys,
):
    spec_path = tmp_path / "task.yaml"
    spec_path.write_text(
        "parameters:\n"
        "  - name: depth\n    required: true\n    default: shallow\n"
        "prompt: '{{ depth }}'\n",
        encoding="utf-8",
    )
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(spec_path=str(spec_path), output_format="json")
    rc = run._execute_with_args(args)
    assert rc == run.EXIT_INVALID_USAGE
    payload = json.loads(capsys.readouterr().out)
    assert "depth" in payload["error"]["message"]


# ---------------------------------------------------------------------------
# 14. default not in choices
# ---------------------------------------------------------------------------


def test_default_not_in_choices(
    monkeypatch, tmp_path, stub_pipeline, capsys,
):
    spec_path = tmp_path / "task.yaml"
    spec_path.write_text(
        "parameters:\n"
        "  - name: depth\n    default: c\n    choices: [a, b]\n"
        "prompt: '{{ depth }}'\n",
        encoding="utf-8",
    )
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(spec_path=str(spec_path), output_format="json")
    rc = run._execute_with_args(args)
    assert rc == run.EXIT_INVALID_USAGE
    payload = json.loads(capsys.readouterr().out)
    msg = payload["error"]["message"]
    assert "'c'" in msg
    assert "a" in msg and "b" in msg


# ---------------------------------------------------------------------------
# 15. Non-identifier parameter names (spec-side) + CLI-side identifier check
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_name", ["", " x ", "pr-number", "1foo", "foo bar"])
def test_non_identifier_parameter_name_spec_side(
    monkeypatch, tmp_path, stub_pipeline, capsys, bad_name,
):
    import yaml

    spec_path = tmp_path / "task.yaml"
    # ``yaml.safe_dump`` round-trips arbitrary strings (including those
    # containing apostrophes) using YAML's own quoting rules — safer
    # than ``{bad_name!r}`` which can flip to a double-quoted Python
    # repr whose escape rules diverge from YAML.
    spec_path.write_text(
        yaml.safe_dump(
            {"parameters": [{"name": bad_name}], "prompt": "hi"},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(spec_path=str(spec_path), output_format="json")
    rc = run._execute_with_args(args)
    assert rc == run.EXIT_INVALID_USAGE


def test_non_identifier_param_key_cli_side(
    monkeypatch, tmp_path, stub_pipeline, capsys,
):
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(
        prompt="hi",
        output_format="json",
        params=["foo-bar=v"],
    )
    rc = run._execute_with_args(args)
    assert rc == run.EXIT_INVALID_USAGE
    payload = json.loads(capsys.readouterr().out)
    msg = payload["error"]["message"]
    assert "foo-bar" in msg
    assert "identifier" in msg


# ---------------------------------------------------------------------------
# Review fixes — regression tests for the post-code-review pass
# ---------------------------------------------------------------------------


def test_empty_rendered_instructions_does_not_override_agentao_md(
    monkeypatch, tmp_path, stub_pipeline,
):
    """An ``instructions`` template that resolves to ``""`` must NOT be
    plumbed into the factory — the agent treats any non-None value as
    authoritative and would silently suppress AGENTAO.md.
    """
    captured, _ = stub_pipeline
    spec_path = tmp_path / "task.yaml"
    spec_path.write_text(
        "parameters:\n  - name: extra\n    default: ''\n"
        "instructions: '{{ extra }}'\n"
        "prompt: 'go'\n",
        encoding="utf-8",
    )
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(spec_path=str(spec_path), output_format="text")
    rc = run._execute_with_args(args)
    assert rc == 0
    assert "project_instructions" not in captured["factory_kwargs"]


def test_whitespace_only_rendered_instructions_does_not_override_agentao_md(
    monkeypatch, tmp_path, stub_pipeline,
):
    """YAML block-scalar instructions that render to whitespace-only
    output (e.g. ``"\\n"`` from ``keep_trailing_newline=True``) must
    also fall back to AGENTAO.md — the empty-string guard alone misses
    this case.
    """
    captured, _ = stub_pipeline
    spec_path = tmp_path / "task.yaml"
    spec_path.write_text(
        "parameters:\n  - name: extra\n    default: ''\n"
        "instructions: |\n  {{ extra }}\n"
        "prompt: 'go'\n",
        encoding="utf-8",
    )
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(spec_path=str(spec_path), output_format="text")
    rc = run._execute_with_args(args)
    assert rc == 0
    assert "project_instructions" not in captured["factory_kwargs"]


def test_prompt_rendered_to_empty_emits_distinct_diagnostic(
    monkeypatch, tmp_path, stub_pipeline, capsys,
):
    """A template that renders to ``""`` must produce a message pointing
    at the --param value, not the misleading 'prompt is required'.
    """
    spec_path = tmp_path / "task.yaml"
    spec_path.write_text(
        "parameters:\n  - name: who\nprompt: '{{ who }}'\n",
        encoding="utf-8",
    )
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(
        spec_path=str(spec_path),
        output_format="json",
        params=["who="],
    )
    rc = run._execute_with_args(args)
    assert rc == run.EXIT_INVALID_USAGE
    msg = json.loads(capsys.readouterr().out)["error"]["message"]
    assert "rendered to empty" in msg
    assert "--param" in msg


def test_sandbox_blocks_attribute_escape(
    monkeypatch, tmp_path, stub_pipeline, capsys,
):
    """A run spec from a shared/untrusted recipe must not reach Python
    internals via Jinja attribute access — the sandboxed environment
    should refuse and surface as ``invalid_spec`` (exit 2).
    """
    spec_path = tmp_path / "task.yaml"
    # Classic Jinja-sandbox bypass attempt: walk from a string subclass
    # to ``os.popen`` through ``__class__.__mro__``. SandboxedEnvironment
    # blocks the underscore-prefixed attribute access.
    spec_path.write_text(
        "parameters:\n  - name: who\n    default: x\n"
        "prompt: \"{{ ''.__class__.__mro__ }}\"\n",
        encoding="utf-8",
    )
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(spec_path=str(spec_path), output_format="json")
    rc = run._execute_with_args(args)
    assert rc == run.EXIT_INVALID_USAGE
    msg = json.loads(capsys.readouterr().out)["error"]["message"]
    assert "sandbox" in msg


@pytest.mark.parametrize(
    "expression",
    [
        "{{ 1 / 0 }}",            # ZeroDivisionError
        "{{ 'x' + 1 }}",          # TypeError
        "{% include 'nope.j2' %}",  # TemplateNotFound (no loader)
    ],
)
def test_runtime_template_errors_map_to_invalid_spec(
    monkeypatch, tmp_path, stub_pipeline, capsys, expression,
):
    """Runtime exceptions raised by ``template.render()`` must hit the
    invalid_spec exit-2 path, not crash the CLI with a Python traceback.
    """
    spec_path = tmp_path / "task.yaml"
    spec_path.write_text(
        "parameters:\n  - name: who\n    default: x\n"
        f"prompt: \"{expression}\"\n",
        encoding="utf-8",
    )
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(spec_path=str(spec_path), output_format="json")
    rc = run._execute_with_args(args)
    assert rc == run.EXIT_INVALID_USAGE
    msg = json.loads(capsys.readouterr().out)["error"]["message"]
    assert "template" in msg


@pytest.mark.parametrize(
    "reserved",
    ["true", "True", "false", "None", "for", "if", "in", "set", "self", "parent"],
)
def test_jinja_reserved_parameter_name_rejected(
    monkeypatch, tmp_path, stub_pipeline, capsys, reserved,
):
    """Jinja constants and keywords must not be accepted as parameter
    names — they either silently win over context variables or cause
    template syntax errors when used inside ``{{ }}``.
    """
    import yaml

    spec_path = tmp_path / "task.yaml"
    spec_path.write_text(
        yaml.safe_dump(
            {"parameters": [{"name": reserved}], "prompt": "hi"},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(spec_path=str(spec_path), output_format="json")
    rc = run._execute_with_args(args)
    assert rc == run.EXIT_INVALID_USAGE
    msg = json.loads(capsys.readouterr().out)["error"]["message"]
    assert "reserved" in msg.lower() or reserved in msg


def test_multiple_unknown_params_aggregated_into_one_error(
    monkeypatch, tmp_path, stub_pipeline, capsys,
):
    """Three --param typos should surface in a single error message, in
    the user's CLI order — not require three CLI round-trips.
    """
    spec_path = tmp_path / "task.yaml"
    spec_path.write_text(
        "parameters:\n  - name: foo\nprompt: '{{ foo }}'\n",
        encoding="utf-8",
    )
    _no_stdin(monkeypatch)
    from agentao.cli import run

    args = _build_args(
        spec_path=str(spec_path),
        output_format="json",
        params=["foo=1", "zoo=2", "apple=3"],
    )
    rc = run._execute_with_args(args)
    assert rc == run.EXIT_INVALID_USAGE
    msg = json.loads(capsys.readouterr().out)["error"]["message"]
    # Both surplus keys present, in the order the user typed them
    # ("zoo" before "apple"); the message also uses the plural form.
    assert "unknown parameters" in msg
    assert msg.index("'zoo'") < msg.index("'apple'")
