"""File glob patterns retain both their base and recursive segments."""

from pathlib import Path

from agentao.tools.search import FindFilesTool


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
