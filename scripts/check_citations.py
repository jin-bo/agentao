#!/usr/bin/env python3
"""Verify the citations in a cross-repo comparison document.

Five checks, run against the **commits the document itself names** — the
anchors are parsed out of its ``**Anchors:**`` line rather than configured
here, so this script cannot silently disagree with the document about which
tree it is checking.

    1. resolve   every ``path:line`` resolves to a unique, non-blank line
    2. roots     no citation is *shorter* than its repo's conventional root
    3. quotes    every ``*"…"*`` excerpt lies inside the range cited beside it
    4. twins     the en/zh pair carry an identical citation sequence
    5. tables    every markdown table has a consistent cell count per row

Checks 1–3 need the peer repositories checked out locally at those anchors.
When a repo is missing they are **skipped with a message, never passed** —
a checker that quietly reports success on an absent input is worse than no
checker. Checks 4 and 5 read only the documents and always run.

Usage::

    uv run python scripts/check_citations.py \\
        docs/design/builtin-tools-four-way-codex-gemini-pi-agentao.md

    --repos DIR   directory holding the peer worktrees (default: the parent
                  of this repository)
    -v            list every deliberate exception as it is skipped

Exit status is 1 if any check fails, 0 otherwise (including when checks are
skipped for want of a repo — that is reported, not failed, so the script
stays usable in an environment that has only this repo).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# Deliberate exceptions. Each needs a reason, because an unexplained entry
# here is indistinguishable from a defect someone silenced.
# --------------------------------------------------------------------------

#: Citations that intentionally do not resolve. They are quoted *as* broken
#: citations inside §10's "a basename is not an address" method note.
UNRESOLVABLE_BY_DESIGN = {
    "apply_patch.rs:73": "§10 counter-example: 4 files share this basename in codex",
    "config.ts:1135": "§10 counter-example: 6 files share this basename in gemini-cli",
    "registry.py:196": "§10 counter-example: 3 files share this basename in agentao",
    "<repo-root>/pyproject.toml:50": "placeholder, not a path",
    "<repo-root>/pyproject.toml:52": "placeholder, not a path",
}

#: Italic-quoted strings that are the document's own phrasing rather than a
#: source excerpt. The two forms are indistinguishable in markdown, so this
#: list is the only way to tell them apart.
NOT_A_SOURCE_QUOTE = {
    "some tools narrowed by declared capability, others admitted and "
    "rejected at execution": "§4's own summary of codex's mixed strategy",
}

#: The path prefix each repo's citations are written relative to. A citation
#: may be *longer* than this (over-qualifying never misleads); it may not be
#: shorter, because two spellings of one directory in one document do.
CONVENTIONAL_ROOT = {
    "codex": "codex-rs/",
    "gemini-cli": "packages/",
    "pi-mono": "packages/coding-agent/src/",
    "agentao": "agentao/",
}

CITATION = re.compile(
    r"`([A-Za-z0-9_./<>-]+\.(?:rs|ts|py|toml|json|md)):(\d+)(?:-(\d+))?[^`]*`"
)
#: Bare token form, for the twin-sequence comparison: every ``path:line``
#: whether or not it sits inside backticks (§10's fenced block has some that
#: do not), truncated at the first line number so a range and a point
#: citation of the same line compare equal.
CITATION_TOKEN = re.compile(r"[A-Za-z0-9_./<>-]+\.(?:rs|ts|py|toml|json|md):\d+")
QUOTE = re.compile(r'\*"([^"]{12,300})"\*')
ANCHOR = re.compile(r"([A-Za-z0-9_-]+)\s+`[^`]*@([0-9a-f]{7,40})`")
#: Leading comment markers, stripped before joining a multi-line quotation:
#: a two-line ``///`` excerpt is verbatim apart from the marker the
#: continuation line carries.
COMMENT_PREFIX = re.compile(r"^\s*(///|//|#+|\*|--)\s?")


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().strip(".")


class Repos:
    """The peer worktrees, read at the anchors the document names."""

    def __init__(self, root: Path, anchors: dict[str, str]) -> None:
        self.root = root
        self.anchors = anchors
        self.trees: dict[str, list[str]] = {}
        self.missing: list[str] = []
        self._files: dict[tuple[str, str], list[str]] = {}
        for name, sha in anchors.items():
            listing = self._git(name, "ls-tree", "-r", "--name-only", sha)
            if listing is None:
                self.missing.append(f"{name}@{sha}")
            else:
                self.trees[name] = listing.splitlines()

    def _git(self, repo: str, *args: str) -> str | None:
        path = self.root / repo
        if not (path / ".git").exists() and not path.is_dir():
            return None
        done = subprocess.run(
            ["git", "-C", str(path), *args], capture_output=True, text=True
        )
        return done.stdout if done.returncode == 0 else None

    @property
    def usable(self) -> bool:
        return not self.missing

    def resolve(self, cited: str) -> list[tuple[str, str]]:
        """Every (repo, path) the citation could name, strictest tier first.

        A looser tier is consulted only when every stricter one is empty, so
        a bare basename can never borrow the uniqueness of an exact match.
        """
        exact = {
            (repo, f)
            for repo, files in self.trees.items()
            for f in files
            if f == cited
        }
        if exact:
            return sorted(exact)

        head, _, rest = cited.partition("/")
        if head in self.trees and rest:
            scoped = {
                (head, f)
                for f in self.trees[head]
                if f == rest or f.endswith("/" + rest)
            }
            if scoped:
                return sorted(scoped)

        return sorted(
            {
                (repo, f)
                for repo, files in self.trees.items()
                for f in files
                if f.endswith("/" + cited)
            }
        )

    def lines(self, repo: str, path: str) -> list[str]:
        key = (repo, path)
        if key not in self._files:
            body = self._git(repo, "show", f"{self.anchors[repo]}:{path}")
            self._files[key] = body.splitlines() if body else []
        return self._files[key]


def parse_anchors(text: str) -> dict[str, str]:
    """Read the anchors out of the document's own header.

    The header's fields are separated by single newlines, not blank lines,
    so the block runs from ``**Anchors:**`` to the next line that opens a
    new bolded field — not to the next paragraph break.
    """
    start = text.find("**Anchors:**")
    if start == -1:
        sys.exit("no '**Anchors:**' field found — cannot tell which commits to read")
    rest = text[start + len("**Anchors:**"):]
    end = re.search(r"\n\*\*[A-Z]", rest)
    block = rest[: end.start()] if end else rest
    anchors = {name: sha for name, sha in ANCHOR.findall(block)}
    if not anchors:
        sys.exit("'**Anchors:**' field names no `<ref>@<sha>` pairs")
    return anchors


def revision_history_span(text: str) -> tuple[int, int]:
    """Character span of the revision-history table, or an empty span.

    Every row that records a fixed citation quotes the **pre-fix** spelling —
    that is the row's evidence — so those citations are deliberately wrong
    and are exempt from the *roots* check. Exempting the table by position
    rather than by listing each spelling keeps the exemption from growing a
    new entry per revision, at the cost of not policing roots inside it; the
    resolve and quote checks still run there, so a citation that points at
    nothing is still caught.
    """
    start = text.find("### Revision history")
    if start == -1:
        start = text.find("### 修订记录")
    if start == -1:
        return (0, 0)
    end = text.find("\n## ", start)
    return (start, end if end != -1 else len(text))


def citations(text: str) -> list[tuple[str, int, int]]:
    out = []
    for m in CITATION.finditer(text):
        start = int(m.group(2))
        end = int(m.group(3)) if m.group(3) else start
        out.append((m.group(1), start, end))
    return out


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------


def check_resolve(text: str, repos: Repos, verbose: bool) -> list[str]:
    failures = []
    for path, start, _ in citations(text):
        token = f"{path}:{start}"
        if token in UNRESOLVABLE_BY_DESIGN:
            if verbose:
                print(f"    skip {token} — {UNRESOLVABLE_BY_DESIGN[token]}")
            continue
        hits = repos.resolve(path)
        if len(hits) != 1:
            where = [f for _, f in hits[:3]]
            failures.append(
                f"{token}: resolves to {len(hits)} files"
                + (f" {where}" if where else "")
            )
            continue
        repo, f = hits[0]
        body = repos.lines(repo, f)
        if not 0 < start <= len(body):
            failures.append(f"{token}: {f} has {len(body)} lines")
        elif not body[start - 1].strip():
            failures.append(f"{token}: {f} line {start} is blank")
    return failures


def check_roots(text: str, repos: Repos, verbose: bool) -> list[str]:
    failures = []
    lo, hi = revision_history_span(text)
    body = text[:lo] + text[hi:]
    for path, start, _ in {(p, s, e) for p, s, e in citations(body)}:
        if f"{path}:{start}" in UNRESOLVABLE_BY_DESIGN or path.startswith("<"):
            continue
        hits = repos.resolve(path)
        if len(hits) != 1:
            continue  # reported by check_resolve
        repo, full = hits[0]
        root = CONVENTIONAL_ROOT.get(repo, "")
        shortest = full[len(root):] if root and full.startswith(root) else full
        # Over-qualifying is fine; only a path shorter than the convention
        # can put two spellings of one directory in the same document.
        if not (path == shortest or path.endswith("/" + shortest) or len(path) >= len(shortest)):
            failures.append(f"{path}:{start}: shorter than {repo}'s root — use {shortest}")
        elif len(path) < len(shortest):
            failures.append(f"{path}:{start}: shorter than {repo}'s root — use {shortest}")
    return failures


def check_quotes(text: str, repos: Repos, verbose: bool) -> list[str]:
    failures = []
    for m in QUOTE.finditer(text):
        quote = normalise(m.group(1))
        if quote in NOT_A_SOURCE_QUOTE:
            if verbose:
                print(f"    skip quote — {NOT_A_SOURCE_QUOTE[quote]}")
            continue
        window = text[max(0, m.start() - 400): m.end() + 400]
        tried, contained = [], False
        for path, start, end in citations(window):
            hits = repos.resolve(path)
            if len(hits) != 1:
                continue
            repo, f = hits[0]
            body = repos.lines(repo, f)[start - 1:end]
            joined = normalise(" ".join(COMMENT_PREFIX.sub("", ln) for ln in body))
            tried.append(f"{path}:{start}-{end}")
            if quote.lower() in joined.lower():
                contained = True
                break
        if tried and not contained:
            failures.append(f'"{quote[:70]}" not inside {", ".join(tried[:3])}')
    return failures


def check_twins(text: str, twin: str) -> list[str]:
    a = CITATION_TOKEN.findall(text)
    b = CITATION_TOKEN.findall(twin)
    if a == b:
        return []
    only_a = [x for x in a if x not in b][:5]
    only_b = [x for x in b if x not in a][:5]
    return [
        f"citation sequences differ ({len(a)} vs {len(b)}); "
        f"en-only {only_a}, zh-only {only_b}"
    ]


def check_tables(text: str, label: str) -> list[str]:
    """A spliced row and an unescaped `|` inside a code span both show up
    here and nowhere else — GFM code spans do not protect the pipe."""
    failures, previous = [], None
    for number, line in enumerate(text.splitlines(), 1):
        if not line.startswith("|"):
            previous = None
            continue
        cells = line.replace("\\|", "").count("|")
        if previous is not None and cells != previous:
            failures.append(
                f"{label} line {number}: {cells} pipes, previous row had {previous}"
            )
        previous = cells
    return failures


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("doc", type=Path)
    ap.add_argument("--repos", type=Path, default=None)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    text = args.doc.read_text(encoding="utf-8")
    twin_path = args.doc.with_suffix("").with_suffix(".zh.md")
    if args.doc.name.endswith(".zh.md"):
        twin_path = Path(str(args.doc).replace(".zh.md", ".md"))
    twin = twin_path.read_text(encoding="utf-8") if twin_path.exists() else None

    repo_root = args.repos or Path(__file__).resolve().parents[2]
    anchors = parse_anchors(text)
    repos = Repos(repo_root, anchors)

    print(f"document : {args.doc}")
    print(f"anchors  : " + ", ".join(f"{k}@{v}" for k, v in anchors.items()))
    print(f"peer root: {repo_root}\n")

    failed = False

    def report(name: str, failures: list[str]) -> None:
        nonlocal failed
        if failures:
            failed = True
            print(f"  FAIL {name} ({len(failures)})")
            for f in failures:
                print(f"       {f}")
        else:
            print(f"  ok   {name}")

    if repos.usable:
        report("resolve", check_resolve(text, repos, args.verbose))
        report("roots", check_roots(text, repos, args.verbose))
        report("quotes", check_quotes(text, repos, args.verbose))
    else:
        print(f"  SKIP resolve/roots/quotes — no worktree for {', '.join(repos.missing)}")
        print(f"       (pass --repos DIR pointing at the peer checkouts)")

    if twin is None:
        print("  SKIP twins — no twin document found")
    else:
        report("twins", check_twins(text, twin))
        report(f"tables ({twin_path.name})", check_tables(twin, twin_path.name))
    report(f"tables ({args.doc.name})", check_tables(text, args.doc.name))

    total = len(CITATION_TOKEN.findall(text))
    print(f"\n{total} citations checked.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
