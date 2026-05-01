from __future__ import annotations

import json
import logging

import pytest

from agentao.runtime.chat_loop import _serialize_tool_call
from agentao.runtime.sanitize import (
    SURROGATE_RE,
    canonicalize_tool_arguments,
    normalize_tool_calls,
    sanitize_assistant_message,
    sanitize_surrogates,
)

from tests.support.tool_calls import make_tool_call


# ---------------------------------------------------------------------------
# sanitize_surrogates
# ---------------------------------------------------------------------------


class TestSanitizeSurrogates:
    def test_clean_string_passes_through_unchanged(self):
        out = sanitize_surrogates("hello world 你好")
        assert out == "hello world 你好"

    def test_lone_high_surrogate_replaced(self):
        out = sanitize_surrogates("ok\ud800bad")
        assert "\ud800" not in out
        assert out == "ok�bad"

    def test_lone_low_surrogate_replaced(self):
        out = sanitize_surrogates("ok\udfffbad")
        assert out == "ok�bad"

    def test_multiple_surrogates_all_replaced(self):
        out = sanitize_surrogates("\ud800ok\udfffmore\ud83d")
        assert SURROGATE_RE.search(out) is None
        assert out == "�ok�more�"

    def test_clean_string_returns_same_object(self):
        # Fast-path optimization: avoid re-allocating when nothing matched.
        s = "no surrogates here"
        assert sanitize_surrogates(s) is s

    def test_non_string_passthrough(self):
        assert sanitize_surrogates(None) is None
        assert sanitize_surrogates(42) == 42


# ---------------------------------------------------------------------------
# canonicalize_tool_arguments
# ---------------------------------------------------------------------------


class TestCanonicalizeToolArguments:
    def test_clean_strict_json_returned_verbatim(self):
        # When no repair is needed, the input string is returned unchanged
        # so conversation-history bytes match what the model emitted —
        # keeps prompt-cache hits stable on the next turn.
        raw = '{"b": 2, "a": 1}'
        assert canonicalize_tool_arguments(raw, tool_name="t") is raw

    def test_repaired_input_emitted_as_compact_canonical(self):
        # Repair happened (single quotes), so we re-emit canonical compact
        # form — the original bytes were unusable anyway.
        out = canonicalize_tool_arguments("{'b': 2, 'a': 1}", tool_name="t")
        assert out == '{"b":2,"a":1}'

    def test_repaired_python_literal_emitted_as_json(self):
        out = canonicalize_tool_arguments("{'k': 1}", tool_name="t")
        assert out == '{"k":1}'

    def test_fenced_args_canonicalised(self):
        out = canonicalize_tool_arguments(
            '```json\n{"command": "ls"}\n```', tool_name="t"
        )
        assert out == '{"command":"ls"}'

    def test_unparseable_returns_empty_object_string(self, caplog):
        logger = logging.getLogger("test.outbound")
        with caplog.at_level(logging.WARNING, logger="test.outbound"):
            out = canonicalize_tool_arguments(
                "{this is not json", tool_name="run_shell_command", logger=logger
            )
        assert out == "{}"
        assert any(
            "run_shell_command" in r.getMessage() and "unparseable" in r.getMessage()
            for r in caplog.records
        )

    def test_unparseable_without_logger_does_not_raise(self):
        # Logger is optional — keep canonicalize total even without one.
        assert canonicalize_tool_arguments("{garbage", tool_name="t") == "{}"

    def test_surrogate_in_string_value_stripped_before_parse(self):
        raw = '{"text": "ok\ud800bad"}'
        out = canonicalize_tool_arguments(raw, tool_name="t")
        # Surrogate gone, output is wire-valid JSON, and round-trips.
        assert SURROGATE_RE.search(out) is None
        assert json.loads(out) == {"text": "ok�bad"}

    def test_non_ascii_preserved_when_repair_needed(self):
        # When repair forces re-serialization, ensure_ascii=False keeps
        # non-ASCII bytes as-is for readable wire bytes and logs.
        out = canonicalize_tool_arguments("{'q': '你好'}", tool_name="t")
        assert out == '{"q":"你好"}'

    def test_trailing_comma_repaired(self):
        out = canonicalize_tool_arguments('{"k": 1,}', tool_name="t")
        assert out == '{"k":1}'

    def test_simple_unclosed_brace_recovered_via_bracket_balance(self):
        # Outbound enables bracket-balance — a single missing ``}`` at the
        # end is recoverable.
        out = canonicalize_tool_arguments('{"k": 1', tool_name="t")
        assert out == '{"k":1}'

    def test_complex_truncation_falls_back_to_empty_object(self):
        # Mid-value truncation is unrecoverable — the canonicaliser must
        # still produce wire-valid JSON so the next API call succeeds.
        out = canonicalize_tool_arguments(
            '{"command": "ls", "timeout":', tool_name="t"
        )
        assert out == "{}"


# ---------------------------------------------------------------------------
# sanitize_assistant_message
# ---------------------------------------------------------------------------


class TestSanitizeAssistantMessage:
    def test_clean_message_returns_false(self):
        msg = {
            "role": "assistant",
            "content": "fine",
            "reasoning_content": "also fine",
            "tool_calls": [{
                "id": "abc",
                "type": "function",
                "function": {"name": "t", "arguments": '{"k":1}'},
            }],
        }
        assert sanitize_assistant_message(msg) is False
        assert msg["content"] == "fine"

    def test_surrogate_in_content_replaced(self):
        msg = {"role": "assistant", "content": "x\ud800y"}
        assert sanitize_assistant_message(msg) is True
        assert msg["content"] == "x�y"

    def test_surrogate_in_reasoning_content_replaced(self):
        msg = {"role": "assistant", "content": "", "reasoning_content": "r\udfffr"}
        assert sanitize_assistant_message(msg) is True
        assert msg["reasoning_content"] == "r�r"

    def test_surrogate_in_tool_call_arguments_replaced(self):
        msg = {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "id1",
                "type": "function",
                "function": {"name": "t", "arguments": '{"x":"\ud800"}'},
            }],
        }
        assert sanitize_assistant_message(msg) is True
        assert "\ud800" not in msg["tool_calls"][0]["function"]["arguments"]

    def test_surrogate_in_tool_call_name_and_id_replaced(self):
        msg = {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "id\ud800",
                "type": "function",
                "function": {"name": "t\udfff", "arguments": "{}"},
            }],
        }
        assert sanitize_assistant_message(msg) is True
        assert msg["tool_calls"][0]["id"] == "id�"
        assert msg["tool_calls"][0]["function"]["name"] == "t�"

    def test_missing_fields_do_not_crash(self):
        # Real messages may omit any of these — sanitize must be defensive.
        sanitize_assistant_message({"role": "assistant"})
        sanitize_assistant_message({"role": "assistant", "tool_calls": []})
        sanitize_assistant_message({"role": "assistant", "tool_calls": [{}]})
        sanitize_assistant_message(
            {"role": "assistant", "tool_calls": [{"function": {}}]}
        )


# ---------------------------------------------------------------------------
# _serialize_tool_call: outbound canonicalisation end-to-end
# ---------------------------------------------------------------------------


class TestSerializeToolCallCanonicalisation:
    def test_manual_path_canonicalises_arguments(self):
        tc = make_tool_call("call-1", "t", "{'k': 1}")
        out = _serialize_tool_call(tc)
        assert out["function"]["arguments"] == '{"k":1}'

    def test_pydantic_path_canonicalises_arguments(self):
        tc = make_tool_call("call-2", "t", '```json\n{"x": 2}\n```', pydantic=True)
        out = _serialize_tool_call(tc)
        assert out["function"]["arguments"] == '{"x":2}'

    def test_unparseable_args_become_empty_object(self):
        tc = make_tool_call("call-3", "t", "{garbage")
        out = _serialize_tool_call(tc)
        assert out["function"]["arguments"] == "{}"

    def test_already_canonical_args_unchanged(self):
        tc = make_tool_call("call-4", "t", '{"k":1}')
        out = _serialize_tool_call(tc)
        assert out["function"]["arguments"] == '{"k":1}'

    def test_surrogate_in_args_stripped(self):
        tc = make_tool_call("call-5", "t", '{"x": "a\ud800b"}')
        out = _serialize_tool_call(tc)
        assert "\ud800" not in out["function"]["arguments"]
        assert json.loads(out["function"]["arguments"]) == {"x": "a�b"}

    def test_thought_signature_preserved_on_manual_path(self):
        # Manual path (non-Pydantic) needs to preserve any extra fields
        # the SDK attached, e.g. Gemini's ``thought_signature``.
        from types import SimpleNamespace

        tc = SimpleNamespace(
            id="call-6",
            function=SimpleNamespace(
                name="t", arguments='{"k":1}', thought_signature="sig123"
            ),
        )
        out = _serialize_tool_call(tc)
        assert out["function"]["thought_signature"] == "sig123"


# ---------------------------------------------------------------------------
# Codex P2 fix #2: ast.literal_eval can produce non-JSON-serializable values
# ---------------------------------------------------------------------------


class TestNonJsonRepairableValues:
    """Outbound-side coverage: regardless of which layer rejects them,
    Python-only values (bytes / set / complex) must never reach the wire."""

    def test_python_bytes_value_falls_back_to_empty_object(self, caplog):
        logger = logging.getLogger("test.non_json")
        with caplog.at_level(logging.WARNING, logger="test.non_json"):
            out = canonicalize_tool_arguments(
                "{'x': b'abc'}", tool_name="t", logger=logger,
            )
        assert out == "{}"
        # A warning fires (either "unparseable" from the parse-layer
        # rejection, or "non-JSON" from a json.dumps fallback) — either
        # path is acceptable, only the outbound result matters.
        assert any(r.levelname == "WARNING" for r in caplog.records)

    def test_python_set_value_falls_back_to_empty_object(self):
        out = canonicalize_tool_arguments("{'x': {1, 2}}", tool_name="t")
        assert out == "{}"

    def test_python_complex_value_falls_back_to_empty_object(self):
        out = canonicalize_tool_arguments("{'x': 1+2j}", tool_name="t")
        assert out == "{}"


# ---------------------------------------------------------------------------
# Codex P2 fix #3: frozen SDK objects must not leave history & runtime
# divergent. ``normalize_tool_calls`` returns a clean list (proxies for
# frozen, mutated originals otherwise) that BOTH consumers iterate.
# ---------------------------------------------------------------------------


class TestNormalizeToolCallsFrozenFallback:
    def _frozen_tc(self, *, tc_id, name, args):
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class _FrozenFn:
            name: str
            arguments: str

        @dataclass(frozen=True)
        class _FrozenTC:
            id: str
            function: _FrozenFn
            type: str = "function"

        return _FrozenTC(id=tc_id, function=_FrozenFn(name=name, arguments=args))

    def test_clean_input_returns_originals_unchanged(self):
        tcs = [make_tool_call("id-1", "name", "{}")]
        cleaned, changed = normalize_tool_calls(tcs)
        assert changed is False
        assert cleaned == tcs
        assert cleaned[0] is tcs[0]

    def test_dirty_mutable_object_mutated_in_place(self):
        tcs = [make_tool_call("id\ud800", "name", "{}")]
        cleaned, changed = normalize_tool_calls(tcs)
        assert changed is True
        # Identity preserved when mutation worked.
        assert cleaned[0] is tcs[0]
        assert cleaned[0].id == "id�"

    def test_dirty_frozen_object_replaced_by_proxy(self):
        tc = self._frozen_tc(tc_id="x\ud800", name="n", args="{}")
        cleaned, changed = normalize_tool_calls([tc])
        assert changed is True
        # Identity NOT preserved (proxy), but cleaned values reach output.
        assert cleaned[0] is not tc
        assert cleaned[0].id == "x�"
        assert cleaned[0].function.name == "n"
        assert cleaned[0].function.arguments == "{}"

    def test_frozen_object_name_repair_via_proxy(self):
        tc = self._frozen_tc(tc_id="ok", name="BrowserClick_tool", args="{}")
        cleaned, changed = normalize_tool_calls(
            [tc], repair_name_fn=lambda n: "browser_click",
        )
        assert changed is True
        assert cleaned[0].function.name == "browser_click"

    def test_frozen_object_history_and_runtime_views_match(self):
        # End-to-end of Codex P2 fix #3: serialize the cleaned proxy and
        # also inspect its ``id`` directly. Both must agree, since both
        # the conversation-history dict and any tool_result downstream
        # iterate the SAME cleaned list.
        tc = self._frozen_tc(tc_id="x\ud800", name="WriteFile_tool", args="{}")
        cleaned, _ = normalize_tool_calls(
            [tc], repair_name_fn=lambda n: "write_file" if "Write" in n else None,
        )
        history_dict = _serialize_tool_call(cleaned[0])
        # Both the history view and the cleaned[0] tool_call carry the
        # same id and name — strict APIs no longer have grounds to reject.
        assert history_dict["id"] == "x�"
        assert history_dict["function"]["name"] == "write_file"
        assert cleaned[0].id == "x�"
        assert cleaned[0].function.name == "write_file"

    def test_frozen_object_preserves_thought_signature(self):
        # Gemini's ``thought_signature`` must survive proxy fallback.
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class _FrozenFn:
            name: str
            arguments: str
            thought_signature: str = ""

        @dataclass(frozen=True)
        class _FrozenTC:
            id: str
            function: _FrozenFn

        tc = _FrozenTC(
            id="x\ud800",
            function=_FrozenFn(name="n", arguments="{}", thought_signature="sig"),
        )
        cleaned, _ = normalize_tool_calls([tc])
        assert cleaned[0].function.thought_signature == "sig"

    def test_empty_input(self):
        out, changed = normalize_tool_calls([])
        assert out == [] and changed is False
        out, changed = normalize_tool_calls(None)
        assert out == [] and changed is False

    def test_frozen_pydantic_model_falls_back_to_proxy(self):
        # Codex P2: frozen Pydantic v2 raises ``ValidationError`` (a
        # ``ValueError``) on setattr — the broad ``except Exception``
        # in the in-place path catches this and the proxy path takes
        # over, producing cleaned values without crashing.
        from pydantic import BaseModel, ConfigDict

        class FrozenFn(BaseModel):
            model_config = ConfigDict(frozen=True)
            name: str
            arguments: str

        class FrozenTC(BaseModel):
            model_config = ConfigDict(frozen=True)
            id: str
            function: FrozenFn

        tc = FrozenTC(
            id="x\ud800", function=FrozenFn(name="WriteFile_tool", arguments="{}"),
        )
        cleaned, changed = normalize_tool_calls(
            [tc], repair_name_fn=lambda n: "write_file" if "Write" in n else None,
        )
        assert changed is True
        assert cleaned[0] is not tc  # proxy substituted
        assert cleaned[0].id == "x�"
        assert cleaned[0].function.name == "write_file"
        assert cleaned[0].function.arguments == "{}"


class TestNormalizeToolCallsMissingId:
    """Regression: providers that return a missing or empty ``tool_call.id``
    used to leave the assistant message with that id while the planner
    later synthesized a UUID4 for the tool_result. Strict Chat Completions
    APIs reject the resulting mismatch. ``normalize_tool_calls`` now
    synthesizes the fallback id so history and tool_result share it."""

    @pytest.mark.parametrize("missing_id", [None, "", "   "])
    def test_missing_id_normalized_to_nonempty_string(self, missing_id):
        tcs = [make_tool_call(missing_id, "name", "{}")]
        cleaned, changed = normalize_tool_calls(tcs)
        assert changed is True
        assert isinstance(cleaned[0].id, str)
        assert cleaned[0].id  # non-empty
        # Sanity: looks like a UUID4 hex form.
        assert "-" in cleaned[0].id

    def test_history_serialization_and_runtime_view_share_id(self):
        # End-to-end of the Codex P1 fix: the assistant message dict the
        # chat loop appends to history and the cleaned list the runner
        # iterates must carry the same id, so the next API request can
        # correlate the tool_result back to the assistant tool_call.
        tcs = [make_tool_call(None, "write_file", '{"path":"a"}')]
        cleaned, _ = normalize_tool_calls(tcs)
        history_dict = _serialize_tool_call(cleaned[0])
        assert history_dict["id"] == cleaned[0].id
        assert history_dict["id"]  # non-empty

    def test_planner_reuses_normalized_id_without_resynthesizing(self):
        # The planner's ``_ensure_tool_call_id`` is a safety net that runs
        # again on whatever ``normalize_tool_calls`` produced. When the id
        # is already a non-empty string, it must be preserved verbatim —
        # otherwise the assistant-message id and the plan id diverge
        # again. This test pins the contract: id stable end-to-end.
        from agentao.runtime.tool_planning import _ensure_tool_call_id

        tcs = [make_tool_call(None, "name", "{}")]
        cleaned, _ = normalize_tool_calls(tcs)
        normalized_once = cleaned[0].id
        normalized_twice = _ensure_tool_call_id(cleaned[0])
        assert normalized_twice == normalized_once
