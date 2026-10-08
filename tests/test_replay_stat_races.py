"""Concurrent replay retention must not break readers or another prune."""

from pathlib import Path

import pytest

from agentao.replay.reader import list_replays, open_replay
from agentao.replay.retention import ReplayRetentionPolicy


@pytest.mark.parametrize("operation", ["list", "open", "prune"])
def test_disappearing_replay_is_skipped_during_stat(tmp_path, monkeypatch, operation):
    directory = tmp_path / ".agentao" / "replays"
    directory.mkdir(parents=True)
    missing = directory / "session.old.jsonl"
    survivor = directory / "session.new.jsonl"
    missing.write_text("{}\n", encoding="utf-8")
    survivor.write_text("{}\n", encoding="utf-8")
    original = Path.stat

    def concurrent_stat(path, *args, **kwargs):
        if path == missing:
            raise FileNotFoundError("another process pruned this replay")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", concurrent_stat)
    if operation == "list":
        assert [meta.path for meta in list_replays(tmp_path)] == [survivor]
    elif operation == "open":
        assert open_replay("session", project_root=tmp_path).path == survivor
    else:
        assert ReplayRetentionPolicy(max_instances=1).prune(tmp_path) == []
    assert survivor.exists()
