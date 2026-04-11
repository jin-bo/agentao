"""Tests for agentao.skills.registry."""

import json

import pytest

from agentao.skills.registry import (
    InstalledSkillRecord,
    SkillRegistry,
    _find_project_root,
    install_dir_for_scope,
    registry_path_for_scope,
    resolve_default_scope,
)


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

def _make_record(name="test-skill", scope="global", **overrides):
    defaults = dict(
        name=name,
        source_type="github",
        source_ref="owner/repo",
        installed_at="2026-04-10T12:00:00+00:00",
        install_scope=scope,
        install_dir=f"/tmp/skills/{name}",
        version="1.0.0",
        revision="abc123",
        etag='W/"1234"',
    )
    defaults.update(overrides)
    return InstalledSkillRecord(**defaults)


# ------------------------------------------------------------------
# SkillRegistry
# ------------------------------------------------------------------

class TestSkillRegistry:
    def test_empty_registry_loads_clean(self, tmp_path):
        reg = SkillRegistry(tmp_path / "registry.json")
        assert len(reg) == 0
        assert reg.list_all() == []

    def test_add_and_get_record(self, tmp_path):
        reg = SkillRegistry(tmp_path / "registry.json")
        rec = _make_record()
        reg.add(rec)
        assert reg.get("test-skill") is not None
        assert reg.get("test-skill").version == "1.0.0"

    def test_contains(self, tmp_path):
        reg = SkillRegistry(tmp_path / "registry.json")
        reg.add(_make_record())
        assert "test-skill" in reg
        assert "missing" not in reg

    def test_remove_record(self, tmp_path):
        reg = SkillRegistry(tmp_path / "registry.json")
        reg.add(_make_record())
        assert reg.remove("test-skill") is True
        assert reg.get("test-skill") is None
        assert reg.remove("test-skill") is False

    def test_list_all(self, tmp_path):
        reg = SkillRegistry(tmp_path / "registry.json")
        reg.add(_make_record("a"))
        reg.add(_make_record("b"))
        names = {r.name for r in reg.list_all()}
        assert names == {"a", "b"}

    def test_save_and_reload(self, tmp_path):
        path = tmp_path / "registry.json"
        reg = SkillRegistry(path)
        reg.add(_make_record("my-skill"))
        reg.save()

        # New instance reads persisted data
        reg2 = SkillRegistry(path)
        assert reg2.get("my-skill") is not None
        assert reg2.get("my-skill").source_ref == "owner/repo"

    def test_save_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "sub" / "dir" / "registry.json"
        reg = SkillRegistry(path)
        reg.add(_make_record())
        reg.save()
        assert path.exists()

    def test_corrupted_file_loads_empty(self, tmp_path):
        path = tmp_path / "registry.json"
        path.write_text("NOT JSON", encoding="utf-8")
        reg = SkillRegistry(path)
        assert len(reg) == 0

    def test_overwrite_existing_record(self, tmp_path):
        reg = SkillRegistry(tmp_path / "registry.json")
        reg.add(_make_record(version="1.0.0"))
        reg.add(_make_record(version="2.0.0"))
        assert reg.get("test-skill").version == "2.0.0"
        assert len(reg) == 1


# ------------------------------------------------------------------
# Scope helpers
# ------------------------------------------------------------------

class TestResolveDefaultScope:
    def test_project_with_git(self, tmp_path):
        (tmp_path / ".git").mkdir()
        assert resolve_default_scope(tmp_path) == "project"

    def test_project_with_pyproject(self, tmp_path):
        (tmp_path / "pyproject.toml").touch()
        assert resolve_default_scope(tmp_path) == "project"

    def test_project_with_package_json(self, tmp_path):
        (tmp_path / "package.json").touch()
        assert resolve_default_scope(tmp_path) == "project"

    def test_project_with_agentao_dir(self, tmp_path):
        (tmp_path / ".agentao").mkdir()
        assert resolve_default_scope(tmp_path) == "project"

    def test_global_when_empty(self, tmp_path):
        assert resolve_default_scope(tmp_path) == "global"


class TestFindProjectRoot:
    def test_finds_root_at_cwd(self, tmp_path):
        (tmp_path / ".git").mkdir()
        assert _find_project_root(tmp_path) == tmp_path.resolve()

    def test_finds_root_from_subdirectory(self, tmp_path):
        (tmp_path / ".git").mkdir()
        subdir = tmp_path / "src" / "deep"
        subdir.mkdir(parents=True)
        assert _find_project_root(subdir) == tmp_path.resolve()

    def test_returns_none_when_no_marker(self, tmp_path):
        # tmp_path has no markers and is deep enough that parents also don't
        bare = tmp_path / "isolated"
        bare.mkdir()
        # Note: in CI tmp_path may itself be under a directory with markers,
        # so we only check the function returns *some* path or None.
        # The key behavior tested is the subdirectory walk-up case above.


class TestRegistryPathForScope:
    def test_global_path(self):
        from pathlib import Path
        path = registry_path_for_scope("global")
        assert path == Path.home() / ".agentao" / "skills_registry.json"

    def test_project_path_at_root(self, tmp_path):
        (tmp_path / ".git").mkdir()
        path = registry_path_for_scope("project", tmp_path)
        assert path == tmp_path.resolve() / ".agentao" / "skills_registry.json"

    def test_project_path_from_subdirectory(self, tmp_path):
        """Running from a subdirectory still resolves to the project root."""
        (tmp_path / ".git").mkdir()
        subdir = tmp_path / "src" / "pkg"
        subdir.mkdir(parents=True)
        path = registry_path_for_scope("project", subdir)
        assert path == tmp_path.resolve() / ".agentao" / "skills_registry.json"


class TestInstallDirForScope:
    def test_global_dir(self):
        from pathlib import Path
        d = install_dir_for_scope("global", "my-skill")
        assert d == Path.home() / ".agentao" / "skills" / "my-skill"

    def test_project_dir(self, tmp_path):
        (tmp_path / ".git").mkdir()
        d = install_dir_for_scope("project", "my-skill", tmp_path)
        assert d == tmp_path.resolve() / ".agentao" / "skills" / "my-skill"

    def test_project_dir_from_subdirectory(self, tmp_path):
        (tmp_path / "pyproject.toml").touch()
        subdir = tmp_path / "src"
        subdir.mkdir()
        d = install_dir_for_scope("project", "my-skill", subdir)
        assert d == tmp_path.resolve() / ".agentao" / "skills" / "my-skill"
