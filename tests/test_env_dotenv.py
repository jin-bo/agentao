"""``safe_load_dotenv`` — NUL hygiene, no-override, and the empty-value carve-out.

The carve-out exists because a plain ``setdefault`` lets an ambient empty
string permanently mask the real value in ``.env``. Claude Code injects
``ANTHROPIC_API_KEY=""`` into child processes, so before this change every
agentao invoked from a Claude Code session reported "no API key" while a
valid key sat unread in ``.env``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentao._env import safe_load_dotenv


@pytest.fixture
def dotenv(tmp_path: Path):
    """Write a ``.env`` and return its path."""

    def _write(text: str) -> Path:
        path = tmp_path / ".env"
        path.write_text(text, encoding="utf-8")
        return path

    return _write


def test_loads_when_key_absent(dotenv, monkeypatch):
    monkeypatch.delenv("AGENTAO_PROBE_KEY", raising=False)
    safe_load_dotenv(dotenv("AGENTAO_PROBE_KEY=from-dotenv\n"))
    import os

    assert os.environ["AGENTAO_PROBE_KEY"] == "from-dotenv"


def test_real_ambient_value_still_wins(dotenv, monkeypatch):
    """No-override is preserved for the case callers actually rely on."""
    import os

    monkeypatch.setenv("AGENTAO_PROBE_KEY", "from-environment")
    safe_load_dotenv(dotenv("AGENTAO_PROBE_KEY=from-dotenv\n"))
    assert os.environ["AGENTAO_PROBE_KEY"] == "from-environment"


@pytest.mark.parametrize("poison", ["", "   ", "\t\n"])
def test_present_but_empty_is_treated_as_absent(dotenv, monkeypatch, poison):
    """An empty / whitespace-only ambient value must not mask ``.env``.

    This is the Claude Code ``ANTHROPIC_API_KEY=""`` case: the key *is* set,
    so ``setdefault`` used to decline, and every downstream ``os.getenv``
    saw ``""`` forever.
    """
    import os

    monkeypatch.setenv("AGENTAO_PROBE_KEY", poison)
    safe_load_dotenv(dotenv("AGENTAO_PROBE_KEY=from-dotenv\n"))
    assert os.environ["AGENTAO_PROBE_KEY"] == "from-dotenv"


def test_nul_bytes_are_stripped(dotenv, monkeypatch):
    """The original reason this wrapper exists: pasted keys with embedded NULs
    crash ``os.environ[k] = v``. Guard it — the assignment moved off
    ``setdefault`` and must keep scrubbing."""
    import os

    monkeypatch.delenv("AGENTAO_PROBE_KEY", raising=False)
    safe_load_dotenv(dotenv('AGENTAO_PROBE_KEY="pre\x00post"\n'))
    assert os.environ["AGENTAO_PROBE_KEY"] == "prepost"


def test_missing_file_is_a_noop(tmp_path, monkeypatch):
    import os

    monkeypatch.delenv("AGENTAO_PROBE_KEY", raising=False)
    safe_load_dotenv(tmp_path / "nope.env")
    assert "AGENTAO_PROBE_KEY" not in os.environ
