#!/usr/bin/env python3
"""Hold a split design-document set together.

A design that lives in several files — a spec, a PR ladder, a gate matrix,
an evidence file, a review log, a typed contract module — drifts at the
seams: a rule is reworded in one file and quoted stale in another, a gate
names a rule that was renamed, a Windows-only case is scheduled on no Windows
job, a rule row grows a sixth MUST that no gate can point at. The
single-definition rule (every rule ID is defined exactly once, everything
else references the ID) removes the *copies*; this script removes the
*dangling references*, the *structural damage* a rewrap leaves behind, and
the *unbounded rule cell*. It reads only the documents and needs no peer
checkout, so it runs in CI.

Checks, per design set (``SETS`` below):

    ids        every ``FAM-NN`` (or sub-rule ``FAM-NNa``) is defined exactly
               once, in a file allowed to define it, and every referenced ID
               (ranges like ``IMG-01–09`` expand) is defined; an ID whose
               family is not in the set's vocabulary is a typo, not a new
               family
    gates      every ``Gnn`` / ``Gnn-mm`` reference resolves to a matrix row
               or a bulleted gate definition; each matrix row has the six
               columns, a defined PR, a platform in {ubuntu, windows, both},
               and a Windows-only row is scheduled on the Windows-job PR
    prs        every ``PR-n`` reference is a row of the ladder table
    coverage   every rule ID appears in at least one gate row and at least
               one PR row — an invariant nobody tests or ships is prose. A
               sub-rule ``ENV-06a`` counts as covered when a row names it or
               its parent; a parent must be named itself, because the parent
               row holds the criterion its children specialise and leaning on
               a child would let that MUST ship ungated. ``--list`` shows
               which sub-rules lean on their parent
    anchors    every rule defined in the spec is named (``# FAM-NN``) at
               least once in the typed contract module, or is exempted with a
               reason — a rule with no branch in the contract is prose the
               pipeline cannot reach (review-log method rule 14)
    size       a rule cell stays under the set's byte cap and full-stop (。)
               cap, and a why cell under its byte cap — a cell that outgrows
               them is several rules under one ID, which no gate can point at
    rev        the spec's body (after its first ``## `` heading) never says
               ``rev N``: history belongs in the review log
    env        every environment key named by an ``ENV-*`` rule also appears
               in the gate matrix and in the contract (the typed module, or
               the ``ChildEnv(`` block of a definer when no module exists)
    structure  no numbered list item or heading starts mid-line (a rewrap
               that swallowed one), no table row disagrees with its header's
               cell count, no paragraph repeats a 30-character phrase within
               400 characters (a rewrap that duplicated one). Code files are
               exempt — a ``.py`` is allowed to look like code

The typed contract module itself is checked with ``mypy --strict`` by
``typecheck_contract`` (run by ``main`` and by ``tests/test_design_set.py``),
so a return type that cannot carry what a rule needs, an assignment to a
frozen record, or a dereference of an optional launcher fails here instead
of in the next review round.

Usage::

    uv run python scripts/check_design_set.py                  # every set
    uv run python scripts/check_design_set.py --list           # coverage table
    uv run python scripts/check_design_set.py --changed-since main
        # review packet: rules / gates that changed since a commit, and the
        # seams between them (a definition that changed while none of its
        # gates did, a gate that changed while its rule did not)

Exit status is 1 if any check fails, 0 otherwise.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import io
import re
import subprocess
import sys
import tokenize
from collections.abc import Iterable
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
    contract_marker: str = "ChildEnv("  # legacy: the contract is the block after this marker in a definer
    contract: Path | None = None  # typed contract module; env keys and anchors are checked against its whole text
    anchor_definer: Path | None = None  # rules defined here must be anchored in `contract` (default: definers[0])
    anchor_exempt: dict[str, str] = field(default_factory=dict)  # rule id -> why it has no branch in the contract
    rule_cell_max_bytes: int | None = None
    rule_cell_max_sentences: int | None = None
    why_cell_max_bytes: int | None = None
    why_column: str = "为什么"  # header text of the why column; definers without it have no why cell to cap


D = ROOT / "docs" / "design"
R = ROOT / "docs" / "reference"

SETS: list[DesignSet] = [
    DesignSet(
        name="powershell-support",
        files=[
            D / "powershell-support-spec.zh.md",
            D / "powershell-support-contracts.py",
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
            "TOOL SPEC LAUNCH ENV IMG LADDER CFG TOK LOWER WRAP NAME EFF CMD BASH SUB MCP ENG".split()
        ),
        no_rev_in_body=[D / "powershell-support-spec.zh.md"],
        contract=D / "powershell-support-contracts.py",
        anchor_definer=D / "powershell-support-spec.zh.md",
        # One row, one rule: a cell that outgrows these is several MUSTs under one ID (spec header, 约定).
        rule_cell_max_bytes=900,
        rule_cell_max_sentences=3,
        why_cell_max_bytes=450,
    ),
]

# `FAM-NN`, an optional sub-rule letter `FAM-NNa`, or a range `FAM-NN–MM` (en
# dash or hyphen). The trailing lookahead keeps `SHA-256`, `ISO-8601` and
# `UTF-16LE` out; a bare `UTF-16` has the shape of a rule ID and is exempted
# by name instead.
RULE = re.compile(r"\b([A-Z]{2,6})-(\d{2})([a-z])?(?:[–-](\d{2}))?(?![\dA-Za-z])")
NON_RULE_FAMILIES = frozenset({"UTF"})
RULE_DEF = re.compile(r"^\*\*([A-Z]{2,6}-\d{2}[a-z]?)\*\*$")
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
SENTENCE_END = re.compile(r"。")  # full stops only: `；` separates clauses and list items inside one rule


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


def parent_of(rid: str) -> str:
    """`ENV-06a` → `ENV-06`; a parent is its own parent."""
    return rid[:-1] if rid[-1].islower() else rid


def own_row_gates(own_cells: list[str]) -> set[str]:
    """Route (2) of `gates_for`: the ``Gnn`` references in a rule's own definition row. One home, two callers."""
    return {f"G{num}" + (f"-{sub}" if sub else "") + suffix
            for num, sub, suffix in GATE_REF.findall(" ".join(own_cells))}


def gates_for(rid: str, cells: dict[str, list[str]], gate_row_refs: list[set[str]], gate_ids: list[str]) -> set[str]:
    """Every gate that covers `rid`, by the two routes `coverage` accepts — the single home for that question.

    (1) A matrix row naming `rid` **or its parent**. (2) A ``Gnn`` in the definition row of `rid` **or of its
    parent**, which is how rules whose gates are bulleted definitions rather than matrix rows are covered.
    The lean is one-directional on both routes: a sub-rule may lean on its parent, a parent may NOT lean on a
    child, because the parent row holds the criterion its children specialise. Applying the lean to route (1)
    and not to route (2) is not a smaller version of the rule — it makes coverage depend on *which kind* of
    gate the parent happens to have, so a `SUB` / `MCP` / `ENG` parent (all gated by bulleted definitions)
    could never be given a sub-rule without the checker calling that child ungated.

    `check_set` asks whether this is empty; the review packet prints it; ``--list`` prints it. Restating
    either route in one caller and not the other is how the packet came to report seams the checker had
    accepted, and how ``--list`` came to show an empty gates column for every rule covered by route (2) alone.
    """
    keys = {rid, parent_of(rid)}
    out = {g for g, refs in zip(gate_ids, gate_row_refs) if keys & refs}
    for k in keys:
        out |= own_row_gates(cells.get(k, []))
    return out


def children_of(ids: Iterable[str]) -> dict[str, set[str]]:
    """parent id -> its defined sub-rules. One home: `check_set`, `coverage_table` and `changed_since` all ask it."""
    out: dict[str, set[str]] = {}
    for rid in ids:
        if parent_of(rid) != rid:
            out.setdefault(parent_of(rid), set()).add(rid)
    return out


def anchored(rid: str, children: dict[str, set[str]], contract_ids: set[str]) -> bool:
    """Whether the contract anchors `rid` — a child's `# FAM-NNa` carries its parent. One home, three callers."""
    return bool(({rid} | children.get(rid, set())) & contract_ids)


def contract_anchors(source: str, families: frozenset[str]) -> set[str]:
    """Rule IDs *anchored* in the typed contract: comments and in-code strings, never a docstring.

    The anchor check exists to catch a rule with no branch in the pipeline, so
    what counts has to be a branch marker: a ``# FAM-NN`` comment, or the
    ``Unspecified("FAM-NN …")`` / verdict-reason string of the seam that
    implements it. **Every** docstring is excluded, not only the module's: a
    function docstring is prose *about* the design in exactly the same way, so
    counting an ID named only there would let the real marker be deleted while
    this check stayed green — the one failure it is here to prevent.
    """
    try:
        tree = ast.parse(source)
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (SyntaxError, tokenize.TokenError):
        return rule_refs(source, families)[0]  # unparseable: fall back to the whole text; typecheck_contract reports the syntax error
    doc_lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = node.body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            doc = body[0].value
            doc_lines |= set(range(doc.lineno, (doc.end_lineno or doc.lineno) + 1))
    parts = [t.string for t in tokens
             if t.type == tokenize.COMMENT or (t.type == tokenize.STRING and t.start[0] not in doc_lines)]
    return rule_refs("\n".join(parts), families)[0]


def rule_refs(text: str, families: frozenset[str]) -> tuple[set[str], list[str]]:
    """Every rule ID the text names (ranges expanded, sub-rule letters kept) and every unknown-family token."""
    ids, unknown = set(), []
    for fam, a, letter, b in RULE.findall(text):
        if fam in NON_RULE_FAMILIES:
            continue
        if fam not in families:
            unknown.append(f"{fam}-{a}{letter}")
            continue
        if letter:
            ids.add(f"{fam}-{a}{letter}")
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


def definitions(texts: dict[Path, str], definers: list[Path]) -> dict[str, tuple[Path, int, list[str]]]:
    """Every rule definition row as id -> (file, 1-based line, cells). Duplicates keep the first; callers report them."""
    out: dict[str, tuple[Path, int, list[str]]] = {}
    for p in definers:
        if p not in texts:
            continue
        for line_no, rows in tables(texts[p]):
            for k, row in enumerate(rows[1:], 2):
                m = RULE_DEF.match(row[0]) if row else None
                if m and m.group(1) not in out:
                    out[m.group(1)] = (p, line_no + k, row)
    return out


def gate_rows(text: str) -> list[tuple[int, list[str]]]:
    """Matrix rows (1-based line, cells) of every table whose first header cell is `Gate`."""
    found: list[tuple[int, list[str]]] = []
    for line_no, rows in tables(text):
        if rows and rows[0] and rows[0][0].strip("* ") == "Gate":
            for k, row in enumerate(rows[1:], 2):
                found.append((line_no + k, row))
    return found


def is_code(p: Path) -> bool:
    return p.suffix == ".py"


def names_key(key: str, haystack: str, *, prefix: bool = False) -> bool:
    """Whether `haystack` names the environment key `key` — a whole identifier, not a substring.

    A bare ``in`` cannot fail for a short key: ``Path`` is inside ``AbsPath``, ``join_path`` and
    ``PSModulePath``, so the env check reported "the contract mentions it" about a module that never
    names it. Two spellings answer, not a case fold: the key as written, and its all-uppercase form,
    because Windows folds environment keys and ``PATH`` *is* the key a spec spelled ``Path`` names. A
    full ``IGNORECASE`` would put the same hole back one level down — the contract has 47 whole-identifier
    occurrences of ``path`` as an ordinary parameter name, so a module that never mentioned the variable
    would still answer for it. A key the rule wrote as a glob (``BASH_FUNC_*``) is a **prefix**, so only
    its left edge is anchored — ``BASH_FUNC_git`` answers it.
    """
    tail = "" if prefix else r"(?![A-Za-z0-9_])"
    return any(re.search(rf"(?<![A-Za-z0-9_]){re.escape(spelling)}{tail}", haystack) is not None
               for spelling in (key, key.upper()))


# ------------------------------------------------------------------ checks


def check_set(ds: DesignSet, texts: dict[Path, str] | None = None) -> list[str]:
    texts = texts or {p: p.read_text(encoding="utf-8") for p in ds.files}
    fails: list[str] = []
    rel = lambda p: str(p.relative_to(ROOT)) if p.is_absolute() and ROOT in p.parents else str(p)

    # ---- definitions
    defined: dict[str, tuple[Path, int]] = {}
    definition_cells: dict[str, list[str]] = {}
    definition_headers: dict[str, list[str]] = {}
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
                definition_cells[rid] = row
                definition_headers[rid] = rows[0]
    for p in ds.files:
        if p in ds.definers or is_code(p):
            continue
        for line_no, rows in tables(texts[p]):
            for k, row in enumerate(rows[1:], 2):
                if row and RULE_DEF.match(row[0]):
                    fails.append(f"ids: {row[0]} looks like a definition outside a definer file — {rel(p)}:{line_no + k}")
    children = children_of(defined)
    for rid in sorted(children):
        if rid not in defined:
            fails.append(f"ids: sub-rules {', '.join(sorted(children[rid]))} have no parent row {rid}")

    # ---- references
    for p in ds.files:
        ids, unknown = rule_refs(texts[p], ds.families)
        for tok in sorted(set(unknown)):
            fails.append(f"ids: unknown rule family in {tok} — {rel(p)}")
        for rid in sorted(ids - defined.keys()):
            fails.append(f"ids: {rid} referenced but never defined — {rel(p)}")

    # ---- gates
    matrix_rows = gate_rows(texts[ds.matrix])
    gate_defs: set[str] = set()
    gate_row_lines: dict[str, int] = {}
    for line_no, row in matrix_rows:
        if len(row) != 6:
            fails.append(f"gates: row has {len(row)} cells, want 6 — {rel(ds.matrix)}:{line_no}")
            continue
        if not GATE_ROW.match(row[0]):
            fails.append(f"gates: bad gate id {row[0]!r} — {rel(ds.matrix)}:{line_no}")
            continue
        # Single definition applies to gates too: two rows under one ID are two different expected verdicts,
        # and the review packet keys its matrix by ID — the second row silently replaces the first there.
        if row[0] in gate_row_lines:
            fails.append(f"gates: {row[0]} has two matrix rows — {rel(ds.matrix)}:{gate_row_lines[row[0]]} and {rel(ds.matrix)}:{line_no}")
        gate_row_lines[row[0]] = line_no
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
        if is_code(p):
            continue
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
    gate_ids = [row[0] for _, row in matrix_rows]
    gate_row_refs = [rule_refs(" ".join(row), ds.families)[0] for _, row in matrix_rows]
    pr_row_refs = [rule_refs(t, ds.families)[0] for t in pr_rows]
    for rid in sorted(defined):
        in_gate = bool(gates_for(rid, definition_cells, gate_row_refs, gate_ids))
        in_pr = any({rid, parent_of(rid)} & refs for refs in pr_row_refs)
        if not in_gate:
            fails.append(f"coverage: {rid} has no gate — no matrix row names it and its definition names no Gnn")
        if not in_pr:
            fails.append(f"coverage: {rid} is delivered by no PR row")

    # ---- anchors: every parent rule defined in the spec has a branch in the typed contract
    if ds.contract is not None and ds.contract in texts:
        anchor_definer = ds.anchor_definer or ds.definers[0]
        contract_ids = contract_anchors(texts[ds.contract], ds.families)
        for rid, (p, _) in sorted(defined.items()):
            if p != anchor_definer or parent_of(rid) != rid:
                continue
            if rid in ds.anchor_exempt:
                continue
            if not anchored(rid, children, contract_ids):
                fails.append(f"anchors: {rid} has no `# {rid}` in {rel(ds.contract)} and no exemption")
        for rid in sorted(ds.anchor_exempt):
            if rid not in defined:
                fails.append(f"anchors: exemption for {rid}, which is not defined")
            elif anchored(rid, children, contract_ids):
                fails.append(f"anchors: {rid} is exempted yet anchored in {rel(ds.contract)} — drop the exemption")
            elif not ds.anchor_exempt[rid].strip():
                fails.append(f"anchors: exemption for {rid} carries no reason")

    # ---- size: a rule cell is one rule, not a chapter
    for rid, cells in sorted(definition_cells.items()):
        rule_cell = cells[1] if len(cells) > 1 else ""
        # The why cell is found by *header*, not by position: the set's two definers do not share a layout —
        # the other one's third column is a section pointer, and measuring it against the why cap reports a
        # cell that is not an argument with advice ("move the argument to the evidence file") that cannot apply.
        header = definition_headers.get(rid, [])
        why_idx = next((i for i, h in enumerate(header) if h.strip("* ") == ds.why_column), None)
        why_cell = cells[why_idx] if why_idx is not None and why_idx < len(cells) else ""
        nbytes = len(rule_cell.encode("utf-8"))
        if ds.rule_cell_max_bytes is not None and nbytes > ds.rule_cell_max_bytes:
            fails.append(f"size: {rid} rule cell is {nbytes} B > {ds.rule_cell_max_bytes} — split it into sub-rules ({rid}a, {rid}b, …)")
        nsent = len(SENTENCE_END.findall(rule_cell))
        if ds.rule_cell_max_sentences is not None and nsent > ds.rule_cell_max_sentences:
            fails.append(f"size: {rid} rule cell has {nsent} full stops > {ds.rule_cell_max_sentences} — one ID, one MUST")
        wbytes = len(why_cell.encode("utf-8"))
        if ds.why_cell_max_bytes is not None and wbytes > ds.why_cell_max_bytes:
            fails.append(f"size: {rid} why cell is {wbytes} B > {ds.why_cell_max_bytes} — one sentence; the argument belongs in the evidence file")

    # ---- rev N in a spec body
    for p in ds.no_rev_in_body:
        text = texts[p]
        cut = text.find("\n## ")
        body = strip_fences(text[cut:]) if cut != -1 else ""
        for m in REV.finditer(body):
            line = text[:cut].count("\n") + body[: m.start()].count("\n") + 1
            fails.append(f"rev: spec body says {m.group(0)!r} — {rel(p)}:{line} (history belongs in the review log)")

    # ---- env keys named by ENV-* rules appear in the matrix and the contract
    contract: str | None = None
    contract_name = ""
    if ds.contract is not None and ds.contract in texts:
        contract, contract_name = texts[ds.contract], rel(ds.contract)
    else:
        spec = next((p for p in ds.definers if ds.contract_marker in texts[p]), None)
        if spec is None:
            fails.append(f"env: no definer contains the contract marker {ds.contract_marker!r} and the set has no contract module")
        else:
            stext = texts[spec]
            start = stext.find(ds.contract_marker)
            end = stext.find("\n\n", start)
            contract, contract_name = stext[start : end if end != -1 else len(stext)], f"{ds.contract_marker}…"
    if contract is not None:
        matrix_text = texts[ds.matrix]
        for rid, cells in sorted(definition_cells.items()):
            if not rid.startswith("ENV-"):
                continue
            for tok in re.findall(r"`([^`]+)`", " ".join(cells)):
                if not ENV_KEY.match(tok) or not re.search(r"[A-Z]", tok) or len(tok) < 3:
                    continue
                name = tok.split("=", 1)[0]
                key, glob = name.rstrip("*"), name.endswith("*")
                if not names_key(key, matrix_text, prefix=glob):
                    fails.append(f"env: {rid} names `{key}` but the gate matrix never mentions it")
                if not names_key(key, contract, prefix=glob):
                    fails.append(f"env: {rid} names `{key}` but the {contract_name} contract never mentions it")

    # ---- structure
    for p in ds.files:
        if is_code(p):
            continue
        text = texts[p]
        prev = ""
        for n, line in enumerate(strip_fences(text).splitlines(), 1):
            # Two identical non-blank lines in a row are a batch edit that inserted where it meant to
            # replace. `duplicated_phrase` cannot see this one: it needs 30 characters, and a heading
            # (`## 3. 已否决的备选`) is shorter than that — which is how one shipped into the working tree.
            if line.strip() and line == prev:
                fails.append(f"structure: line repeats the one above it — {rel(p)}:{n}")
            prev = line
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


# Every child this script spawns is bounded. `subprocess.run(timeout=)` is the whole budget here on purpose:
# the script is deliberately import-free of `agentao`, so `capabilities/process.py::run_captured` is out of
# reach — but the failure it guards against (a child that never returns wedging a required CI gate) is the same.
CHILD_TIMEOUT_S = 300.0


def typecheck_contract(ds: DesignSet, python: str = sys.executable) -> list[str]:
    """Run ``mypy --strict`` over the set's typed contract module; each reported line is a failure."""
    if ds.contract is None:
        return []
    if not ds.contract.exists():
        return [f"typecheck: contract module {ds.contract} is missing"]
    proc = subprocess.run(
        [python, "-m", "mypy", "--strict", "--no-error-summary", "--no-color-output", str(ds.contract)],
        capture_output=True,
        text=True,
        encoding="utf-8",  # mypy quotes the offending source line, and these files are Chinese — never the host locale
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,  # a required CI gate: never let a child read the job's stdin, never wait forever
        timeout=CHILD_TIMEOUT_S,
    )
    if proc.returncode == 0:
        return []
    out = (proc.stdout + proc.stderr).strip().splitlines()
    if "No module named mypy" in " ".join(out):
        return ["typecheck: mypy is not installed (uv sync installs the dev group)"]
    # A nonzero exit with nothing on either stream is mypy *dying*, not mypy passing — an OOM kill (-9), a
    # crashed plugin, a segfault. Falling out of this comprehension with [] reports the gate green when it
    # never ran, and this is the one check in the set whose absence nothing else would notice.
    return [f"typecheck: {line}" for line in out if line.strip()] or \
        [f"typecheck: mypy exited {proc.returncode} and said nothing — the type check did not run"]


# ------------------------------------------------------------- reporting


def coverage_table(ds: DesignSet) -> str:
    texts = {p: p.read_text(encoding="utf-8") for p in ds.files}
    defs = definitions(texts, ds.definers)
    matrix = [row for _, row in gate_rows(texts[ds.matrix])]
    gate_ids = [r[0] for r in matrix]
    gate_row_refs = [rule_refs(" ".join(r), ds.families)[0] for r in matrix]
    cells_by_id = {rid: c for rid, (_, _, c) in defs.items()}
    pr_rows = [row for p in ds.files if not is_code(p) for _, rows in tables(texts[p]) for row in rows[1:] if row and re.match(r"^PR-\d$", row[0].strip("* "))]
    lines = [f"{'rule':<11} {'gates':<44} prs"]
    for rid, (_, _, cells) in defs.items():
        # Both routes, through the one home `coverage` uses — a rule gated by a `Gnn` in its own row (the
        # SUB / MCP / ENG shape) is covered, and printing an empty cell for it contradicted the checker.
        own = {g for g, refs in zip(gate_ids, gate_row_refs) if rid in refs} | own_row_gates(cells)
        via = gates_for(rid, cells_by_id, gate_row_refs, gate_ids) - own  # only a sub-rule may lean, and only on its parent
        gates = ", ".join(sorted(own)) if own else ("(via " + ", ".join(sorted(via)) + ")" if via else "")
        keys = {rid, parent_of(rid)}
        prs = sorted({r[0].strip("* ") for r in pr_rows if keys & rule_refs(" ".join(r), ds.families)[0]})
        lines.append(f"{rid:<11} {gates:<44} {', '.join(prs)}")
    return "\n".join(lines)


def resolve_rev(rev: str) -> str | None:
    """The commit ``rev`` names, or None if this repository cannot resolve it."""
    proc = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"],
                          capture_output=True, text=True, encoding="utf-8",
                          stdin=subprocess.DEVNULL, timeout=CHILD_TIMEOUT_S)
    return proc.stdout.strip() or None


def git_show(rev: str, path: Path) -> str | None:
    """The file at ``rev``, or None when it did not exist there.

    Only that. ``changed_since`` resolves ``rev`` before calling this, so a
    failure here cannot also mean "no such revision" — without that, a typo
    (``--changed-since mian``) reports every rule and gate as newly added and
    exits 0, which reads as a review packet rather than as a broken baseline.
    """
    rel = path.relative_to(ROOT) if path.is_absolute() and ROOT in path.parents else path
    # `text=True` alone decodes with the host locale: on Windows without UTF-8 mode that is cp1252 or GBK, and
    # these documents fail to decode under both — the review packet would be unavailable on the one platform the
    # design targets. The working-tree reader already says utf-8; this is the same bytes from a different source.
    proc = subprocess.run(["git", "-C", str(ROOT), "show", f"{rev}:{rel.as_posix()}"],
                          capture_output=True, text=True, encoding="utf-8",
                          stdin=subprocess.DEVNULL, timeout=CHILD_TIMEOUT_S)
    return proc.stdout if proc.returncode == 0 else None


TOKEN = re.compile(r"[A-Za-z0-9_]+|\s+|.", re.DOTALL)


def fragments(old: str, new: str, width: int = 60) -> str:
    """The changed pieces of two cells, as `−「…」 +「…」`, so a reviewer reads the delta and not the row.

    Diffed over tokens (a Latin word, a whitespace run, or one CJK character /
    punctuation mark), not characters: `removes` → `drops` reads as two words,
    not as the letters they happen to share.
    """
    a, b = TOKEN.findall(old), TOKEN.findall(new)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    parts: list[str] = []

    def clip(s: str) -> str:
        return s if len(s) <= width else s[:width] + "…"

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if i2 > i1:
            parts.append("−「" + clip("".join(a[i1:i2])) + "」")
        if j2 > j1:
            parts.append("+「" + clip("".join(b[j1:j2])) + "」")
    return " ".join(parts)


def changed_since(ds: DesignSet, rev: str, texts: dict[Path, str] | None = None, old_texts: dict[Path, str | None] | None = None) -> str:
    """The review packet: what changed in the rule table and the gate matrix since `rev`, and the seams between them."""
    texts = texts or {p: p.read_text(encoding="utf-8") for p in ds.files if p.exists()}
    if old_texts is None:
        if resolve_rev(rev) is None:
            raise ValueError(f"cannot resolve {rev!r} to a commit in this repository")
        old_texts = {p: git_show(rev, p) for p in ds.files}
    old_present = {p: t for p, t in old_texts.items() if t is not None}
    new_defs = definitions(texts, ds.definers)
    old_defs = definitions(old_present, ds.definers)
    new_gates = {row[0]: row for _, row in gate_rows(texts[ds.matrix])} if ds.matrix in texts else {}
    old_gates = {row[0]: row for _, row in gate_rows(old_present[ds.matrix])} if ds.matrix in old_present else {}
    contract_ids = contract_anchors(texts[ds.contract], ds.families) if ds.contract is not None and ds.contract in texts else set()
    new_children = children_of(new_defs)
    new_cells = {rid: c for rid, (_, _, c) in new_defs.items()}

    gate_ids = list(new_gates)
    gate_row_refs = [rule_refs(" ".join(row), ds.families)[0] for row in new_gates.values()]
    def gates_naming(rid: str) -> list[str]:
        return sorted(gates_for(rid, new_cells, gate_row_refs, gate_ids))

    def gate_changed(g: str) -> bool:
        # A gate the matrix does not hold is a bulleted definition; this packet diffs matrix rows only, so
        # "changed" is a question it cannot answer for one — and answering "yes" because it is absent from
        # `old_gates` would report every definition-linked gate as changed on every run.
        return g in new_gates and (g not in old_gates or old_gates[g] != new_gates[g])

    out = [f"# 评审包：{rev} → 工作树（{ds.name}）", ""]
    added = sorted(set(new_defs) - set(old_defs))
    removed = sorted(set(old_defs) - set(new_defs))
    changed = sorted(rid for rid in set(new_defs) & set(old_defs) if new_defs[rid][2][1:] != old_defs[rid][2][1:])
    out.append(f"规则：新增 {len(added)}、删除 {len(removed)}、改动 {len(changed)}；门槛：新增 "
               f"{len(set(new_gates) - set(old_gates))}、删除 {len(set(old_gates) - set(new_gates))}、改动 "
               f"{len([g for g in set(new_gates) & set(old_gates) if old_gates[g] != new_gates[g]])}")
    out.append("")
    if added:
        out.append("## 新增规则")
        for rid in added:
            cells = new_defs[rid][2]
            gs = gates_naming(rid)
            flags = [] if gs else ["**无门槛**"]
            # Every condition `check_set`'s anchor check applies, applied here too: only the anchor definer's
            # own parent rules need an anchor, an exemption is an answer, and a child's anchor carries its
            # parent. Any of the three left out reports a seam the checker itself considers closed.
            if ds.contract is not None and parent_of(rid) == rid and rid not in ds.anchor_exempt \
                    and new_defs[rid][0] == (ds.anchor_definer or ds.definers[0]) \
                    and not anchored(rid, new_children, contract_ids):
                flags.append("**契约里无锚点**")
            out.append(f"- **{rid}**（{len(cells[1].encode()) if len(cells) > 1 else 0} B）门槛：{', '.join(gs) or '—'} {' '.join(flags)}".rstrip())
        out.append("")
    if removed:
        out.append("## 删除规则")
        out.extend(f"- **{rid}**" for rid in removed)
        out.append("")
    if changed:
        out.append("## 改动的规则")
        for rid in changed:
            oc, nc = old_defs[rid][2], new_defs[rid][2]
            o_rule = oc[1] if len(oc) > 1 else ""
            n_rule = nc[1] if len(nc) > 1 else ""
            gs = gates_naming(rid)
            gs_changed = [g for g in gs if gate_changed(g)]
            out.append(f"- **{rid}** 规则格 {len(o_rule.encode())} → {len(n_rule.encode())} B；门槛 {len(gs)} 行，其中改动 {len(gs_changed)}"
                       + ("" if gs_changed or not gs else " —— **定义改了、门槛一行没动：核对门槛是否仍成立**")
                       + ("" if gs else " —— **无门槛**"))
            if o_rule != n_rule:
                out.append(f"  - 规则：{fragments(o_rule, n_rule)}")
            o_why = oc[2] if len(oc) > 2 else ""
            n_why = nc[2] if len(nc) > 2 else ""
            if o_why != n_why:
                out.append(f"  - 为什么：{fragments(o_why, n_why)}")
            if gs:
                out.append("  - 门槛：" + ", ".join(
                    f"{g}（改）" if gate_changed(g) else (f"{g}（定义内引用，未比对）" if g not in new_gates else g) for g in gs))
        out.append("")
    gate_added = sorted(set(new_gates) - set(old_gates))
    gate_changed_ids = sorted(g for g in set(new_gates) & set(old_gates) if old_gates[g] != new_gates[g])
    gate_removed = sorted(set(old_gates) - set(new_gates))
    if gate_added or gate_changed_ids:
        out.append("## 新增或改动的门槛")
        touched = set(added) | set(changed)
        # A gate row may name the parent and cover every child (that is what `coverage` allows), so a
        # changed sub-rule counts for a gate that names its *parent*. It must not count for the parent's
        # other children: folding the parent into `touched` would make an unchanged sibling look changed
        # and swallow the real "this gate moved and its rule did not" warning.
        parents_of_touched = {parent_of(r) for r in touched if parent_of(r) != r}

        def synchronized(r: str) -> bool:
            return r in touched or parent_of(r) in touched or r in parents_of_touched

        for g in gate_added + gate_changed_ids:
            rids = sorted(rule_refs(" ".join(new_gates[g]), ds.families)[0])
            stale = [r for r in rids if r in new_defs and not synchronized(r)]
            note = ""
            if rids and len(stale) == len(rids):
                note = " —— **门槛改了、它点名的规则定义一条没动：核对散文是否也该改**"
            out.append(f"- {g}（{'新增' if g in gate_added else '改'}）→ {', '.join(rids) or '—'}{note}")
            if g in gate_changed_ids:
                out.append(f"  - {fragments(' | '.join(old_gates[g][1:]), ' | '.join(new_gates[g][1:]))}")
        out.append("")
    if gate_removed:
        out.append("## 删除的门槛")
        out.extend(f"- {g}" for g in gate_removed)
        out.append("")
    if ds.contract is not None:
        old_c = old_texts.get(ds.contract)
        new_c = texts.get(ds.contract)
        if old_c is None and new_c is not None:
            out.append(f"## 契约模块：新文件 {ds.contract.name}")
        elif old_c is not None and new_c is not None and old_c != new_c:
            diff = list(difflib.unified_diff(old_c.splitlines(), new_c.splitlines(), lineterm="", n=0))
            out.append(f"## 契约模块：{ds.contract.name} 改动 {sum(1 for l in diff if l.startswith(('+', '-')) and not l.startswith(('+++', '---')))} 行")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--list", action="store_true", help="print the rule → gates/PRs coverage table")
    ap.add_argument("--changed-since", metavar="REV", help="print the review packet for changes since a git revision")
    ap.add_argument("--no-typecheck", action="store_true", help="skip mypy on the contract module")
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
        if args.changed_since:
            try:
                print(changed_since(ds, args.changed_since))
            except ValueError as exc:
                print(f"--changed-since: {exc}", file=sys.stderr)
                return 2
            continue
        fails = check_set(ds)
        if not args.no_typecheck:
            fails += typecheck_contract(ds)
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
