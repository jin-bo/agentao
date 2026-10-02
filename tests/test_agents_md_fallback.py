"""``AGENTS.md`` is read only when a project has no ``AGENTAO.md``."""

from __future__ import annotations

from unittest.mock import Mock, patch

from agentao.prompts import load_project_instructions


def test_agents_md_is_read_when_agentao_md_is_absent(tmp_path):
    (tmp_path / "AGENTS.md").write_text("use tabs", encoding="utf-8")
    assert load_project_instructions(tmp_path) == "use tabs"


def test_agentao_md_wins_and_the_two_are_not_merged(tmp_path):
    (tmp_path / "AGENTAO.md").write_text("agentao rules", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("generic rules", encoding="utf-8")
    assert load_project_instructions(tmp_path) == "agentao rules"


def test_neither_file_gives_none(tmp_path):
    assert load_project_instructions(tmp_path) is None


def test_frontmatter_is_stripped_from_agents_md(tmp_path):
    (tmp_path / "AGENTS.md").write_text(
        "---\ndescription: x\n---\nbody\n", encoding="utf-8",
    )
    assert load_project_instructions(tmp_path) == "body\n"


def test_unreadable_agentao_md_does_not_fall_through(tmp_path):
    """The project meant AGENTAO.md; reading AGENTS.md instead would be a
    silent swap of instructions."""
    (tmp_path / "AGENTAO.md").mkdir()  # exists, cannot be read as text
    (tmp_path / "AGENTS.md").write_text("generic rules", encoding="utf-8")
    logger = Mock()

    assert load_project_instructions(tmp_path, logger) is None
    (message,), _ = logger.warning.call_args
    assert "AGENTAO.md" in message


def test_the_loaded_file_is_named_in_the_log(tmp_path):
    (tmp_path / "AGENTS.md").write_text("x", encoding="utf-8")
    logger = Mock()
    load_project_instructions(tmp_path, logger)
    (message,), _ = logger.info.call_args
    assert message.endswith("AGENTS.md")


def _agent(tmp_path, **kwargs):
    with patch("agentao.agent.LLMClient") as mock_llm_cls, \
         patch("agentao.tooling.mcp_tools.load_mcp_config", return_value={}), \
         patch("agentao.tooling.mcp_tools.McpClientManager"):
        mock_llm_cls.return_value.logger = Mock()
        mock_llm_cls.return_value.model = "gpt-test"
        from agentao.agent import Agentao

        return Agentao(working_directory=tmp_path, **kwargs)


def test_agents_md_reaches_the_system_prompt(tmp_path):
    (tmp_path / "AGENTS.md").write_text("Run make lint before committing.", encoding="utf-8")
    agent = _agent(tmp_path)
    prompt = agent._build_system_prompt()
    assert "=== Project Instructions ===" in prompt
    assert "Run make lint before committing." in prompt


def test_host_project_instructions_still_override_agents_md(tmp_path):
    (tmp_path / "AGENTS.md").write_text("from disk", encoding="utf-8")
    agent = _agent(tmp_path, project_instructions="HOST OVERRIDE")
    assert agent.project_instructions == "HOST OVERRIDE"


def test_empty_project_instructions_reads_neither_file(tmp_path):
    """The documented opt-out for hosts: an empty string, not None."""
    (tmp_path / "AGENTAO.md").write_text("agentao rules", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("generic rules", encoding="utf-8")
    agent = _agent(tmp_path, project_instructions="")
    prompt = agent._build_system_prompt()
    assert "=== Project Instructions ===" not in prompt
    assert "agentao rules" not in prompt and "generic rules" not in prompt
