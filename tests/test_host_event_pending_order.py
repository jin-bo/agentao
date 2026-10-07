"""A resumed consumer must not let newer events pass queued publishers."""

import asyncio

from agentao.host.events import EventStream
from agentao.host.models import ToolLifecycleEvent


def test_on_loop_publish_keeps_pending_events_before_new_events():
    async def run():
        stream = EventStream(max_queue_size=2)
        iterator = stream.subscribe(session_id="session")
        first = asyncio.create_task(iterator.__anext__())
        await asyncio.sleep(0)

        def publish(call_id):
            stream.publish(ToolLifecycleEvent(
                session_id="session", tool_call_id=call_id,
                tool_name="read_file", phase="started", started_at="2026-10-06T00:00:00Z",
            ))

        try:
            publish("initial")
            assert (await first).tool_call_id == "initial"
            publish("a")
            publish("b")
            publish("c")  # Queue full; this delivery waits on a task.
            received = [(await iterator.__anext__()).tool_call_id]
            # No scheduling point between freeing a queue slot and producing
            # the next event: a host may react synchronously to an event.
            publish("d")
            for _ in range(3):
                received.append((await asyncio.wait_for(iterator.__anext__(), 1)).tool_call_id)
            return received
        finally:
            await iterator.aclose()

    assert asyncio.run(run()) == ["a", "b", "c", "d"]
