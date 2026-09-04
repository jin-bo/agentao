#!/usr/bin/env python3
"""Hold a split design-document set together.

A design that lives in several files — a spec, a PR ladder, a gate matrix,
an evidence file, a review log — drifts at the seams: a rule is reworded in
one file and quoted stale in another, a gate names a rule that was renamed,
a Windows-only case is scheduled on no Windows job. The single-definition
rule (every rule ID is defined exactly once, everything else references the
ID) removes the *copies*; this script removes the *dangling references* and
the structural damage a rewrap leaves behind. It reads only the documents
and needs no peer checkout, so it runs in CI.

Checks, per design set (``SETS`` below):

    ids        every ``FAM-NN`` is defined exactly once, in a file allowed to
               define it, and every referenced ID (ranges like ``IMG-01–09``
               expand) is defined; an ID whose family is not in the set's
               vocabulary is a typo, not a new family
    gates      every ``Gnn`` / ``Gnn-mm`` reference resolves to a matrix row
               or a bulleted gate definition; each matrix row has the six
               columns, a defined PR, a platform in {ubuntu, windows, both},
               and a Windows-only row is scheduled on the Windows-job PR
    prs        every ``PR-n`` reference is a row of the ladder table
    coverage   every rule ID appears in at least one gate row and at least
               one PR row — an invariant nobody tests or ships is prose
    rev        the spec's body (after its first ``## `` heading) never says
               ``rev N``: history belongs in the review log
    env        every environment key named by an ``ENV-*`` rule also appears
               in the gate matrix and in the spec's ``ChildEnv`` contract
    structure  no numbered list item or heading starts mid-line (a rewrap
               that swallowed one), no table row disagrees with its header's
               cell count, no paragraph repeats a 30-character phrase within
               400 characters (a rewrap that duplicated one)

Usage::

    uv run python scripts/check_design_set.py            # every set
    uv run python scripts/check_design_set.py --list     # coverage table

Exit status is 1 if any check fails, 0 otherwise.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class DesignSet:
    name: str
    files: list[Path]  # every document in the set; references are allowed anywhere
    definers: list[Path]  # documents that may define rule IDs (table rows `| **FAM-NN** |`)
    matrix: Path  # the gate matrix (rows `| Gnn-mm | rules | fixture | verdict | reason | platform / PR |`)
    ladder: Path  # the PR table (rows `| PR-n | ... |`)
    families: frozenset[str]
    windows_pr: str = "PR-6"
    no_rev_in_body: list[Path] = field(default_factory=list)
    contract_marker: str = "ChildEnv("


D = ROOT / "docs" / "design"
R = ROOT / "docs" / "reference"

SETS: list[DesignSet] = [
    DesignSet(
        name="powershell-support",
        files=[
            D / "powershell-support-spec.zh.md",
            D / "powershell-support-implementation.zh.md",
            D / "powershell-support-gates.zh.md",
            D / "powershell-support-review-log.zh.md",
            D / "subagent-runtime-safety-plan.zh.md",
            R / "powershell-support-evidence.zh.md",
        ],
        definers=[D / "powershell-support-spec.zh.md", D / "subagent-runtime-safety-plan.zh.md"],
        matrix=D / "powershell-support-gates.zh.md",
        ladder=D / "powershell-support-implementation.zh.md",
        families=frozenset(
            "TOOL SPEC LAUNCH ENV IMG LADDER CFG TOK LOWER WRAP NAME EFF CMD SUB MCP ENG".split()
        ),
        no_rev_in_body=[D / "powershell-support-spec.zh.md"],
    ),
]

# `FAM-NN`, optionally a range `FAM-NN–MM` (en dash or hyphen). The trailing
# lookahead keeps `SHA-256` and `ISO-8601` out.
RULE = re.compile(r"\b([A-Z]{2,6})-(\d{2})(?:[–-](\d{2}))?(?!\d)")
RULE_DEF = re.compile(r"^\*\*([A-Z]{2,6}-\d{2})\*\*$")
GATE_REF = re.compile(r"\bG(\d{2})(?:-(\d{2}))?([a-z])?\b")
GATE_ROW = re.compile(r"^G\d{2}-\d{2}$")
GATE_BULLET = re.compile(r"^\s*- \*\*(G\d{2}[a-z]?)\b")
PR_REF = re.compile(r"\bPR-(\d)\b")
PLATFORM = re.compile(r"^(ubuntu|windows|both) / (PR-\d)$")
REV = re.compile(r"\brev\s*\d+\b", re.IGNORECASE)
MIDLINE_ITEM = re.compile(r"(?<=[。．.;；)）\]*])\s*(?<![\d.])\d{1,2}\.\s")
MIDLINE_HEADING = re.compile(r"\S\s+#{2,6}\s")
ENV_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]*\*?(?:=.*)?$")
LIST_START = re.compile(r"^\s*(\d{1,2}\.|[-*])\s")


# ----------------------------------------------------------------- parsing


def strip_fences(text: str) -> str:
    """Blank out fenced code blocks, keeping line numbers stable."""
    out, fenced = [], False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            fenced = not fenced
            out.append("")
            continue
        out.append("" if fenced else line)
    return "\n".join(out)


def split_cells(line: str) -> list[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", body)]


def tables(text: str) -> list[tuple[int, list[list[str]]]]:
    """Every markdown table as (1-based header line, rows incl. header, excl. the rule line)."""
    lines = strip_fences(text).splitlines()
    found, i = [], 0
    while i < len(lines):
        if lines[i].lstrip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{3,}", lines[i + 1]):
            start, rows = i + 1, [split_cells(lines[i])]
            i += 2
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append(split_cells(lines[i]))
                i += 1
            found.append((start, rows))
        else:
            i += 1
    return found


def rule_refs(text: str, families: frozenset[str]) -> tuple[set[str], list[str]]:
    """Every rule ID the text names (ranges expanded) and every unknown-family token."""
    ids, unknown = set(), []
    for fam, a, b in RULE.findall(text):
        if fam not in families:
            unknown.append(f"{fam}-{a}")
            continue
        lo, hi = int(a), int(b) if b else int(a)
        for n in range(lo, hi + 1):
            ids.add(f"{fam}-{n:02d}")
    return ids, unknown


def paragraphs(text: str) -> list[tuple[int, str]]:
    """Prose paragraphs (fences and tables excluded) as (1-based first line, joined text)."""
    out, cur, start = [], [], None
    for n, line in enumerate(strip_fences(text).splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("|"):
            if cur:
                out.append((start, " ".join(s.strip() for s in cur)))
            cur, start = [], None
            continue
        if start is None:
            start = n
        cur.append(line)
    if cur:
        out.append((start, " ".join(s.strip() for s in cur)))
    return out


# ------------------------------------------------------------------ checks


def check_set(ds: DesignSet, texts: dict[Path, str] | None = None) -> list[str]:
    texts = texts or {p: p.read_text(encoding="utf-8") for p in ds.files}
    fails: list[str] = []
    rel = lambda p: str(p.relative_to(ROOT)) if p.is_absolute() and ROOT in p.parents else str(p)

    # ---- definitions
    defined: dict[str, tuple[Path, int]] = {}
    definition_rows: dict[str, str] = {}
    for p in ds.definers:
        for line_no, rows in tables(texts[p]):
            for k, row in enumerate(rows[1:], 2):
                m = RULE_DEF.match(row[0]) if row else None
                if not m:
                    continue
                rid = m.group(1)
                if rid in defined:
                    fails.append(f"ids: {rid} defined twice — {rel(defined[rid][0])}:{defined[rid][1]} and {rel(p)}:{line_no + k}")
                defined[rid] = (p, line_no + k)
                definition_rows[rid] = " ".join(row)
    for p in ds.files:
        if p in ds.definers:
            continue
        for line_no, rows in tables(texts[p]):
            for k, row in enumerate(rows[1:], 2):
                if row and RULE_DEF.match(row[0]):
                    fails.append(f"ids: {row[0]} looks like a definition outside a definer file — {rel(p)}:{line_no + k}")

    # ---- references
    for p in ds.files:
        ids, unknown = rule_refs(texts[p], ds.families)
        for tok in sorted(set(unknown)):
            fails.append(f"ids: unknown rule family in {tok} — {rel(p)}")
        for rid in sorted(ids - defined.keys()):
            fails.append(f"ids: {rid} referenced but never defined — {rel(p)}")

    # ---- gates
    matrix_rows: list[tuple[int, list[str]]] = []
    for line_no, rows in tables(texts[ds.matrix]):
        if rows and rows[0] and rows[0][0].strip("* ") == "Gate":
            for k, row in enumerate(rows[1:], 2):
                matrix_rows.append((line_no + k, row))
    gate_defs: set[str] = set()
    for line_no, row in matrix_rows:
        if len(row) != 6:
            fails.append(f"gates: row has {len(row)} cells, want 6 — {rel(ds.matrix)}:{line_no}")
            continue
        if not GATE_ROW.match(row[0]):
            fails.append(f"gates: bad gate id {row[0]!r} — {rel(ds.matrix)}:{line_no}")
            continue
        gate_defs.add(row[0])
        gate_defs.add(row[0][:3])
        m = PLATFORM.match(row[5])
        if not m:
            fails.append(f"gates: {row[0]} platform/PR cell {row[5]!r} is not `<ubuntu|windows|both> / PR-n` — {rel(ds.matrix)}:{line_no}")
        elif m.group(1) == "windows" and m.group(2) != ds.windows_pr:
            fails.append(f"gates: {row[0]} is windows-only but scheduled on {m.group(2)}, not {ds.windows_pr} — {rel(ds.matrix)}:{line_no}")
    for p in ds.definers:
        for line in strip_fences(texts[p]).splitlines():
            m = GATE_BULLET.match(line)
            if m:
                gate_defs.add(m.group(1))
    for p in ds.files:
        # Raw text on purpose: a `# G24` in a pseudocode comment is a reference too.
        for num, sub, suffix in GATE_REF.findall(texts[p]):
            ref = f"G{num}" + (f"-{sub}" if sub else "") + suffix
            if ref not in gate_defs:
                fails.append(f"gates: {ref} referenced but no matrix row or gate bullet defines it — {rel(p)}")

    # ---- PRs
    pr_rows: list[str] = []
    pr_defs: set[str] = set()
    for p in ds.files:
        for _, rows in tables(texts[p]):
            for row in rows[1:]:
                if row and re.match(r"^PR-\d$", row[0].strip("* ")):
                    pr_rows.append(" ".join(row))
                    if p == ds.ladder:
                        pr_defs.add(row[0].strip("* "))
    for p in ds.files:
        for n in set(PR_REF.findall(texts[p])):
            if f"PR-{n}" not in pr_defs:
                fails.append(f"prs: PR-{n} referenced but not a row of the ladder table — {rel(p)}")

    # ---- coverage
    gate_row_text = [" ".join(row) for _, row in matrix_rows]
    for rid in sorted(defined):
        own = definition_rows[rid]
        in_gate = any(rid in rule_refs(t, ds.families)[0] for t in gate_row_text) or bool(GATE_REF.search(own))
        in_pr = any(rid in rule_refs(t, ds.families)[0] for t in pr_rows)
        if not in_gate:
            fails.append(f"coverage: {rid} has no gate — no matrix row names it and its definition names no Gnn")
        if not in_pr:
            fails.append(f"coverage: {rid} is delivered by no PR row")

    # ---- rev N in a spec body
    for p in ds.no_rev_in_body:
        text = texts[p]
        cut = text.find("\n## ")
        body = strip_fences(text[cut:]) if cut != -1 else ""
        for m in REV.finditer(body):
            line = text[:cut].count("\n") + body[: m.start()].count("\n") + 1
            fails.append(f"rev: spec body says {m.group(0)!r} — {rel(p)}:{line} (history belongs in the review log)")

    # ---- env keys named by ENV-* rules appear in the matrix and the contract
    spec = next((p for p in ds.definers if ds.contract_marker in texts[p]), None)
    if spec is None:
        fails.append(f"env: no definer contains the contract marker {ds.contract_marker!r}")
    else:
        stext = texts[spec]
        start = stext.find(ds.contract_marker)
        end = stext.find("\n\n", start)
        contract = stext[start : end if end != -1 else len(stext)]
        matrix_text = texts[ds.matrix]
        for rid, row in sorted(definition_rows.items()):
            if not rid.startswith("ENV-"):
                continue
            for tok in re.findall(r"`([^`]+)`", row):
                if not ENV_KEY.match(tok) or not re.search(r"[A-Z]", tok) or len(tok) < 3:
                    continue
                key = tok.split("=", 1)[0].rstrip("*")
                if key not in matrix_text:
                    fails.append(f"env: {rid} names `{key}` but the gate matrix never mentions it")
                if key not in contract:
                    fails.append(f"env: {rid} names `{key}` but the {ds.contract_marker}… contract never mentions it")

    # ---- structure
    for p in ds.files:
        text = texts[p]
        for n, line in enumerate(strip_fences(text).splitlines(), 1):
            if line.lstrip().startswith("|") or line.lstrip().startswith("#"):
                continue
            # A swallowed item can sit on the previous item's first line as
            # easily as on a continuation line, so strip the line's own
            # marker rather than skipping list lines.
            body = LIST_START.sub("", line, count=1)
            if MIDLINE_ITEM.search(body):
                fails.append(f"structure: numbered list item starts mid-line — {rel(p)}:{n}")
            if MIDLINE_HEADING.search(body):
                fails.append(f"structure: heading starts mid-line — {rel(p)}:{n}")
        for line_no, rows in tables(text):
            width = len(rows[0])
            for k, row in enumerate(rows[1:], 2):
                if len(row) != width:
                    fails.append(f"structure: table row has {len(row)} cells, header has {width} — {rel(p)}:{line_no + k}")
        for start, para in paragraphs(text):
            hit = duplicated_phrase(para)
            if hit:
                fails.append(f"structure: paragraph repeats {hit!r} — {rel(p)}:{start}")
    return fails


def duplicated_phrase(para: str, width: int = 30, window: int = 400) -> str | None:
    """A 30-character prose chunk that recurs within 400 characters — a rewrap's duplicate.

    Code spans are blanked first: a paragraph that cites the same file twice
    is normal, a paragraph that says the same sentence twice is damage.
    """
    para = re.sub(r"`[^`]*`", "`", para)
    for i in range(0, max(0, len(para) - width)):
        chunk = para[i : i + width]
        if sum(ch.isalnum() for ch in chunk) < 12:
            continue
        j = para.find(chunk, i + 1)
        if j != -1 and j - i <= window:
            return chunk
    return None


def coverage_table(ds: DesignSet) -> str:
    texts = {p: p.read_text(encoding="utf-8") for p in ds.files}
    defined = []
    for p in ds.definers:
        for _, rows in tables(texts[p]):
            for row in rows[1:]:
                m = RULE_DEF.match(row[0]) if row else None
                if m:
                    defined.append(m.group(1))
    gate_rows = [row for _, rows in tables(texts[ds.matrix]) if rows and rows[0][0].strip("* ") == "Gate" for row in rows[1:]]
    pr_rows = [row for p in ds.files for _, rows in tables(texts[p]) for row in rows[1:] if row and re.match(r"^PR-\d$", row[0].strip("* "))]
    lines = [f"{'rule':<10} {'gates':<40} prs"]
    for rid in defined:
        gs = [r[0] for r in gate_rows if rid in rule_refs(" ".join(r), ds.families)[0]]
        prs = [r[0].strip("* ") for r in pr_rows if rid in rule_refs(" ".join(r), ds.families)[0]]
        lines.append(f"{rid:<10} {', '.join(gs):<40} {', '.join(sorted(set(prs)))}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--list", action="store_true", help="print the rule → gates/PRs coverage table")
    args = ap.parse_args()
    failed = False
    for ds in SETS:
        missing = [p for p in ds.files if not p.exists()]
        if missing:
            print(f"{ds.name}: SKIP — missing {', '.join(str(p.relative_to(ROOT)) for p in missing)}")
            continue
        if args.list:
            print(coverage_table(ds))
            continue
        fails = check_set(ds)
        if fails:
            failed = True
            print(f"{ds.name}: FAIL ({len(fails)})")
            for f in fails:
                print(f"  {f}")
        else:
            print(f"{ds.name}: ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
