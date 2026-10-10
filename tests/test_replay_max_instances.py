"""``replay.max_instances``: one reading for runtime and doctor.

``int()`` is kept as the reader, except for a JSON bool: ``int(True)`` is 1,
so ``"max_instances": true`` kept only the newest replay and pruned the rest
while ``agentao doctor`` reported nothing. Every other accepted value keeps
the count it has today, since a stricter reading would fall back to 20 and
prune *more* (a ``"100"`` would lose 80 replays); doctor warns about those.
"""

from __future__ import annotations

import json
import os

import pytest

from agentao.cli.diagnostics.collectors import _collect_replay
from agentao.cli.diagnostics.models import DiagnosticReport
from agentao.replay import ReplayRetentionPolicy, load_replay_config
from agentao.replay.config import ReplayConfig, check_max_instances

# value -> (count runtime uses, doctor finding level, text in the finding)
CASES = [
    (20, 20, None, None),
    (7.0, 7, None, None),
    (True, 20, "error", "got true"),
    (False, 20, "error", "got false"),
    (2.9, 2, "warning", "not a whole number"),
    (" 7 ", 7, "warning", "is a string"),
    ("100", 100, "warning", "is a string"),
    (0, 20, "error", ">= 1"),
    (0.5, 20, "error", ">= 1"),
    (-3, 20, "error", ">= 1"),
    ("abc", 20, "error", "must be an integer"),
    (None, 20, "error", "must be an integer"),
    ([5], 20, "error", "must be an integer"),
    (float("inf"), 20, "error", "must be an integer"),
    (float("nan"), 20, "error", "must be an integer"),
]


@pytest.mark.parametrize("raw,count,level,text", CASES, ids=[repr(c[0]) for c in CASES])
def test_runtime_and_doctor_read_the_value_the_same_way(tmp_path, raw, count, level, text):
    assert ReplayConfig.from_mapping({"max_instances": raw}).max_instances == count

    check = check_max_instances(raw)
    assert (check.value, check.level) == (count, level)

    report = DiagnosticReport()
    _collect_replay(tmp_path, report, settings_data={"replay": {"max_instances": raw}})
    findings = [f for f in report.findings if "max_instances" in f.message]
    if level is None:
        assert findings == []
    else:
        assert [f.level for f in findings] == [level]
        assert text in findings[0].message
        assert report.ok is (level != "error")


def test_a_missing_value_is_the_default_and_no_finding(tmp_path):
    assert ReplayConfig.from_mapping({"enabled": True}).max_instances == 20
    report = DiagnosticReport()
    _collect_replay(tmp_path, report, settings_data={"replay": {"enabled": True}})
    assert not [f for f in report.findings if "max_instances" in f.message]


def test_true_no_longer_prunes_all_but_one_replay(tmp_path):
    """End to end: ``true`` read from the file, then retention on that count."""
    (tmp_path / ".agentao").mkdir()
    (tmp_path / ".agentao" / "settings.json").write_text(
        json.dumps({"replay": {"enabled": True, "max_instances": True}}), encoding="utf-8"
    )
    replays = tmp_path / ".agentao" / "replays"
    replays.mkdir()
    for i in range(5):
        path = replays / f"s{i}.jsonl"
        path.write_text("{}\n", encoding="utf-8")
        os.utime(path, (1_000_000 + i, 1_000_000 + i))

    cfg = load_replay_config(tmp_path)
    assert cfg.max_instances == 20
    assert ReplayRetentionPolicy(max_instances=cfg.max_instances).prune(tmp_path) == []
    assert len(list(replays.glob("*.jsonl"))) == 5
