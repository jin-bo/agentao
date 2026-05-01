"""Offline smoke for the slack-bot example.

Avoids slack-bolt's HTTP stack — exercises ``handle_mention`` directly
with a recording ``say`` callable and a fake LLM. Channel-scoped
permission selection is verified independently.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from agentao.permissions import PermissionMode

from src.bot import (
    WRITE_ALLOWLIST_CHANNELS,
    handle_mention,
    make_permission_engine_for_channel,
)


def _fake_llm():
    fake = MagicMock(name="FakeLLM")
    fake.logger = MagicMock()
    fake.model = "fake-model"
    fake.api_key = "fake-key"
    fake.base_url = "http://localhost:1"
    fake.temperature = 0.0
    fake.max_tokens = 256
    fake.total_prompt_tokens = 0
    fake.total_completion_tokens = 0
    return fake


def _fake_response(content: str = "slack reply"):
    response = MagicMock()
    response.choices[0].message.tool_calls = None
    response.choices[0].message.content = content
    response.choices[0].message.reasoning_content = None
    return response


def test_permission_engine_for_writeable_channel(tmp_path) -> None:
    """A channel on the write allowlist gets workspace-write."""
    a_channel = next(iter(WRITE_ALLOWLIST_CHANNELS))
    engine = make_permission_engine_for_channel(a_channel, project_root=tmp_path)
    assert engine.active_mode == PermissionMode.WORKSPACE_WRITE


def test_permission_engine_for_random_channel_is_read_only(tmp_path) -> None:
    engine = make_permission_engine_for_channel("C_RANDOM_PUBLIC", project_root=tmp_path)
    assert engine.active_mode == PermissionMode.READ_ONLY


def test_handle_mention_calls_say_with_reply() -> None:
    """A mention runs one turn and posts the reply via ``say``."""
    posted: list[dict] = []

    async def fake_say(*, text, thread_ts=None):  # noqa: ANN001
        posted.append({"text": text, "thread_ts": thread_ts})

    with patch(
        "agentao.agent.Agentao._llm_call",
        lambda self, msgs, tools, token: _fake_response(),
    ):
        result = asyncio.run(
            handle_mention(
                text="@bot summarise this",
                channel_id="C_RANDOM_PUBLIC",
                say=fake_say,
                thread_ts="1700000000.000100",
                llm_client_factory=_fake_llm,
            )
        )

    assert result == "slack reply"
    assert posted == [
        {"text": "slack reply", "thread_ts": "1700000000.000100"},
    ]


def test_handle_mention_uses_channel_specific_permission_mode() -> None:
    """The engine the agent gets reflects the channel's policy.

    We capture the constructed agent's ``permission_engine`` and
    assert its mode matches the allowlist outcome.
    """
    captured: list = []
    real_init = None

    posted: list[dict] = []

    async def fake_say(*, text, thread_ts=None):  # noqa: ANN001
        posted.append({"text": text, "thread_ts": thread_ts})

    from agentao.agent import Agentao as _Agentao

    real_init = _Agentao.__init__

    def _capturing_init(self, *args, **kwargs):  # noqa: ANN001
        captured.append(kwargs.get("permission_engine"))
        real_init(self, *args, **kwargs)

    with patch(
        "agentao.agent.Agentao._llm_call",
        lambda self, msgs, tools, token: _fake_response(),
    ), patch("agentao.agent.Agentao.__init__", _capturing_init):
        # Allowlisted channel → workspace-write
        a_channel = next(iter(WRITE_ALLOWLIST_CHANNELS))
        asyncio.run(
            handle_mention(
                text="@bot deploy",
                channel_id=a_channel,
                say=fake_say,
                llm_client_factory=_fake_llm,
            )
        )
        # Random channel → read-only
        asyncio.run(
            handle_mention(
                text="@bot read this",
                channel_id="C_RANDOM_OPEN",
                say=fake_say,
                llm_client_factory=_fake_llm,
            )
        )

    assert len(captured) == 2
    assert captured[0].active_mode == PermissionMode.WORKSPACE_WRITE
    assert captured[1].active_mode == PermissionMode.READ_ONLY
