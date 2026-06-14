"""Shared YAML-frontmatter parsing for markdown definition files.

A single parser for the ``---\\nkey: val\\n---\\nbody`` convention used by
SKILL.md, agent ``*.md`` definitions, and plugin manifests. This logic was
previously copy-pasted as a private ``_parse_yaml_frontmatter`` in five places
(``skills/installer.py``, ``skills/manager.py``, ``agents/manager.py``,
``embedding/plugins/resolvers/{agents,skills}.py``) that had drifted on value
coercion, body stripping, malformed-YAML fallback, and non-mapping handling.

For the *stripping-only* variant used by AGENTAO.md — free-form prose, where a
stray ``---`` horizontal rule must never be mistaken for a fence — see
:func:`agentao.prompts.helpers.strip_frontmatter`, which deliberately uses a
stricter, line-anchored fence match instead of this lenient ``split``.
"""

from __future__ import annotations

from typing import Any

import yaml


def parse_frontmatter(
    content: str, *, coerce_str: bool = False
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
    """
    if not content.startswith("---"):
        return {}, content

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content

    body = parts[2].strip()
    try:
        meta = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return {}, body
    if not isinstance(meta, dict):
        return {}, body
    if coerce_str:
        meta = {k: str(v).strip() if v is not None else "" for k, v in meta.items()}
    return meta, body
