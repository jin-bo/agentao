"""Shared YAML-frontmatter parsing for markdown definition files.

A single parser for the ``---\\nkey: val\\n---\\nbody`` convention used by
SKILL.md, agent ``*.md`` definitions, and plugin manifests. This logic was
previously copy-pasted as a private ``_parse_yaml_frontmatter`` in five places
(``skills/installer.py``, ``skills/manager.py``, ``agents/manager.py``,
``embedding/plugins/resolvers/{agents,skills}.py``) that had drifted on value
coercion, body stripping, malformed-YAML fallback, and non-mapping handling.

For the *stripping-only* variant used by AGENTAO.md — free-form prose, where a
stray ``---`` horizontal rule must never be mistaken for a fence — see
:func:`agentao.prompts.helpers.strip_frontmatter`, which additionally
leaves the content untouched unless the block parses to a mapping. Both match
the closing fence as a whole line.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

# The closing fence is a line of its own. ``content.split("---", 2)`` took the
# first ``---`` anywhere, so ``description: handles a---b markers`` ended the
# block mid-value: the description became ``handles a`` and the rest of the
# block leaked into the body, with no warning. ``meta`` may be empty
# (``---\n---``), and a ``----`` rule is not a fence.
_FRONTMATTER_RE = re.compile(
    r"\A---[ \t]*\r?\n(?P<meta>.*?)^---[ \t]*\r?$\n?(?P<body>.*)\Z",
    re.DOTALL | re.MULTILINE,
)


def match_frontmatter(content: str) -> Optional["re.Match[str]"]:
    """The leading ``---`` block of ``content`` (groups ``meta``, ``body``), or ``None``."""
    return _FRONTMATTER_RE.match(content)


def parse_frontmatter(
    content: str, *, coerce_str: bool = False, source: str | None = None
) -> tuple[dict[str, Any], str]:
    """Split a leading YAML frontmatter block from a markdown document.

    Returns ``(frontmatter, body)``:

    - ``frontmatter`` is the parsed mapping, or ``{}`` when there is no
      frontmatter block, the block is malformed YAML, or it parses to a
      non-mapping (scalar / list). Guarding the non-mapping case means a
      malformed block degrades to ``{}`` rather than raising ``AttributeError``
      on ``.items()`` — the behavior the agent resolver already had and the
      other four call sites lacked.
    - ``body`` is everything after the closing ``---`` fence, stripped. When
      there is no frontmatter block the original ``content`` is returned
      verbatim as the body (matching every prior call site's guard).

    With ``coerce_str=True`` every value is coerced to a stripped ``str``
    (``None`` -> ``""``) — what the skill / plugin loaders rely on. With
    ``coerce_str=False`` (default) native YAML types are preserved, which the
    agent loaders need so e.g. ``tools: [read_file]`` stays a list.

    A ``---``-fenced block that is *present but unusable* (malformed YAML, or a
    scalar/list where a mapping was expected) still degrades to ``{}`` — but
    emits a ``WARNING`` first, because a caller that only sees ``{}`` cannot
    tell a parse error from genuinely-absent frontmatter, and that ambiguity
    silently drops the definition (e.g. an unquoted ``description: Deploy to
    AWS: ECS`` makes a skill load with an empty description and vanish from the
    model-visible catalog). Pass ``source`` (a path or identifier) to name the
    offending file in that warning. A genuinely empty fence (``---\\n---``)
    stays silent.
    """
    match = match_frontmatter(content)
    if match is None:
        return {}, content

    body = match.group("body").strip()
    where = source or "<unknown source>"
    try:
        meta = yaml.safe_load(match.group("meta"))
    except yaml.YAMLError as exc:
        logger.warning(
            "Ignoring malformed YAML frontmatter in %s (treated as absent): %s",
            where,
            exc,
        )
        return {}, body
    if not isinstance(meta, dict):
        if meta is not None:
            logger.warning(
                "Ignoring non-mapping YAML frontmatter in %s "
                "(parsed as %s, expected `key: value` pairs).",
                where,
                type(meta).__name__,
            )
        return {}, body
    if coerce_str:
        meta = {k: str(v).strip() if v is not None else "" for k, v in meta.items()}
    return meta, body
