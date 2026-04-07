"""Tests for SkillManager two-layer scan, bootstrap, and config persistence."""

import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

import agentao.skills.manager as _mod
from agentao.skills.manager import SkillManager


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _write_skill(skills_dir: Path, name: str, description: str = "A skill", body: str = "## Body") -> Path:
    """Create a minimal SKILL.md for a skill in skills_dir/<name>/SKILL.md."""
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    content = f"---\nname: {name}\ndescription: {description}\nwhen-to-use: Use for {name}\n---\n\n{body}\n"
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    return skill_dir


def _make_manager(tmp_path, global_dir=None, project_dir=None, bundled_dir=None, repo_skills=None):
    """Patch module-level constants and return a fresh SkillManager()."""
    g = global_dir or (tmp_path / "global_skills")
    p = project_dir or (tmp_path / "project_skills")
    b = bundled_dir or (tmp_path / "bundled_skills")
    cfg = tmp_path / "config" / "skills_config.json"
    patches = {
        "_GLOBAL_SKILLS_DIR": g,
        "_PROJECT_SKILLS_DIR": p,
        "_BUNDLED_SKILLS_DIR": b,
        "_CONFIG_FILE": cfg,
        "_CONFIG_DIR": cfg.parent,
    }
    with patch.multiple(_mod, **patches):
        if repo_skills is not None:
            # Patch Path.cwd() to return a dir that contains repo_skills
            with patch("agentao.skills.manager.Path") as MockPath:
                # This is complex — easier to just monkeypatch the load method
                pass
        return SkillManager()


# ---------------------------------------------------------------------------
# Legacy / explicit skills_dir
# ---------------------------------------------------------------------------

def test_explicit_nonexistent_dir_yields_no_skills(tmp_path):
    m = SkillManager(skills_dir=str(tmp_path / "nonexistent"))
    assert m.list_available_skills() == []


def test_explicit_dir_loads_skills(tmp_path):
    skills_dir = tmp_path / "my_skills"
    _write_skill(skills_dir, "alpha")
    m = SkillManager(skills_dir=str(skills_dir))
    assert "alpha" in m.list_available_skills()


def test_explicit_dir_skips_bootstrap(tmp_path):
    bundled = tmp_path / "bundled"
    _write_skill(bundled, "skill-creator", "The creator skill")
    global_dir = tmp_path / "global"
    with patch.multiple(_mod, _GLOBAL_SKILLS_DIR=global_dir, _BUNDLED_SKILLS_DIR=bundled):
        SkillManager(skills_dir=str(tmp_path / "nonexistent"))
    # Bootstrap should NOT have run because skills_dir was explicit
    assert not (global_dir / "skill-creator").exists()


# ---------------------------------------------------------------------------
# Two-layer scanning
# ---------------------------------------------------------------------------

def test_global_skills_loaded(tmp_path):
    g = tmp_path / "global"
    _write_skill(g, "global-skill")
    with patch.multiple(_mod,
                        _GLOBAL_SKILLS_DIR=g,
                        _PROJECT_SKILLS_DIR=tmp_path / "project",
                        _BUNDLED_SKILLS_DIR=tmp_path / "bundled",
                        _CONFIG_FILE=tmp_path / "cfg.json",
                        _CONFIG_DIR=tmp_path):
        m = SkillManager()
    assert "global-skill" in m.list_available_skills()


def test_project_skills_loaded(tmp_path):
    p = tmp_path / "project"
    _write_skill(p, "project-skill")
    with patch.multiple(_mod,
                        _GLOBAL_SKILLS_DIR=tmp_path / "global",
                        _PROJECT_SKILLS_DIR=p,
                        _BUNDLED_SKILLS_DIR=tmp_path / "bundled",
                        _CONFIG_FILE=tmp_path / "cfg.json",
                        _CONFIG_DIR=tmp_path):
        m = SkillManager()
    assert "project-skill" in m.list_available_skills()


def test_project_skill_overrides_global_on_name_clash(tmp_path):
    g = tmp_path / "global"
    p = tmp_path / "project"
    _write_skill(g, "shared", description="global version")
    _write_skill(p, "shared", description="project version")
    with patch.multiple(_mod,
                        _GLOBAL_SKILLS_DIR=g,
                        _PROJECT_SKILLS_DIR=p,
                        _BUNDLED_SKILLS_DIR=tmp_path / "bundled",
                        _CONFIG_FILE=tmp_path / "cfg.json",
                        _CONFIG_DIR=tmp_path):
        m = SkillManager()
    assert m.get_skill_description("shared") == "project version"


def test_repo_root_skills_override_project(tmp_path, monkeypatch):
    g = tmp_path / "global"
    p = tmp_path / "project"
    repo = tmp_path / "repo_skills"
    _write_skill(p, "shared", description="project version")
    _write_skill(repo, "shared", description="repo version")
    monkeypatch.chdir(tmp_path)
    # Symlink repo_skills → skills so Path.cwd() / "skills" points there
    (tmp_path / "skills").symlink_to(repo)
    with patch.multiple(_mod,
                        _GLOBAL_SKILLS_DIR=g,
                        _PROJECT_SKILLS_DIR=p,
                        _BUNDLED_SKILLS_DIR=tmp_path / "bundled",
                        _CONFIG_FILE=tmp_path / "cfg.json",
                        _CONFIG_DIR=tmp_path):
        m = SkillManager()
    assert m.get_skill_description("shared") == "repo version"


def test_both_layers_merged(tmp_path):
    g = tmp_path / "global"
    p = tmp_path / "project"
    _write_skill(g, "g1")
    _write_skill(p, "p1")
    with patch.multiple(_mod,
                        _GLOBAL_SKILLS_DIR=g,
                        _PROJECT_SKILLS_DIR=p,
                        _BUNDLED_SKILLS_DIR=tmp_path / "bundled",
                        _CONFIG_FILE=tmp_path / "cfg.json",
                        _CONFIG_DIR=tmp_path):
        m = SkillManager()
    available = m.list_available_skills()
    assert "g1" in available
    assert "p1" in available


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

def test_bootstrap_copies_bundled_skill(tmp_path):
    bundled = tmp_path / "bundled"
    _write_skill(bundled, "skill-creator")
    global_dir = tmp_path / "global"
    with patch.multiple(_mod,
                        _GLOBAL_SKILLS_DIR=global_dir,
                        _PROJECT_SKILLS_DIR=tmp_path / "project",
                        _BUNDLED_SKILLS_DIR=bundled,
                        _CONFIG_FILE=tmp_path / "cfg.json",
                        _CONFIG_DIR=tmp_path):
        SkillManager()
    assert (global_dir / "skill-creator" / "SKILL.md").exists()


def test_bootstrap_skips_existing_skill(tmp_path):
    bundled = tmp_path / "bundled"
    _write_skill(bundled, "skill-creator", description="bundled version")
    global_dir = tmp_path / "global"
    # Pre-create with different content
    _write_skill(global_dir, "skill-creator", description="user version")
    with patch.multiple(_mod,
                        _GLOBAL_SKILLS_DIR=global_dir,
                        _PROJECT_SKILLS_DIR=tmp_path / "project",
                        _BUNDLED_SKILLS_DIR=bundled,
                        _CONFIG_FILE=tmp_path / "cfg.json",
                        _CONFIG_DIR=tmp_path):
        SkillManager()
    # User version must not be overwritten
    content = (global_dir / "skill-creator" / "SKILL.md").read_text()
    assert "user version" in content


def test_bootstrap_no_crash_on_missing_bundled_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # ensure cwd/skills doesn't pick up real project skills
    with patch.multiple(_mod,
                        _GLOBAL_SKILLS_DIR=tmp_path / "global",
                        _PROJECT_SKILLS_DIR=tmp_path / "project",
                        _BUNDLED_SKILLS_DIR=tmp_path / "nonexistent_bundled",
                        _CONFIG_FILE=tmp_path / "cfg.json",
                        _CONFIG_DIR=tmp_path):
        m = SkillManager()  # should not raise
    assert m.list_available_skills() == []


# ---------------------------------------------------------------------------
# YAML frontmatter parsing
# ---------------------------------------------------------------------------

def test_parse_frontmatter_full_metadata(tmp_path):
    g = tmp_path / "global"
    skill_dir = g / "myskill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: myskill\ndescription: Does things\nwhen-to-use: Use when needed\n---\n\n# My Skill\n",
        encoding="utf-8"
    )
    with patch.multiple(_mod,
                        _GLOBAL_SKILLS_DIR=g,
                        _PROJECT_SKILLS_DIR=tmp_path / "p",
                        _BUNDLED_SKILLS_DIR=tmp_path / "b",
                        _CONFIG_FILE=tmp_path / "cfg.json",
                        _CONFIG_DIR=tmp_path):
        m = SkillManager()
    info = m.get_skill_info("myskill")
    assert info["description"] == "Does things"
    assert info["frontmatter"]["when-to-use"] == "Use when needed"


def test_parse_frontmatter_missing_delimiters(tmp_path):
    g = tmp_path / "global"
    skill_dir = g / "plain"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("No frontmatter here.\n", encoding="utf-8")
    with patch.multiple(_mod,
                        _GLOBAL_SKILLS_DIR=g,
                        _PROJECT_SKILLS_DIR=tmp_path / "p",
                        _BUNDLED_SKILLS_DIR=tmp_path / "b",
                        _CONFIG_FILE=tmp_path / "cfg.json",
                        _CONFIG_DIR=tmp_path):
        m = SkillManager()
    # Falls back to directory name
    assert "plain" in m.list_all_skills()


def test_parse_frontmatter_invalid_yaml(tmp_path):
    g = tmp_path / "global"
    skill_dir = g / "badyaml"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n: this is invalid: yaml: [\n---\n\nBody\n", encoding="utf-8"
    )
    with patch.multiple(_mod,
                        _GLOBAL_SKILLS_DIR=g,
                        _PROJECT_SKILLS_DIR=tmp_path / "p",
                        _BUNDLED_SKILLS_DIR=tmp_path / "b",
                        _CONFIG_FILE=tmp_path / "cfg.json",
                        _CONFIG_DIR=tmp_path):
        m = SkillManager()
    assert "badyaml" in m.list_all_skills()


def test_malformed_skill_md_skipped_gracefully(tmp_path):
    g = tmp_path / "global"
    _write_skill(g, "good-skill")
    # Create a skill dir with an unreadable SKILL.md (we'll make it a directory)
    bad = g / "bad-skill"
    bad.mkdir()
    (bad / "SKILL.md").mkdir()  # SKILL.md is a directory → IOError on open
    with patch.multiple(_mod,
                        _GLOBAL_SKILLS_DIR=g,
                        _PROJECT_SKILLS_DIR=tmp_path / "p",
                        _BUNDLED_SKILLS_DIR=tmp_path / "b",
                        _CONFIG_FILE=tmp_path / "cfg.json",
                        _CONFIG_DIR=tmp_path):
        m = SkillManager()
    # good-skill still loaded, bad-skill skipped
    assert "good-skill" in m.list_available_skills()


# ---------------------------------------------------------------------------
# Disabled skills / config persistence
# ---------------------------------------------------------------------------

def test_disabled_skill_hidden_from_available(tmp_path):
    g = tmp_path / "global"
    _write_skill(g, "alpha")
    cfg_file = tmp_path / "cfg.json"
    with patch.multiple(_mod,
                        _GLOBAL_SKILLS_DIR=g,
                        _PROJECT_SKILLS_DIR=tmp_path / "p",
                        _BUNDLED_SKILLS_DIR=tmp_path / "b",
                        _CONFIG_FILE=cfg_file,
                        _CONFIG_DIR=tmp_path):
        m = SkillManager()
        m.disable_skill("alpha")
        assert "alpha" not in m.list_available_skills()
        assert "alpha" in m.list_all_skills()


def test_enable_skill_restores_to_available(tmp_path):
    g = tmp_path / "global"
    _write_skill(g, "alpha")
    cfg_file = tmp_path / "cfg.json"
    with patch.multiple(_mod,
                        _GLOBAL_SKILLS_DIR=g,
                        _PROJECT_SKILLS_DIR=tmp_path / "p",
                        _BUNDLED_SKILLS_DIR=tmp_path / "b",
                        _CONFIG_FILE=cfg_file,
                        _CONFIG_DIR=tmp_path):
        m = SkillManager()
        m.disable_skill("alpha")
        m.enable_skill("alpha")
        assert "alpha" in m.list_available_skills()


def test_disabled_skill_persisted_to_disk(tmp_path):
    g = tmp_path / "global"
    _write_skill(g, "alpha")
    cfg_file = tmp_path / "cfg.json"
    with patch.multiple(_mod,
                        _GLOBAL_SKILLS_DIR=g,
                        _PROJECT_SKILLS_DIR=tmp_path / "p",
                        _BUNDLED_SKILLS_DIR=tmp_path / "b",
                        _CONFIG_FILE=cfg_file,
                        _CONFIG_DIR=tmp_path):
        m = SkillManager()
        m.disable_skill("alpha")
    saved = json.loads(cfg_file.read_text())
    assert "alpha" in saved["disabled_skills"]


def test_reload_skills_refreshes_from_disk(tmp_path):
    g = tmp_path / "global"
    _write_skill(g, "alpha")
    cfg_file = tmp_path / "cfg.json"
    with patch.multiple(_mod,
                        _GLOBAL_SKILLS_DIR=g,
                        _PROJECT_SKILLS_DIR=tmp_path / "p",
                        _BUNDLED_SKILLS_DIR=tmp_path / "b",
                        _CONFIG_FILE=cfg_file,
                        _CONFIG_DIR=tmp_path):
        m = SkillManager()
        assert "alpha" in m.list_available_skills()
        # Add a new skill to disk and reload
        _write_skill(g, "beta")
        m.reload_skills()
        assert "beta" in m.list_available_skills()


# ---------------------------------------------------------------------------
# Activation / deactivation
# ---------------------------------------------------------------------------

def test_activate_skill_returns_message(tmp_path):
    g = tmp_path / "global"
    _write_skill(g, "alpha", description="Alpha skill")
    with patch.multiple(_mod,
                        _GLOBAL_SKILLS_DIR=g,
                        _PROJECT_SKILLS_DIR=tmp_path / "p",
                        _BUNDLED_SKILLS_DIR=tmp_path / "b",
                        _CONFIG_FILE=tmp_path / "cfg.json",
                        _CONFIG_DIR=tmp_path):
        m = SkillManager()
        result = m.activate_skill("alpha", "test task")
    assert "alpha" in result
    assert "alpha" in m.get_active_skills()


def test_deactivate_skill_removes_from_active(tmp_path):
    g = tmp_path / "global"
    _write_skill(g, "alpha")
    with patch.multiple(_mod,
                        _GLOBAL_SKILLS_DIR=g,
                        _PROJECT_SKILLS_DIR=tmp_path / "p",
                        _BUNDLED_SKILLS_DIR=tmp_path / "b",
                        _CONFIG_FILE=tmp_path / "cfg.json",
                        _CONFIG_DIR=tmp_path):
        m = SkillManager()
        m.activate_skill("alpha", "task")
        assert m.deactivate_skill("alpha") is True
        assert "alpha" not in m.get_active_skills()


def test_activate_unknown_skill_returns_error(tmp_path):
    with patch.multiple(_mod,
                        _GLOBAL_SKILLS_DIR=tmp_path / "g",
                        _PROJECT_SKILLS_DIR=tmp_path / "p",
                        _BUNDLED_SKILLS_DIR=tmp_path / "b",
                        _CONFIG_FILE=tmp_path / "cfg.json",
                        _CONFIG_DIR=tmp_path):
        m = SkillManager()
        result = m.activate_skill("nonexistent", "task")
    assert "Error" in result


# ---------------------------------------------------------------------------
# Content and resources
# ---------------------------------------------------------------------------

def test_get_skill_content_reads_full_file(tmp_path):
    g = tmp_path / "global"
    _write_skill(g, "alpha", body="## Detailed section\nLots of content here.")
    with patch.multiple(_mod,
                        _GLOBAL_SKILLS_DIR=g,
                        _PROJECT_SKILLS_DIR=tmp_path / "p",
                        _BUNDLED_SKILLS_DIR=tmp_path / "b",
                        _CONFIG_FILE=tmp_path / "cfg.json",
                        _CONFIG_DIR=tmp_path):
        m = SkillManager()
        content = m.get_skill_content("alpha")
    assert "Detailed section" in content


def test_resource_enumeration_references(tmp_path):
    g = tmp_path / "global"
    skill_dir = _write_skill(g, "alpha")
    ref_dir = skill_dir / "references"
    ref_dir.mkdir()
    (ref_dir / "guide.md").write_text("# Guide", encoding="utf-8")
    with patch.multiple(_mod,
                        _GLOBAL_SKILLS_DIR=g,
                        _PROJECT_SKILLS_DIR=tmp_path / "p",
                        _BUNDLED_SKILLS_DIR=tmp_path / "b",
                        _CONFIG_FILE=tmp_path / "cfg.json",
                        _CONFIG_DIR=tmp_path):
        m = SkillManager()
        resources = m._list_skill_resources("alpha")
    assert any("guide.md" in r for r in resources["references"])


def test_skills_context_includes_active_skill(tmp_path):
    g = tmp_path / "global"
    _write_skill(g, "alpha", body="## Alpha Content\nSpecific instructions.")
    with patch.multiple(_mod,
                        _GLOBAL_SKILLS_DIR=g,
                        _PROJECT_SKILLS_DIR=tmp_path / "p",
                        _BUNDLED_SKILLS_DIR=tmp_path / "b",
                        _CONFIG_FILE=tmp_path / "cfg.json",
                        _CONFIG_DIR=tmp_path):
        m = SkillManager()
        m.activate_skill("alpha", "my task")
        ctx = m.get_skills_context()
    assert "alpha" in ctx
    assert "Alpha Content" in ctx
