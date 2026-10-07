"""External grep output uses physical newlines, not Unicode text separators."""

import shutil

import pytest

from agentao.capabilities.process import run_captured
from agentao.tools.search import SearchTextTool


@pytest.mark.parametrize("engine", ["git", "rg"])
@pytest.mark.parametrize("separator", ["\u0085", "\u2028", "\u2029"])
def test_external_search_keeps_unicode_separators_in_one_match(tmp_path, engine, separator):
    if shutil.which(engine) is None:
        pytest.skip(f"{engine} is not installed")
    content = f"needle before{separator}after"
    (tmp_path / "sample.txt").write_text(content + "\n", encoding="utf-8")
    if engine == "git":
        assert run_captured(["git", "init", "-q"], cwd=str(tmp_path), timeout=5).returncode == 0
        assert run_captured(["git", "add", "sample.txt"], cwd=str(tmp_path), timeout=5).returncode == 0
    tool = SearchTextTool()
    method = tool._git_grep if engine == "git" else tool._ripgrep
    result = method(tmp_path, "needle", "**/*", True, False)
    assert result is not None
    assert result.startswith("Found 1 match(es):"), result
    assert content in result
