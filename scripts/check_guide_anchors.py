#!/usr/bin/env python3
"""Fail when a developer-guide link names an ``#anchor`` the target page lacks.

VitePress builds with ``ignoreDeadLinks: true`` and does not check anchors at
all, so a broken ``#anchor`` builds and deploys cleanly and lands the reader at
the top of the page. Hand-written anchors break easily: VitePress keeps ``·``,
``—`` and ``⚠️`` in slugs, prefixes a leading digit with ``_``, and the zh
pages have slugs of their own. 90 such links had accumulated before #452/#453.

The check reads the **built** site, so the slugs are VitePress's own rather
than a re-implementation of its slugger. Build first::

    (cd developer-guide && npm ci && npm run docs:build)
    python3 scripts/check_guide_anchors.py developer-guide

Every ``](target#anchor)`` in ``developer-guide/{en,zh}/**/*.md`` is resolved
— absolute (``/en/...``), relative and same-page — outside fenced code blocks.
External URLs are skipped. A link whose page is missing from the build is
reported too. Exit status 1 if anything is broken, 0 otherwise. Standard
library only, so CI runs it without installing the project.
"""

from __future__ import annotations

import argparse
import html
import posixpath
import re
import sys
import urllib.parse
from pathlib import Path

_LINK = re.compile(r"\]\(([^)\s]*)#([^)\s]+)\)")
_FENCE = re.compile(r"```.*?```", re.S)
_ID = re.compile(r'id="([^"]+)"')


def _ids(dist: Path, page: str, cache: dict) -> set[str] | None:
    if page not in cache:
        found = None
        for candidate in (dist / f"{page}.html", dist / page / "index.html"):
            if candidate.is_file():
                text = candidate.read_text(encoding="utf-8")
                found = {
                    html.unescape(urllib.parse.unquote(x)) for x in _ID.findall(text)
                }
                break
        cache[page] = found
    return cache[page]


def check(root: Path) -> list[str]:
    dist = root / ".vitepress" / "dist"
    if not dist.is_dir():
        raise SystemExit(f"{dist} not found: build the guide first (npm run docs:build)")
    cache: dict = {}
    problems: list[str] = []
    sources = sorted(p for lang in ("en", "zh") for p in (root / lang).rglob("*.md"))
    for path in sources:
        rel = path.relative_to(root).with_suffix("").as_posix()  # en/part-1/4-hello
        # Blank out fenced code, keeping its newlines so line numbers hold.
        text = _FENCE.sub(
            lambda m: "\n" * m.group(0).count("\n"), path.read_text(encoding="utf-8")
        )
        for m in _LINK.finditer(text):
            target, anchor = m.group(1), urllib.parse.unquote(m.group(2))
            if re.match(r"^[a-z][a-z0-9+.-]*:", target):  # https:, mailto:, …
                continue
            if target == "":
                page = rel
            elif target.startswith("/"):
                page = target.lstrip("/")
            else:
                page = posixpath.normpath(posixpath.join(posixpath.dirname(rel), target))
            page = re.sub(r"\.(md|html)$", "", page)
            if page.endswith("/") or page == "":
                page += "index"
            line = text.count("\n", 0, m.start()) + 1
            ids = _ids(dist, page, cache)
            where = f"{path.relative_to(root.parent).as_posix()}:{line}"
            if ids is None:
                problems.append(f"{where}: no page {page!r} for #{anchor}")
            elif anchor not in ids:
                problems.append(f"{where}: no #{anchor} on {page}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", nargs="?", default="developer-guide", type=Path)
    problems = check(parser.parse_args().root)
    for p in problems:
        print(p)
    print(f"{len(problems)} broken anchor link(s)", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
