"""Contract for the shared `parse_frontmatter` helper.

Consolidates the five previously-duplicated `_parse_yaml_frontmatter` copies.
The two behavioral axes that mattered to the call sites — value coercion
(skills str-coerce vs agents native types) and graceful degradation on
malformed / non-mapping frontmatter — are pinned here.
"""

import pytest

from agentao.frontmatter import parse_frontmatter


# --- happy path ------------------------------------------------------------


def test_parses_mapping_and_strips_body():
    meta, body = parse_frontmatter("---\nname: x\ndescription: y\n---\n\n# Body\n")
    assert meta == {"name": "x", "description": "y"}
    assert body == "# Body"


def test_no_frontmatter_returns_content_verbatim():
    content = "# Just a heading\n\nNo frontmatter.\n"
    assert parse_frontmatter(content) == ({}, content)


def test_missing_closing_fence_returns_content_verbatim():
    content = "---\nname: x\nno closing fence\n"
    assert parse_frontmatter(content) == ({}, content)


# --- value coercion axis ---------------------------------------------------


def test_native_types_preserved_by_default():
    # Agent loaders rely on this: `tools: [a, b]` must stay a list.
    meta, _ = parse_frontmatter(
        "---\ntools:\n  - read_file\n  - glob\nmax_turns: 20\ntemp: 0.5\n---\nbody\n"
    )
    assert meta["tools"] == ["read_file", "glob"]
    assert meta["max_turns"] == 20
    assert meta["temp"] == 0.5


def test_coerce_str_stringifies_values():
    # Skill / plugin loaders rely on str-coerced, stripped values.
    meta, _ = parse_frontmatter(
        "---\nname: x\ncount: 3\nempty:\n---\nbody\n", coerce_str=True
    )
    assert meta == {"name": "x", "count": "3", "empty": ""}


# --- graceful degradation (the isinstance guard) ---------------------------


def test_malformed_yaml_degrades_to_empty_mapping():
    meta, body = parse_frontmatter("---\nname: [unclosed\n---\nbody\n")
    assert meta == {}
    assert body == "body"


def test_non_mapping_frontmatter_degrades_to_empty_mapping():
    # A YAML list/scalar between the fences is not a mapping → {} (no crash).
    meta, body = parse_frontmatter("---\n- a\n- b\n---\nbody\n")
    assert meta == {}
    assert body == "body"
    # And it must not raise even with coerce_str (the old copies would have
    # hit `.items()` on a list).
    assert parse_frontmatter("---\n- a\n---\nbody\n", coerce_str=True) == ({}, "body")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
