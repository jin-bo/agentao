"""Smoke tests proving every injected protocol is actually consulted.

Each test exercises one slot and asserts the host-side adapter saw the
call. Together they confirm the four ``agentao.host.protocols`` extension
points are real, not theoretical.
"""

from __future__ import annotations

from pathlib import Path

from agentao.memory.models import SaveMemoryRequest

from src.protocol_demo import (
    AuditingShellExecutor,
    DictMemoryStore,
    InMemoryFileSystem,
    RecordingMCPRegistry,
    make_agent,
)


def test_filesystem_protocol_is_used_by_read_file_tool(tmp_path: Path) -> None:
    """``read_file`` routes through the injected :class:`FileSystem`."""
    target = tmp_path / "hello.txt"
    handles = make_agent(
        tmp_path,
        files={str(target): b"line one\nline two\n"},
    )

    output = handles.agent.tools.get("read_file").execute(file_path=str(target))

    assert "line one" in output
    assert "line two" in output
    # No real file ever existed on disk.
    assert not target.exists()


def test_shell_protocol_records_every_command(tmp_path: Path) -> None:
    """``run_shell_command`` flows through :class:`AuditingShellExecutor`."""
    handles = make_agent(tmp_path)

    output = handles.agent.tools.get("run_shell_command").execute(
        command="echo hi",
        working_directory=str(tmp_path),
    )

    assert "[audited] echo hi" in output
    assert len(handles.shell.entries) == 1
    assert handles.shell.entries[0].command == "echo hi"


def test_shell_protocol_can_refuse_background(tmp_path: Path) -> None:
    """A ``ShellExecutor`` that rejects detach surfaces a clean tool error."""
    handles = make_agent(tmp_path)

    output = handles.agent.tools.get("run_shell_command").execute(
        command="sleep 1",
        working_directory=str(tmp_path),
        is_background=True,
    )

    assert "does not support background execution" in output


def test_mcp_registry_is_consulted_at_construction(tmp_path: Path) -> None:
    """``Agentao()`` calls ``list_servers()`` once during construction."""
    handles = make_agent(tmp_path, mcp_servers={})

    assert handles.mcp.calls == 1
    assert handles.agent.mcp_manager is None  # empty dict → no manager


def test_memory_store_round_trip_via_save_memory_tool(tmp_path: Path) -> None:
    """``save_memory`` lands in the injected :class:`MemoryStore`."""
    handles = make_agent(tmp_path)

    handles.agent.memory_tool.execute(
        key="favorite-language",
        value="Python",
        tags=["preference"],
    )

    rows = handles.memory_store.list_memories(scope="project")
    assert len(rows) == 1
    assert rows[0].title == "favorite-language"
    assert rows[0].content == "Python"
    assert "preference" in rows[0].tags


def test_protocols_are_byte_for_byte_independent(tmp_path: Path) -> None:
    """Two agents with separate adapters do not leak through each other."""
    a = make_agent(tmp_path / "a", files={str(tmp_path / "a" / "f"): b"AAA"})
    b = make_agent(tmp_path / "b", files={str(tmp_path / "b" / "f"): b"BBB"})

    out_a = a.agent.tools.get("read_file").execute(file_path=str(tmp_path / "a" / "f"))
    out_b = b.agent.tools.get("read_file").execute(file_path=str(tmp_path / "b" / "f"))

    assert "AAA" in out_a and "BBB" not in out_a
    assert "BBB" in out_b and "AAA" not in out_b
    assert isinstance(a.filesystem, InMemoryFileSystem)
    assert isinstance(a.shell, AuditingShellExecutor)
    assert isinstance(a.mcp, RecordingMCPRegistry)
    assert isinstance(a.memory_store, DictMemoryStore)
