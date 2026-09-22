"""Recommendation stays advisory, request-only and scoped to a single turn."""
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from agentao import Agentao

pytestmark = pytest.mark.usefixtures("isolated_cwd")


def _response(content, calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=content, tool_calls=calls, reasoning_content=None),
        finish_reason="stop")], usage=None, model="test-model")


def _agent():
    return Agentao(api_key="dummy", base_url="https://example.test/v1", model="test",
                   working_directory=Path.cwd())


def _recommender():
    from agentao.recommendations import JevConfig, JevSkillRecommender

    requests = []

    def handler(req):
        payload = json.loads(req.content)
        requests.append(payload)
        choices = payload["questions"]["skill"]["criteria"]
        selected = next(k for k in choices if k != "none")
        return httpx.Response(200, json={"model": "jev-1.13.0", "answers": {"skill": {
            "type": "choice", "choice": selected, "confidence": .99,
            "probabilities": {k: float(k == selected) for k in choices},
        }}, "usage": {"input_tokens": 20, "output_tokens": 5}})

    return JevSkillRecommender(JevConfig(enabled=True), "jev-test",
                               transport=httpx.MockTransport(handler)), requests


def _skills(agent):
    agent.skill_manager.available_skills = {
        "useful-skill": {"name": "useful-skill", "description": "Help with this task", "full_content": "Useful instructions"},
        "disabled-skill": {"name": "disabled-skill", "description": "Disabled", "full_content": "Disabled instructions"},
    }
    agent.skill_manager.disabled_skills = {"disabled-skill"}


def test_injected_service_runs_once_per_turn_and_never_activates():
    service, jev_requests = _recommender()
    agent = Agentao(api_key="dummy", base_url="https://example.test/v1", model="test",
                     working_directory=Path.cwd(), skill_recommender=service)
    _skills(agent)
    sent = []
    todo = SimpleNamespace(id="todo1", type="function", function=SimpleNamespace(
        name="todo_write", arguments=json.dumps({"todos": [{"content": "step", "status": "pending"}]})))
    responses = iter([_response(None, [todo]), _response("done"), _response("second")])

    def call(messages, tools, token):
        sent.append(list(messages))
        return next(responses)

    agent._llm_call = call
    try:
        assert agent.chat("Help with the task") == "done"
        assert len(jev_requests) == 2
        assert len(sent) == 2
        for request in sent:
            assert "<skill-recommendation>" in request[-1]["content"]
            assert "<skill-recommendation>" not in request[0]["content"]
        assert not any("<skill-recommendation>" in str(m) for m in agent.messages)
        assert agent.skill_manager.get_active_skills() == {}
        assert "disabled-skill" not in json.dumps(jev_requests)
        assert agent.chat("Continue with the next task") == "second"
        assert len(jev_requests) == 4
    finally:
        agent.close()


def test_disabling_between_turns_removes_stale_suggestion():
    from dataclasses import replace

    service, jev_requests = _recommender()
    agent = _agent()
    agent.skill_recommender = service
    _skills(agent)
    sent = []
    agent._llm_call = lambda messages, *_: (sent.append(list(messages)) or _response("ok"))
    try:
        agent.chat("Help with the task")
        service.config = replace(service.config, enabled=False)
        agent.chat("A different task")
        assert len(jev_requests) == 2
        assert not any("<skill-recommendation>" in str(m) for m in sent[-1])
    finally:
        agent.close()


def test_no_activate_skill_tool_means_no_recommendation():
    service, jev_requests = _recommender()
    agent = Agentao(api_key="dummy", base_url="https://example.test/v1", model="test",
                     working_directory=Path.cwd(), disable_tools=["activate_skill"],
                     skill_recommender=service)
    _skills(agent)
    agent._llm_call = lambda *_: _response("ok")
    try:
        assert agent.chat("Help with the task") == "ok"
        assert jev_requests == []
    finally:
        agent.close()


def test_explicit_disabled_skill_does_not_get_replaced():
    service, jev_requests = _recommender()
    agent = _agent()
    agent.skill_recommender = service
    _skills(agent)
    agent._llm_call = lambda *_: _response("ok")
    try:
        agent.chat("Use $disabled-skill")
        assert jev_requests == []
    finally:
        agent.close()


def test_factory_keeps_dotenv_keys_project_local(tmp_path, monkeypatch):
    from agentao.embedding import build_from_environment

    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setattr("agentao.paths.user_root", lambda: tmp_path / "user")
    for name in ("first", "second"):
        project = tmp_path / name
        project.mkdir()
        (project / ".env").write_text(f"TYPESAFE_API_KEY={name}-key\n")
        with_agent = build_from_environment(working_directory=project)
        try:
            assert with_agent.skill_recommender.api_key == f"{name}-key"
        finally:
            with_agent.close()


def test_core_does_not_discover_jev_and_child_env_scrubs_key(monkeypatch):
    from agentao.capabilities.process import build_child_env

    monkeypatch.setenv("TYPESAFE_API_KEY", "never-inherit-this")
    agent = _agent()
    try:
        assert agent.skill_recommender is None
        assert "TYPESAFE_API_KEY" not in build_child_env()
    finally:
        agent.close()
