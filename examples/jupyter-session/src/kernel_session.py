"""One ``Agentao`` per kernel; ``events()`` drives a notebook display.

The notebook (``session.ipynb``) calls :func:`build_session` once at
the top, then runs cells that invoke :func:`turn` and
:func:`drain_events_into`. The smoke test imports the same helpers
and asserts they wire up correctly without an OpenAI key.
"""

from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from agentao import Agentao
from agentao.host.models import HostEvent
from agentao.llm import LLMClient


@dataclass
class KernelSession:
    """Kernel-scoped state. Construct once per notebook."""

    agent: Agentao
    history: list[HostEvent] = field(default_factory=list)


def build_session(
    *,
    llm_client: Optional[LLMClient] = None,
    working_directory: Optional[Path] = None,
) -> KernelSession:
    """Construct an Agentao for the kernel lifetime.

    Pass ``llm_client`` to inject a fake or pre-configured LLM
    (the smoke test uses this; a real notebook reads from env).
    """
    if llm_client is None:
        import os

        llm_client = LLMClient(
            api_key=os.environ["OPENAI_API_KEY"],
            base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            model=os.environ.get("OPENAI_MODEL", "gpt-5.5"),
        )
    wd = working_directory or Path(tempfile.mkdtemp(prefix="agentao-jupyter-"))
    agent = Agentao(working_directory=wd, llm_client=llm_client)
    return KernelSession(agent=agent)


async def drain_events_into(
    session: KernelSession,
    *,
    on_event: Callable[[HostEvent], None] = lambda _e: None,
    until_count: int = 1,
    timeout: float = 5.0,
) -> list[HostEvent]:
    """Iterate ``session.agent.events()`` until ``until_count`` events.

    A real notebook would attach an ``ipywidgets`` Output and update it
    on each event; this helper centralises the iteration loop so a
    smoke test can assert the wiring without rendering anything.
    """
    received: list[HostEvent] = []

    async def _drain() -> None:
        async for event in session.agent.events():
            session.history.append(event)
            received.append(event)
            try:
                on_event(event)
            except Exception:
                pass
            if len(received) >= until_count:
                return

    await asyncio.wait_for(_drain(), timeout=timeout)
    return received


async def turn(
    session: KernelSession, prompt: str
) -> tuple[str, list[HostEvent]]:
    """Run one turn and concurrently drain a few harness events.

    Returns ``(reply, events_seen_during_turn)``. The notebook cell
    calls this with ``await turn(session, "...")``.
    """
    drainer = asyncio.create_task(
        drain_events_into(session, until_count=2, timeout=3.0)
    )
    try:
        reply = await session.agent.arun(prompt)
    finally:
        # If the agent finished without emitting enough events, cancel
        # the drainer so the notebook cell does not hang.
        if not drainer.done():
            drainer.cancel()
    try:
        events = await drainer
    except (asyncio.CancelledError, asyncio.TimeoutError):
        events = []
    return reply, events


def close_session(session: KernelSession) -> None:
    """Tear down at kernel shutdown — call from ``atexit`` if you like."""
    try:
        session.agent.close()
    except Exception:
        pass


__all__ = [
    "KernelSession",
    "build_session",
    "close_session",
    "drain_events_into",
    "turn",
]
