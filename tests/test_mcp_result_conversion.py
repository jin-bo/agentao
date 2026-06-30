"""Tests for ``McpClient.call_tool`` result conversion + timeout passthrough.

Covers two behaviors surfaced by the opencode 6/29 borrow review:

* content / ``structuredContent`` precedence (opencode #34505): keep content
  blocks when present; fall back to serialized ``structuredContent`` only when
  there are no content blocks at all (so a structured-only result is no longer
  flattened to an empty string), and serialize it the codebase way
  (``ensure_ascii=False``, ``default=str``).
* per-request read-timeout passthrough (opencode #33977): a configured
  ``timeout.request`` bounds each tool call; a legacy int ``timeout`` (connect
  budget) leaves the per-request wait unbounded.
"""

import json

from tests.support.mcp import (
    connected_client,
    image_block,
    run_async,
    text_block,
    tool_result,
)


# ---------------------------------------------------------------------------
# content / structuredContent precedence (opencode #34505)
# ---------------------------------------------------------------------------

def test_content_only_returns_joined_text():
    client = connected_client(tool_result([text_block("hello")]))
    assert run_async(client.call_tool("t", {})) == "hello"


def test_content_present_with_structured_keeps_content():
    # Spec-compliant server returns BOTH content and structuredContent.
    # Content wins; the structured JSON must not be appended/leaked.
    client = connected_client(tool_result([text_block("visible")], structured={"k": "v"}))
    out = run_async(client.call_tool("t", {}))
    assert out == "visible"
    assert "{" not in out


def test_image_block_with_structured_keeps_image_placeholder():
    # An image alongside structured data must not be clobbered by the JSON —
    # this is exactly opencode's #34505 regression, guarded here.
    client = connected_client(tool_result([image_block("image/png")], structured={"k": "v"}))
    assert run_async(client.call_tool("t", {})) == "[image: image/png]"


def test_empty_content_falls_back_to_structured_json():
    # content=[] + structuredContent → serialize it instead of returning "".
    client = connected_client(tool_result([], structured={"results": [1, 2]}))
    out = run_async(client.call_tool("t", {}))
    assert json.loads(out) == {"results": [1, 2]}


def test_empty_content_and_no_structured_returns_empty_string():
    client = connected_client(tool_result([], structured=None))
    assert run_async(client.call_tool("t", {})) == ""


def test_structured_fallback_respects_is_error():
    client = connected_client(tool_result([], structured={"e": 1}, is_error=True))
    out = run_async(client.call_tool("t", {}))
    assert out.startswith("MCP tool error:")
    assert '"e"' in out  # structured payload serialized into the error text


def test_structured_fallback_preserves_non_ascii():
    # ensure_ascii=False: CJK/emoji reach the model readable, not \uXXXX-escaped.
    client = connected_client(tool_result([], structured={"msg": "你好 🌏"}))
    out = run_async(client.call_tool("t", {}))
    assert "你好 🌏" in out
    assert "\\u" not in out


def test_structured_fallback_serializes_non_json_native_without_raising():
    # default=str: a value json can't natively serialize degrades to its repr
    # instead of raising an uncaught TypeError out of call_tool.
    class Weird:
        def __str__(self):
            return "WEIRD"

    client = connected_client(tool_result([], structured={"k": Weird()}))
    out = run_async(client.call_tool("t", {}))
    assert "WEIRD" in out


# ---------------------------------------------------------------------------
# per-request read-timeout passthrough (opencode #33977)
# ---------------------------------------------------------------------------

def test_no_request_timeout_passes_none():
    capture = {}
    client = connected_client(tool_result([text_block("ok")]), capture=capture)
    run_async(client.call_tool("t", {}))
    assert capture["read_timeout_seconds"] is None


def test_request_timeout_passed_as_timedelta():
    from datetime import timedelta

    capture = {}
    client = connected_client(
        tool_result([text_block("ok")]), config={"command": "echo", "timeout": {"request": 42}}, capture=capture
    )
    run_async(client.call_tool("t", {}))
    assert capture["read_timeout_seconds"] == timedelta(seconds=42)


def test_legacy_int_timeout_does_not_bound_request():
    # Legacy int = connect/startup budget only; per-request stays unbounded.
    capture = {}
    client = connected_client(
        tool_result([text_block("ok")]), config={"command": "echo", "timeout": 30}, capture=capture
    )
    run_async(client.call_tool("t", {}))
    assert capture["read_timeout_seconds"] is None
