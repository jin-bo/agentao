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
# AC5 — terminal-escape sanitization of server-controlled display text
# ---------------------------------------------------------------------------


class TestTerminalSanitization:
    """A third-party ACP server's text must not carry terminal escapes."""

    def test_sanitize_strips_c0_c1_keeps_whitespace(self) -> None:
        from agentao.acp_client.render import _sanitize_terminal_text as san

        # ESC (begins every CSI/OSC), BEL, DEL, and a C1 CSI are removed.
        assert san("a\x1bb") == "ab"
        assert san("ring\x07bell") == "ringbell"
        assert san("del\x7fx") == "delx"
        assert san("c1\x9bx") == "c1x"
        # \n and \t are preserved; printable + higher Unicode untouched.
        assert san("l1\nl2\tok 中文") == "l1\nl2\tok 中文"
        assert san("plain text") == "plain text"
        assert san("") == ""

    def test_sanitize_strips_bidi_overrides_keeps_zwj(self) -> None:
        # Trojan-Source (CVE-2021-42574): bidi controls reorder text with no ESC.
        from agentao.acp_client.render import _sanitize_terminal_text as san

        for cp in (0x202A, 0x202B, 0x202C, 0x202D, 0x202E,
                   0x2066, 0x2067, 0x2068, 0x2069, 0x200E, 0x200F, 0x061C):
            assert san("a" + chr(cp) + "b") == "ab", hex(cp)
        # ZWJ / ZWNJ / BOM are legitimate (emoji, Arabic/Indic) — NOT stripped.
        for cp in (0x200D, 0x200C, 0xFEFF):  # ZWJ, ZWNJ, BOM/ZWNBSP
            assert san("a" + chr(cp) + "b") == "a" + chr(cp) + "b", hex(cp)

    def test_render_plain_strips_escape_sequences(self) -> None:
        # The plain fallback writes verbatim to stdout — the unambiguous vector.
        msg = InboxMessage(
            server="srv",
            session_id="s",
            kind=MessageKind.RESPONSE,
            text="hello\x1b[2J\x1b]0;PWNED\x07world",
        )
        rendered = render_plain(msg)
        assert "\x1b" not in rendered
        assert "\x07" not in rendered
        assert "hello" in rendered and "world" in rendered

    def test_render_all_plain_strips_escapes(self) -> None:
        msgs = [
            InboxMessage(
                server="a",
                session_id="s",
                kind=MessageKind.RESPONSE,
                text="x\x1b[31mred",
            ),
        ]
        assert "\x1b" not in render_all_plain(msgs)

    def test_flush_to_console_strips_osc_from_prefixed_path(self) -> None:
        from rich.console import Console
        import io

        buf = io.StringIO()
        c = Console(file=buf, force_terminal=True, width=80)
        msgs = [
            InboxMessage(
                server="srv",
                session_id="s",
                kind=MessageKind.RESPONSE,
                text="setting title\x1b]0;PWNED\x07 done",
            ),
        ]
        flush_to_console(msgs, c)
        out = buf.getvalue()
        # Rich emits its own CSI (\x1b[) color codes, but the server's OSC
        # set-title introducer (\x1b]) and BEL terminator must be gone.
        assert "\x1b]" not in out
        assert "\x07" not in out

    def test_flush_to_console_strips_escapes_from_agent_markdown(self) -> None:
        from rich.console import Console
        import io

        buf = io.StringIO()
        c = Console(file=buf, force_terminal=True, width=80)
        msgs = [
            InboxMessage(
                server="srv",
                session_id="s",
                kind=MessageKind.RESPONSE,
                text="agent reply\x1b]0;PWNED\x07 body",
                update_kind="agent_message_chunk",
            ),
        ]
        flush_to_console(msgs, c)
        out = buf.getvalue()
        assert "\x1b]" not in out
        assert "\x07" not in out


class TestChunkCap:
    """AC5 — agent chunks are size-bounded so a server can't force GB buffering."""

    def test_cap_chunk_passthrough_at_or_under_limit(self) -> None:
        from agentao.acp_client.manager.helpers import (
            _cap_chunk,
            _MAX_CHUNK_DISPLAY_CHARS,
        )

        assert _cap_chunk("small") == "small"
        exact = "x" * _MAX_CHUNK_DISPLAY_CHARS
        assert _cap_chunk(exact) == exact  # exactly at the cap is untouched

    def test_cap_chunk_truncates_pathological(self) -> None:
        from agentao.acp_client.manager.helpers import (
            _cap_chunk,
            _MAX_CHUNK_DISPLAY_CHARS,
        )

        big = "y" * (_MAX_CHUNK_DISPLAY_CHARS + 5000)
        capped = _cap_chunk(big)
        body = capped.split("...[truncated", 1)[0]
        assert len(body) == _MAX_CHUNK_DISPLAY_CHARS  # body bounded to the cap
        assert "truncated 5000 chars" in capped
        assert capped.isascii()  # marker is ASCII (no U+2026) — safe under LANG=C

    def test_cap_chunk_passthrough_non_str(self) -> None:
        # A hostile server may send a JSON number/bool for content.text; the cap
        # must return it unchanged (no TypeError on len()), matching pre-cap code.
        from agentao.acp_client.manager.helpers import _cap_chunk

        assert _cap_chunk(42) == 42
        assert _cap_chunk(None) is None
        assert _cap_chunk(True) is True

    def test_format_session_update_caps_agent_message_chunk(self) -> None:
        from agentao.acp_client.manager.helpers import (
            _format_session_update,
            _MAX_CHUNK_DISPLAY_CHARS,
        )

        big = "z" * (_MAX_CHUNK_DISPLAY_CHARS + 100)
        params = {
            "update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"text": big},
            }
        }
        out = _format_session_update(params)
        assert len(out) <= _MAX_CHUNK_DISPLAY_CHARS + 64
        assert "truncated 100 chars" in out

    def test_format_session_update_caps_agent_thought_chunk(self) -> None:
        from agentao.acp_client.manager.helpers import (
            _format_session_update,
            _MAX_CHUNK_DISPLAY_CHARS,
        )

        big = "t" * (_MAX_CHUNK_DISPLAY_CHARS + 7)
        params = {
            "update": {
                "sessionUpdate": "agent_thought_chunk",
                "content": {"text": big},
            }
        }
        assert "truncated 7 chars" in _format_session_update(params)

    def test_extract_display_text_caps_permission_title(self) -> None:
        from agentao.acp_client.manager.helpers import (
            _extract_display_text,
            _MAX_CHUNK_DISPLAY_CHARS,
        )

        big = "T" * (_MAX_CHUNK_DISPLAY_CHARS + 500)
        out = _extract_display_text(
            "session/request_permission", {"toolCall": {"title": big}}
        )
        assert "truncated 500 chars" in out
        assert len(out) <= _MAX_CHUNK_DISPLAY_CHARS + 64

    def test_extract_display_text_caps_ask_user_question(self) -> None:
        from agentao.acp_client.manager.helpers import (
            _extract_display_text,
            _MAX_CHUNK_DISPLAY_CHARS,
        )

        big = "Q" * (_MAX_CHUNK_DISPLAY_CHARS + 42)
        out = _extract_display_text("_agentao.cn/ask_user", {"question": big})
        assert "truncated 42 chars" in out


class TestAggregateRenderCap:
    """AC5 — the cross-chunk Markdown accumulation is bounded, not just per-chunk."""

    def test_flush_bounds_accumulated_agent_text(self, monkeypatch) -> None:
        import io
        from rich.console import Console
        import agentao.acp_client.render as render_mod

        # Shrink the aggregate bound so the test stays cheap.
        monkeypatch.setattr(render_mod, "_MAX_AGENT_RENDER_CHARS", 1000)
        buf = io.StringIO()
        c = Console(file=buf, force_terminal=True, width=120)
        # 5 same-server chunks of 400 chars each -> 2000 accumulated > 1000 bound.
        msgs = [
            InboxMessage(
                server="srv",
                session_id="s",
                kind=MessageKind.RESPONSE,
                text="a" * 400,
                update_kind="agent_message_chunk",
            )
            for _ in range(5)
        ]
        render_mod.flush_to_console(msgs, c)
        assert "truncated 1000 chars" in buf.getvalue()


class TestLogOnlySanitized:
    """AC5 — the debug log-only branch must not echo raw server escapes."""

    def test_log_only_message_text_is_sanitized(self, caplog) -> None:
        import logging
        import io
        from rich.console import Console

        buf = io.StringIO()
        c = Console(file=buf, force_terminal=True, width=80)
        msgs = [
            InboxMessage(
                server="srv",
                session_id="s",
                kind=MessageKind.NOTIFICATION,
                text="reset\x1bc term",
                update_kind="tool_call",  # a log-only kind
            ),
        ]
        with caplog.at_level(logging.DEBUG, logger="agentao.acp_client"):
            flush_to_console(msgs, c)
        joined = "".join(r.getMessage() for r in caplog.records)
        assert "\x1b" not in joined
        assert "reset" in joined and "term" in joined


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
