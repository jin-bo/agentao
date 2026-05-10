#!/usr/bin/env python3
"""Batch-create kanban cards from a YAML spec.

Usage:
    uv run python scripts/kanban_cards_from_yaml.py path/to/cards.yaml

Schema (see playbooks/cards.template.yaml). Each card may carry a `name:`
alias so later cards can reference it in `depends:` instead of guessing UUIDs.

Calls `uv run kanban card add` per card (sequentially, since the board has
a single writer); after add, runs `card context add` for any context refs.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.stderr.write("error: PyYAML not installed. Run: uv add pyyaml\n")
    sys.exit(2)


CARD_ID_RE = re.compile(r"Created card ([0-9a-f-]+)")


def run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(f"  cmd failed: {' '.join(cmd)}\n  stderr: {proc.stderr}\n")
        raise SystemExit(proc.returncode)
    return proc.stdout


def card_add(card: dict) -> str:
    cmd = ["uv", "run", "kanban", "card", "add",
           "--title", str(card["title"]),
           "--goal",  str(card["goal"])]
    if (p := card.get("priority")):
        cmd += ["--priority", p.upper()]
    for a in card.get("acceptance") or []:
        cmd += ["--acceptance", str(a)]
    for d in card.get("depends") or []:
        cmd += ["--depends", str(d)]
    out = run(cmd)
    if (m := CARD_ID_RE.search(out)):
        return m.group(1)
    raise SystemExit(f"could not parse card id from output:\n{out}")


def add_context(card_id: str, refs: list) -> None:
    for ref in refs or []:
        if isinstance(ref, str):
            ref = {"path": ref}
        cmd = ["uv", "run", "kanban", "card", "context", "add", card_id,
               "--path", str(ref["path"])]
        if (k := ref.get("kind")):
            cmd += ["--kind", k]
        if (n := ref.get("note")):
            cmd += ["--note", n]
        run(cmd)


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write(__doc__ or "")
        return 1

    spec = yaml.safe_load(Path(sys.argv[1]).read_text())
    cards = spec.get("cards") if isinstance(spec, dict) else spec
    if not isinstance(cards, list):
        sys.stderr.write("error: top-level must be a list or {cards: [...]}\n")
        return 1

    name_to_id: dict[str, str] = {}
    for card in cards:
        if "depends" in card:
            card["depends"] = [name_to_id.get(d, d) for d in (card["depends"] or [])]
        cid = card_add(card)
        if (n := card.get("name")):
            name_to_id[n] = cid
        add_context(cid, card.get("context") or [])
        print(f"  {cid}  {card['title']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
