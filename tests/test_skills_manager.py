"""Tests for SkillManager two-layer scan, bootstrap, and config persistence."""

import json
from pathlib import Path
from types import SimpleNamespace
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
    content = (global_dir / "skill-creator" / "SKILL.md").read_text(encoding="utf-8")
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
    saved = json.loads(cfg_file.read_text(encoding="utf-8"))
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


def test_resource_enumeration_scripts_any_extension(tmp_path):
    g = tmp_path / "global"
    skill_dir = _write_skill(g, "alpha")
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "run.py").write_text("print('hi')", encoding="utf-8")
    (scripts_dir / "helper.sh").write_text("echo hi", encoding="utf-8")
    with patch.multiple(_mod,
                        _GLOBAL_SKILLS_DIR=g,
                        _PROJECT_SKILLS_DIR=tmp_path / "p",
                        _BUNDLED_SKILLS_DIR=tmp_path / "b",
                        _CONFIG_FILE=tmp_path / "cfg.json",
                        _CONFIG_DIR=tmp_path):
        m = SkillManager()
        resources = m._list_skill_resources("alpha")
    assert any("run.py" in s for s in resources["scripts"])
    assert any("helper.sh" in s for s in resources["scripts"])


def test_resource_enumeration_skips_junk_and_caps(tmp_path):
    g = tmp_path / "global"
    skill_dir = _write_skill(g, "alpha")
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "run.py").write_text("print('hi')", encoding="utf-8")
    (scripts_dir / ".DS_Store").write_text("junk", encoding="utf-8")
    (scripts_dir / ".env").write_text("SECRET=1", encoding="utf-8")
    pycache = scripts_dir / "__pycache__"
    pycache.mkdir()
    (pycache / "run.cpython-312.pyc").write_text("", encoding="utf-8")
    with patch.multiple(_mod,
                        _GLOBAL_SKILLS_DIR=g,
                        _PROJECT_SKILLS_DIR=tmp_path / "p",
                        _BUNDLED_SKILLS_DIR=tmp_path / "b",
                        _CONFIG_FILE=tmp_path / "cfg.json",
                        _CONFIG_DIR=tmp_path):
        m = SkillManager()
        resources = m._list_skill_resources("alpha")
    assert [Path(s).name for s in resources["scripts"]] == ["run.py"]

    # Cap: with the limit patched down, the list is truncated and says so.
    for i in range(4):
        (scripts_dir / f"s{i}.py").write_text("", encoding="utf-8")
    with patch.multiple(_mod,
                        _GLOBAL_SKILLS_DIR=g,
                        _PROJECT_SKILLS_DIR=tmp_path / "p",
                        _BUNDLED_SKILLS_DIR=tmp_path / "b",
                        _CONFIG_FILE=tmp_path / "cfg.json",
                        _CONFIG_DIR=tmp_path,
                        _RESOURCE_FILE_CAP=2):
        m = SkillManager()
        resources = m._list_skill_resources("alpha")
    assert len(resources["scripts"]) == 3  # 2 paths + truncation marker
    assert "3 more files" in resources["scripts"][-1]


def test_plugin_command_entry_gets_no_skill_directory(tmp_path):
    """Command .md files live in a shared commands/ folder — reporting it
    as a skill directory (or scanning it for resources) would misattribute
    sibling files to every command."""
    commands_dir = tmp_path / "plug" / "commands"
    scripts_dir = commands_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (commands_dir / "foo.md").write_text("# Foo", encoding="utf-8")
    (scripts_dir / "shared.py").write_text("", encoding="utf-8")
    with patch.multiple(_mod,
                        _GLOBAL_SKILLS_DIR=tmp_path / "g",
                        _PROJECT_SKILLS_DIR=tmp_path / "p",
                        _BUNDLED_SKILLS_DIR=tmp_path / "b",
                        _CONFIG_FILE=tmp_path / "cfg.json",
                        _CONFIG_DIR=tmp_path):
        m = SkillManager()
        m.available_skills["plug:foo"] = {
            "name": "plug:foo",
            "path": str(commands_dir / "foo.md"),
            "source_kind": "plugin-command",
        }
        resources = m._list_skill_resources("plug:foo")
        result = m.activate_skill("plug:foo", "task")
    assert resources["scripts"] == []
    assert "Skill directory:" not in result


def test_list_skill_resources_tolerates_pathless_entry(tmp_path):
    """Plugin-style entries carry path=None — must not raise."""
    with patch.multiple(_mod,
                        _GLOBAL_SKILLS_DIR=tmp_path / "g",
                        _PROJECT_SKILLS_DIR=tmp_path / "p",
                        _BUNDLED_SKILLS_DIR=tmp_path / "b",
                        _CONFIG_FILE=tmp_path / "cfg.json",
                        _CONFIG_DIR=tmp_path):
        m = SkillManager()
        m.available_skills["inline"] = {"name": "inline", "path": None}
        resources = m._list_skill_resources("inline")
    assert resources == {"references": [], "assets": [], "scripts": []}


def test_activate_skill_reports_skill_directory_and_scripts(tmp_path):
    g = tmp_path / "global"
    skill_dir = _write_skill(g, "alpha", description="Alpha skill")
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "ocr.py").write_text("print('ocr')", encoding="utf-8")
    with patch.multiple(_mod,
                        _GLOBAL_SKILLS_DIR=g,
                        _PROJECT_SKILLS_DIR=tmp_path / "p",
                        _BUNDLED_SKILLS_DIR=tmp_path / "b",
                        _CONFIG_FILE=tmp_path / "cfg.json",
                        _CONFIG_DIR=tmp_path):
        m = SkillManager()
        result = m.activate_skill("alpha", "test task")
    assert f"Skill directory: {skill_dir}" in result
    assert "NOT your current working directory" in result
    assert str(scripts_dir / "ocr.py") in result


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
    assert f"Skill directory: {g / 'alpha'}" in ctx
    assert "NOT your current working directory" in ctx


# ---------------------------------------------------------------------------
# child_view — the catalogue a sub-agent is built with (#254)
# ---------------------------------------------------------------------------

def _manager_with_alpha(tmp_path):
    g = tmp_path / "global"
    _write_skill(g, "alpha")
    cfg_file = tmp_path / "cfg.json"
    patches = dict(
        _GLOBAL_SKILLS_DIR=g,
        _PROJECT_SKILLS_DIR=tmp_path / "p",
        _BUNDLED_SKILLS_DIR=tmp_path / "b",
        _CONFIG_FILE=cfg_file,
        _CONFIG_DIR=tmp_path,
    )
    return patches, cfg_file


def test_child_view_refuses_a_copy_that_answers_with_the_parent(tmp_path):
    """A ``__copy__`` returning ``self`` would make the next three lines write
    to the *parent*: ``active_skills = {}`` clears what the parent has active
    mid-session, and every later child activation lands in the parent's prompt.
    Raising instead leaves the caller to fall back to an empty manager."""
    patches, _ = _manager_with_alpha(tmp_path)

    class _SelfCopying(SkillManager):
        def __copy__(self):
            return self

    with patch.multiple(_mod, **patches):
        m = _SelfCopying()
    m.activate_skill("alpha", "parent task")

    with pytest.raises(TypeError, match="same instance"):
        m.child_view()
    assert "alpha" in m.get_active_skills()


def test_reload_skills_answers_with_the_available_count(tmp_path):
    """Both callers read the return value: ``/skills reload`` prints it, and
    ``/crystallize`` gates its "Skills reloaded (N available)" line on it not
    being ``None`` — so a method that answered with nothing reported a skill
    it had just written as one it could not confirm."""
    patches, _ = _manager_with_alpha(tmp_path)
    with patch.multiple(_mod, **patches):
        m = SkillManager()
        # Relative to what the manager already holds: the repo-root
        # ``skills/`` directory is resolved from the process cwd, which these
        # module-constant patches do not move.
        before = len(m.list_available_skills())
        assert m.reload_skills() == before
        _write_skill(tmp_path / "global", "beta")
        assert m.reload_skills() == before + 1
        m.disable_skill("beta")
        assert m.reload_skills() == before


def test_a_child_views_disabled_set_is_never_persisted(tmp_path):
    """The child forks ``disabled_skills`` but shares the parent's config
    path, so a write from it would put an ephemeral sub-agent's set over the
    user's ``skills_config.json``."""
    patches, cfg_file = _manager_with_alpha(tmp_path)
    with patch.multiple(_mod, **patches):
        parent = SkillManager()
        child = parent.child_view()
        assert child.disable_skill("alpha").endswith("has been disabled.")

    assert not cfg_file.exists()
    assert "alpha" in child.disabled_skills
    assert parent.disabled_skills == set()


# ---------------------------------------------------------------------------
# A disabled skill cannot be activated (#266)
# ---------------------------------------------------------------------------

def _manager_with_skills(tmp_path, *names):
    """A manager holding exactly ``names``, each with an identifiable body.

    ``working_directory=tmp_path`` moves the project *and* repo-root skill
    dirs plus the config file under ``tmp_path``. The module constants patched
    here are the two the constructor still reads globally (global skills,
    bundled skills) — patching ``_PROJECT_SKILLS_DIR`` / ``_CONFIG_*`` instead
    would leave ``<cwd>/skills`` in the scan, which is the repo's own
    ``skills/`` when the suite runs from the repo root, and the catalogue
    under test would then vary with the checkout.
    """
    g = tmp_path / "global"
    for name in names:
        _write_skill(g, name, body=f"## Body\nSecret {name} instructions.")
    patches = dict(
        _GLOBAL_SKILLS_DIR=g,
        _BUNDLED_SKILLS_DIR=tmp_path / "b",
    )
    with patch.multiple(_mod, **patches):
        return SkillManager(working_directory=tmp_path)


def test_activating_a_disabled_skill_is_refused(tmp_path):
    """The name stays resolvable through ``available_skills`` after a
    ``/skills disable``, so without a check at activation the whole
    ``SKILL.md`` body re-enters the prompt on every later turn."""
    m = _manager_with_skills(tmp_path, "alpha", "beta")
    m.disable_skill("alpha")

    result = m.activate_skill("alpha", "task")

    assert result.startswith("Error: Unknown skill 'alpha'")
    assert "beta" in result  # the message lists what *is* activatable
    assert "alpha" not in m.get_active_skills()
    assert "Secret alpha instructions." not in m.get_skills_context()


def test_disabling_an_active_skill_still_deactivates_it(tmp_path):
    """The other half of the pair, and the reason the refusal above belongs
    in the manager: disabling already meant "not active"."""
    m = _manager_with_skills(tmp_path, "alpha")
    m.activate_skill("alpha", "task")
    assert "alpha" in m.get_active_skills()

    m.disable_skill("alpha")

    assert "alpha" not in m.get_active_skills()
    assert "Secret alpha instructions." not in m.get_skills_context()


def test_re_enabling_a_skill_makes_it_activatable_again(tmp_path):
    """The refusal is state, not a permanent verdict on the name."""
    m = _manager_with_skills(tmp_path, "alpha")
    m.disable_skill("alpha")
    m.enable_skill("alpha")

    assert "Skill Activated: alpha" in m.activate_skill("alpha", "task")
    assert "alpha" in m.get_active_skills()


def test_the_activate_skill_tool_refuses_a_disabled_skill(tmp_path):
    """The model's path. Its ``enum`` already omits the name, but an enum is
    advisory and several providers do not enforce it."""
    from agentao.tools.skill import ActivateSkillTool

    m = _manager_with_skills(tmp_path, "alpha", "beta")
    m.disable_skill("alpha")
    tool = ActivateSkillTool(skill_manager=m)

    assert "alpha" not in tool.parameters["properties"]["skill_name"]["enum"]
    assert tool.execute("alpha", "task").startswith("Error: Unknown skill 'alpha'")
    assert "alpha" not in m.get_active_skills()
    assert "Skill Activated: beta" in tool.execute("beta", "task")


def test_a_disabled_skill_is_refused_when_the_enum_is_not_even_emitted(tmp_path):
    """The worst shape in #266: with *every* skill disabled the tool drops
    the ``enum`` key entirely (``tools/skill.py``: ``if skill_names:``) and
    ``skill_name`` widens back to a free-form string, so nothing but this
    check constrains the argument."""
    from agentao.tools.skill import ActivateSkillTool

    m = _manager_with_skills(tmp_path, "alpha")
    m.disable_skill("alpha")
    tool = ActivateSkillTool(skill_manager=m)

    assert m.list_all_skills() == ["alpha"]
    assert m.list_available_skills() == []
    assert "enum" not in tool.parameters["properties"]["skill_name"]
    assert tool.execute("alpha", "task").startswith("Error: Unknown skill 'alpha'")
    assert m.get_active_skills() == {}


def test_slash_skills_activate_refuses_a_disabled_skill(tmp_path, monkeypatch):
    """``/skills activate`` reaches the same entry, so refusing in the manager
    is what stops it here. The *message* is the CLI's, not the manager's:
    ``/skills`` lists this name under "Disabled Skills", so "Unknown skill"
    would read as a bug and hide the one-word remedy."""
    from agentao.cli.commands import handle_skills_command
    from agentao.cli.commands import skills as skills_mod

    printed: list[str] = []
    monkeypatch.setattr(
        skills_mod.console, "print", lambda *a, **k: printed.append(" ".join(map(str, a)))
    )

    m = _manager_with_skills(tmp_path, "alpha")
    m.disable_skill("alpha")
    cli = SimpleNamespace(agent=SimpleNamespace(skill_manager=m))

    handle_skills_command(cli, "activate alpha")

    assert "is disabled" in printed[-1]
    assert "/skills enable alpha" in printed[-1]
    assert "alpha" not in m.get_active_skills()


def test_a_sub_agent_cannot_activate_a_skill_the_parent_disabled(tmp_path):
    """``child_view`` forks ``disabled_skills`` precisely so a disabled skill
    stays out of a sub-agent's catalogue; that guarantee is only as strong as
    the check in ``activate_skill``."""
    m = _manager_with_skills(tmp_path, "alpha", "beta")
    m.disable_skill("alpha")
    child = m.child_view()

    assert child.activate_skill("alpha", "task").startswith("Error: Unknown skill 'alpha'")
    assert child.get_active_skills() == {}
    assert "Skill Activated: beta" in child.activate_skill("beta", "task")


# ---------------------------------------------------------------------------
# A malformed ``disabled_skills`` value never reaches ``set()``
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [None, 3, {"alpha": True}])
def test_a_non_list_disabled_skills_value_is_ignored_not_raised(tmp_path, bad):
    """``set(None)`` / ``set(3)`` is a ``TypeError`` straight out of
    ``SkillManager.__init__`` — and so out of ``Agentao.__init__``, which
    builds one — so a hand-edited ``skills_config.json`` used to stop the CLI
    from starting. The top-level-dict check next to it closed the same class
    of crash one level up."""
    g = tmp_path / "global"
    _write_skill(g, "alpha")
    cfg = tmp_path / ".agentao" / "skills_config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"disabled_skills": bad}), encoding="utf-8")

    with patch.multiple(_mod, _GLOBAL_SKILLS_DIR=g, _BUNDLED_SKILLS_DIR=tmp_path / "b"):
        m = SkillManager(working_directory=tmp_path)

    assert m.disabled_skills == set()
    assert "alpha" in m.list_available_skills()
    assert "Skill Activated: alpha" in m.activate_skill("alpha", "task")


def test_a_string_disabled_skills_value_is_not_expanded_to_characters(tmp_path):
    """``set("ab")`` is ``{"a", "b"}``, which hid — and, since #266, refused
    to activate — every skill whose name is one of those characters."""
    g = tmp_path / "global"
    _write_skill(g, "a")
    cfg = tmp_path / ".agentao" / "skills_config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"disabled_skills": "ab"}), encoding="utf-8")

    with patch.multiple(_mod, _GLOBAL_SKILLS_DIR=g, _BUNDLED_SKILLS_DIR=tmp_path / "b"):
        m = SkillManager(working_directory=tmp_path)

    assert m.disabled_skills == set()
    assert "Skill Activated: a" in m.activate_skill("a", "task")


def test_non_string_entries_are_dropped_and_the_rest_kept(tmp_path):
    g = tmp_path / "global"
    _write_skill(g, "alpha")
    _write_skill(g, "beta")
    cfg = tmp_path / ".agentao" / "skills_config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        json.dumps({"disabled_skills": ["alpha", 7, None]}), encoding="utf-8"
    )

    with patch.multiple(_mod, _GLOBAL_SKILLS_DIR=g, _BUNDLED_SKILLS_DIR=tmp_path / "b"):
        m = SkillManager(working_directory=tmp_path)

    assert m.disabled_skills == {"alpha"}
    assert m.list_available_skills() == ["beta"]
