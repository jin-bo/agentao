"""`scripts/check_design_set.py` — the checker that holds a split design set together.

Two halves. The live half runs the checker over every set it knows about and
requires zero failures (plus a clean ``mypy --strict`` over the set's typed
contract module), which is what CI enforces. The synthetic half builds a
minimal valid set in ``tmp_path`` and breaks one thing per test, asserting the
specific failure the break must produce — and that the unbroken set passes —
so a check that silently stopped firing cannot hide behind a green live set.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_design_set.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_design_set", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_design_set"] = mod
    spec.loader.exec_module(mod)
    return mod


cds = _load()


# ------------------------------------------------------------------ live sets


@pytest.mark.parametrize("ds", cds.SETS, ids=lambda d: d.name)
def test_live_set_holds_together(ds):
    missing = [p for p in ds.files if not p.exists()]
    if missing:
        pytest.skip(f"set incomplete: {missing}")
    assert cds.check_set(ds) == []


@pytest.mark.parametrize("ds", [d for d in cds.SETS if d.contract is not None], ids=lambda d: d.name)
def test_live_contract_typechecks(ds):
    """The typed contract module is the spec's §3–§5; a type error there is a design seam, not a lint nit."""
    pytest.importorskip("mypy")
    if not ds.contract.exists():
        pytest.skip(f"contract module missing: {ds.contract}")
    assert cds.typecheck_contract(ds) == []


# ------------------------------------------------------------------ synthetic


SPEC = """# spec

**状态：** rev 3

## 1. 范围

## 2. 不变量

| ID | 规则 | 为什么 | 证据 |
|---|---|---|---|
| **TOOL-01** | one tool | because | §2.2 |
| **ENV-01** | child env removes `BASH_ENV` and every `BASH_FUNC_*` | tree | §3.16 |

## 3. 契约

```text
ChildEnv(rung) = base
    remove BASH_ENV, BASH_FUNC_*
```

## 4. 流水线

See G01 and PR-1.
"""

IMPL = """# impl

| PR | 交付 | 实现的规则 | 用户可见 | 依赖 |
|---|---|---|---|---|
| PR-1 | dialect | TOOL-01、ENV-01 | 否 | — |
| PR-6 | windows job | — | 否 | PR-1 |
"""

GATES = """# gates

| Gate | 规则 | 输入 / 夹具 | 预期裁定 | 预期 reason | 平台 / PR |
|---|---|---|---|---|---|
| G01-01 | TOOL-01 | a body | DENY | — | ubuntu / PR-1 |
| G02-01 | ENV-01 | `BASH_ENV` set; `BASH_FUNC_git%%` exported | body only | — | windows / PR-6 |
"""

CONTRACT = '''"""typed contract"""
from __future__ import annotations


def decide(body: str) -> str:
    return "DENY"  # TOOL-01


def child_env(base: dict[str, str]) -> dict[str, str]:
    # ENV-01: remove BASH_ENV, BASH_FUNC_*
    return {k: v for k, v in base.items() if k != "BASH_ENV" and not k.startswith("BASH_FUNC_")}
'''


def make_set(tmp_path: Path, spec: str = SPEC, impl: str = IMPL, gates: str = GATES, contract: str | None = None, **kw):
    d = tmp_path
    (d / "spec.md").write_text(spec, encoding="utf-8")
    (d / "impl.md").write_text(impl, encoding="utf-8")
    (d / "gates.md").write_text(gates, encoding="utf-8")
    files = [d / "spec.md", d / "impl.md", d / "gates.md"]
    extra = {}
    if contract is not None:
        (d / "contract.py").write_text(contract, encoding="utf-8")
        files.append(d / "contract.py")
        extra = {"contract": d / "contract.py"}
    return cds.DesignSet(
        name="synthetic",
        files=files,
        definers=[d / "spec.md"],
        matrix=d / "gates.md",
        ladder=d / "impl.md",
        families=frozenset({"TOOL", "ENV"}),
        no_rev_in_body=[d / "spec.md"],
        **extra,
        **kw,
    )


def failures(tmp_path, **kw) -> list[str]:
    return cds.check_set(make_set(tmp_path, **kw))


def test_baseline_passes(tmp_path):
    assert failures(tmp_path) == []


def test_duplicate_definition(tmp_path):
    spec = SPEC.replace("| **ENV-01** |", "| **TOOL-01** |", 1)
    out = failures(tmp_path, spec=spec)
    assert any("TOOL-01 defined twice" in f for f in out)


def test_reference_to_undefined_rule(tmp_path):
    impl = IMPL.replace("TOOL-01、ENV-01", "TOOL-01、ENV-01、TOOL-02")
    out = failures(tmp_path, impl=impl)
    assert any("TOOL-02 referenced but never defined" in f for f in out)


def test_range_reference_expands(tmp_path):
    spec = SPEC.replace("| **ENV-01** |", "| **TOOL-02** | two | b | c |\n| **ENV-01** |", 1)
    impl = IMPL.replace("TOOL-01、ENV-01", "TOOL-01–02、ENV-01")
    gates = GATES.replace("| G01-01 | TOOL-01 |", "| G01-01 | TOOL-01、TOOL-02 |")
    assert failures(tmp_path, spec=spec, impl=impl, gates=gates) == []


def test_unknown_family_is_a_typo(tmp_path):
    impl = IMPL.replace("TOOL-01、ENV-01", "TOOL-01、ENV-01、TOOOL-01")
    out = failures(tmp_path, impl=impl)
    assert any("unknown rule family in TOOOL-01" in f for f in out)


def test_encoding_names_are_not_rule_ids(tmp_path):
    spec = SPEC.replace("See G01 and PR-1.", "See G01 and PR-1; bodies are UTF-16LE (UTF-16 code units), hashes SHA-256, dates ISO-8601.")
    assert failures(tmp_path, spec=spec) == []


def test_definition_outside_a_definer(tmp_path):
    impl = IMPL + "\n| ID | x |\n|---|---|\n| **TOOL-09** | y |\n"
    out = failures(tmp_path, impl=impl)
    assert any("TOOL-09** looks like a definition outside a definer" in f for f in out)


def test_rule_without_a_gate(tmp_path):
    spec = SPEC.replace("| **ENV-01** |", "| **TOOL-02** | two | b | c |\n| **ENV-01** |", 1)
    impl = IMPL.replace("TOOL-01、ENV-01", "TOOL-01、TOOL-02、ENV-01")
    out = failures(tmp_path, spec=spec, impl=impl)
    assert any("TOOL-02 has no gate" in f for f in out)
    assert not any("TOOL-02 is delivered by no PR" in f for f in out)


def test_rule_without_a_pr(tmp_path):
    spec = SPEC.replace("| **ENV-01** |", "| **TOOL-02** | two | b | c |\n| **ENV-01** |", 1)
    gates = GATES.replace("| G01-01 | TOOL-01 |", "| G01-01 | TOOL-01、TOOL-02 |")
    out = failures(tmp_path, spec=spec, gates=gates)
    assert any("TOOL-02 is delivered by no PR row" in f for f in out)


def test_gate_named_in_its_own_definition_counts_as_covered(tmp_path):
    spec = SPEC.replace("| **ENV-01** |", "| **TOOL-02** | two (G01) | b | c |\n| **ENV-01** |", 1)
    impl = IMPL.replace("TOOL-01、ENV-01", "TOOL-01、TOOL-02、ENV-01")
    assert failures(tmp_path, spec=spec, impl=impl) == []


def test_windows_row_must_land_on_the_windows_pr(tmp_path):
    gates = GATES.replace("windows / PR-6", "windows / PR-1")
    out = failures(tmp_path, gates=gates)
    assert any("G02-01 is windows-only but scheduled on PR-1, not PR-6" in f for f in out)


def test_platform_cell_shape(tmp_path):
    gates = GATES.replace("ubuntu / PR-1", "linux / PR-1")
    out = failures(tmp_path, gates=gates)
    assert any("G01-01 platform/PR cell" in f for f in out)


def test_matrix_row_needs_six_cells(tmp_path):
    gates = GATES.replace("| G01-01 | TOOL-01 | a body | DENY | — | ubuntu / PR-1 |", "| G01-01 | TOOL-01 | a body | DENY | ubuntu / PR-1 |")
    out = failures(tmp_path, gates=gates)
    assert any("row has 5 cells, want 6" in f for f in out)


def test_dangling_gate_reference(tmp_path):
    spec = SPEC.replace("See G01 and PR-1.", "See G01, G09-03 and PR-1.")
    out = failures(tmp_path, spec=spec)
    assert any("G09-03 referenced but no matrix row" in f for f in out)


def test_gate_bullet_in_a_definer_defines_a_gate(tmp_path):
    spec = SPEC.replace("See G01 and PR-1.", "See G01, G13b and PR-1.\n\n- **G13b · sub-agent half** holds.")
    assert failures(tmp_path, spec=spec) == []


def test_dangling_pr_reference(tmp_path):
    spec = SPEC.replace("See G01 and PR-1.", "See G01 and PR-4.")
    out = failures(tmp_path, spec=spec)
    assert any("PR-4 referenced but not a row of the ladder" in f for f in out)


def test_rev_in_spec_body(tmp_path):
    spec = SPEC.replace("## 1. 范围", "## 1. 范围\n\nAs rev 2 said, nothing.")
    out = failures(tmp_path, spec=spec)
    assert any("spec body says 'rev 2'" in f for f in out)
    # the header's own status line is allowed
    assert not any("rev 3" in f for f in out)


def test_env_key_missing_from_matrix(tmp_path):
    gates = GATES.replace("`BASH_ENV` set; ", "")
    out = failures(tmp_path, gates=gates)
    assert any("ENV-01 names `BASH_ENV` but the gate matrix never mentions it" in f for f in out)


def test_env_key_missing_from_contract(tmp_path):
    spec = SPEC.replace("    remove BASH_ENV, BASH_FUNC_*", "    remove BASH_ENV")
    out = failures(tmp_path, spec=spec)
    assert any("ENV-01 names `BASH_FUNC_` but the ChildEnv(… contract never mentions it" in f for f in out)


def test_numbered_item_swallowed_mid_line(tmp_path):
    spec = SPEC.replace("## 1. 范围", "## 1. 范围\n\n1. first item does this. 2. second item was swallowed here.")
    out = failures(tmp_path, spec=spec)
    assert any("numbered list item starts mid-line" in f for f in out)


def test_version_number_is_not_a_swallowed_item(tmp_path):
    spec = SPEC.replace("## 1. 范围", "## 1. 范围\n\nPython 3.12. It ships bash 3.2.57 and PowerShell 7.4 too.")
    assert failures(tmp_path, spec=spec) == []


def test_heading_swallowed_mid_line(tmp_path):
    spec = SPEC.replace("## 1. 范围", "## 1. 范围\n\nsome prose ## 1.1 swallowed heading")
    out = failures(tmp_path, spec=spec)
    assert any("heading starts mid-line" in f for f in out)


def test_duplicated_phrase_from_a_rewrap(tmp_path):
    dup = "the cancelled token must reach the in-flight call on the owner loop"
    spec = SPEC.replace("## 1. 范围", f"## 1. 范围\n\nSo {dup}, and then {dup} again.")
    out = failures(tmp_path, spec=spec)
    assert any("paragraph repeats" in f for f in out)


def test_table_row_cell_count(tmp_path):
    impl = IMPL.replace("| PR-6 | windows job | — | 否 | PR-1 |", "| PR-6 | windows job | — | 否 |")
    out = failures(tmp_path, impl=impl)
    assert any("table row has 4 cells, header has 5" in f for f in out)


def test_fenced_blocks_are_exempt_from_structure_checks_only(tmp_path):
    # Structure checks (mid-line items, headings, repeats) ignore fences: a
    # pseudocode block is allowed to look like anything.
    spec = SPEC.replace("## 4. 流水线", "## 4. 流水线\n\n```text\n1. a. 2. b  ## not a heading\n```")
    assert failures(tmp_path, spec=spec) == []
    # Reference checks do not: a typo in a `# SPEC-01` comment is a dangling
    # reference like any other.
    spec = SPEC.replace("## 4. 流水线", "## 4. 流水线\n\n```text\nx = 1   # G77-01 TOOL-99\n```")
    out = failures(tmp_path, spec=spec)
    assert any("TOOL-99 referenced but never defined" in f for f in out)
    assert any("G77-01 referenced but no matrix row" in f for f in out)


# ---------------------------------------------------------- typed contract module


def test_contract_module_baseline_passes(tmp_path):
    assert failures(tmp_path, contract=CONTRACT) == []


def test_contract_module_is_the_env_contract(tmp_path):
    # With a contract module the whole module is the contract: the spec's own
    # `ChildEnv(` block is no longer consulted, and a key the module lacks fails.
    spec = SPEC.replace("    remove BASH_ENV, BASH_FUNC_*", "    remove BASH_ENV")
    assert failures(tmp_path, spec=spec, contract=CONTRACT) == []
    contract = CONTRACT.replace('not k.startswith("BASH_FUNC_")', "True").replace("remove BASH_ENV, BASH_FUNC_*", "remove BASH_ENV")
    out = failures(tmp_path, contract=contract)
    assert any("ENV-01 names `BASH_FUNC_` but the" in f and "contract.py contract never mentions it" in f for f in out)


def test_rule_without_an_anchor_in_the_contract(tmp_path):
    contract = CONTRACT.replace('return "DENY"  # TOOL-01', 'return "DENY"')
    out = failures(tmp_path, contract=contract)
    assert any("anchors: TOOL-01 has no `# TOOL-01` in" in f for f in out)


def test_anchor_exemption_needs_a_reason_and_must_not_be_stale(tmp_path):
    contract = CONTRACT.replace('return "DENY"  # TOOL-01', 'return "DENY"')
    assert failures(tmp_path, contract=contract, anchor_exempt={"TOOL-01": "registration guard, no pipeline branch"}) == []
    out = failures(tmp_path, contract=CONTRACT, anchor_exempt={"TOOL-01": "stale"})
    assert any("TOOL-01 is exempted yet anchored" in f for f in out)
    out = failures(tmp_path, contract=CONTRACT, anchor_exempt={"TOOL-07": "never defined"})
    assert any("exemption for TOOL-07, which is not defined" in f for f in out)
    # An empty reason is not a reason: key membership alone used to suppress the missing-anchor failure.
    out = failures(tmp_path, contract=contract, anchor_exempt={"TOOL-01": "   "})
    assert any("exemption for TOOL-01 carries no reason" in f for f in out)


def _sub_rule_set(anchor: str):
    """A spec with ENV-01a under ENV-01, and a contract that anchors only whichever ID `anchor` names."""
    spec = SPEC.replace("| **ENV-01** | child env removes `BASH_ENV` and every `BASH_FUNC_*` | tree | §3.16 |",
                        "| **ENV-01** | child env removes `BASH_ENV` and every `BASH_FUNC_*` | tree | §3.16 |\n"
                        "| **ENV-01a** | and every `BASH_FUNC_*` entry, folded per platform | tree | §3.16 |")
    contract = CONTRACT.replace("# ENV-01: remove BASH_ENV, BASH_FUNC_*", f"# {anchor}: remove BASH_ENV, BASH_FUNC_*")
    return spec, contract


def test_a_child_anchor_makes_its_parents_exemption_stale(tmp_path):
    """`check_set` accepts a parent through a child's anchor, so the stale-exemption check must look there too."""
    spec, contract = _sub_rule_set("ENV-01a")
    assert failures(tmp_path, spec=spec, contract=contract) == []  # the child anchor carries the parent
    out = failures(tmp_path, spec=spec, contract=contract, anchor_exempt={"ENV-01": "no branch"})
    assert any("ENV-01 is exempted yet anchored" in f for f in out)


def test_changed_since_credits_a_parent_with_its_childs_anchor(tmp_path):
    """A parent and sub-rule added together, anchored only on the child: the packet must not report a seam."""
    ds = make_set(tmp_path, contract=CONTRACT)
    old = {p: p.read_text(encoding="utf-8") for p in ds.files}
    spec, contract = _sub_rule_set("ENV-01a")
    old[tmp_path / "spec.md"] = SPEC.replace("| **ENV-01** | child env removes `BASH_ENV` and every `BASH_FUNC_*` | tree | §3.16 |\n", "")
    (tmp_path / "spec.md").write_text(spec, encoding="utf-8")
    (tmp_path / "contract.py").write_text(contract, encoding="utf-8")
    packet = cds.changed_since(ds, "A", old_texts=old)
    assert "**ENV-01**" in packet and "**ENV-01a**" in packet
    assert "契约里无锚点" not in packet


def test_changed_since_treats_a_changed_child_as_a_changed_parent(tmp_path):
    """A gate naming the parent covers every child, so a changed sub-rule is not a stale gate."""
    ds = make_set(tmp_path)
    old = {p: p.read_text(encoding="utf-8") for p in ds.files}
    spec = SPEC.replace("| **ENV-01** | child env removes `BASH_ENV` and every `BASH_FUNC_*` | tree | §3.16 |",
                        "| **ENV-01** | child env removes `BASH_ENV` and every `BASH_FUNC_*` | tree | §3.16 |\n"
                        "| **ENV-01a** | folded per platform | tree | §3.16 |")
    old[tmp_path / "spec.md"] = spec.replace("folded per platform", "folded")
    (tmp_path / "spec.md").write_text(spec, encoding="utf-8")
    (tmp_path / "gates.md").write_text(GATES.replace("`BASH_FUNC_git%%` exported", "`BASH_FUNC_git%%` exported, folded"), encoding="utf-8")
    packet = cds.changed_since(ds, "A", old_texts=old)
    assert "G02-01（改）" in packet
    assert "门槛改了、它点名的规则定义一条没动" not in packet


def test_contract_module_is_exempt_from_structure_checks_but_not_reference_checks(tmp_path):
    contract = CONTRACT + "\n# 1. a. 2. b  ## not a heading, and this phrase repeats, and this phrase repeats, and this phrase repeats\n"
    assert failures(tmp_path, contract=contract) == []
    contract = CONTRACT + "\nx = 1  # G77-01 TOOL-99\n"
    out = failures(tmp_path, contract=contract)
    assert any("TOOL-99 referenced but never defined" in f for f in out)
    assert any("G77-01 referenced but no matrix row" in f for f in out)


def test_live_contract_is_not_importable_from_the_package():
    """The module is a spec: it lives under docs/ and imports nothing from agentao."""
    for ds in cds.SETS:
        if ds.contract is None or not ds.contract.exists():
            continue
        text = ds.contract.read_text(encoding="utf-8")
        assert "docs" in ds.contract.parts
        assert "import agentao" not in text and "from agentao" not in text


# ---------------------------------------------------------- sub-rules and cell caps


def test_sub_rule_is_covered_via_its_parent_and_anchors_its_parent(tmp_path):
    spec = SPEC.replace("| **ENV-01** |", "| **ENV-01a** | the `BASH_ENV` half | a | b |\n| **ENV-01** |", 1)
    # gates and PRs name only ENV-01; ENV-01a leans on it — allowed, and shown in --list
    assert failures(tmp_path, spec=spec) == []
    contract = CONTRACT.replace("# ENV-01: remove", "# ENV-01a: remove")
    assert failures(tmp_path, spec=spec, contract=contract) == []


def test_a_parent_may_not_lean_on_a_child_gate(tmp_path):
    """The lean is one-directional: a sub-rule may ride its parent's gate, never the reverse.

    The parent row holds the criterion its children specialise, so "one child is gated" would
    let the parent's own MUST ship with no gate pointing at it.
    """
    spec = SPEC.replace("| **ENV-01** |", "| **ENV-01a** | the `BASH_FUNC_*` half | a | b |\n| **ENV-01** |", 1)
    gates = GATES.replace("| G02-01 | ENV-01 |", "| G02-01 | ENV-01a |")
    out = failures(tmp_path, spec=spec, gates=gates)
    assert any("coverage: ENV-01 has no gate" in f for f in out)
    assert not any("coverage: ENV-01a has no gate" in f for f in out)  # the child rides the parent's PR row


def test_sub_rule_without_a_parent_row(tmp_path):
    spec = SPEC.replace("| **ENV-01** |", "| **TOOL-02a** | half | a | b |\n| **ENV-01** |", 1)
    impl = IMPL.replace("TOOL-01、ENV-01", "TOOL-01、TOOL-02a、ENV-01")
    gates = GATES.replace("| G01-01 | TOOL-01 |", "| G01-01 | TOOL-01、TOOL-02a |")
    out = failures(tmp_path, spec=spec, impl=impl, gates=gates)
    assert any("sub-rules TOOL-02a have no parent row TOOL-02" in f for f in out)


def test_sub_rule_reference_resolves_exactly(tmp_path):
    impl = IMPL.replace("TOOL-01、ENV-01", "TOOL-01、ENV-01、ENV-01b")
    out = failures(tmp_path, impl=impl)
    assert any("ENV-01b referenced but never defined" in f for f in out)


def test_rule_cell_caps(tmp_path):
    long_rule = "。".join(["a rule sentence that goes on"] * 6)
    spec = SPEC.replace("| **TOOL-01** | one tool |", f"| **TOOL-01** | {long_rule} |")
    out = failures(tmp_path, spec=spec, rule_cell_max_bytes=100, rule_cell_max_sentences=3)
    assert any("size: TOOL-01 rule cell is" in f and "> 100" in f for f in out)
    assert any("size: TOOL-01 rule cell has 5 full stops > 3" in f for f in out)
    assert failures(tmp_path, rule_cell_max_bytes=100, rule_cell_max_sentences=3) == []


def test_why_cell_cap(tmp_path):
    spec = SPEC.replace("| **TOOL-01** | one tool | because |", "| **TOOL-01** | one tool | because, and because, and because again |")
    out = failures(tmp_path, spec=spec, why_cell_max_bytes=20)
    assert any("size: TOOL-01 why cell is" in f for f in out)


# ---------------------------------------------------------- review packet


def test_changed_since_packet_names_the_seams(tmp_path):
    ds = make_set(tmp_path, contract=CONTRACT)
    old = {p: p.read_text(encoding="utf-8") for p in ds.files}
    # rev A → work tree: ENV-01 reworded with no gate touched; TOOL-01's gate reworded with no rule touched; TOOL-02 added
    spec = SPEC.replace("child env removes `BASH_ENV`", "child env drops `BASH_ENV`")
    spec = spec.replace("| **ENV-01** |", "| **TOOL-02** | two | b | c |\n| **ENV-01** |", 1)
    gates = GATES.replace("| G01-01 | TOOL-01 | a body |", "| G01-01 | TOOL-01 | a different body |")
    (tmp_path / "spec.md").write_text(spec, encoding="utf-8")
    (tmp_path / "gates.md").write_text(gates, encoding="utf-8")
    packet = cds.changed_since(ds, "A", old_texts=old)
    assert "新增规则" in packet and "**TOOL-02**" in packet and "**无门槛**" in packet and "**契约里无锚点**" in packet
    assert "**ENV-01**" in packet and "定义改了、门槛一行没动" in packet
    assert "−「removes」" in packet and "+「drops」" in packet
    assert "G01-01（改）" in packet and "门槛改了、它点名的规则定义一条没动" in packet


def test_changed_since_counts_a_gate_named_in_the_definition(tmp_path):
    """`coverage` accepts a `Gnn` in the rule's own row — the SUB / MCP / ENG shape — so the packet must too."""
    ds = make_set(tmp_path)
    old = {p: p.read_text(encoding="utf-8") for p in ds.files}
    spec = SPEC.replace("| **TOOL-01** | one tool | because | §2.2 |",
                        "| **TOOL-01** | one tool, gated by G05 | because | §2.2 |")
    (tmp_path / "spec.md").write_text(spec, encoding="utf-8")
    packet = cds.changed_since(ds, "A", old_texts=old)
    line = next(l for l in packet.splitlines() if l.startswith("- **TOOL-01**"))
    assert "无门槛" not in line
    # The matrix does not hold G05, so the packet cannot diff it — and must not claim it changed.
    assert "G05（定义内引用，未比对）" in packet and "G05（改）" not in packet


def test_changed_since_does_not_treat_a_sibling_as_changed(tmp_path):
    """A changed sub-rule covers a gate naming its *parent* — never one naming an unchanged sibling."""
    two_children = ("| **ENV-01a** | folded per platform | tree | §3.16 |\n"
                    "| **ENV-01b** | and the reserved keys | tree | §3.16 |")
    spec = SPEC.replace("| **ENV-01** | child env removes `BASH_ENV` and every `BASH_FUNC_*` | tree | §3.16 |",
                        "| **ENV-01** | child env removes `BASH_ENV` and every `BASH_FUNC_*` | tree | §3.16 |\n" + two_children)
    # The matrix gate names ENV-01b only; the edit changes ENV-01a only. The gate moved, its rule did not.
    gates = GATES.replace("| G02-01 | ENV-01 |", "| G02-01 | ENV-01b |")
    ds = make_set(tmp_path, spec=spec, gates=gates)
    old = {p: p.read_text(encoding="utf-8") for p in ds.files}
    old[tmp_path / "spec.md"] = spec.replace("folded per platform", "folded")
    old[tmp_path / "gates.md"] = gates.replace("`BASH_FUNC_git%%` exported", "`BASH_FUNC_git%%` set")
    packet = cds.changed_since(ds, "A", old_texts=old)
    assert "G02-01（改）→ ENV-01b" in packet
    assert "门槛改了、它点名的规则定义一条没动" in packet


def test_changed_since_packet_uses_check_sets_anchor_scope(tmp_path):
    """The packet may only flag a missing anchor where `check_set` would actually require one."""
    spec = SPEC.replace("| **ENV-01** |", "| **TOOL-02** | two | b | c |\n| **ENV-01** |", 1)

    def packet(**kw):
        ds = make_set(tmp_path, contract=CONTRACT, **kw)
        old = {p: p.read_text(encoding="utf-8") for p in ds.files}
        (tmp_path / "spec.md").write_text(spec, encoding="utf-8")
        try:
            return cds.changed_since(ds, "A", old_texts=old)
        finally:
            (tmp_path / "spec.md").write_text(SPEC, encoding="utf-8")

    assert "契约里无锚点" in packet()  # the baseline: spec.md is the anchor definer, TOOL-02 has no branch
    assert "契约里无锚点" not in packet(anchor_definer=tmp_path / "impl.md")  # out of anchor scope
    assert "契约里无锚点" not in packet(anchor_exempt={"TOOL-02": "registration only, no pipeline branch"})


def test_git_reads_are_decoded_as_utf8():
    """`text=True` decodes with the host locale; these documents are Chinese and cp1252/GBK cannot read them."""
    src = SCRIPT.read_text(encoding="utf-8")
    calls = [c for c in src.split("subprocess.run(")[1:]]
    assert calls, "no subprocess.run in the checker — update this guard"
    for call in calls:
        head = call[: call.index(")\n") if ")\n" in call else 400]
        assert "text=True" not in head or 'encoding="utf-8"' in head, head[:200]


def test_changed_since_reads_the_revision_from_git(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    ds = make_set(tmp_path)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "a"], check=True)
    (tmp_path / "spec.md").write_text(SPEC.replace("one tool", "one shell tool"), encoding="utf-8")
    old_root, cds.ROOT = cds.ROOT, tmp_path
    try:
        packet = cds.changed_since(ds, "HEAD")
    finally:
        cds.ROOT = old_root
    assert "**TOOL-01**" in packet and "+「shell 」" in packet


# ---------------------------------------------------------- one home per question


def test_list_shows_a_gate_named_in_the_rules_own_row(tmp_path):
    """`coverage` accepts a `Gnn` in the definition row (the SUB / MCP / ENG shape), so `--list` must show it.

    It used to print an empty gates column for every such rule — the checker said covered, the table a
    reader consults said ungated. `gates_for` is the single home for that question; `--list` now asks it.
    """
    spec = SPEC.replace("| **TOOL-01** | one tool | because | §2.2 |",
                        "| **TOOL-01** | one tool, gated by G05 | because | §2.2 |")
    gates = GATES.replace("| G01-01 | TOOL-01 | a body | DENY | — | ubuntu / PR-1 |\n", "")
    ds = make_set(tmp_path, spec=spec, gates=gates + "\n- **G05** a bulleted gate\n")
    line = next(l for l in cds.coverage_table(ds).splitlines() if l.startswith("TOOL-01"))
    assert "G05" in line


def test_list_still_marks_a_sub_rule_that_leans_on_its_parent(tmp_path):
    spec = SPEC.replace("| **ENV-01** |", "| **ENV-01a** | the `BASH_ENV` half | a | b |\n| **ENV-01** |", 1)
    ds = make_set(tmp_path, spec=spec)
    line = next(l for l in cds.coverage_table(ds).splitlines() if l.startswith("ENV-01a"))
    assert "(via G02-01)" in line


def test_two_matrix_rows_under_one_gate_id(tmp_path):
    """Single definition applies to gates too: the review packet keys its matrix by ID and keeps only one."""
    gates = GATES + "| G01-01 | TOOL-04 | another body | ALLOW | — | ubuntu / PR-1 |\n"
    out = failures(tmp_path, gates=gates)
    assert any("gates: G01-01 has two matrix rows" in f for f in out)


def test_a_docstring_is_not_an_anchor(tmp_path):
    """Every docstring is prose about the design; only a comment or an in-code string marks a branch."""
    contract = CONTRACT.replace('    return "DENY"  # TOOL-01', '    """Implements TOOL-01."""\n    return "DENY"')
    out = failures(tmp_path, contract=contract)
    assert any("anchors: TOOL-01 has no `# TOOL-01` in" in f for f in out)


def test_an_env_key_is_not_satisfied_by_a_substring(tmp_path):
    """`Path` inside `AbsPath` used to answer for the env key `Path` — a check that could not fail."""
    spec = SPEC.replace("child env removes `BASH_ENV` and every `BASH_FUNC_*`",
                        "child env removes `BASH_ENV`, every `BASH_FUNC_*` and `ENVOY`")
    contract = CONTRACT.replace("# ENV-01: remove BASH_ENV, BASH_FUNC_*", "# ENV-01: remove BASH_ENV, BASH_FUNC_*, ENVOYAGE")
    out = failures(tmp_path, spec=spec, contract=contract)
    assert any("ENV-01 names `ENVOY` but the" in f and "contract never mentions it" in f for f in out)
    # ...but a glob key is a prefix, and a longer identifier does answer it (`BASH_FUNC_git` in the matrix).
    assert not any("BASH_FUNC_" in f for f in out)


def test_a_line_repeated_immediately_below_itself_is_reported(tmp_path):
    """An insert that meant to be a replace. `duplicated_phrase` needs 30 characters; a heading is shorter."""
    spec = SPEC.replace("## 4. 流水线", "## 4. 流水线\n## 4. 流水线")
    assert any("line repeats the one above it" in f for f in failures(tmp_path, spec=spec))
    assert not any("line repeats" in f for f in failures(tmp_path))  # blank lines and ordinary prose do not


def test_a_subrule_inherits_the_gate_its_parent_names_in_its_own_row(tmp_path):
    """The parent/child lean applies to both coverage routes, not only to matrix rows.

    A parent gated by a `Gnn` in its own definition row (the SUB / MCP / ENG shape) could otherwise never be
    given a sub-rule: route (1) leans on the parent, route (2) did not, so the child read as ungated.
    """
    spec = SPEC.replace("| **ENV-01** |", "| **TOOL-04** | uses G01 | because | §2.5 |\n| **TOOL-04a** | narrower | because | §2.5 |\n| **ENV-01** |")
    impl = IMPL.replace("| PR-1 | dialect | TOOL-01、ENV-01 |", "| PR-1 | dialect | TOOL-01、TOOL-04、ENV-01 |")
    out = failures(tmp_path, spec=spec, impl=impl)
    assert not any("TOOL-04a has no gate" in f for f in out), out
    assert not any("TOOL-04 has no gate" in f for f in out), out


def test_a_silent_nonzero_typecheck_is_a_failure_not_a_pass(tmp_path, monkeypatch):
    """mypy killed by the OOM reaper exits nonzero and says nothing; [] there reports a gate that never ran."""
    # The result is what is under test, not the plumbing that produced it. Faking the
    # interpreter with a shell script needed a shebang and an executable bit, neither of
    # which Windows has — it answered ``WinError 193`` instead of exiting 137.
    def _silent(*args, **kwargs):
        return subprocess.CompletedProcess(args=args[0], returncode=137, stdout="", stderr="")

    monkeypatch.setattr(cds.subprocess, "run", _silent)
    ds = make_set(tmp_path, contract=CONTRACT)
    out = cds.typecheck_contract(ds)
    assert out and "exited 137" in out[0], out


def test_an_env_key_folds_to_uppercase_not_to_any_case(tmp_path):
    """`Path` and `PATH` are one Windows key; a lowercase `path` parameter is not that key.

    Whole-identifier matching alone still let the contract's 47 ordinary `path` parameters answer for the
    env key — the same vacuity one level down. Two spellings answer: the key as written and its uppercase
    form.
    """
    spec = SPEC.replace("child env removes `BASH_ENV` and every `BASH_FUNC_*`",
                        "child env removes `BASH_ENV`, every `BASH_FUNC_*` and `Tmpdir`")
    gates = GATES.replace("| G01-01 |", "| G01-02 | ENV-01 | TMPDIR | pinned | — | ubuntu / PR-1 |\n| G01-01 |")
    lower = CONTRACT.replace("def child_env(base: dict[str, str])", "def child_env(tmpdir: str, base: dict[str, str])")
    out = failures(tmp_path, spec=spec, gates=gates, contract=lower)
    assert any("ENV-01 names `Tmpdir` but the" in f and "contract never mentions it" in f for f in out)
    assert not any("gate matrix never mentions it" in f for f in out)  # the matrix spells it TMPDIR, which answers
    upper = CONTRACT.replace("# ENV-01: remove BASH_ENV, BASH_FUNC_*", "# ENV-01: remove BASH_ENV, BASH_FUNC_*, TMPDIR")
    assert not any("`Tmpdir`" in f for f in failures(tmp_path, spec=spec, gates=gates, contract=upper))


def test_the_why_cap_is_found_by_header_not_by_column_number(tmp_path):
    """A definer whose third column is not `为什么` has no why cell — measuring it reports a pointer as an argument."""
    long_third = "x" * 40
    spec = SPEC.replace("| ID | 规则 | 为什么 | 证据 |", "| ID | 规则 | 定义所在 | 门槛 |")
    spec = spec.replace("| **TOOL-01** | one tool | because |", f"| **TOOL-01** | one tool | {long_third} |")
    assert not any("why cell" in f for f in failures(tmp_path, spec=spec, why_cell_max_bytes=20))
    # The same over-long cell under a `为什么` header is still caught.
    assert any("why cell" in f for f in failures(
        tmp_path, spec=SPEC.replace("| **TOOL-01** | one tool | because |", f"| **TOOL-01** | one tool | {long_third} |"),
        why_cell_max_bytes=20))
