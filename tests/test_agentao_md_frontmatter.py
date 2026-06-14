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


def test_strips_leading_frontmatter_mapping():
    content = "---\nname: my-rules\ndescription: project rules\n---\n# Heading\n\nBody text.\n"
    assert strip_frontmatter(content) == "# Heading\n\nBody text.\n"


def test_no_frontmatter_passthrough():
    content = "# Heading\n\nUse uv, not pip.\n"
    assert strip_frontmatter(content) == content


def test_single_fence_is_not_frontmatter():
    # A lone opening fence with no closing fence is not frontmatter.
    content = "---\n# Heading after a rule\n"
    assert strip_frontmatter(content) == content


def test_thematic_rule_wrapping_prose_is_kept():
    # `---` ... `---` around prose parses to a YAML string, not a mapping —
    # stripping it would drop real content, so it must be kept verbatim.
    content = "---\njust some prose between rules\n---\nmore text\n"
    assert strip_frontmatter(content) == content


def test_malformed_yaml_is_kept():
    content = "---\nname: [unclosed\n---\nBody.\n"
    assert strip_frontmatter(content) == content


def test_empty_frontmatter_is_kept():
    # Degenerate empty block parses to None (non-mapping) → keep untouched.
    content = "---\n\n---\nBody.\n"
    assert strip_frontmatter(content) == content


def test_crlf_frontmatter_stripped():
    content = "---\r\nname: r\r\n---\r\nBody.\r\n"
    assert strip_frontmatter(content) == "Body.\r\n"


def test_frontmatter_only_no_body():
    content = "---\nname: only\n---\n"
    assert strip_frontmatter(content) == ""


def test_body_internal_triple_dash_preserved():
    content = "---\nname: x\n---\nA\n\n---\n\nB\n"
    assert strip_frontmatter(content) == "A\n\n---\n\nB\n"


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
