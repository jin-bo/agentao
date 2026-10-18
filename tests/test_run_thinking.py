"""Thinking depth as a first-class setting (#550).

``agentao/llm/thinking.py`` writes one level where each wire reads it, and
``agentao run`` takes it as ``thinking:`` / ``--thinking`` and echoes the
level the requests carried. Before, a run could only reach it through
``LLM_EXTRA_BODY``, as ``reasoning_effort``, which two of the three wires
reject on every request.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agentao.llm.thinking import clear_thinking, current_thinking, set_thinking
from tests.test_run_subcommand import _build_args, _no_stdin, stub_pipeline  # noqa: F401 — pytest fixture injection

# Agentao here writes to the process cwd: see ``isolated_cwd`` in conftest.py.
pytestmark = pytest.mark.usefixtures("isolated_cwd")


def _llm(api_format: str, extra_body=None, capabilities=None) -> SimpleNamespace:
    return SimpleNamespace(
        api_format=api_format, extra_body=extra_body, model_capabilities=capabilities,
    )


# -- the helper, per wire -----------------------------------------------------


def test_chat_completions_reads_reasoning_effort():
    llm = _llm("openai-completions")
    assert set_thinking(llm, "HIGH") == "high"
    assert llm.extra_body == {"reasoning_effort": "high"}
    assert current_thinking(llm) == (True, "high")
    assert clear_thinking(llm) is True
    assert llm.extra_body == {}
    assert clear_thinking(llm) is False


def test_a_non_standard_level_is_kept_as_written():
    """The scale is the model's, and a case-sensitive one must not be mangled."""
    llm = _llm("openai-completions")
    assert set_thinking(llm, "XHigh") == "XHigh"


def test_anthropic_reads_output_config_effort_and_drops_a_carried_reasoning_effort():
    llm = _llm("anthropic-messages", {"reasoning_effort": "high", "output_config": {"x": 1}})
    assert set_thinking(llm, "max") == "max"
    assert llm.extra_body == {"output_config": {"x": 1, "effort": "max"}}
    assert clear_thinking(llm) is True
    assert llm.extra_body == {"output_config": {"x": 1}}


def test_anthropic_refuses_a_level_the_model_does_not_list():
    llm = _llm("anthropic-messages", {}, {"effort": {"low": {"supported": True}}})
    with pytest.raises(ValueError, match="low"):
        set_thinking(llm, "high")
    with pytest.raises(ValueError):
        set_thinking(_llm("anthropic-messages", {}), "minimal")


def test_responses_reads_reasoning_effort_inside_reasoning():
    llm = _llm("openai-responses", {"reasoning_effort": "low", "reasoning": {"summary": "auto"}})
    assert set_thinking(llm, "medium") == "medium"
    assert llm.extra_body == {"reasoning": {"summary": "auto", "effort": "medium"}}
    assert clear_thinking(llm) is True
    assert llm.extra_body == {"reasoning": {"summary": "auto"}}


def test_responses_replaces_a_reasoning_that_is_not_an_object():
    llm = _llm("openai-responses", {"reasoning": "high"})
    assert current_thinking(llm) == (True, "high")
    set_thinking(llm, "low")
    assert llm.extra_body == {"reasoning": {"effort": "low"}}


@pytest.mark.parametrize("level", ["high please", "", "off"])
def test_what_is_not_one_level_is_refused(level):
    with pytest.raises(ValueError):
        set_thinking(_llm("openai-completions"), level)


def test_a_client_without_extra_body_has_nowhere_to_write():
    with pytest.raises(ValueError, match="extra_body"):
        set_thinking(SimpleNamespace(api_format="openai-completions"), "high")
    assert current_thinking(SimpleNamespace()) == (False, None)


# -- agentao run --------------------------------------------------------------


def _run(monkeypatch, tmp_path, spec: dict, *, extra_body=None, api_format=None, **args):
    """Run a real ``Agentao`` through the pipeline, with the LLM call stubbed."""
    import agentao.embedding as embedding
    from agentao.cli import run

    spec_path = tmp_path / "task.json"
    spec_path.write_text(json.dumps({"prompt": "hi", **spec}), encoding="utf-8")
    built = {}
    real_build = embedding.build_from_environment

    def _build(**kwargs):
        resolved = {"api_key": "k", "base_url": "https://example.test/v1", "model": "m"}
        if api_format is not None:
            resolved["api_format"] = api_format
        if extra_body is not None:
            kwargs["extra_body"] = dict(extra_body)
        agent = real_build(resolved_llm=resolved, **kwargs)

        def _call(messages, tools, token):
            built["extra_body"] = dict(agent.llm.extra_body or {})
            message = SimpleNamespace(content="done", tool_calls=None, reasoning_content=None)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=message, finish_reason="stop")],
                usage=None, model="m",
            )

        agent._llm_call = _call
        return agent

    monkeypatch.setattr("agentao.embedding.build_from_environment", _build)
    monkeypatch.setattr(
        "agentao.cli.subcommands._load_and_register_plugins", lambda agent: None,
    )
    _no_stdin(monkeypatch)
    rc = run._execute_with_args(_build_args(spec_path=str(spec_path), output_format="json", **args))
    return rc, built


def test_the_spec_sets_it_and_the_result_says_so(monkeypatch, tmp_path, capsys):
    rc, built = _run(monkeypatch, tmp_path, {"thinking": "high"})
    assert rc == 0
    assert built["extra_body"] == {"reasoning_effort": "high"}
    assert json.loads(capsys.readouterr().out)["thinking"] == "high"


def test_the_flag_wins_over_the_spec(monkeypatch, tmp_path, capsys):
    from agentao.cli import run

    spec = run.RunSpec(prompt="hi", thinking="high")
    args = _build_args()
    args.thinking = "low"
    assert run._apply_cli_overrides(spec, args).thinking == "low"


def test_the_anthropic_wire_gets_output_config_effort(monkeypatch, tmp_path, capsys):
    rc, built = _run(monkeypatch, tmp_path, {"thinking": "max"}, api_format="anthropic-messages")
    assert rc == 0
    assert built["extra_body"] == {"output_config": {"effort": "max"}}
    assert json.loads(capsys.readouterr().out)["thinking"] == "max"


def test_off_clears_what_llm_extra_body_set(monkeypatch, tmp_path, capsys):
    rc, built = _run(
        monkeypatch, tmp_path, {"thinking": "off"}, extra_body={"reasoning_effort": "low"},
    )
    assert rc == 0
    assert "reasoning_effort" not in built["extra_body"]
    # ``None`` fields are left out of the envelope.
    assert "thinking" not in json.loads(capsys.readouterr().out)


def test_the_result_echoes_a_level_from_llm_extra_body(monkeypatch, tmp_path, capsys):
    rc, _ = _run(monkeypatch, tmp_path, {}, extra_body={"reasoning_effort": "medium"})
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["thinking"] == "medium"


def test_a_level_the_wire_refuses_is_invalid_usage(monkeypatch, tmp_path, capsys):
    from agentao.cli import run

    rc, built = _run(monkeypatch, tmp_path, {"thinking": "minimal"}, api_format="anthropic-messages")
    assert rc == run.EXIT_INVALID_USAGE
    assert built == {}  # nothing was sent
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["type"] == "invalid_spec"
    assert "thinking" in error["message"]


def test_yaml_off_is_off_not_false():
    """YAML 1.1 reads a bare ``off`` as ``False``."""
    import yaml

    from agentao.cli.run_models import RunSpec

    spec = RunSpec.model_validate(yaml.safe_load("prompt: hi\nthinking: off\n"))
    assert spec.thinking == "off"


def test_a_multi_word_level_is_refused_before_the_agent_is_built(
    monkeypatch, tmp_path, capsys,
):
    from agentao.cli import run

    built = []
    monkeypatch.setattr(
        "agentao.embedding.build_from_environment",
        lambda **kwargs: built.append(kwargs),
    )
    _no_stdin(monkeypatch)
    args = _build_args(prompt="hi", output_format="json")
    args.thinking = "high please"
    assert run._execute_with_args(args) == run.EXIT_INVALID_USAGE
    assert built == []
    assert json.loads(capsys.readouterr().out)["error"]["type"] == "invalid_spec"


def test_slash_thinking_reports_an_extra_body_it_cannot_write(capsys):
    from collections import UserDict

    from agentao.cli.commands.provider import handle_thinking_command

    llm = SimpleNamespace(api_format="openai-completions", extra_body=UserDict())
    handle_thinking_command(SimpleNamespace(agent=SimpleNamespace(llm=llm)), "high")
    assert dict(llm.extra_body) == {}


@pytest.mark.usefixtures("stub_pipeline")
def test_a_client_with_nowhere_to_write_a_level_is_a_runtime_error(monkeypatch, capsys):
    """The stub agent's client has no ``extra_body``. That is the runtime's
    problem, not the spec's, so it is exit 1 and not exit 2."""
    from agentao.cli import run

    _no_stdin(monkeypatch)
    args = _build_args(prompt="hi", output_format="json")
    args.thinking = "high"
    assert run._execute_with_args(args) == run.EXIT_RUNTIME_ERROR
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["type"] == "runtime_error"
    assert "extra_body" in error["message"]


def test_off_on_an_extra_body_it_cannot_edit_is_a_runtime_error():
    """``off`` there would exit 0 with the level still sent."""
    from collections import UserDict

    from agentao.cli.run import _apply_thinking
    from agentao.llm.thinking import ThinkingUnsupported

    llm = SimpleNamespace(
        api_format="openai-completions", extra_body=UserDict(reasoning_effort="high"),
    )
    with pytest.raises(ThinkingUnsupported):
        _apply_thinking(llm, "off")


@pytest.mark.parametrize("value", [True, 1, 0.5])
def test_true_and_numbers_are_not_levels(value):
    """Only ``False`` (YAML's bare ``off`` / ``false`` / ``no``) means ``off``."""
    from pydantic import ValidationError

    from agentao.cli.run_models import RunSpec

    with pytest.raises(ValidationError):
        RunSpec(thinking=value)


def test_a_quoted_false_stays_a_level():
    from agentao.cli.run_models import RunSpec

    assert RunSpec(thinking="false").thinking == "false"
