"""slack-bolt app: each ``app_mention`` runs one ``Agentao`` turn.

Channel-scoped permissions: the channel id of the inbound mention
selects a rule preset (``read-only`` for general channels, full
write-mode for an explicit allowlist). The bot then constructs a
fresh ``Agentao`` per mention with that engine injected, runs one
``arun()``, and posts the reply back as a thread reply.

Heavy-handed simplifications for an example:

- One in-process bot instance — no HA / sticky sessions.
- Permissions are computed from a hard-coded allowlist; a real bot
  would read them from a workspace store.
- The agent is constructed per mention. For higher throughput, pool
  agents per (workspace, channel) pair.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from agentao import Agentao
from agentao.llm import LLMClient
from agentao.permissions import PermissionEngine, PermissionMode


# The channels in this set get full workspace-write power; any other
# channel falls back to read-only mode. Production bots read this
# from a workspace settings store, not from a constant.
WRITE_ALLOWLIST_CHANNELS: frozenset[str] = frozenset({"C_PRIV_DEVOPS", "C_PRIV_OPS"})


def make_permission_engine_for_channel(
    channel_id: str, *, project_root: Path
) -> PermissionEngine:
    """Build a ``PermissionEngine`` rooted at ``project_root``.

    The engine reads ``<project_root>/.agentao/permissions.json`` if
    present and falls back to no project rules otherwise — so the
    caller need not pre-create that file.
    """
    engine = PermissionEngine(project_root=project_root)
    mode = (
        PermissionMode.WORKSPACE_WRITE
        if channel_id in WRITE_ALLOWLIST_CHANNELS
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


async def handle_mention(
    *,
    text: str,
    channel_id: str,
    say: Callable[..., Awaitable[Any]],
    thread_ts: Optional[str] = None,
    llm_client_factory: Callable[[], LLMClient] = make_llm_client,
) -> str:
    """Process one ``app_mention`` event and post the reply back.

    ``say`` matches ``slack_bolt.AsyncSay``. ``llm_client_factory`` is
    the test seam — production reads from env, smoke tests inject a
    fake.
    """
    work_dir = Path(tempfile.mkdtemp(prefix="agentao-slack-"))
    agent = Agentao(
        working_directory=work_dir,
        llm_client=llm_client_factory(),
        permission_engine=make_permission_engine_for_channel(
            channel_id, project_root=work_dir
        ),
    )
    try:
        reply = await agent.arun(text)
    finally:
        agent.close()
        # ``agent.close()`` releases handles but does not delete the
        # working directory; without this every mention leaks one
        # tempdir.
        shutil.rmtree(work_dir, ignore_errors=True)
    await say(text=reply, thread_ts=thread_ts)
    return reply


def build_app(
    *,
    llm_client_factory: Callable[[], LLMClient] = make_llm_client,
):  # type: ignore[no-untyped-def]
    """Construct a configured ``slack_bolt.AsyncApp``.

    Imported lazily so the smoke test can exercise ``handle_mention``
    without bringing in slack-bolt's full HTTP stack.
    """
    from slack_bolt.async_app import AsyncApp

    app = AsyncApp(token=os.environ["SLACK_BOT_TOKEN"])

    @app.event("app_mention")
    async def _on_mention(event, say):  # noqa: ANN001
        await handle_mention(
            text=event.get("text", ""),
            channel_id=event.get("channel", ""),
            say=say,
            thread_ts=event.get("thread_ts") or event.get("ts"),
            llm_client_factory=llm_client_factory,
        )

    return app


__all__ = [
    "WRITE_ALLOWLIST_CHANNELS",
    "build_app",
    "handle_mention",
    "make_llm_client",
    "make_permission_engine_for_channel",
]
