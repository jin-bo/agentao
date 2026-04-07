"""Skills manager for Agentao."""

import json
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Set

import yaml

# Two-layer skill directories (global then project; project takes priority)
_GLOBAL_SKILLS_DIR = Path.home() / ".agentao" / "skills"
_PROJECT_SKILLS_DIR = Path.cwd() / ".agentao" / "skills"

# Bundled skills shipped with the package (skill-creator lives here after install)
_BUNDLED_SKILLS_DIR = Path(__file__).parent.parent.parent / "skills"

# Config file for persisting disabled skills (project-scoped)
_CONFIG_DIR = Path.cwd() / ".agentao"
_CONFIG_FILE = _CONFIG_DIR / "skills_config.json"


class SkillManager:
    """Manager for Agentao skills.

    Scans two layers of skill directories:
      1. ~/.agentao/skills/     — global skills (shared across projects)
      2. cwd/.agentao/skills/   — project skills (override global on name clash)

    On first run, bundled skills (e.g. skill-creator) are copied to the global
    skills directory so they are available immediately after install.
    """

    def __init__(self, skills_dir: Optional[str] = None):
        """Initialize skill manager.

        Args:
            skills_dir: If provided, scan only this directory (legacy / sub-agent use).
                       Pass a non-existent path to suppress all skills.
                       If None (default), use the two-layer global + project scan.
        """
        self.active_skills: Dict[str, dict] = {}
        self.available_skills: Dict[str, dict] = {}
        self.disabled_skills: Set[str] = set()
        self._explicit_dir = Path(skills_dir) if skills_dir is not None else None

        if self._explicit_dir is None:
            self._bootstrap_bundled_skills()
        self._load_config()
        self._load_skills()

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------

    def _bootstrap_bundled_skills(self) -> None:
        """Copy bundled skills to ~/.agentao/skills/ on first run.

        Each bundled skill is copied only if it does not already exist in the
        global skills directory, so user modifications are never overwritten.
        """
        if not _BUNDLED_SKILLS_DIR.exists():
            return
        for skill_dir in _BUNDLED_SKILLS_DIR.iterdir():
            if not skill_dir.is_dir():
                continue
            dest = _GLOBAL_SKILLS_DIR / skill_dir.name
            if not dest.exists():
                try:
                    _GLOBAL_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(skill_dir, dest)
                except Exception:
                    pass  # best-effort; don't crash on bootstrap failure

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    def _load_config(self):
        """Load disabled skills list from config file."""
        if _CONFIG_FILE.exists():
            try:
                with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                    config = json.load(f)
                self.disabled_skills = set(config.get("disabled_skills", []))
            except (IOError, json.JSONDecodeError):
                self.disabled_skills = set()

    def _save_config(self):
        """Save disabled skills list to config file."""
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        config = {"disabled_skills": sorted(self.disabled_skills)}
        with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)

    def disable_skill(self, skill_name: str) -> str:
        """Disable a skill, hiding it from the available list."""
        if skill_name not in self.available_skills:
            available = ", ".join(sorted(self.available_skills.keys()))
            return f"Error: Unknown skill '{skill_name}'. Known skills: {available}"
        if skill_name in self.disabled_skills:
            return f"Skill '{skill_name}' is already disabled."
        self.disabled_skills.add(skill_name)
        if skill_name in self.active_skills:
            self.deactivate_skill(skill_name)
        self._save_config()
        return f"Skill '{skill_name}' has been disabled."

    def enable_skill(self, skill_name: str) -> str:
        """Re-enable a previously disabled skill."""
        if skill_name not in self.disabled_skills:
            if skill_name in self.available_skills:
                return f"Skill '{skill_name}' is not disabled."
            available = ", ".join(sorted(self.available_skills.keys()))
            return f"Error: Unknown skill '{skill_name}'. Known skills: {available}"
        self.disabled_skills.discard(skill_name)
        self._save_config()
        return f"Skill '{skill_name}' has been re-enabled."

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _parse_yaml_frontmatter(self, content: str) -> tuple[Dict[str, str], str]:
        """Parse YAML frontmatter from markdown file."""
        if not content.startswith('---'):
            return {}, content

        parts = content.split('---', 2)
        if len(parts) < 3:
            return {}, content

        frontmatter_text = parts[1]
        remaining_content = parts[2].strip()

        try:
            frontmatter = yaml.safe_load(frontmatter_text) or {}
            frontmatter = {k: str(v).strip() if v is not None else "" for k, v in frontmatter.items()}
        except yaml.YAMLError:
            frontmatter = {}

        return frontmatter, remaining_content

    def _load_skills(self):
        """Load skills from all configured directories.

        Priority order (highest last — later entries overwrite earlier ones):
          1. ~/.agentao/skills/      global skills
          2. cwd/.agentao/skills/    project config skills
          3. cwd/skills/             repo-root skills (highest priority)
        """
        if self._explicit_dir is not None:
            # Legacy / sub-agent mode: single directory only
            self._load_skills_from_dir(self._explicit_dir)
        else:
            self._load_skills_from_dir(_GLOBAL_SKILLS_DIR)
            self._load_skills_from_dir(_PROJECT_SKILLS_DIR)
            repo_skills = Path.cwd() / "skills"
            if repo_skills.exists():
                self._load_skills_from_dir(repo_skills)

    def _load_skills_from_dir(self, skills_dir: Path) -> None:
        """Scan one directory for skills and merge into available_skills."""
        if not skills_dir.exists():
            return

        for skill_dir in skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue

            skill_md_path = skill_dir / "SKILL.md"
            if not skill_md_path.exists():
                continue

            try:
                with open(skill_md_path, "r", encoding="utf-8") as f:
                    content = f.read()

                frontmatter, body_content = self._parse_yaml_frontmatter(content)

                skill_name = frontmatter.get("name", skill_dir.name)
                description = frontmatter.get("description", "")
                when_to_use = frontmatter.get("when-to-use", "")

                title_match = re.search(r'^#\s+(.+)$', body_content, re.MULTILINE)
                title = title_match.group(1) if title_match else skill_name

                self.available_skills[skill_name] = {
                    "name": skill_name,
                    "title": title,
                    "description": description,
                    "when_to_use": when_to_use,
                    "path": str(skill_md_path),
                    "content": body_content[:500],
                    "frontmatter": frontmatter,
                }

            except (IOError, UnicodeDecodeError) as e:
                print(f"Warning: Could not load skill from {skill_md_path}: {e}")
                continue

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def list_available_skills(self) -> List[str]:
        """List available (non-disabled) skills."""
        return [name for name in self.available_skills if name not in self.disabled_skills]

    def list_all_skills(self) -> List[str]:
        """List all discovered skills including disabled ones."""
        return list(self.available_skills.keys())

    def get_skill_description(self, skill_name: str) -> Optional[str]:
        skill_data = self.available_skills.get(skill_name)
        return skill_data.get("description") if skill_data else None

    def get_skill_info(self, skill_name: str) -> Optional[dict]:
        return self.available_skills.get(skill_name)

    def _list_skill_resources(self, skill_name: str) -> Dict[str, List[str]]:
        skill_info = self.get_skill_info(skill_name)
        if not skill_info or 'path' not in skill_info:
            return {"references": [], "assets": []}

        skill_dir = Path(skill_info['path']).parent
        resources = {"references": [], "assets": []}

        references_dir = skill_dir / "references"
        if references_dir.exists() and references_dir.is_dir():
            for file_path in references_dir.rglob("*.md"):
                resources["references"].append(str(file_path))

        assets_dir = skill_dir / "assets"
        if assets_dir.exists() and assets_dir.is_dir():
            for file_path in assets_dir.rglob("*.md"):
                resources["assets"].append(str(file_path))

        return resources

    # ------------------------------------------------------------------
    # Activation
    # ------------------------------------------------------------------

    def activate_skill(self, skill_name: str, task_description: str) -> str:
        skill_info = self.get_skill_info(skill_name)
        if not skill_info:
            available = ", ".join(self.list_available_skills())
            return f"Error: Unknown skill '{skill_name}'. Available skills: {available}"

        self.active_skills[skill_name] = {
            "task": task_description,
            "skill_info": skill_info,
        }

        message = f"\nSkill Activated: {skill_name}\n"
        message += f"Title: {skill_info.get('title', skill_name)}\n"
        if skill_info.get('description'):
            message += f"Description: {skill_info['description'][:200]}...\n"
        message += f"Task: {task_description}\n"
        message += f"Documentation: {skill_info.get('path', 'N/A')}\n"

        resources = self._list_skill_resources(skill_name)
        if resources["references"] or resources["assets"]:
            message += "\n=== Available Resource Files ===\n"
            message += "You can use the read_file tool to load these files as needed:\n\n"
            if resources["references"]:
                message += "References:\n"
                for ref_path in sorted(resources["references"]):
                    message += f"  - {ref_path}\n"
            if resources["assets"]:
                message += "\nAssets:\n"
                for asset_path in sorted(resources["assets"]):
                    message += f"  - {asset_path}\n"

        message += "\nThe skill is now active. You can reference its documentation for detailed usage."
        return message

    def deactivate_skill(self, skill_name: str) -> bool:
        if skill_name in self.active_skills:
            del self.active_skills[skill_name]
            return True
        return False

    def get_active_skills(self) -> Dict[str, dict]:
        return self.active_skills.copy()

    def clear_active_skills(self):
        self.active_skills.clear()

    def get_skills_context(self) -> str:
        """Get context about active skills for the LLM."""
        if not self.active_skills:
            return ""

        context = "\n=== Active Skills ===\n"
        for name, info in self.active_skills.items():
            skill_info = info['skill_info']
            context += f"\n## {name} - {skill_info.get('title', name)}\n"
            context += f"Task: {info['task']}\n"

            skill_content = self.get_skill_content(name)
            if skill_content:
                context += f"\n{skill_content}\n"

            resources = self._list_skill_resources(name)
            if resources["references"] or resources["assets"]:
                context += "\nAvailable resource files (use read_file to load):\n"
                for ref in sorted(resources.get("references", [])):
                    context += f"  - {ref}\n"
                for asset in sorted(resources.get("assets", [])):
                    context += f"  - {asset}\n"

        return context

    def reload_skills(self):
        """Reload skill definitions from disk."""
        self.available_skills.clear()
        self._load_skills()
        self.disabled_skills &= set(self.available_skills.keys())
        self._save_config()

    def get_skill_content(self, skill_name: str) -> Optional[str]:
        skill_info = self.get_skill_info(skill_name)
        if not skill_info or 'path' not in skill_info:
            return None
        try:
            with open(skill_info['path'], 'r', encoding='utf-8') as f:
                return f.read()
        except IOError:
            return None
