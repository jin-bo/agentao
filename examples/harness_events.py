"""Subscribe to the public harness event stream while a turn runs.

Demonstrates the host-stable observation surface added in 0.3.1:

- ``Agentao.events(session_id=None)`` — async iterator over
  :class:`agentao.harness.HarnessEvent`.
- ``Agentao.active_permissions()`` — JSON-safe snapshot of the active
  permission policy.

The pattern below is the typical embedding shape: one task consumes
events for UI / audit / metrics, another task drives the LLM via
``agent.arun(...)``. The two coroutines share the same event loop, so
events arrive synchronously with the turn — no thread bridging needed.

Running from the repository root::

    OPENAI_API_KEY=sk-... uv run python examples/harness_events.py

Without ``OPENAI_API_KEY``, the script exits early with instructions
rather than crashing at first LLM call.

For the full host-facing contract — delivery semantics, schema
snapshots, redaction rules — see ``docs/api/harness.md``.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

from agentao.embedding import build_from_environment
from agentao.harness import (
    HarnessEvent,
    PermissionDecisionEvent,
    SubagentLifecycleEvent,
    ToolLifecycleEvent,
)


PROMPT = (
    "List the files directly under the current working directory. "
    "Just call the tool once and summarise what you found in one short line."
)


async def consume_events(agent) -> None:
    """Drain the harness event stream until the iterator closes.

    Closing the iterator (via ``break`` or task cancellation) releases
    the per-subscriber queue. The driver task cancels this consumer
    after the chat returns so the example exits cleanly.
    """
    async for ev in agent.events():
        _print_event(ev)


def _print_event(ev: HarnessEvent) -> None:
    if isinstance(ev, ToolLifecycleEvent):
        print(
            f"[tool] {ev.tool_name} phase={ev.phase} "
            f"outcome={ev.outcome} call_id={ev.tool_call_id}"
        )
    elif isinstance(ev, PermissionDecisionEvent):
        print(
            f"[perm] {ev.tool_name} -> {ev.outcome} "
            f"mode={ev.mode} decision_id={ev.decision_id}"
        )
    elif isinstance(ev, SubagentLifecycleEvent):
        print(
            f"[subagent] child={ev.child_session_id} "
            f"phase={ev.phase} task_id={ev.child_task_id}"
        )
    else:
        # Forward-compatible fallback in case new HarnessEvent variants
        # land in a future minor release.
        print(f"[event] {ev!r}")


async def amain(workdir: Path) -> int:
    print(f"[harness_events] working_directory={workdir}")

    agent = build_from_environment(working_directory=workdir)
    try:
        # Take a JSON-safe snapshot of the policy before the turn runs;
        # hosts use this to render mode banners or pin into audit logs.
        snap = agent.active_permissions()
        print(
            f"[active_permissions] mode={snap.mode!r} "
            f"loaded_sources={snap.loaded_sources}"
        )

        consumer = asyncio.create_task(consume_events(agent))

        try:
            reply = await agent.arun(PROMPT)
        finally:
            consumer.cancel()
            try:
                await consumer
            except asyncio.CancelledError:
                pass

        print(f"\n[reply] {reply.strip()}")
        return 0
    finally:
        agent.close()


def main() -> int:
    if not os.environ.get("OPENAI_API_KEY"):
        print(
            "OPENAI_API_KEY is not set. Configure it (or any "
            "LLM_PROVIDER-prefixed credential — see docs/EMBEDDING.md) "
            "before running this example.",
            file=sys.stderr,
        )
        return 2

    workdir = Path(tempfile.mkdtemp(prefix="agentao-harness-events-"))
    return asyncio.run(amain(workdir))


if __name__ == "__main__":
    raise SystemExit(main())
