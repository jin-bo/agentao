"""Agent construction shared by chat-loop regression tests.

Call under the ``isolated_cwd`` fixture: runtime state is written to cwd.
"""

from pathlib import Path

from agentao import Agentao


def make_chat_agent() -> Agentao:
    return Agentao(
        api_key="test-key",
        base_url="https://example.test/v1",
        model="test-model",
        working_directory=Path.cwd(),
    )
