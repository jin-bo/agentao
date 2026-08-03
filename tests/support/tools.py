"""Shared doubles for tool-registration tests.

The three tool-wiring suites (``test_host_tool_injection``,
``test_host_tool_allowlist``, ``test_runtime_tool_injection``) each need
the same two things: a concrete :class:`Tool` whose name is decided at
construction, and an ``Agentao`` built with throwaway LLM config so
construction touches no network.

Distinct from ``support/tool_calls.py``, which fabricates the OpenAI SDK's
*wire* ``tool_call`` shape. This module is about registrable tool objects.
"""

from __future__ import annotations

from typing import Any, Dict

from agentao import Agentao
from agentao.host import Tool


class NamedTool(Tool):
    """Minimal concrete Tool with a configurable name + marker description."""

    def __init__(self, name: str, description: str = "marker") -> None:
        self._name = name
        self._description = description

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    def execute(self, **kwargs: Any) -> str:
        return "ran"


def make_dummy_agent(tmp_path: Any, **kwargs: Any) -> Agentao:
    """Construct an Agentao with dummy LLM config (no network at init).

    ``base_url`` points at port 0 deliberately: if anything in
    construction were to actually dial the provider, it fails immediately
    and loudly rather than hanging or reaching a real endpoint.
    """
    return Agentao(
        working_directory=tmp_path,
        api_key="x",
        base_url="http://localhost:0",
        model="dummy",
        **kwargs,
    )
