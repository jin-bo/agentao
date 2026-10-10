"""File glob patterns retain both their base and recursive segments."""

from pathlib import Path

import pytest

from agentao.capabilities import LocalFileSystem
from agentao.tools.search import FindFilesTool, SearchTextTool


def test_recursive_segment_matches_nested_files_without_moving_prefix(tmp_path):
    names = ["src/test.py", "src/nested/test.py", "other/src/test.py"]
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    tool = FindFilesTool()
    tool.working_directory = tmp_path
    result = tool.execute("src/**/test.py")
    matches = set(result.split("\n\n", 1)[1].splitlines())
    assert matches == {str(Path("src/test.py")), str(Path("src/nested/test.py"))}


def test_multiple_recursive_segments_are_preserved(tmp_path):
    path = tmp_path / "src" / "nested" / "tests" / "unit" / "test.py"
    path.parent.mkdir(parents=True)
    path.write_text("")
    tool = FindFilesTool()
    tool.working_directory = tmp_path
    result = tool.execute("src/**/tests/**/*.py")
    matches = set(result.split("\n\n", 1)[1].splitlines())
    assert matches == {str(path.relative_to(tmp_path))}


def test_python_search_fallback_preserves_recursive_prefix(tmp_path, monkeypatch):
    for name in ["src/test.py", "src/nested/test.py", "other/src/test.py"]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("needle\n")
    monkeypatch.setattr("agentao.tools.search._find_executable", lambda name: None)
    tool = SearchTextTool()
    tool.working_directory = tmp_path
    result = tool.execute("needle", file_pattern="src/**/*.py")
    matches = {line.rsplit(":", 2)[0] for line in result.split("\n\n", 1)[1].splitlines()}
    assert matches == {str(Path("src/test.py")), str(Path("src/nested/test.py"))}


@pytest.mark.parametrize(
    "pattern,base,remaining",
    [("**/*.py", ".", "*.py"), ("src/**/test.py", "src", "test.py")],
)
def test_glob_retains_common_host_call_shape(tmp_path, pattern, base, remaining):
    class RecordingFileSystem(LocalFileSystem):
        def glob(self, path, pattern, *, recursive):
            self.calls.append((path, pattern, recursive))
            return super().glob(path, pattern, recursive=recursive)

    fs = RecordingFileSystem()
    fs.calls = []
    tool = FindFilesTool()
    tool.filesystem = fs
    tool.working_directory = tmp_path
    tool.execute(pattern)
    assert fs.calls == [(tmp_path / base, remaining, True)]
