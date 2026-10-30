"""``--shard I/N`` (tests/conftest.py): the shards partition the suite.

CI splits each Windows test job in two. A test that lands in no shard is never run and
nothing reports it, so the split is checked here rather than trusted.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.support.sharding import parse_shard, shard_of

REPO_ROOT = Path(__file__).resolve().parent.parent
# Small, fast modules with plain and parametrized tests and test classes.
SAMPLE = ["tests/test_memory_session.py", "tests/test_mcp_tool.py", "tests/test_edit_crlf.py"]


def _collect(*args: str) -> list[str]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *SAMPLE, *args],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return [line for line in result.stdout.splitlines() if "::" in line]


@pytest.mark.parametrize("count", [2, 3])
def test_every_test_lands_in_exactly_one_shard(count):
    everything = _collect()
    shards = [_collect("--shard", f"{index}/{count}") for index in range(1, count + 1)]

    assert sorted(sum(shards, [])) == sorted(everything)
    assert all(shards), "a shard of the sample is empty: pick a larger sample"


def test_xdist_workers_agree_on_the_shard():
    expected = len(_collect("--shard", "1/2"))
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-n", "2", *SAMPLE, "--shard", "1/2"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{expected} passed" in result.stdout


def test_the_owner_does_not_depend_on_the_process():
    # crc32, not hash(): PYTHONHASHSEED would give each xdist worker its own answer.
    # Fixed values, so a change of hash shows up here and not as a silently moved split.
    nodeids = ["tests/test_a.py::test_one", "tests/test_a.py::test_two",
               "tests/test_b.py::T::test_c[x]", "tests/test_b.py::T::test_c[y]"]
    assert [shard_of(nodeid, 3) for nodeid in nodeids] == [1, 2, 3, 3]


@pytest.mark.parametrize("spec", ["", "2", "0/2", "3/2", "a/2", "1/0"])
def test_a_malformed_shard_is_refused(spec):
    with pytest.raises(ValueError):
        parse_shard(spec)


def test_a_malformed_shard_is_a_usage_error():
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", SAMPLE[0], "--shard", "3/2"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )

    assert result.returncode == 4
    assert "--shard expects 1 <= I <= N" in result.stderr
