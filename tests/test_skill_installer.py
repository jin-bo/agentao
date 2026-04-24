"""Tests for agentao.skills.installer."""

import json
import shutil
from pathlib import Path

import pytest

from agentao.skills.installer import (
    SkillConflictError,
    SkillInstaller,
    SkillValidationError,
    normalize_skill_name,
)
from agentao.skills.registry import InstalledSkillRecord, SkillRegistry
from agentao.skills.sources import FetchResult, SkillSource, SourceSpec, UpdateInfo
from agentao.skills.sources import GitHubSkillSource


# ------------------------------------------------------------------
# Fake source for testing (no network)
# ------------------------------------------------------------------

class FakeSource(SkillSource):
    """In-memory skill source for deterministic tests."""

    def __init__(self, package_dir: Path, has_update: bool = False):
        self._package_dir = package_dir
        self._has_update = has_update

    def resolve(self, ref: str):
        parts = ref.split("/")
        return SourceSpec(
            source_type="github",
            owner=parts[0] if len(parts) > 0 else "owner",
            repo=parts[1] if len(parts) > 1 else "repo",
            ref=None,
            package_path="",
            archive_url=f"https://fake/{ref}",
        )

    def fetch(self, spec, dest_dir):
        # Copy the prepared package into dest_dir
        target = dest_dir / "extracted"
        shutil.copytree(self._package_dir, target)
        return FetchResult(
            extracted_dir=target,
            revision="fake-rev-123",
            etag='W/"fake-etag"',
            version="",
        )

    def check_update(self, source_ref, current_etag):
        return UpdateInfo(
            has_update=self._has_update,
            current_revision="old",
            latest_revision="new" if self._has_update else "old",
            latest_etag='W/"new-etag"' if self._has_update else current_etag,
        )


def _create_valid_skill(path: Path, name: str = "test-skill"):
    """Create a minimal valid skill package at *path*."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: A test skill.\n---\n# {name}\nContent.",
        encoding="utf-8",
    )


# ------------------------------------------------------------------
# normalize_skill_name
# ------------------------------------------------------------------

class TestNormalizeSkillName:
    def test_lowercase(self):
        assert normalize_skill_name("My-Skill") == "my-skill"

    def test_underscores_to_hyphens(self):
        assert normalize_skill_name("my_skill") == "my-skill"

    def test_spaces_to_hyphens(self):
        assert normalize_skill_name("my skill") == "my-skill"

    def test_strip_edges(self):
        assert normalize_skill_name("  -my-skill- ") == "my-skill"

    def test_collapse_hyphens(self):
        assert normalize_skill_name("my--skill") == "my-skill"


# ------------------------------------------------------------------
# GitHub ref parsing
# ------------------------------------------------------------------

class TestGitHubSkillSourceResolve:
    def test_owner_repo(self):
        spec = GitHubSkillSource().resolve("owner/repo")
        assert spec.owner == "owner"
        assert spec.repo == "repo"
        assert spec.package_path == ""
        assert spec.ref is None

    def test_owner_repo_with_ref(self):
        spec = GitHubSkillSource().resolve("owner/repo@v1.2")
        assert spec.owner == "owner"
        assert spec.repo == "repo"
        assert spec.package_path == ""
        assert spec.ref == "v1.2"

    def test_owner_repo_with_package_path(self):
        spec = GitHubSkillSource().resolve("anthropics/skills:skills/pdf")
        assert spec.owner == "anthropics"
        assert spec.repo == "skills"
        assert spec.package_path == "skills/pdf"
        assert spec.ref is None

    def test_owner_repo_with_package_path_and_ref(self):
        spec = GitHubSkillSource().resolve("anthropics/skills:skills/pdf@main")
        assert spec.owner == "anthropics"
        assert spec.repo == "skills"
        assert spec.package_path == "skills/pdf"
        assert spec.ref == "main"

    def test_rejects_empty_package_path(self):
        with pytest.raises(ValueError, match="Package path cannot be empty"):
            GitHubSkillSource().resolve("owner/repo:")

    def test_rejects_parent_package_path(self):
        with pytest.raises(ValueError, match="relative path"):
            GitHubSkillSource().resolve("owner/repo:../secret")


# ------------------------------------------------------------------
# Validation
# ------------------------------------------------------------------

class TestValidation:
    def test_missing_skill_md(self, tmp_path):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        reg = SkillRegistry(tmp_path / "reg.json")
        source = FakeSource(pkg)
        installer = SkillInstaller(reg, source, "project", tmp_path)

        with pytest.raises(SkillValidationError, match="No SKILL.md"):
            installer._validate_package(pkg)

    def test_no_frontmatter(self, tmp_path):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "SKILL.md").write_text("# Just a title\nNo frontmatter here.", encoding="utf-8")
        reg = SkillRegistry(tmp_path / "reg.json")
        installer = SkillInstaller(reg, FakeSource(pkg), "project", tmp_path)

        with pytest.raises(SkillValidationError, match="frontmatter"):
            installer._validate_package(pkg)

    def test_missing_name(self, tmp_path):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "SKILL.md").write_text(
            "---\ndescription: test\n---\n# Test", encoding="utf-8"
        )
        reg = SkillRegistry(tmp_path / "reg.json")
        installer = SkillInstaller(reg, FakeSource(pkg), "project", tmp_path)

        with pytest.raises(SkillValidationError, match="name"):
            installer._validate_package(pkg)

    def test_missing_description(self, tmp_path):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "SKILL.md").write_text(
            "---\nname: test\n---\n# Test", encoding="utf-8"
        )
        reg = SkillRegistry(tmp_path / "reg.json")
        installer = SkillInstaller(reg, FakeSource(pkg), "project", tmp_path)

        with pytest.raises(SkillValidationError, match="description"):
            installer._validate_package(pkg)

    def test_invalid_name_chars(self, tmp_path):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "SKILL.md").write_text(
            "---\nname: test@skill!\ndescription: bad\n---\n# Test", encoding="utf-8"
        )
        reg = SkillRegistry(tmp_path / "reg.json")
        installer = SkillInstaller(reg, FakeSource(pkg), "project", tmp_path)

        with pytest.raises(SkillValidationError, match="invalid characters"):
            installer._validate_package(pkg)

    def test_name_mismatch_skill_json(self, tmp_path):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "SKILL.md").write_text(
            "---\nname: skill-a\ndescription: desc\n---\n# A", encoding="utf-8"
        )
        (pkg / "skill.json").write_text(
            json.dumps({"schema_version": 1, "name": "skill-b", "version": "1.0"}), encoding="utf-8"
        )
        reg = SkillRegistry(tmp_path / "reg.json")
        installer = SkillInstaller(reg, FakeSource(pkg), "project", tmp_path)

        with pytest.raises(SkillValidationError, match="mismatch"):
            installer._validate_package(pkg)

    def test_valid_minimal_package(self, tmp_path):
        pkg = tmp_path / "pkg"
        _create_valid_skill(pkg, "my-skill")
        reg = SkillRegistry(tmp_path / "reg.json")
        installer = SkillInstaller(reg, FakeSource(pkg), "project", tmp_path)

        name, warnings = installer._validate_package(pkg)
        assert name == "my-skill"
        assert warnings == []

    def test_valid_full_package(self, tmp_path):
        pkg = tmp_path / "pkg"
        _create_valid_skill(pkg, "my-skill")
        (pkg / "skill.json").write_text(
            json.dumps({
                "schema_version": 1,
                "name": "my-skill",
                "version": "2.0.0",
                "description": "Full package",
            }), encoding="utf-8"
        )
        (pkg / "reference").mkdir()
        (pkg / "reference" / "guide.md").write_text("# Guide", encoding="utf-8")
        reg = SkillRegistry(tmp_path / "reg.json")
        installer = SkillInstaller(reg, FakeSource(pkg), "project", tmp_path)

        name, warnings = installer._validate_package(pkg)
        assert name == "my-skill"

    def test_suspicious_files_warning(self, tmp_path):
        pkg = tmp_path / "pkg"
        _create_valid_skill(pkg)
        (pkg / ".env").write_text("SECRET=yes", encoding="utf-8")
        reg = SkillRegistry(tmp_path / "reg.json")
        installer = SkillInstaller(reg, FakeSource(pkg), "project", tmp_path)

        name, warnings = installer._validate_package(pkg)
        assert any("sensitive" in w.lower() for w in warnings)


# ------------------------------------------------------------------
# Install flow
# ------------------------------------------------------------------

class TestInstall:
    def test_install_success(self, tmp_path):
        pkg = tmp_path / "source-pkg"
        _create_valid_skill(pkg, "my-skill")

        reg_path = tmp_path / "reg.json"
        reg = SkillRegistry(reg_path)
        source = FakeSource(pkg)
        installer = SkillInstaller(reg, source, "project", tmp_path)

        record = installer.install("owner/my-skill")
        assert record.name == "my-skill"
        assert record.source_type == "github"
        assert record.install_scope == "project"

        # Verify on disk
        installed = Path(record.install_dir)
        assert installed.exists()
        assert (installed / "SKILL.md").exists()

        # Verify in registry
        reg2 = SkillRegistry(reg_path)
        assert reg2.get("my-skill") is not None

    def test_install_conflict_without_force(self, tmp_path):
        pkg = tmp_path / "source-pkg"
        _create_valid_skill(pkg, "my-skill")

        # Pre-create the target dir
        target = tmp_path / ".agentao" / "skills" / "my-skill"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("existing", encoding="utf-8")

        reg = SkillRegistry(tmp_path / "reg.json")
        source = FakeSource(pkg)
        installer = SkillInstaller(reg, source, "project", tmp_path)

        with pytest.raises(SkillConflictError):
            installer.install("owner/my-skill")

    def test_install_force_overwrites(self, tmp_path):
        pkg = tmp_path / "source-pkg"
        _create_valid_skill(pkg, "my-skill")

        # Pre-create the target
        target = tmp_path / ".agentao" / "skills" / "my-skill"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("old content", encoding="utf-8")

        reg = SkillRegistry(tmp_path / "reg.json")
        source = FakeSource(pkg)
        installer = SkillInstaller(reg, source, "project", tmp_path)

        record = installer.install("owner/my-skill", force=True)
        assert record.name == "my-skill"
        content = (Path(record.install_dir) / "SKILL.md").read_text(encoding="utf-8")
        assert "A test skill" in content  # new content, not "old content"

    def test_install_preserves_pinned_ref(self, tmp_path):
        """Installing owner/repo@v1.2 preserves the @ref in source_ref."""
        pkg = tmp_path / "source-pkg"
        _create_valid_skill(pkg, "my-skill")

        reg = SkillRegistry(tmp_path / "reg.json")

        class PinnedSource(FakeSource):
            def resolve(self, ref):
                spec = super().resolve(ref.split("@")[0])
                spec.ref = "v1.2"
                return spec

        source = PinnedSource(pkg)
        installer = SkillInstaller(reg, source, "project", tmp_path)
        record = installer.install("owner/my-skill@v1.2")
        assert record.source_ref == "owner/my-skill@v1.2"

    def test_install_from_package_path(self, tmp_path):
        """Installing owner/repo:path validates and copies only that subdirectory."""
        repo = tmp_path / "repo"
        _create_valid_skill(repo / "skills" / "pdf", "pdf")
        _create_valid_skill(repo / "skills" / "docx", "docx")

        class PathSource(FakeSource):
            def resolve(self, ref):
                spec = super().resolve("anthropics/skills")
                spec.package_path = "skills/pdf"
                return spec

        reg = SkillRegistry(tmp_path / "reg.json")
        installer = SkillInstaller(reg, PathSource(repo), "project", tmp_path)
        record = installer.install("anthropics/skills:skills/pdf")

        assert record.name == "pdf"
        assert record.source_ref == "anthropics/skills:skills/pdf"
        installed = Path(record.install_dir)
        assert (installed / "SKILL.md").exists()
        assert not (installed / "skills" / "docx").exists()

    def test_install_from_missing_package_path_fails(self, tmp_path):
        repo = tmp_path / "repo"
        _create_valid_skill(repo / "skills" / "pdf", "pdf")

        class PathSource(FakeSource):
            def resolve(self, ref):
                spec = super().resolve("anthropics/skills")
                spec.package_path = "skills/missing"
                return spec

        reg = SkillRegistry(tmp_path / "reg.json")
        installer = SkillInstaller(reg, PathSource(repo), "project", tmp_path)

        with pytest.raises(SkillValidationError, match="Package path 'skills/missing'"):
            installer.install("anthropics/skills:skills/missing")

    def test_install_rejects_invalid_package(self, tmp_path):
        pkg = tmp_path / "bad-pkg"
        pkg.mkdir()
        # No SKILL.md

        reg = SkillRegistry(tmp_path / "reg.json")
        source = FakeSource(pkg)
        installer = SkillInstaller(reg, source, "project", tmp_path)

        with pytest.raises(SkillValidationError):
            installer.install("owner/bad-pkg")


# ------------------------------------------------------------------
# Update flow
# ------------------------------------------------------------------

class TestUpdate:
    def _setup_installed(self, tmp_path, has_update=False):
        """Install a skill and return (installer, reg, record)."""
        pkg = tmp_path / "source-pkg"
        _create_valid_skill(pkg, "my-skill")

        reg = SkillRegistry(tmp_path / "reg.json")
        source = FakeSource(pkg, has_update=has_update)
        installer = SkillInstaller(reg, source, "project", tmp_path)

        # Install first
        record = installer.install("owner/my-skill")
        return installer, reg, record

    def test_update_up_to_date(self, tmp_path):
        installer, reg, record = self._setup_installed(tmp_path, has_update=False)
        result = installer.update("my-skill")
        assert result is None

    def test_update_when_changed(self, tmp_path):
        installer, reg, record = self._setup_installed(tmp_path, has_update=True)
        result = installer.update("my-skill")
        assert result is not None
        assert result.name == "my-skill"

    def test_update_nonexistent_skill(self, tmp_path):
        pkg = tmp_path / "source-pkg"
        _create_valid_skill(pkg)
        reg = SkillRegistry(tmp_path / "reg.json")
        installer = SkillInstaller(reg, FakeSource(pkg), "project", tmp_path)

        from agentao.skills.installer import SkillInstallError
        with pytest.raises(SkillInstallError, match="not found"):
            installer.update("nonexistent")

    def test_update_preserves_package_path_source_ref(self, tmp_path):
        repo = tmp_path / "repo"
        _create_valid_skill(repo / "skills" / "pdf", "pdf")

        class PathSource(FakeSource):
            def resolve(self, ref):
                spec = super().resolve("anthropics/skills")
                spec.package_path = "skills/pdf"
                return spec

        reg = SkillRegistry(tmp_path / "reg.json")
        source = PathSource(repo, has_update=True)
        installer = SkillInstaller(reg, source, "project", tmp_path)

        record = installer.install("anthropics/skills:skills/pdf")
        assert record.source_ref == "anthropics/skills:skills/pdf"

        updated = installer.update("pdf")
        assert updated is not None
        assert updated.source_ref == "anthropics/skills:skills/pdf"


# ------------------------------------------------------------------
# Atomic replace
# ------------------------------------------------------------------

class TestAtomicReplace:
    def test_fresh_install(self, tmp_path):
        source = tmp_path / "src"
        source.mkdir()
        (source / "file.txt").write_text("hello", encoding="utf-8")
        target = tmp_path / "dst"

        SkillInstaller._atomic_replace(source, target)
        assert (target / "file.txt").read_text(encoding="utf-8") == "hello"
        assert not (tmp_path / "dst.bak").exists()

    def test_overwrite_existing(self, tmp_path):
        source = tmp_path / "src"
        source.mkdir()
        (source / "new.txt").write_text("new", encoding="utf-8")

        target = tmp_path / "dst"
        target.mkdir()
        (target / "old.txt").write_text("old", encoding="utf-8")

        SkillInstaller._atomic_replace(source, target)
        assert (target / "new.txt").read_text(encoding="utf-8") == "new"
        assert not (target / "old.txt").exists()
        assert not (tmp_path / "dst.bak").exists()


# ------------------------------------------------------------------
# find_package_root
# ------------------------------------------------------------------

class TestFindPackageRoot:
    def _make_installer(self, tmp_path):
        reg = SkillRegistry(tmp_path / "reg.json")
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        return SkillInstaller(reg, FakeSource(pkg), "project", tmp_path)

    def test_skill_md_at_root(self, tmp_path):
        extracted = tmp_path / "extracted"
        extracted.mkdir()
        (extracted / "SKILL.md").write_text("test", encoding="utf-8")
        installer = self._make_installer(tmp_path)
        assert installer._find_package_root(extracted) == extracted

    def test_single_subdir(self, tmp_path):
        extracted = tmp_path / "extracted"
        sub = extracted / "owner-repo-abc123"
        sub.mkdir(parents=True)
        (sub / "SKILL.md").write_text("test", encoding="utf-8")
        installer = self._make_installer(tmp_path)
        assert installer._find_package_root(extracted) == sub

    def test_no_skill_md(self, tmp_path):
        extracted = tmp_path / "extracted"
        extracted.mkdir()
        (extracted / "README.md").write_text("not a skill", encoding="utf-8")
        installer = self._make_installer(tmp_path)
        with pytest.raises(SkillValidationError, match="No SKILL.md"):
            installer._find_package_root(extracted)

    def test_package_path(self, tmp_path):
        extracted = tmp_path / "extracted"
        skill = extracted / "skills" / "pdf"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("test", encoding="utf-8")
        installer = self._make_installer(tmp_path)
        assert installer._find_package_root(extracted, "skills/pdf") == skill

    def test_package_path_without_skill_md(self, tmp_path):
        extracted = tmp_path / "extracted"
        skill = extracted / "skills" / "pdf"
        skill.mkdir(parents=True)
        installer = self._make_installer(tmp_path)
        with pytest.raises(SkillValidationError, match="No SKILL.md"):
            installer._find_package_root(extracted, "skills/pdf")

    def test_ambiguous_multiple_subdirs(self, tmp_path):
        extracted = tmp_path / "extracted"
        for name in ("skill-a", "skill-b"):
            d = extracted / name
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text("test", encoding="utf-8")
        installer = self._make_installer(tmp_path)
        with pytest.raises(SkillValidationError, match="Ambiguous"):
            installer._find_package_root(extracted)
