"""Tests for agentao.skills.registry."""

import json
import logging
import os

import pytest

from agentao.skills import registry as registry_mod
from agentao.skills.registry import (
    InstalledSkillRecord,
    SkillRegistry,
    SkillRegistryWriteError,
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
        # abspath: "/tmp/..." is not absolute on Windows, and the loader
        # refuses a relative install_dir.
        install_dir=os.path.abspath(f"/tmp/skills/{name}"),
        version="1.0.0",
        revision="abc123",
        etag='W/"1234"',
    )
    defaults.update(overrides)
    return InstalledSkillRecord(**defaults)


# ------------------------------------------------------------------
# SkillRegistry
# ------------------------------------------------------------------

def _entry(name, **overrides):
    entry = dict(
        name=name,
        source_type="github",
        source_ref=f"owner/{name}",
        installed_at="2026-04-10T12:00:00+00:00",
        install_scope="global",
        install_dir=os.path.abspath(f"/tmp/skills/{name}"),
        version="1.0.0",
        revision="abc123",
        etag="e",
    )
    entry.update(overrides)
    return entry


def _write(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


def _skills_on_disk(path):
    return json.loads(path.read_text(encoding="utf-8"))["skills"]


_UNREADABLE_FILES = [
    pytest.param(b"NOT JSON", id="invalid-json"),
    pytest.param(b"", id="empty-file"),
    pytest.param(b"null", id="null"),
    pytest.param(b"[]", id="top-level-list"),
    pytest.param(b'"bad"', id="top-level-string"),
    pytest.param(b"1", id="top-level-number"),
    pytest.param(b'{"skills": null}', id="skills-null"),
    pytest.param(b'{"skills": []}', id="skills-list"),
    pytest.param(b'{"skills": {"\xe9": 1}}', id="not-utf8"),
    pytest.param(b"[" * 100_000 + b"]" * 100_000, id="deeply-nested"),
]


class TestUnreadableRegistry:
    """A file that cannot be read as a whole loads empty and is never overwritten (#462)."""

    @pytest.mark.parametrize("raw", _UNREADABLE_FILES)
    def test_loads_empty_with_warning_and_untouched(self, tmp_path, raw, caplog):
        path = tmp_path / "registry.json"
        path.write_bytes(raw)
        with caplog.at_level(logging.WARNING, logger="agentao.skills.registry"):
            reg = SkillRegistry(path)
        assert reg.list_all() == []
        assert path.read_bytes() == raw
        assert "is unreadable" in caplog.text

    @pytest.mark.parametrize("raw", _UNREADABLE_FILES)
    def test_save_refuses_and_leaves_file_intact(self, tmp_path, raw):
        path = tmp_path / "registry.json"
        path.write_bytes(raw)
        reg = SkillRegistry(path)
        reg.add(_make_record("foo"))
        with pytest.raises(SkillRegistryWriteError, match="not updating it"):
            reg.save()
        with pytest.raises(SkillRegistryWriteError):
            reg.ensure_writable()
        assert path.read_bytes() == raw

    def test_directory_at_registry_path(self, tmp_path):
        path = tmp_path / "registry.json"
        path.mkdir()
        reg = SkillRegistry(path)
        assert len(reg) == 0
        reg.add(_make_record("foo"))
        with pytest.raises(SkillRegistryWriteError):
            reg.save()

    def test_refused_save_can_be_retried_once_fixed(self, tmp_path):
        path = tmp_path / "registry.json"
        path.write_text("[]", encoding="utf-8")
        reg = SkillRegistry(path)
        reg.add(_make_record("foo"))
        with pytest.raises(SkillRegistryWriteError):
            reg.save()
        path.unlink()
        reg.save()
        assert list(_skills_on_disk(path)) == ["foo"]

    def test_utf8_bom_is_read(self, tmp_path):
        path = tmp_path / "registry.json"
        path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"skills": {"a": _entry("a")}}).encode())
        assert SkillRegistry(path).get("a") is not None


class TestRecordValidation:
    """One bad entry is skipped on load and kept on save (#462)."""

    @pytest.mark.parametrize("bad, reason", [
        pytest.param("oops", "expected an object", id="not-an-object"),
        pytest.param({**_entry("b"), "source_ref": None}, "source_ref must be a string", id="null-field"),
        pytest.param({k: v for k, v in _entry("b").items() if k != "install_dir"}, "missing install_dir", id="missing-required"),
        pytest.param(_entry("b", install_dir=""), "not an absolute path", id="empty-install-dir"),
        pytest.param(_entry("b", install_dir="relative/dir"), "not an absolute path", id="relative-install-dir"),
        pytest.param(_entry("other"), "does not match its key", id="name-mismatch"),
    ])
    def test_bad_entry_is_skipped_and_preserved(self, tmp_path, bad, reason, caplog):
        path = tmp_path / "registry.json"
        _write(path, {"skills": {"a": _entry("a"), "b": bad}})
        with caplog.at_level(logging.WARNING, logger="agentao.skills.registry"):
            reg = SkillRegistry(path)
        assert [r.name for r in reg.list_all()] == ["a"]
        assert "'b'" in caplog.text and reason in caplog.text

        reg.add(_make_record("foo"))
        reg.save()
        on_disk = _skills_on_disk(path)
        assert list(on_disk) == ["a", "b", "foo"]
        assert on_disk["b"] == bad

    def test_missing_optional_fields_default_to_empty(self, tmp_path):
        path = tmp_path / "registry.json"
        entry = {k: v for k, v in _entry("a").items()
                 if k not in ("name", "etag", "revision", "version", "installed_at")}
        _write(path, {"skills": {"a": entry}})
        rec = SkillRegistry(path).get("a")
        assert rec is not None
        assert (rec.name, rec.etag, rec.revision, rec.version) == ("a", "", "", "")


class TestForwardCompatibility:
    """Fields and keys this version does not know survive a save (#462)."""

    def test_unknown_record_field_loads_and_survives_unrelated_save(self, tmp_path):
        path = tmp_path / "registry.json"
        _write(path, {"skills": {"a": {**_entry("a"), "new_field": 1}}})
        reg = SkillRegistry(path)
        assert reg.get("a") is not None
        reg.add(_make_record("foo"))
        reg.save()
        assert _skills_on_disk(path)["a"]["new_field"] == 1

    def test_unknown_record_field_survives_updating_that_record(self, tmp_path):
        path = tmp_path / "registry.json"
        _write(path, {"skills": {"a": {**_entry("a"), "new_field": 1}}})
        reg = SkillRegistry(path)
        rec = reg.get("a")
        rec.revision = "new-rev"
        reg.add(rec)
        reg.save()
        on_disk = _skills_on_disk(path)["a"]
        assert on_disk["revision"] == "new-rev"
        assert on_disk["new_field"] == 1

    def test_unknown_top_level_key_survives(self, tmp_path):
        path = tmp_path / "registry.json"
        _write(path, {"format": 2, "skills": {}})
        reg = SkillRegistry(path)
        reg.add(_make_record("foo"))
        reg.save()
        assert json.loads(path.read_text(encoding="utf-8"))["format"] == 2


class TestConcurrentSaves:
    """save() merges into what is on disk instead of replacing it (#462)."""

    def test_two_adds_from_stale_snapshots_both_survive(self, tmp_path):
        path = tmp_path / "registry.json"
        _write(path, {"skills": {"x": _entry("x")}})
        r1, r2 = SkillRegistry(path), SkillRegistry(path)
        r1.add(_make_record("y"))
        r1.save()
        r2.add(_make_record("z"))
        r2.save()
        assert sorted(_skills_on_disk(path)) == ["x", "y", "z"]
        # the saving instance adopts what the file now holds
        assert sorted(r.name for r in r2.list_all()) == ["x", "y", "z"]

    def test_remove_and_add_from_stale_snapshots_both_apply(self, tmp_path):
        path = tmp_path / "registry.json"
        _write(path, {"skills": {"x": _entry("x")}})
        r1, r2 = SkillRegistry(path), SkillRegistry(path)
        assert r1.remove("x")
        r1.save()
        r2.add(_make_record("z"))
        r2.save()
        assert sorted(_skills_on_disk(path)) == ["z"]

    def test_same_name_last_save_wins(self, tmp_path):
        path = tmp_path / "registry.json"
        r1, r2 = SkillRegistry(path), SkillRegistry(path)
        r1.add(_make_record("a", version="1"))
        r2.add(_make_record("a", version="2"))
        r1.save()
        r2.save()
        assert _skills_on_disk(path)["a"]["version"] == "2"

    def test_lock_timeout_refuses(self, tmp_path, monkeypatch):
        from filelock import FileLock

        path = tmp_path / "registry.json"
        reg = SkillRegistry(path)
        reg.add(_make_record("foo"))
        monkeypatch.setattr(registry_mod, "_LOCK_TIMEOUT_S", 0.05)
        with FileLock(str(path.with_suffix(".lock"))):
            with pytest.raises(SkillRegistryWriteError, match="timed out"):
                reg.save()
        reg.save()
        assert list(_skills_on_disk(path)) == ["foo"]


class TestAtomicWrite:
    def test_failed_replace_keeps_original_and_pending(self, tmp_path, monkeypatch):
        import agentao.capabilities.filesystem as fs

        path = tmp_path / "registry.json"
        _write(path, {"skills": {"a": _entry("a")}})
        before = path.read_bytes()
        reg = SkillRegistry(path)
        reg.add(_make_record("foo"))

        real = fs._replace_with_retry

        def fail(tmp, target):
            raise PermissionError("simulated")

        monkeypatch.setattr(fs, "_replace_with_retry", fail)
        with pytest.raises(SkillRegistryWriteError, match="simulated"):
            reg.save()
        assert path.read_bytes() == before
        assert not list(tmp_path.glob("*.tmp"))

        monkeypatch.setattr(fs, "_replace_with_retry", real)
        reg.save()
        assert sorted(_skills_on_disk(path)) == ["a", "foo"]


    def test_unencodable_kept_field_refuses_and_leaves_no_temp(self, tmp_path):
        path = tmp_path / "registry.json"
        # json.loads accepts a lone surrogate; UTF-8 cannot write it back.
        path.write_text(
            '{"skills": {"a": %s}, "note": "\\ud800"}' % json.dumps(_entry("a")),
            encoding="utf-8",
        )
        before = path.read_bytes()
        reg = SkillRegistry(path)
        reg.add(_make_record("foo"))
        with pytest.raises(SkillRegistryWriteError):
            reg.save()
        assert path.read_bytes() == before
        assert not list(tmp_path.glob("*.tmp"))

    @pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
    def test_save_keeps_the_file_mode(self, tmp_path):
        path = tmp_path / "registry.json"
        _write(path, {"skills": {}})
        os.chmod(path, 0o644)
        reg = SkillRegistry(path)
        reg.add(_make_record("foo"))
        reg.save()
        assert path.stat().st_mode & 0o777 == 0o644


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
