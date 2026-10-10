"""``mcp.json`` is never rewritten from a reading that failed (#497).

Startup ignores an unreadable ``mcp.json`` with a warning. ``/mcp add`` then
read the same ``{}`` and saved ``{"mcpServers": {<new>}}`` over the file,
deleting every server already in it. Each file below is written literally,
and every refusal leaves its bytes unchanged.
"""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from agentao.cli.commands import mcp as mcp_cmd
from agentao.mcp.config import (
    McpConfigWriteError,
    load_mcp_config,
    read_mcp_servers_for_update,
    save_mcp_config,
)

_GH = '{"command": "gh-mcp"}'
UNREADABLE = {
    "invalid-json": '{"mcpServers": {"gh": ' + _GH + '}, "keepMe": 1,}',
    "not-an-object": '[{"mcpServers": {"gh": ' + _GH + "}}]",
    "oversized-int": '{"mcpServers": {"gh": ' + _GH + '}, "n": ' + "1" * 5000 + "}",
    "servers-not-an-object": '{"mcpServers": [' + _GH + '], "keepMe": 1}',
}


def _write(tmp_path, text: str):
    path = tmp_path / ".agentao" / "mcp.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


def _run(tmp_path, args: str, monkeypatch) -> str:
    printed = []
    monkeypatch.setattr(mcp_cmd.console, "print", lambda *a, **k: printed.append(" ".join(map(str, a))))
    mcp_cmd.handle_mcp_command(SimpleNamespace(agent=SimpleNamespace(working_directory=tmp_path)), args)
    return "\n".join(printed)


@pytest.mark.parametrize("label", sorted(UNREADABLE))
def test_mcp_add_refuses_and_leaves_the_file_unchanged(tmp_path, monkeypatch, label):
    if label == "oversized-int" and not hasattr(sys, "get_int_max_str_digits"):
        pytest.skip("this Python has no integer string conversion limit")
    path = _write(tmp_path, UNREADABLE[label])
    out = _run(tmp_path, "add new new-mcp", monkeypatch)
    assert "not added" in out and "was not changed" in out
    assert "Added MCP server" not in out
    assert path.read_bytes() == UNREADABLE[label].encode("utf-8")


@pytest.mark.parametrize("label", ["invalid-json", "servers-not-an-object"])
def test_mcp_remove_refuses_and_leaves_the_file_unchanged(tmp_path, monkeypatch, label):
    path = _write(tmp_path, UNREADABLE[label])
    out = _run(tmp_path, "remove gh", monkeypatch)
    assert "not removed" in out and "was not changed" in out
    assert path.read_bytes() == UNREADABLE[label].encode("utf-8")


def test_mcp_add_still_keeps_other_servers_and_keys(tmp_path, monkeypatch):
    path = _write(tmp_path, '{"mcpServers": {"gh": ' + _GH + '}, "keepMe": 1}')
    out = _run(tmp_path, "add new new-mcp", monkeypatch)
    assert "Added MCP server 'new'" in out
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "mcpServers": {"gh": {"command": "gh-mcp"}, "new": {"command": "new-mcp"}},
        "keepMe": 1,
    }


def test_mcp_add_creates_a_missing_file(tmp_path, monkeypatch):
    _run(tmp_path, "add new new-mcp", monkeypatch)
    saved = json.loads((tmp_path / ".agentao" / "mcp.json").read_text(encoding="utf-8"))
    assert saved == {"mcpServers": {"new": {"command": "new-mcp"}}}


def test_save_refuses_a_file_it_cannot_read(tmp_path):
    path = _write(tmp_path, UNREADABLE["invalid-json"])
    with pytest.raises(McpConfigWriteError, match="was not changed"):
        save_mcp_config({"new": {"command": "x"}}, config_dir=path.parent)
    assert path.read_bytes() == UNREADABLE["invalid-json"].encode("utf-8")


def test_save_refuses_a_non_finite_number_instead_of_writing_infinity(tmp_path):
    text = '{"mcpServers": {}, "elsewhere": 1e309}'
    path = _write(tmp_path, text)
    with pytest.raises(McpConfigWriteError, match="cannot represent"):
        save_mcp_config({"new": {"command": "x"}}, config_dir=path.parent)
    assert path.read_text(encoding="utf-8") == text


def test_read_for_update_is_empty_for_a_missing_file_or_key(tmp_path):
    assert read_mcp_servers_for_update(tmp_path / ".agentao") == {}
    path = _write(tmp_path, '{"other": 1}')
    assert read_mcp_servers_for_update(path.parent) == {}


def test_startup_still_ignores_an_unreadable_file(tmp_path, caplog):
    _write(tmp_path, UNREADABLE["invalid-json"])
    with caplog.at_level("WARNING", logger="agentao.mcp.config"):
        assert load_mcp_config(project_root=tmp_path) == {}
    assert "Ignoring" in caplog.text and "JSONDecodeError" in caplog.text
