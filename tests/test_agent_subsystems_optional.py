"""``Agentao`` accepts ``replay_config`` / ``sandbox_policy`` /
``bg_store`` as opt-in keyword args.

When ``None`` (the default), each subsystem is fully disabled:

- ``bg_store=None`` → ``check_background_agent`` /
  ``cancel_background_agent`` are not registered, ``run_in_background``
  is omitted from sub-agent tool schemas, the chat loop's background
  notification drain short-circuits.
- ``sandbox_policy=None`` → ``ToolRunner`` runs shell commands without
  the macOS sandbox-exec wrapper.
- ``replay_config=None`` → ``Agentao._replay_config`` is the no-op
  default, no ``<wd>/.agentao/replay.json`` is read.

The factory (:func:`agentao.embedding.build_from_environment`) wires
all three up from disk so CLI / ACP behavior is preserved.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentao.agent import Agentao
from agentao.agents.bg_store import BackgroundTaskStore
from agentao.embedding import build_from_environment
from agentao.embedding.factory import discover_llm_kwargs
from agentao.replay import ReplayConfig
from agentao.sandbox import SandboxPolicy


# ---------------------------------------------------------------------------
# Bare construction with all three None → fully disabled
# ---------------------------------------------------------------------------


def _bare(tmp_path: Path) -> Agentao:
    """Bare-construction Agentao with all opt-in subsystems left at None."""
    return Agentao(
        working_directory=tmp_path,
        api_key="k",
        base_url="https://test.local/v1",
        model="m",
    )


class TestDefaultsAreNone:
    def test_bg_store_default_is_none(self, tmp_path):
        agent = _bare(tmp_path)
        assert agent.bg_store is None

    def test_sandbox_policy_default_is_none(self, tmp_path):
        agent = _bare(tmp_path)
        assert agent.sandbox_policy is None

    def test_replay_config_default_is_disabled(self, tmp_path):
        """No disk read; ``ReplayConfig()`` (no-op default) is what's stored."""
        agent = _bare(tmp_path)
        assert isinstance(agent._replay_config, ReplayConfig)
        # No ``<wd>/.agentao/replay.json`` was touched — confirm by
        # asserting the directory wasn't created as a side effect.
        assert not (tmp_path / ".agentao" / "replay.json").exists()


# ---------------------------------------------------------------------------
# bg_store=None → tool surface collapses
# ---------------------------------------------------------------------------


class TestBgStoreNoneRemovesTools:
    def test_check_and_cancel_tools_absent(self, tmp_path):
        agent = _bare(tmp_path)
        names = set(agent.tools.tools.keys())
        assert "check_background_agent" not in names
        assert "cancel_background_agent" not in names

    def test_check_and_cancel_tools_present_when_bg_store_supplied(self, tmp_path):
        agent = Agentao(
            working_directory=tmp_path,
            api_key="k",
            base_url="https://test.local/v1",
            model="m",
            bg_store=BackgroundTaskStore(persistence_dir=tmp_path),
        )
        names = set(agent.tools.tools.keys())
        assert "check_background_agent" in names
        assert "cancel_background_agent" in names

    def test_run_in_background_omitted_from_sub_agent_schema(self, tmp_path):
        """Sub-agent tools must NOT advertise ``run_in_background`` when
        the bg subsystem is disabled — the LLM should not see a phantom
        feature."""
        from agentao.agents.tools import AgentToolWrapper

        wrapper = AgentToolWrapper(
            definition={
                "name": "test-agent",
                "description": "stub",
                "tools": [],
            },
            all_tools={},
            llm_config_getter=discover_llm_kwargs,
            bg_store=None,
        )
        params = wrapper.parameters
        assert set(params["properties"].keys()) == {"task"}, (
            "run_in_background must be absent from the schema when bg_store is None"
        )
        assert params["required"] == ["task"]

    def test_run_in_background_present_when_bg_store_supplied(self, tmp_path):
        from agentao.agents.tools import AgentToolWrapper

        wrapper = AgentToolWrapper(
            definition={
                "name": "test-agent",
                "description": "stub",
                "tools": [],
            },
            all_tools={},
            llm_config_getter=discover_llm_kwargs,
            bg_store=BackgroundTaskStore(persistence_dir=tmp_path),
        )
        params = wrapper.parameters
        assert "run_in_background" in params["properties"]


# ---------------------------------------------------------------------------
# sandbox_policy=None → ToolRunner sees no sandbox shim
# ---------------------------------------------------------------------------


class TestSandboxPolicyNone:
    def test_tool_runner_receives_none(self, tmp_path):
        agent = _bare(tmp_path)
        assert agent.tool_runner._sandbox_policy is None


# ---------------------------------------------------------------------------
# Chat loop's background drain short-circuits when bg_store is None
# ---------------------------------------------------------------------------


def test_chat_loop_inject_background_notifications_handles_none(tmp_path):
    """The chat loop must not crash when ``agent.bg_store is None``."""
    agent = _bare(tmp_path)
    # Use the real ChatLoopRunner; its method takes the messages list
    # and a system prompt and returns the (possibly augmented) messages.
    runner = agent._chat_loop_runner if hasattr(agent, "_chat_loop_runner") else None
    if runner is None:
        # ChatLoopRunner is constructed lazily on the first chat() call;
        # build one directly to test the drain path.
        from agentao.runtime.chat_loop import ChatLoopRunner

        runner = ChatLoopRunner(agent)

    msgs = [{"role": "user", "content": "hi"}]
    # The drain method is private but stable; testing through chat()
    # would require a live LLM, so probe the helper directly.
    out = runner._inject_background_notifications(msgs, system_prompt="")
    assert out is msgs  # untouched — no notifications drained


# ---------------------------------------------------------------------------
# Factory wires up all three to CLI defaults
# ---------------------------------------------------------------------------


@pytest.fixture
def _stub_factory_env(monkeypatch):
    """Defang the factory's ``.env`` auto-discovery and pin the LLM
    provider/credentials so the test sees a deterministic env."""
    monkeypatch.setattr(
        "agentao.embedding.factory.load_dotenv", lambda *a, **kw: False
    )
    monkeypatch.setenv("LLM_PROVIDER", "OPENAI")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://test.local/v1")
    monkeypatch.setenv("OPENAI_MODEL", "m")


class TestFactoryDefaults:
    def test_factory_supplies_bg_store(self, tmp_path, _stub_factory_env):
        agent = build_from_environment(working_directory=tmp_path)
        assert agent.bg_store is not None
        assert isinstance(agent.bg_store, BackgroundTaskStore)

    def test_factory_supplies_sandbox_policy(self, tmp_path, _stub_factory_env):
        agent = build_from_environment(working_directory=tmp_path)
        assert agent.sandbox_policy is not None
        assert isinstance(agent.sandbox_policy, SandboxPolicy)

    def test_factory_caller_can_disable(self, tmp_path, _stub_factory_env):
        """Embedded host explicitly passing ``bg_store=None`` to the
        factory must survive — the override wins over the CLI default."""
        agent = build_from_environment(
            working_directory=tmp_path,
            bg_store=None,
            sandbox_policy=None,
            replay_config=None,
        )
        assert agent.bg_store is None
        assert agent.sandbox_policy is None
        # No bg-related tools advertised through the factory either.
        assert "check_background_agent" not in agent.tools.tools
