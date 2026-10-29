"""Shared scaffolding for the MCP Skills suite (``tests/test_mcp_skills_*.py``).

Plain helpers, per ``tests/support/README.md``: each test module wraps
:func:`docs_server` in its own ``docs`` fixture.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
import logging
from typing import Any, Dict, List

import pytest

from agentao.mcp._compat import SUPPORTS_MODERN_ERA
from agentao.mcp.client import McpClientManager
from tests.support.skills_mcp_server import (
    SkillsServer,
    pdf_skill,
)
from tests.support.tool_calls import make_tool_call


modern = pytest.mark.skipif(
    not SUPPORTS_MODERN_ERA, reason="the Skills extension needs the modern protocol era (mcp 2.x)"
)

PDF = "mcp:docs:skill://pdf-processing/SKILL.md"


class Prompts:
    """A confirm callback that records every prompt and answers ``answer``."""

    def __init__(self, answer: bool = True):
        self.answer = answer
        self.seen: List[tuple] = []

    def __call__(self, name: str, description: str, args: Dict[str, Any]) -> bool:
        self.seen.append((name, description, args))
        return self.answer


def _manager(configs) -> McpClientManager:
    manager = McpClientManager(configs)
    manager.connect_all()
    return manager


def _agent(tmp_path, manager, *, prompts=None, rules=None, **kwargs):
    from agentao.agent import Agentao
    from agentao.permissions import PermissionEngine
    from agentao.transport.sdk import SdkTransport

    wd = tmp_path / "wd"
    wd.mkdir(exist_ok=True)
    engine = PermissionEngine(project_root=wd, rules=rules or [])
    return Agentao(
        working_directory=wd,
        api_key="k",
        base_url="https://test.local/v1",
        model="m",
        logger=logging.getLogger("test.mcp_skills"),
        mcp_manager=manager,
        permission_engine=engine,
        transport=SdkTransport(confirm_tool=prompts or Prompts()),
        **kwargs,
    )


def _call(agent, name: str, **args) -> str:
    """Run one tool call through the agent's planner, confirm phase and executor."""
    _, messages = agent.tool_runner.execute([make_tool_call("c1", name, json.dumps(args))])
    return messages[-1]["content"]


def _decide(agent, name: str, **args):
    tool = agent.tools.get(name)
    return agent.tool_runner._planner._decide(tool, name, args, readonly_mode=False)


def _serve(server, entry, files):
    server.update(pages=[[entry]], files=files)


def _loaded(tmp_path, manager, **kwargs):
    agent = _agent(tmp_path, manager, **kwargs)
    result = _call(agent, "activate_skill", skill_name=PDF, task_description="t")
    assert "<mcp-skill" in result
    return agent


def _prep(to_summarize):
    from agentao.context_manager import PreparedCompaction

    return PreparedCompaction(
        trigger="t", kind="full", reason="r", is_auto=True, split_index=len(to_summarize),
        to_summarize=to_summarize, to_keep=[], pinned=[], recently_read=[],
        summary_messages=to_summarize, summary_input="", pre_tokens=1,
    )


def _sync(agent) -> List[str]:
    """Append what the runner's skill-state sync would, and return it."""
    notes = agent.skill_manager.skill_state_messages(agent.messages)
    agent.messages.extend({"role": "user", "content": n} for n in notes)
    return notes


#: How the held block opens, in the tool result and in the tail.
BLOCK_OPEN = '<mcp-skill server="docs" uri="skill://pdf-processing/SKILL.md">'


@contextmanager
def docs_server(tmp_path):
    """A connected Skills server ``docs`` serving the spec's pdf-processing skill."""
    entry, files = pdf_skill()
    server = SkillsServer(tmp_path / "docs", pages=[[entry]], files=files)
    manager = _manager({"docs": server.config()})
    try:
        yield server, manager
    finally:
        manager.disconnect_all(timeout=0)
