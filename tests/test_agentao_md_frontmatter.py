"""AGENTAO.md leading-YAML-frontmatter stripping.

`load_project_instructions` drops a leading YAML frontmatter block so it does
not leak into the system prompt, but only when the document genuinely opens
with a frontmatter *mapping* — a stray `---` thematic break must never cause
real instructions to be dropped.
"""

from pathlib import Path
from unittest.mock import Mock

import pytest

from agentao.prompts.helpers import load_project_instructions, strip_frontmatter


# --- strip_frontmatter: unit cases -----------------------------------------


@pytest.mark.parametrize("content, stripped", [
    pytest.param("---\nname: my-rules\ndescription: project rules\n---\n# Heading\n\nBody text.\n",
                 "# Heading\n\nBody text.\n", id="leading-mapping"),
    pytest.param("---\r\nname: r\r\n---\r\nBody.\r\n", "Body.\r\n", id="crlf"),
    pytest.param("---\nname: only\n---\n", "", id="frontmatter-only-no-body"),
    pytest.param("---\nname: x\n---\nA\n\n---\n\nB\n", "A\n\n---\n\nB\n",
                 id="body-internal-triple-dash-preserved"),
])
def test_a_frontmatter_mapping_is_stripped(content, stripped):
    assert strip_frontmatter(content) == stripped


@pytest.mark.parametrize("content", [
    pytest.param("# Heading\n\nUse uv, not pip.\n", id="no-frontmatter"),
    # A lone opening fence with no closing fence is not frontmatter.
    pytest.param("---\n# Heading after a rule\n", id="single-fence"),
    # `---` ... `---` around prose parses to a YAML string, not a mapping —
    # stripping it would drop real content, so it must be kept verbatim.
    pytest.param("---\njust some prose between rules\n---\nmore text\n",
                 id="thematic-rule-wrapping-prose"),
    pytest.param("---\nname: [unclosed\n---\nBody.\n", id="malformed-yaml"),
    # Degenerate empty block parses to None (non-mapping) → keep untouched.
    pytest.param("---\n\n---\nBody.\n", id="empty-frontmatter"),
])
def test_content_without_a_frontmatter_mapping_is_kept(content):
    assert strip_frontmatter(content) == content


# --- load_project_instructions: integration on disk ------------------------


def test_loader_strips_frontmatter(tmp_path: Path):
    (tmp_path / "AGENTAO.md").write_text(
        "---\nname: rules\n---\nUse uv, not pip.\n", encoding="utf-8"
    )
    logger = Mock()
    result = load_project_instructions(tmp_path, logger)
    assert result == "Use uv, not pip.\n"
    # The ignored-frontmatter branch logs an explicit note.
    assert any(
        "frontmatter" in str(call.args[0]).lower() for call in logger.info.call_args_list
    )


def test_loader_passthrough_without_frontmatter(tmp_path: Path):
    body = "# Project\n\nUse uv, not pip.\n"
    (tmp_path / "AGENTAO.md").write_text(body, encoding="utf-8")
    assert load_project_instructions(tmp_path) == body


def test_loader_missing_file_returns_none(tmp_path: Path):
    assert load_project_instructions(tmp_path) is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
