"""Tests for McpTool name handling and schema adaptation."""

from unittest.mock import MagicMock, Mock

import pytest

from agentao.mcp.tool import (
    McpTool,
    _sanitize_name,
    make_mcp_tool_name,
    parse_mcp_tool_name,
)


# ---------------------------------------------------------------------------
# _sanitize_name
# ---------------------------------------------------------------------------

def test_sanitize_name_replaces_hyphens():
    assert _sanitize_name("my-tool") == "my_tool"


def test_sanitize_name_replaces_spaces():
    assert _sanitize_name("my tool") == "my_tool"


def test_sanitize_name_replaces_dots():
    assert _sanitize_name("my.tool") == "my_tool"


def test_sanitize_name_keeps_underscores():
    assert _sanitize_name("my_tool") == "my_tool"


def test_sanitize_name_keeps_alphanumeric():
    assert _sanitize_name("myTool123") == "myTool123"


def test_sanitize_name_empty_string():
    assert _sanitize_name("") == ""


# ---------------------------------------------------------------------------
# make_mcp_tool_name
# ---------------------------------------------------------------------------

def test_make_mcp_tool_name_format():
    assert make_mcp_tool_name("github", "create_issue") == "mcp_github_create_issue"


def test_make_mcp_tool_name_sanitizes_server():
    assert make_mcp_tool_name("my-server", "list") == "mcp_my_server_list"


def test_make_mcp_tool_name_sanitizes_tool():
    assert make_mcp_tool_name("server", "get-item") == "mcp_server_get_item"


# ---------------------------------------------------------------------------
# parse_mcp_tool_name
# ---------------------------------------------------------------------------

def test_parse_mcp_tool_name_valid():
    server, tool = parse_mcp_tool_name("mcp_github_create_issue")
    assert server == "github"
    assert tool == "create_issue"


def test_parse_mcp_tool_name_underscore_in_tool():
    server, tool = parse_mcp_tool_name("mcp_myserver_do_something_complex")
    assert server == "myserver"
    assert tool == "do_something_complex"


def test_parse_mcp_tool_name_no_underscore_after_prefix():
    # "mcp_" + rest with no underscore → both return rest
    server, tool = parse_mcp_tool_name("mcp_onlyone")
    assert server == "onlyone"
    assert tool == "onlyone"


def test_parse_mcp_tool_name_invalid_prefix():
    with pytest.raises(ValueError, match="Not an MCP tool name"):
        parse_mcp_tool_name("notmcp_something")


# ---------------------------------------------------------------------------
# McpTool
# ---------------------------------------------------------------------------

def _make_mcp_tool_def(name="list_repos", description="List repos", schema=None, annotations=None):
    """Build a stub MCP tool definition.

    ``annotations`` accepts either ``None`` (server provided none),
    a real ``ToolAnnotations`` Pydantic instance, or a dict that we
    convert to one so production-realistic ``model_dump`` paths are
    exercised.
    """
    mcp_tool = MagicMock()
    mcp_tool.name = name
    mcp_tool.description = description
    mcp_tool.inputSchema = schema or {"type": "object", "properties": {}}
    if annotations is None:
        mcp_tool.annotations = None
    elif isinstance(annotations, dict):
        from mcp.types import ToolAnnotations
        mcp_tool.annotations = ToolAnnotations(**annotations)
    else:
        mcp_tool.annotations = annotations
    return mcp_tool


def test_mcptool_name_property():
    t = McpTool("github", _make_mcp_tool_def("list_repos"), call_fn=Mock())
    assert t.name == "mcp_github_list_repos"


def test_mcptool_description_includes_server():
    t = McpTool("github", _make_mcp_tool_def(description="Lists repos"), call_fn=Mock())
    assert "github" in t.description
    assert "Lists repos" in t.description


def test_mcptool_description_fallback_when_none():
    tool_def = _make_mcp_tool_def()
    tool_def.description = None
    t = McpTool("github", tool_def, call_fn=Mock())
    assert "github" in t.description


def test_mcptool_parameters_schema_forwarded():
    schema = {"type": "object", "properties": {"repo": {"type": "string"}}}
    t = McpTool("github", _make_mcp_tool_def(schema=schema), call_fn=Mock())
    assert t.parameters == schema


def test_mcptool_parameters_adds_type_when_missing():
    schema = {"properties": {"foo": {"type": "string"}}}
    t = McpTool("github", _make_mcp_tool_def(schema=schema), call_fn=Mock())
    assert t.parameters["type"] == "object"


def test_mcptool_parameters_handles_non_dict_schema():
    tool_def = _make_mcp_tool_def()
    tool_def.inputSchema = "invalid"
    t = McpTool("github", tool_def, call_fn=Mock())
    assert t.parameters == {"type": "object", "properties": {}}


def test_mcptool_execute_calls_call_fn():
    call_fn = Mock(return_value="result")
    tool_def = _make_mcp_tool_def("list_repos")
    t = McpTool("github", tool_def, call_fn=call_fn)
    result = t.execute(owner="octocat")
    call_fn.assert_called_once_with("github", "list_repos", {"owner": "octocat"})
    assert result == "result"


def test_mcptool_execute_returns_string():
    call_fn = Mock(return_value="ok")
    t = McpTool("srv", _make_mcp_tool_def(), call_fn=call_fn)
    assert isinstance(t.execute(), str)


# ---------------------------------------------------------------------------
# MCP annotation hints (readOnlyHint / destructiveHint)
# ---------------------------------------------------------------------------

def test_mcp_annotations_empty_when_none():
    t = McpTool("srv", _make_mcp_tool_def(annotations=None), call_fn=Mock())
    assert t.mcp_annotations == {}


def test_mcp_annotations_exposed_as_dict():
    t = McpTool(
        "srv",
        _make_mcp_tool_def(annotations={"readOnlyHint": True, "title": "X"}),
        call_fn=Mock(),
    )
    ann = t.mcp_annotations
    assert ann["readOnlyHint"] is True
    assert ann["title"] == "X"


def test_read_only_hint_ignored_for_untrusted_server():
    """Spec: never make tool-use decisions on annotations from untrusted servers."""
    t = McpTool(
        "srv",
        _make_mcp_tool_def(annotations={"readOnlyHint": True}),
        call_fn=Mock(),
        trusted=False,
    )
    assert t.is_read_only is False
    assert t.requires_confirmation is True


def test_read_only_hint_honored_when_trusted():
    t = McpTool(
        "srv",
        _make_mcp_tool_def(annotations={"readOnlyHint": True}),
        call_fn=Mock(),
        trusted=True,
    )
    assert t.is_read_only is True
    assert t.requires_confirmation is False  # trusted + read-only


def test_destructive_hint_overrides_trust():
    """Trusted server flagging an op as destructive should still prompt.

    This is the security-positive direction the spec allows: hints can
    add friction but must never remove it on the untrusted path.
    """
    t = McpTool(
        "srv",
        _make_mcp_tool_def(annotations={"destructiveHint": True}),
        call_fn=Mock(),
        trusted=True,
    )
    assert t.requires_confirmation is True


def test_destructive_hint_blocks_is_read_only_even_with_read_only_hint():
    """A contradictory annotation pair (both ``readOnlyHint=true`` and
    ``destructiveHint=true``) must not classify the tool as read-only.

    Otherwise the read-only-mode gate in the runner would let the call
    through and only ask for confirmation later — even though the
    server itself flagged the op as destructive.
    """
    t = McpTool(
        "srv",
        _make_mcp_tool_def(annotations={
            "readOnlyHint": True,
            "destructiveHint": True,
        }),
        call_fn=Mock(),
        trusted=True,
    )
    assert t.is_read_only is False
    assert t.requires_confirmation is True


def test_destructive_hint_ignored_for_untrusted_is_already_confirming():
    """Untrusted servers always require confirmation regardless of hints."""
    t = McpTool(
        "srv",
        _make_mcp_tool_def(annotations={"destructiveHint": False}),
        call_fn=Mock(),
        trusted=False,
    )
    # destructiveHint=False from an untrusted server should NOT downgrade
    # confirmation — that would be the spec violation we're guarding against.
    assert t.requires_confirmation is True


def test_no_hints_falls_back_to_trust_default():
    """With no annotations the legacy trusted/untrusted contract holds."""
    t_untrusted = McpTool("srv", _make_mcp_tool_def(annotations=None), call_fn=Mock(), trusted=False)
    t_trusted = McpTool("srv", _make_mcp_tool_def(annotations=None), call_fn=Mock(), trusted=True)
    assert t_untrusted.requires_confirmation is True
    assert t_trusted.requires_confirmation is False
    assert t_untrusted.is_read_only is False
    assert t_trusted.is_read_only is False
