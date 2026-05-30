"""Tests for structured ask_user (options / header / multiple / allow_custom).

The structured fields are advisory hints that flow from the tool through
the Transport contract to each transport implementation. The key contract
is backward compatibility: legacy 1-arg ``Callable[[str], str]`` callbacks
must keep working, and a plain ask_user must keep its original wire shape.
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from agentao.acp.protocol import METHOD_ASK_USER
from agentao.acp.schema import AcpAskUserParams
from agentao.acp.transport import ACPTransport
from agentao.cli.transport import _resolve_option_selection
from agentao.tools.ask_user import AskUserTool
from agentao.transport.sdk import SdkTransport, invoke_ask_user_callback


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------


class TestAskUserTool:
    def test_schema_advertises_structured_fields(self) -> None:
        props = AskUserTool().parameters["properties"]
        assert set(props) == {"question", "header", "options", "multiple", "allow_custom"}
        assert props["options"]["type"] == "array"
        assert props["options"]["items"]["type"] == "string"

    def test_execute_forwards_structured_kwargs(self) -> None:
        seen: Dict[str, Any] = {}

        def cb(question: str, **kw: Any) -> str:
            seen["question"] = question
            seen.update(kw)
            return "ok"

        result = AskUserTool(cb).execute(
            "Pick one", header="Setup", options=["a", "b"], multiple=True, allow_custom=False
        )
        assert result == "ok"
        assert seen == {
            "question": "Pick one",
            "header": "Setup",
            "options": ["a", "b"],
            "multiple": True,
            "allow_custom": False,
        }

    def test_execute_plain_question_uses_defaults(self) -> None:
        seen: Dict[str, Any] = {}
        AskUserTool(lambda q, **kw: seen.update(kw) or "x").execute("hi")
        assert seen == {
            "header": None,
            "options": None,
            "multiple": False,
            "allow_custom": True,
        }

    def test_execute_no_callback_returns_sentinel(self) -> None:
        assert "not available" in AskUserTool().execute("hi")

    def test_execute_legacy_one_arg_callback_does_not_break(self) -> None:
        # A directly-constructed tool with the legacy 1-arg callback shape
        # must not TypeError when execute() forwards the structured kwargs.
        tool = AskUserTool(lambda q: f"ok:{q}")
        assert tool.execute("hi") == "ok:hi"
        assert tool.execute("hi", options=["a", "b"], multiple=True) == "ok:hi"


# ---------------------------------------------------------------------------
# Backward-compatible callback invocation
# ---------------------------------------------------------------------------


STRUCTURED = {"header": "H", "options": ["x"], "multiple": True, "allow_custom": False}


class TestInvokeAskUserCallback:
    def test_legacy_one_arg_callback_drops_structured(self) -> None:
        # A legacy Callable[[str], str] must not TypeError on structured kwargs.
        assert invoke_ask_user_callback(lambda q: f"got:{q}", "Q", STRUCTURED) == "got:Q"

    def test_var_keyword_callback_receives_all(self) -> None:
        captured: Dict[str, Any] = {}
        invoke_ask_user_callback(
            lambda q, **kw: captured.update(kw) or "", "Q", STRUCTURED
        )
        assert captured == STRUCTURED

    def test_named_param_callback_receives_only_named(self) -> None:
        captured: Dict[str, Any] = {}

        def cb(question: str, options: Any = None) -> str:
            captured["options"] = options
            return ""

        invoke_ask_user_callback(cb, "Q", STRUCTURED)
        assert captured == {"options": ["x"]}

    def test_uninspectable_callable_falls_back_to_one_arg(self) -> None:
        # ``str`` is a builtin whose signature can't be introspected here;
        # it must be called with the question alone.
        assert invoke_ask_user_callback(str, "Q", STRUCTURED) == "Q"

    def test_positional_only_named_field_is_not_passed_by_keyword(self) -> None:
        # A positional-only parameter sharing a structured field name must
        # not be forwarded by keyword (would raise TypeError).
        ns: Dict[str, Any] = {}
        exec(
            "def cb(question, header, /):\n"
            "    return f'{question}:{header}'",
            ns,
        )
        # `header` is positional-only → dropped, so cb is called with the
        # question alone, which raises its own TypeError (missing header) —
        # but crucially NOT the 'positional-only passed as keyword' error.
        with pytest.raises(TypeError) as exc:
            invoke_ask_user_callback(ns["cb"], "Q", STRUCTURED)
        assert "positional-only" not in str(exc.value)


class TestSdkTransport:
    def test_one_arg_callback_via_transport(self) -> None:
        t = SdkTransport(ask_user=lambda q: f"answer:{q}")
        assert t.ask_user("Q", options=["a"], multiple=True) == "answer:Q"

    def test_structured_callback_via_transport(self) -> None:
        captured: Dict[str, Any] = {}
        t = SdkTransport(ask_user=lambda q, **kw: captured.update(kw) or "ok")
        t.ask_user("Q", header="H", options=["a", "b"], multiple=True, allow_custom=False)
        assert captured == {"header": "H", "options": ["a", "b"], "multiple": True, "allow_custom": False}

    def test_no_callback_returns_sentinel(self) -> None:
        assert "not available" in SdkTransport().ask_user("Q", options=["a"])


# ---------------------------------------------------------------------------
# CLI option resolution
# ---------------------------------------------------------------------------


class TestResolveOptionSelection:
    def test_single_number(self) -> None:
        assert _resolve_option_selection("2", ["a", "b", "c"], False) == "b"

    def test_multiple_numbers(self) -> None:
        assert _resolve_option_selection("1,3", ["a", "b", "c"], True) == "a, c"

    def test_multiple_when_not_allowed_is_custom(self) -> None:
        assert _resolve_option_selection("1,2", ["a", "b"], False) is None

    def test_non_numeric_is_custom(self) -> None:
        assert _resolve_option_selection("hello", ["a", "b"], False) is None

    def test_out_of_range_is_custom(self) -> None:
        assert _resolve_option_selection("9", ["a", "b"], False) is None

    def test_empty_is_none(self) -> None:
        assert _resolve_option_selection("   ", ["a"], False) is None


class TestCliAskUserPrompt:
    def _stub_cli(self):
        from types import SimpleNamespace

        return SimpleNamespace(current_status=None)

    def test_allow_custom_false_reprompts_until_valid(self, monkeypatch) -> None:
        from agentao.cli import transport as cli_transport

        responses = iter(["banana", "9", "2"])  # invalid custom, out-of-range, then valid
        monkeypatch.setattr(cli_transport.console, "input", lambda *a, **k: next(responses))
        result = cli_transport.ask_user(
            self._stub_cli(), "Pick", options=["a", "b"], allow_custom=False
        )
        assert result == "b"

    def test_allow_custom_true_accepts_free_form(self, monkeypatch) -> None:
        from agentao.cli import transport as cli_transport

        monkeypatch.setattr(cli_transport.console, "input", lambda *a, **k: "my custom")
        result = cli_transport.ask_user(
            self._stub_cli(), "Pick", options=["a", "b"], allow_custom=True
        )
        assert result == "my custom"

    def test_plain_question_returns_text(self, monkeypatch) -> None:
        from agentao.cli import transport as cli_transport

        monkeypatch.setattr(cli_transport.console, "input", lambda *a, **k: "  hello  ")
        assert cli_transport.ask_user(self._stub_cli(), "Q?") == "hello"


# ---------------------------------------------------------------------------
# ACP schema
# ---------------------------------------------------------------------------


class TestAcpAskUserParams:
    def test_defaults(self) -> None:
        p = AcpAskUserParams(sessionId="s", question="q")
        assert p.header is None
        assert p.options is None
        assert p.multiple is False
        assert p.allowCustom is True

    def test_structured(self) -> None:
        p = AcpAskUserParams(
            sessionId="s", question="q", header="H", options=["a"], multiple=True, allowCustom=False
        )
        assert p.options == ["a"]
        assert p.allowCustom is False

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            AcpAskUserParams(sessionId="s", question="q", apiKey="secret")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# ACP transport wire shape
# ---------------------------------------------------------------------------


def _acp_transport_with_capture() -> tuple[ACPTransport, List[Dict[str, Any]]]:
    captured: List[Dict[str, Any]] = []
    server = MagicMock()
    pending = MagicMock()
    pending.wait.return_value = {"outcome": "answered", "text": "ans"}

    def _call(method: str, params: Dict[str, Any]) -> Any:
        captured.append({"method": method, "params": params})
        return pending

    server.call.side_effect = _call
    return ACPTransport(server, "sess"), captured


class TestAcpTransportWireShape:
    def test_plain_question_minimal_wire(self) -> None:
        t, captured = _acp_transport_with_capture()
        assert t.ask_user("Q") == "ans"
        assert captured[0]["method"] == METHOD_ASK_USER
        assert captured[0]["params"] == {"sessionId": "sess", "question": "Q"}

    def test_structured_question_full_wire(self) -> None:
        t, captured = _acp_transport_with_capture()
        t.ask_user("Q", header="H", options=["a", "b"], multiple=True, allow_custom=False)
        assert captured[0]["params"] == {
            "sessionId": "sess",
            "question": "Q",
            "header": "H",
            "options": ["a", "b"],
            "multiple": True,
            "allowCustom": False,
        }

    def test_default_structured_values_omitted(self) -> None:
        # multiple=False / allow_custom=True are defaults → not on the wire.
        t, captured = _acp_transport_with_capture()
        t.ask_user("Q", options=["a"])
        assert captured[0]["params"] == {
            "sessionId": "sess",
            "question": "Q",
            "options": ["a"],
        }
