"""A duck-typed agent for driving ``CompactionCoordinator`` directly."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List
from unittest.mock import Mock

from agentao.compaction.coordinator import CompactionCoordinator


def make_coordinator_agent(cm, messages):
    """Return ``(agent, events)``: an agent over ``cm`` and the events its transport emits."""
    events: List[Any] = []
    agent = SimpleNamespace(
        messages=messages,
        context_manager=cm,
        transport=SimpleNamespace(emit=events.append),
        llm=SimpleNamespace(logger=Mock()),
        _plugin_hook_rules=[],
        _last_session_summary_id=None,
        _turn_finish_reason_missing=False,
        _build_system_prompt=lambda: "sys",
        memory_manager=None,
    )
    agent.compaction_coordinator = CompactionCoordinator(agent)
    return agent, events
