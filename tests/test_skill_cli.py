"""Tests for skill CLI subcommand parsing and dispatch."""

import json
import shutil
from pathlib import Path
from unittest import mock

import pytest

from agentao.cli import _build_parser
from agentao.skills.registry import InstalledSkillRecord, SkillRegistry


# ------------------------------------------------------------------
# Argument parsing
# ------------------------------------------------------------------

class TestBuildParser:
    def _parse(self, argv):
        parser = _build_parser()
        args, _ = parser.parse_known_args(argv)
        return args

    def test_skill_install(self):
        args = self._parse(["skill", "install", "owner/repo"])
        assert args.subcommand == "skill"
        assert args.skill_action == "install"
        assert args.ref == "owner/repo"
        assert args.force is False
        assert args.scope is None

    def test_skill_install_with_package_path(self):
        args = self._parse(["skill", "install", "anthropics/skills:skills/pdf"])
        assert args.subcommand == "skill"
        assert args.skill_action == "install"
        assert args.ref == "anthropics/skills:skills/pdf"

    def test_skill_install_with_scope_and_force(self):
        args = self._parse(
            ["skill", "install", "owner/repo", "--scope", "global", "--force"]
        )
        assert args.scope == "global"
        assert args.force is True

    def test_skill_remove(self):
        args = self._parse(["skill", "remove", "my-skill"])
        assert args.skill_action == "remove"
        assert args.name == "my-skill"

    def test_skill_list(self):
        args = self._parse(["skill", "list"])
        assert args.skill_action == "list"
        assert args.json_output is False

    def test_skill_list_json(self):
        args = self._parse(["skill", "list", "--json"])
        assert args.json_output is True

    def test_skill_update_single(self):
        args = self._parse(["skill", "update", "my-skill"])
        assert args.skill_action == "update"
        assert args.name == "my-skill"
        assert args.update_all is False

    def test_skill_update_all(self):
        args = self._parse(["skill", "update", "--all"])
        assert args.update_all is True
        assert args.name is None

    # Backward compatibility
    def test_init_still_works(self):
        args = self._parse(["init"])
        assert args.subcommand == "init"

    def test_print_mode_still_works(self):
        args = self._parse(["-p", "hello world"])
        assert args.prompt == "hello world"

    def test_acp_still_works(self):
        args = self._parse(["--acp"])
        assert args.acp is True

    def test_interactive_default(self):
        args = self._parse([])
        assert args.subcommand is None
        assert args.prompt is None


# ------------------------------------------------------------------
# _skill_remove logic
# ------------------------------------------------------------------

class TestSkillRemove:
    def test_remove_managed_skill(self, tmp_path):
        """Remove a managed skill: deletes directory and registry entry."""
        # Setup registry
        reg_path = tmp_path / "skills_registry.json"
        reg = SkillRegistry(reg_path)
        skill_dir = tmp_path / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: test-skill\n---\n# Test", encoding="utf-8")
        reg.add(InstalledSkillRecord(
            name="test-skill",
            source_type="github",
            source_ref="owner/repo",
            installed_at="2026-01-01T00:00:00+00:00",
            install_scope="project",
            install_dir=str(skill_dir),
            version="1.0.0",
            revision="abc",
            etag="",
        ))
        reg.save()

        # Import and call _skill_remove
        from agentao.cli import _skill_remove

        class FakeArgs:
            name = "test-skill"

        with mock.patch(
            "agentao.skills.registry.registry_path_for_scope", return_value=reg_path
        ):
            _skill_remove(FakeArgs(), "project")

        # Verify
        assert not skill_dir.exists()
        reg2 = SkillRegistry(reg_path)
        assert reg2.get("test-skill") is None

    def test_remove_not_found_exits(self, tmp_path):
        """Remove non-existent skill exits with error."""
        reg_path = tmp_path / "skills_registry.json"
        SkillRegistry(reg_path).save()

        from agentao.cli import _skill_remove

        class FakeArgs:
            name = "missing"

        with mock.patch(
            "agentao.skills.registry.registry_path_for_scope", return_value=reg_path
        ), pytest.raises(SystemExit):
            _skill_remove(FakeArgs(), "project")


# ------------------------------------------------------------------
# _skill_list logic
# ------------------------------------------------------------------

class TestSkillList:
    def test_list_json_output(self, tmp_path, capsys):
        """--json flag produces valid JSON."""
        reg_path = tmp_path / "skills_registry.json"
        reg = SkillRegistry(reg_path)
        reg.add(InstalledSkillRecord(
            name="test-skill",
            source_type="github",
            source_ref="owner/repo",
            installed_at="2026-01-01T00:00:00+00:00",
            install_scope="global",
            install_dir=str(tmp_path / "skills" / "test-skill"),
            version="1.0.0",
            revision="abc",
            etag="",
        ))
        reg.save()

        from agentao.cli import _skill_list

        class FakeArgs:
            json_output = True
            installed = True  # filter to managed installs only

        # Return the real registry for "global", non-existent for "project"
        def fake_path(scope, cwd=None):
            if scope == "global":
                return reg_path
            return tmp_path / "nonexistent" / "registry.json"

        with mock.patch(
            "agentao.skills.registry.registry_path_for_scope", side_effect=fake_path
        ):
            _skill_list(FakeArgs())

        out = capsys.readouterr().out
        data = json.loads(out)
        assert len(data) == 1
        assert data[0]["name"] == "test-skill"
