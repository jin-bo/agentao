"""Subsystem constructors no longer fall back to ``os.environ`` /
``Path.cwd()`` / ``Path.home()``.

These tests pin the contract that:

- ``LLMClient`` / ``MemoryManager`` / ``PermissionEngine`` /
  ``load_mcp_config`` raise ``TypeError`` when their required arguments
  are missing — no implicit auto-discovery from the surrounding
  environment.
- When the required arguments *are* supplied, those constructors do
  not silently read ``os.environ`` / ``Path.cwd()`` / ``Path.home()``
  for fields that used to fall back. ``Path.home()`` and ``Path.cwd()``
  are stubbed to a sentinel during the call so any leftover lookup
  surfaces as a wrong path or a deliberate ``RuntimeError``.

The fixture isolates these subsystems from the rest of the test
suite — the autouse ``Agentao``-patching conftest fixture is a
test-only convenience that the production runtime no longer relies on.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

import pytest

from agentao.llm.client import LLMClient
from agentao.mcp.config import load_mcp_config, save_mcp_config
from agentao.memory import MemoryManager
from agentao.permissions import PermissionEngine


@pytest.fixture
def _trip_wires(monkeypatch, tmp_path) -> Iterator[Path]:
    """Trap any ``Path.cwd()`` / ``Path.home()`` lookup during a test.

    ``Path.cwd`` is bound to a clearly-non-project sentinel; ``Path.home``
    raises so a stray read is loud rather than silently steered into
    the user's real home directory.
    """
    sentinel = tmp_path / "__no_cwd_fallback__"
    sentinel.mkdir()
    monkeypatch.setattr(Path, "cwd", staticmethod(lambda: sentinel))

    def _explode_home() -> Path:
        raise RuntimeError(
            "Subsystem constructor read Path.home() — implicit "
            "user-scope fallback is forbidden."
        )

    monkeypatch.setattr(Path, "home", staticmethod(_explode_home))
    yield sentinel


# ---------------------------------------------------------------------------
# Required-argument contracts
# ---------------------------------------------------------------------------


class TestRequiredArgs:
    def test_llm_client_requires_credentials(self):
        """No more env auto-discovery for the four LLM kwargs."""
        with pytest.raises(TypeError):
            LLMClient()  # type: ignore[call-arg]

    def test_llm_client_rejects_empty_strings(self):
        """Empty strings are not silently treated as ``None`` env-fall-through."""
        with pytest.raises(ValueError, match="api_key"):
            LLMClient(api_key="", base_url="https://x", model="m")
        with pytest.raises(ValueError, match="base_url"):
            LLMClient(api_key="k", base_url="", model="m")
        with pytest.raises(ValueError, match="model"):
            LLMClient(api_key="k", base_url="https://x", model="")

    def test_permission_engine_requires_project_root(self):
        with pytest.raises(TypeError):
            PermissionEngine()  # type: ignore[call-arg]

    def test_load_mcp_config_requires_project_root(self):
        with pytest.raises(TypeError):
            load_mcp_config()  # type: ignore[call-arg]

    def test_save_mcp_config_requires_config_dir(self):
        with pytest.raises(TypeError):
            save_mcp_config({"x": {}})  # type: ignore[call-arg]

    def test_memory_manager_requires_project_store(self):
        with pytest.raises(TypeError):
            MemoryManager()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# No implicit reads when required args ARE supplied
# ---------------------------------------------------------------------------


class TestNoImplicitReads:
    def test_llm_client_does_not_read_env(self, monkeypatch, _trip_wires):
        """LLMClient with explicit kwargs must not consult any
        ``LLM_*`` / ``OPENAI_*`` env var. Wipe the entire env and watch
        construction succeed."""
        for name in list(os.environ):
            if name.startswith(("LLM_", "OPENAI_", "ANTHROPIC_", "GEMINI_")):
                monkeypatch.delenv(name, raising=False)

        client = LLMClient(
            api_key="explicit-key",
            base_url="https://explicit.local/v1",
            model="explicit-model",
            log_file=None,
            logger=__import__("logging").getLogger("test"),
        )
        assert client.api_key == "explicit-key"
        assert client.base_url == "https://explicit.local/v1"
        assert client.model == "explicit-model"
        assert client.temperature == 0.2  # in-code default, not env-derived
        assert client.max_tokens == 65536  # in-code default, not env-derived

    def test_permission_engine_no_user_root_skips_user_scope(
        self, tmp_path, _trip_wires
    ):
        """``user_root=None`` must not poke ``Path.home()`` even though
        legacy code used to read ``Path.home() / ".agentao"`` here."""
        engine = PermissionEngine(project_root=tmp_path)
        assert engine.rules == []  # no project file, no user scope

    def test_permission_engine_explicit_user_root(self, tmp_path, _trip_wires):
        """Explicit ``user_root`` is the only way to load cross-project rules."""
        user_root = tmp_path / "user"
        user_root.mkdir()
        (user_root / "permissions.json").write_text(
            '{"rules": [{"tool": "explicit_user_tool", "action": "allow"}]}',
            encoding="utf-8",
        )
        engine = PermissionEngine(project_root=tmp_path, user_root=user_root)
        assert any(r.get("tool") == "explicit_user_tool" for r in engine.rules)

    def test_load_mcp_config_no_user_root_skips_user_scope(
        self, tmp_path, _trip_wires
    ):
        """``user_root=None`` must not consult ``Path.home()``."""
        result = load_mcp_config(project_root=tmp_path)
        assert result == {}

    def test_load_mcp_config_explicit_user_root(self, tmp_path, _trip_wires):
        user_root = tmp_path / "user"
        user_root.mkdir()
        (user_root / "mcp.json").write_text(
            '{"mcpServers": {"only-user": {"command": "x", "args": []}}}',
            encoding="utf-8",
        )
        result = load_mcp_config(project_root=tmp_path, user_root=user_root)
        assert "only-user" in result

    def test_save_mcp_config_uses_explicit_dir(self, tmp_path, _trip_wires):
        out = tmp_path / "explicit"
        path = save_mcp_config({"svc": {}}, config_dir=out)
        assert path == out / "mcp.json"
        assert path.parent == out

    def test_memory_manager_uses_explicit_stores(self, tmp_path, _trip_wires):
        """Pre-built stores must be passed explicitly. ``Path.home()``
        must not be consulted as a default — and after Issue #16 the
        manager itself never imports :mod:`sqlite3` or constructs any
        store, so disk reads happen only at the call site."""
        from agentao.memory import SQLiteMemoryStore
        mgr = MemoryManager(
            project_store=SQLiteMemoryStore.open_or_memory(
                tmp_path / ".agentao" / "memory.db"
            ),
            user_store=None,  # explicit "no user scope"
        )
        # The contract here is just: construction did not crash from a
        # ``Path.home()`` trip-wire raise.
        assert mgr is not None


# ---------------------------------------------------------------------------
# Factory still works end-to-end (sanity check)
# ---------------------------------------------------------------------------


def test_factory_still_works_with_env(tmp_path, monkeypatch):
    """``build_from_environment`` is the single env-reader and must still
    happily construct a runtime."""
    # Defang the factory's ``.env`` auto-discovery — we want this test
    # to assert that the env-derived path is wired correctly, not that
    # the surrounding repo's ``.env`` happens to win.
    monkeypatch.setattr(
        "agentao.embedding.factory.load_dotenv", lambda *a, **kw: False
    )
    # Pin the provider so a leaked ``LLM_PROVIDER`` from a prior test's
    # ``load_dotenv`` (or the developer's real shell env) cannot redirect
    # the factory to ``QWEN_API_KEY`` / ``DEEPSEEK_API_KEY`` / etc.
    monkeypatch.setenv("LLM_PROVIDER", "OPENAI")
    monkeypatch.setenv("OPENAI_API_KEY", "factory-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://factory.local/v1")
    monkeypatch.setenv("OPENAI_MODEL", "factory-model")

    from agentao.embedding import build_from_environment

    agent = build_from_environment(working_directory=tmp_path)
    assert agent.llm.api_key == "factory-key"
    assert agent.llm.base_url == "https://factory.local/v1"
    assert agent.llm.model == "factory-model"
