"""Tests for ACP client inbox queue and render (Issue 05)."""

import time
import threading
from typing import List

import pytest

from agentao.acp_client.inbox import (
    DEFAULT_CAPACITY,
    Inbox,
    InboxMessage,
    MessageKind,
)
from agentao.acp_client.render import flush_to_console, render_plain, render_all_plain


# ---------------------------------------------------------------------------
# InboxMessage
# ---------------------------------------------------------------------------


class TestInboxMessage:
    def test_basic_fields(self) -> None:
        msg = InboxMessage(
            server="srv",
            session_id="sess_1",
            kind=MessageKind.RESPONSE,
            text="hello",
        )
        assert msg.server == "srv"
        assert msg.session_id == "sess_1"
        assert msg.kind == MessageKind.RESPONSE
        assert msg.text == "hello"
        assert msg.timestamp > 0
        assert msg.raw is None

    def test_is_interaction_permission(self) -> None:
        msg = InboxMessage(
            server="srv",
            session_id="s",
            kind=MessageKind.PERMISSION,
            text="allow?",
        )
        assert msg.is_interaction is True

    def test_is_interaction_input(self) -> None:
        msg = InboxMessage(
            server="srv",
            session_id="s",
            kind=MessageKind.INPUT,
            text="enter value",
        )
        assert msg.is_interaction is True

    def test_is_interaction_response(self) -> None:
        msg = InboxMessage(
            server="srv",
            session_id="s",
            kind=MessageKind.RESPONSE,
            text="data",
        )
        assert msg.is_interaction is False

    def test_frozen(self) -> None:
        msg = InboxMessage(
            server="srv", session_id="s", kind=MessageKind.RESPONSE, text="x"
        )
        with pytest.raises(AttributeError):
            msg.text = "y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Inbox queue
# ---------------------------------------------------------------------------


class TestInbox:
    def test_fifo_order(self) -> None:
        inbox = Inbox()
        for i in range(5):
            inbox.push(
                InboxMessage(
                    server="srv",
                    session_id="s",
                    kind=MessageKind.RESPONSE,
                    text=f"msg-{i}",
                )
            )

        drained = inbox.drain()
        assert [m.text for m in drained] == [f"msg-{i}" for i in range(5)]

    def test_drain_clears_queue(self) -> None:
        inbox = Inbox()
        inbox.push(
            InboxMessage(
                server="srv",
                session_id="s",
                kind=MessageKind.RESPONSE,
                text="a",
            )
        )
        assert inbox.pending_count == 1

        drained = inbox.drain()
        assert len(drained) == 1
        assert inbox.pending_count == 0
        assert inbox.is_empty

    def test_drain_empty(self) -> None:
        inbox = Inbox()
        assert inbox.drain() == []

    def test_capacity_limit(self) -> None:
        inbox = Inbox(capacity=3)
        for i in range(5):
            inbox.push(
                InboxMessage(
                    server="srv",
                    session_id="s",
                    kind=MessageKind.RESPONSE,
                    text=f"msg-{i}",
                )
            )

        assert inbox.pending_count == 3
        assert inbox.dropped_count == 2
        drained = inbox.drain()
        # Should keep the 3 most recent (FIFO with oldest dropped).
        assert [m.text for m in drained] == ["msg-2", "msg-3", "msg-4"]

    def test_peek_does_not_consume(self) -> None:
        inbox = Inbox()
        inbox.push(
            InboxMessage(
                server="srv",
                session_id="s",
                kind=MessageKind.RESPONSE,
                text="peek-me",
            )
        )
        peeked = inbox.peek()
        assert len(peeked) == 1
        assert inbox.pending_count == 1  # Still there.

    def test_pending_interactions(self) -> None:
        inbox = Inbox()
        inbox.push(
            InboxMessage(
                server="srv",
                session_id="s",
                kind=MessageKind.RESPONSE,
                text="normal",
            )
        )
        inbox.push(
            InboxMessage(
                server="srv",
                session_id="s",
                kind=MessageKind.PERMISSION,
                text="allow tool?",
            )
        )
        inbox.push(
            InboxMessage(
                server="srv",
                session_id="s",
                kind=MessageKind.INPUT,
                text="enter name",
            )
        )

        interactions = inbox.pending_interactions
        assert len(interactions) == 2
        assert interactions[0].kind == MessageKind.PERMISSION
        assert interactions[1].kind == MessageKind.INPUT

    def test_thread_safety(self) -> None:
        inbox = Inbox(capacity=1000)
        errors: List[Exception] = []

        def _writer(start: int) -> None:
            try:
                for i in range(100):
                    inbox.push(
                        InboxMessage(
                            server="srv",
                            session_id="s",
                            kind=MessageKind.RESPONSE,
                            text=f"w{start}-{i}",
                        )
                    )
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_writer, args=(n,)) for n in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        drained = inbox.drain()
        assert len(drained) == 500  # 5 writers * 100 msgs

    def test_is_empty(self) -> None:
        inbox = Inbox()
        assert inbox.is_empty
        inbox.push(
            InboxMessage(
                server="srv",
                session_id="s",
                kind=MessageKind.RESPONSE,
                text="x",
            )
        )
        assert not inbox.is_empty


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


class TestRender:
    def test_render_plain_format(self) -> None:
        msg = InboxMessage(
            server="my-agent",
            session_id="s",
            kind=MessageKind.RESPONSE,
            text="Hello world",
        )
        rendered = render_plain(msg)
        assert "[my-agent]" in rendered
        assert "Hello world" in rendered

    def test_render_plain_with_kind(self) -> None:
        msg = InboxMessage(
            server="srv",
            session_id="s",
            kind=MessageKind.PERMISSION,
            text="Allow?",
        )
        rendered = render_plain(msg)
        assert "[permission]" in rendered

    def test_render_all_plain_empty(self) -> None:
        assert render_all_plain([]) == ""

    def test_render_all_plain_multiple(self) -> None:
        msgs = [
            InboxMessage(
                server="a",
                session_id="s",
                kind=MessageKind.RESPONSE,
                text="first",
            ),
            InboxMessage(
                server="b",
                session_id="s",
                kind=MessageKind.NOTIFICATION,
                text="second",
            ),
        ]
        rendered = render_all_plain(msgs)
        assert "first" in rendered
        assert "second" in rendered
        assert rendered.index("first") < rendered.index("second")

    def test_flush_to_console_returns_count(self) -> None:
        from rich.console import Console
        import io

        buf = io.StringIO()
        c = Console(file=buf, force_terminal=True, width=80)

        msgs = [
            InboxMessage(
                server="srv",
                session_id="s",
                kind=MessageKind.RESPONSE,
                text="hello",
            ),
        ]
        count = flush_to_console(msgs, c)
        assert count == 1

        output = buf.getvalue()
        assert "srv" in output
        assert "hello" in output

    def test_flush_to_console_empty(self) -> None:
        assert flush_to_console([]) == 0


# ---------------------------------------------------------------------------
# Multi-server ordering
# ---------------------------------------------------------------------------


class TestMultiServerOrdering:
    def test_messages_interleaved_correctly(self) -> None:
        inbox = Inbox()
        inbox.push(
            InboxMessage(
                server="alpha",
                session_id="s1",
                kind=MessageKind.RESPONSE,
                text="a1",
            )
        )
        inbox.push(
            InboxMessage(
                server="beta",
                session_id="s2",
                kind=MessageKind.RESPONSE,
                text="b1",
            )
        )
        inbox.push(
            InboxMessage(
                server="alpha",
                session_id="s1",
                kind=MessageKind.RESPONSE,
                text="a2",
            )
        )

        drained = inbox.drain()
        assert [m.text for m in drained] == ["a1", "b1", "a2"]
        assert [m.server for m in drained] == ["alpha", "beta", "alpha"]
