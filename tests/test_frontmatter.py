"""Contract for the shared `parse_frontmatter` helper.

Consolidates the five previously-duplicated `_parse_yaml_frontmatter` copies.
The two behavioral axes that mattered to the call sites — value coercion
(skills str-coerce vs agents native types) and graceful degradation on
malformed / non-mapping frontmatter — are pinned here.
"""

import logging

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


# --- warn on present-but-unusable frontmatter ------------------------------
#
# A caller that only sees `{}` cannot tell a parse error from genuinely-absent
# frontmatter, and that ambiguity silently drops the definition (e.g. an
# unquoted `description: Deploy to AWS: ECS` makes a skill load with an empty
# description and vanish from the model-visible catalog). So a *present but
# unusable* fence must emit a WARNING naming the source; an *absent* one stays
# silent. (codex PR #28628 surfaced this gap in agentao's own skill loader.)


def test_malformed_yaml_warns_naming_the_source(caplog):
    # The classic footgun: an unquoted scalar containing ": " — valid prose,
    # invalid YAML — which `yaml.safe_load` rejects.
    with caplog.at_level(logging.WARNING, logger="agentao.frontmatter"):
        meta, body = parse_frontmatter(
            "---\ndescription: Deploy to AWS: ECS\n---\nbody\n",
            coerce_str=True,
            source="skills/deploy/SKILL.md",
        )
    assert meta == {}  # return contract unchanged
    assert body == "body"
    assert len(caplog.records) == 1
    rec = caplog.records[0]
    assert rec.levelno == logging.WARNING
    assert "skills/deploy/SKILL.md" in rec.getMessage()
    assert "malformed YAML" in rec.getMessage()


def test_non_mapping_frontmatter_warns(caplog):
    with caplog.at_level(logging.WARNING, logger="agentao.frontmatter"):
        parse_frontmatter("---\n- a\n- b\n---\nbody\n", source="agents/x.md")
    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert "agents/x.md" in msg
    assert "non-mapping" in msg


def test_unknown_source_placeholder_when_unnamed(caplog):
    with caplog.at_level(logging.WARNING, logger="agentao.frontmatter"):
        parse_frontmatter("---\nname: [unclosed\n---\nbody\n")
    assert "<unknown source>" in caplog.records[0].getMessage()


def test_absent_frontmatter_does_not_warn(caplog):
    # No fence → legitimately "no frontmatter", not an error. Must stay silent.
    with caplog.at_level(logging.WARNING, logger="agentao.frontmatter"):
        parse_frontmatter("# Just a heading\n\nNo frontmatter.\n")
        parse_frontmatter("---\nname: x\nno closing fence\n")  # missing fence
    assert caplog.records == []


def test_empty_fence_does_not_warn(caplog):
    # An empty fence parses to None (not a non-mapping value) → silent {}.
    with caplog.at_level(logging.WARNING, logger="agentao.frontmatter"):
        meta, _ = parse_frontmatter("---\n\n---\nbody\n", coerce_str=True)
    assert meta == {}
    assert caplog.records == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
