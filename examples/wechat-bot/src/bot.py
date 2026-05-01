"""WeChat polling bridge: each inbound message runs one ``Agentao`` turn.

Inspired by `Wechat-ggGitHub/wechat-claude-code`, which long-polls the
ilink bot API from a Node daemon. Same shape, Python and Agentao
instead of TypeScript and the Claude Code SDK:

    WeChat (phone) → ilink-style bot API → polling daemon → Agentao

WeChat has no de-facto Python SDK — production deployments wire any
client (ilink HTTP, wechaty, itchat, custom hooks) behind the
``WeChatClient`` Protocol below. The example is dependency-free
beyond ``agentao`` so the offline smoke test runs in CI without a
phone, a QR-code login, or a third-party network call.

Heavy-handed simplifications: one in-process daemon (no HA), agent
constructed per inbound message (pool per ``contact_id`` for higher
throughput), reply sent in one shot (use ``Agentao.events()`` if you
want streaming previews like the reference repo).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Protocol

from agentao import Agentao
from agentao.llm import LLMClient
from agentao.permissions import PermissionEngine, PermissionMode


# Allowlist holds wxid contact ids (e.g. ``wxid_owner_self``) and group
# room ids (``<id>@chatroom``); both kinds appear as ``contact_id`` on
# inbound messages. Anything else falls back to read-only. Production
# bots read this from a per-account settings store, not a constant.
WRITE_ALLOWLIST_CONTACTS: frozenset[str] = frozenset(
    {"wxid_owner_self", "ROOM_devops@chatroom"}
)


class WeChatMessage(Protocol):
    """Minimal shape of an inbound WeChat message.

    Concrete clients (ilink, wechaty, itchat, …) all expose at least
    these three fields under one name or another.
    """

    text: str
    contact_id: str
    message_id: str


class WeChatClient(Protocol):
    """Abstracts the polling/sending surface of a WeChat bot client."""

    async def fetch_messages(self) -> list[WeChatMessage]:
        """Long-poll for new inbound messages. Returns ``[]`` if none."""
        ...

    async def send_message(self, *, contact_id: str, text: str) -> None:
        """Post a reply back to ``contact_id``."""
        ...


def make_permission_engine_for_contact(
    contact_id: str, *, project_root: Path
) -> PermissionEngine:
    """Build a ``PermissionEngine`` rooted at ``project_root``.

    The engine reads ``<project_root>/.agentao/permissions.json`` if
    present and falls back to no project rules otherwise — so the
    caller need not pre-create that file.
    """
    engine = PermissionEngine(project_root=project_root)
    mode = (
        PermissionMode.WORKSPACE_WRITE
        if contact_id in WRITE_ALLOWLIST_CONTACTS
        else PermissionMode.READ_ONLY
    )
    engine.set_mode(mode)
    return engine


def make_llm_client() -> LLMClient:
    return LLMClient(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        model=os.environ.get("OPENAI_MODEL", "gpt-5.5"),
    )


async def handle_message(
    *,
    text: str,
    contact_id: str,
    send: Callable[..., Awaitable[Any]],
    llm_client_factory: Callable[[], LLMClient] = make_llm_client,
) -> str:
    """Process one inbound WeChat message and post the reply back.

    ``send`` matches ``WeChatClient.send_message``. ``llm_client_factory``
    is the test seam — production reads from env, smoke tests inject a
    fake.
    """
    work_dir = Path(tempfile.mkdtemp(prefix="agentao-wechat-"))
    agent = Agentao(
        working_directory=work_dir,
        llm_client=llm_client_factory(),
        permission_engine=make_permission_engine_for_contact(
            contact_id, project_root=work_dir
        ),
    )
    try:
        reply = await agent.arun(text)
    finally:
        agent.close()
        # ``agent.close()`` releases handles but does not delete the
        # working directory; without this the daemon leaks one tempdir
        # per inbound message.
        shutil.rmtree(work_dir, ignore_errors=True)
    await send(contact_id=contact_id, text=reply)
    return reply


async def run_polling_loop(
    client: WeChatClient,
    *,
    poll_interval_s: float = 1.0,
    stop_event: Optional[asyncio.Event] = None,
    llm_client_factory: Callable[[], LLMClient] = make_llm_client,
) -> None:
    """Long-poll ``client`` and dispatch each message to ``handle_message``.

    ``stop_event`` is the graceful-shutdown hook; tests fire it from
    the fake client once its queue drains so the loop exits cleanly.
    ``llm_client_factory`` is the same test seam used by
    ``handle_message`` and is forwarded verbatim.
    """
    stop = stop_event or asyncio.Event()
    while not stop.is_set():
        messages = await client.fetch_messages()
        for msg in messages:
            await handle_message(
                text=msg.text,
                contact_id=msg.contact_id,
                send=client.send_message,
                llm_client_factory=llm_client_factory,
            )
        if stop.is_set():
            break
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_interval_s)
        except asyncio.TimeoutError:
            continue


__all__ = [
    "WRITE_ALLOWLIST_CONTACTS",
    "WeChatClient",
    "WeChatMessage",
    "handle_message",
    "make_llm_client",
    "make_permission_engine_for_contact",
    "run_polling_loop",
]
