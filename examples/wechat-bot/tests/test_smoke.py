"""Offline smoke for the wechat-bot example.

No real WeChat client, no network, no API key. The tests exercise:

- ``make_permission_engine_for_contact`` for allowlisted vs random ids
- ``handle_message`` with a recording ``send`` callable + fake LLM
- ``run_polling_loop`` driving a ``FakeWeChatClient`` for one batch
- Permission-mode capture for contact-specific policy
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

from agentao.permissions import PermissionMode

from src.bot import (
    WRITE_ALLOWLIST_CONTACTS,
    handle_message,
    make_permission_engine_for_contact,
    run_polling_loop,
)


def _fake_llm() -> Any:
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


def _fake_response(content: str = "wechat reply"):
    response = MagicMock()
    response.choices[0].message.tool_calls = None
    response.choices[0].message.content = content
    response.choices[0].message.reasoning_content = None
    return response


@dataclass
class _Msg:
    text: str
    contact_id: str
    message_id: str


class _FakeWeChatClient:
    """In-memory client that yields a queued batch, then signals shutdown."""

    def __init__(self, queued: list[_Msg], stop: asyncio.Event) -> None:
        self._queued = list(queued)
        self._stop = stop
        self.sent: list[dict] = []

    async def fetch_messages(self) -> list[_Msg]:
        if self._queued:
            batch, self._queued = self._queued, []
            return batch
        # Queue drained — request graceful shutdown so the loop exits.
        self._stop.set()
        return []

    async def send_message(self, *, contact_id: str, text: str) -> None:
        self.sent.append({"contact_id": contact_id, "text": text})


def test_permission_engine_for_writeable_contact(tmp_path) -> None:
    """An allowlisted contact gets workspace-write."""
    a_contact = next(iter(WRITE_ALLOWLIST_CONTACTS))
    engine = make_permission_engine_for_contact(a_contact, project_root=tmp_path)
    assert engine.active_mode == PermissionMode.WORKSPACE_WRITE


def test_permission_engine_for_random_contact_is_read_only(tmp_path) -> None:
    engine = make_permission_engine_for_contact(
        "wxid_some_random_user", project_root=tmp_path
    )
    assert engine.active_mode == PermissionMode.READ_ONLY


async def test_handle_message_calls_send_with_reply() -> None:
    """A message runs one turn and posts the reply via ``send``."""
    posted: list[dict] = []

    async def fake_send(*, contact_id: str, text: str) -> None:
        posted.append({"contact_id": contact_id, "text": text})

    with patch(
        "agentao.agent.Agentao._llm_call",
        lambda self, msgs, tools, token: _fake_response(),
    ):
        result = await handle_message(
            text="hello there",
            contact_id="wxid_random_friend",
            send=fake_send,
            llm_client_factory=_fake_llm,
        )

    assert result == "wechat reply"
    assert posted == [{"contact_id": "wxid_random_friend", "text": "wechat reply"}]


async def test_run_polling_loop_processes_one_batch_then_exits() -> None:
    """The loop drains one fetch_messages batch and exits on stop_event."""
    stop = asyncio.Event()
    client = _FakeWeChatClient(
        queued=[
            _Msg(text="ping", contact_id="wxid_a", message_id="1"),
            _Msg(text="status?", contact_id="wxid_b", message_id="2"),
        ],
        stop=stop,
    )

    with patch(
        "agentao.agent.Agentao._llm_call",
        lambda self, msgs, tools, token: _fake_response("ok"),
    ):
        await run_polling_loop(
            client,
            stop_event=stop,
            llm_client_factory=_fake_llm,
        )

    assert client.sent == [
        {"contact_id": "wxid_a", "text": "ok"},
        {"contact_id": "wxid_b", "text": "ok"},
    ]


async def test_handle_message_uses_contact_specific_permission_mode() -> None:
    """The engine the agent gets reflects the contact's policy.

    Capture the constructed agent's ``permission_engine`` and assert
    its mode matches the allowlist outcome for each contact.
    """
    captured: list = []

    async def fake_send(*, contact_id: str, text: str) -> None:
        return None

    from agentao.agent import Agentao as _Agentao

    real_init = _Agentao.__init__

    def _capturing_init(self, *args, **kwargs):
        captured.append(kwargs.get("permission_engine"))
        real_init(self, *args, **kwargs)

    with patch(
        "agentao.agent.Agentao._llm_call",
        lambda self, msgs, tools, token: _fake_response(),
    ), patch("agentao.agent.Agentao.__init__", _capturing_init):
        a_contact = next(iter(WRITE_ALLOWLIST_CONTACTS))
        await handle_message(
            text="deploy",
            contact_id=a_contact,
            send=fake_send,
            llm_client_factory=_fake_llm,
        )
        await handle_message(
            text="read this",
            contact_id="wxid_some_random_user",
            send=fake_send,
            llm_client_factory=_fake_llm,
        )

    assert len(captured) == 2
    assert captured[0].active_mode == PermissionMode.WORKSPACE_WRITE
    assert captured[1].active_mode == PermissionMode.READ_ONLY
