"""`scripts/check_design_set.py` — the checker that holds a split design set together.

Two halves. The live half runs the checker over every set it knows about and
requires zero failures, which is what CI enforces. The synthetic half builds
a minimal valid set in ``tmp_path`` and breaks one thing per test, asserting
the specific failure the break must produce — and that the unbroken set
passes — so a check that silently stopped firing cannot hide behind a green
live set.
"""

from __future__ import annotations

import importlib.util
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


def make_set(tmp_path: Path, spec: str = SPEC, impl: str = IMPL, gates: str = GATES):
    d = tmp_path
    (d / "spec.md").write_text(spec, encoding="utf-8")
    (d / "impl.md").write_text(impl, encoding="utf-8")
    (d / "gates.md").write_text(gates, encoding="utf-8")
    return cds.DesignSet(
        name="synthetic",
        files=[d / "spec.md", d / "impl.md", d / "gates.md"],
        definers=[d / "spec.md"],
        matrix=d / "gates.md",
        ladder=d / "impl.md",
        families=frozenset({"TOOL", "ENV"}),
        no_rev_in_body=[d / "spec.md"],
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
