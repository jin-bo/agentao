"""Test memory management features via MemoryManager + SaveMemoryTool."""

from pathlib import Path

from agentao.memory.manager import MemoryManager
from agentao.tools.memory import SaveMemoryTool


def test_memory_management(tmp_path):
    """Test save, search, filter, delete, and clear via MemoryManager."""
    mgr = MemoryManager(project_root=tmp_path / ".agentao", global_root=None)
    tool = SaveMemoryTool(memory_manager=mgr)

    # Save memories
    result = tool.execute(key="project_name", value="Agentao", tags=["project", "important"])
    assert "memory" in result.lower()

    result = tool.execute(key="user_preference", value="Use Python 3.11+", tags=["preference", "python"])
    assert "memory" in result.lower()

    result = tool.execute(key="reminder", value="Run tests before committing", tags=["reminder"])
    assert "memory" in result.lower()

    # Search
    results = mgr.search("project")
    keys = {e.title for e in results}
    assert "project_name" in keys

    # Filter by tag
    results = mgr.filter_by_tag("python")
    keys = {e.title for e in results}
    assert "user_preference" in keys

    # Delete
    count = mgr.delete_by_title("reminder")
    assert count == 1
    all_titles = {e.title for e in mgr.get_all_entries()}
    assert "reminder" not in all_titles

    # Clear all
    count = mgr.clear()
    assert count >= 1
    assert mgr.get_all_entries() == []
