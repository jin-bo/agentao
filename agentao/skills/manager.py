"""Skills manager for handling Claude skills."""

import re
from pathlib import Path
from typing import Dict, List, Optional

import yaml


class SkillManager:
    """Manager for Claude skills."""

    def __init__(self, skills_dir: Optional[str] = None):
        """Initialize skill manager.

        Args:
            skills_dir: Directory containing skill subdirectories.
                       Each subdirectory should contain a SKILL.md file.
                       If None, looks for 'skills' directory in current working directory.
        """
        self.active_skills: Dict[str, dict] = {}
        self.available_skills: Dict[str, dict] = {}

        # Determine skills directory
        if skills_dir is None:
            # Default to 'skills' directory in current working directory
            # This allows finding skills relative to where the program runs
            skills_dir = Path.cwd() / "skills"

            # If that doesn't exist, try relative to the chatagent package
            if not skills_dir.exists():
                # Look for skills at project root (parent of chatagent package)
                project_root = Path(__file__).parent.parent.parent
                skills_dir = project_root / "skills"
        else:
            skills_dir = Path(skills_dir)

        self.skills_dir = Path(skills_dir)
        self._load_skills()

    def _parse_yaml_frontmatter(self, content: str) -> tuple[Dict[str, str], str]:
        """Parse YAML frontmatter from markdown file.

        Args:
            content: Full file content

        Returns:
            Tuple of (frontmatter_dict, remaining_content)
        """
        # Check if file starts with ---
        if not content.startswith('---'):
            return {}, content

        # Find the closing ---
        parts = content.split('---', 2)
        if len(parts) < 3:
            return {}, content

        frontmatter_text = parts[1]
        remaining_content = parts[2].strip()

        try:
            frontmatter = yaml.safe_load(frontmatter_text) or {}
            # Ensure all values are strings
            frontmatter = {k: str(v).strip() if v is not None else "" for k, v in frontmatter.items()}
        except yaml.YAMLError:
            frontmatter = {}

        return frontmatter, remaining_content

    def _load_skills(self):
        """Load skill definitions from SKILL.md files in subdirectories."""
        if not self.skills_dir.exists():
            # If directory doesn't exist, use empty skills dict
            return

        # Scan all subdirectories for SKILL.md files
        for skill_dir in self.skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue

            skill_md_path = skill_dir / "SKILL.md"
            if not skill_md_path.exists():
                continue

            try:
                # Read SKILL.md file
                with open(skill_md_path, "r", encoding="utf-8") as f:
                    content = f.read()

                # Parse YAML frontmatter
                frontmatter, body_content = self._parse_yaml_frontmatter(content)

                # Extract skill name (prefer from frontmatter, fallback to directory name)
                skill_name = frontmatter.get("name", skill_dir.name)

                # Extract description from frontmatter
                description = frontmatter.get("description", "")

                # Extract first heading from body as title if available
                title_match = re.search(r'^#\s+(.+)$', body_content, re.MULTILINE)
                title = title_match.group(1) if title_match else skill_name

                # Store skill data
                self.available_skills[skill_name] = {
                    "name": skill_name,
                    "title": title,
                    "description": description,
                    "path": str(skill_md_path),
                    "content": body_content[:500],  # Store first 500 chars as preview
                    "frontmatter": frontmatter,
                }

            except (IOError, UnicodeDecodeError) as e:
                # Skip invalid skill files
                print(f"Warning: Could not load skill from {skill_md_path}: {e}")
                continue

    def list_available_skills(self) -> List[str]:
        """List all available skills.

        Returns:
            List of skill names
        """
        return list(self.available_skills.keys())

    def get_skill_description(self, skill_name: str) -> Optional[str]:
        """Get description of a skill.

        Args:
            skill_name: Name of the skill

        Returns:
            Skill description or None if not found
        """
        skill_data = self.available_skills.get(skill_name)
        return skill_data.get("description") if skill_data else None

    def get_skill_info(self, skill_name: str) -> Optional[dict]:
        """Get full information about a skill.

        Args:
            skill_name: Name of the skill

        Returns:
            Skill data dictionary or None if not found
        """
        return self.available_skills.get(skill_name)

    def _list_skill_resources(self, skill_name: str) -> Dict[str, List[str]]:
        """List available resource files (references and assets) for a skill.

        Args:
            skill_name: Name of the skill

        Returns:
            Dictionary with 'references' and 'assets' keys containing file paths
        """
        skill_info = self.get_skill_info(skill_name)
        if not skill_info or 'path' not in skill_info:
            return {"references": [], "assets": []}

        # Get skill directory
        skill_dir = Path(skill_info['path']).parent
        resources = {"references": [], "assets": []}

        # Check references directory
        references_dir = skill_dir / "references"
        if references_dir.exists() and references_dir.is_dir():
            for file_path in references_dir.rglob("*.md"):
                resources["references"].append(str(file_path))

        # Check assets directory
        assets_dir = skill_dir / "assets"
        if assets_dir.exists() and assets_dir.is_dir():
            for file_path in assets_dir.rglob("*.md"):
                resources["assets"].append(str(file_path))

        return resources

    def activate_skill(self, skill_name: str, task_description: str) -> str:
        """Activate a skill.

        Args:
            skill_name: Name of the skill to activate
            task_description: Description of the task

        Returns:
            Activation result message
        """
        skill_info = self.get_skill_info(skill_name)
        if not skill_info:
            available = ", ".join(self.list_available_skills())
            return f"Error: Unknown skill '{skill_name}'. Available skills: {available}"

        self.active_skills[skill_name] = {
            "task": task_description,
            "skill_info": skill_info,
        }

        # Build activation message
        message = f"\nSkill Activated: {skill_name}\n"
        message += f"Title: {skill_info.get('title', skill_name)}\n"
        if skill_info.get('description'):
            message += f"Description: {skill_info['description'][:200]}...\n"
        message += f"Task: {task_description}\n"
        message += f"Documentation: {skill_info.get('path', 'N/A')}\n"

        # List available resource files
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
        """Deactivate a skill.

        Args:
            skill_name: Name of the skill to deactivate

        Returns:
            True if deactivated, False if not active
        """
        if skill_name in self.active_skills:
            del self.active_skills[skill_name]
            return True
        return False

    def get_active_skills(self) -> Dict[str, dict]:
        """Get currently active skills.

        Returns:
            Dictionary of active skills
        """
        return self.active_skills.copy()

    def clear_active_skills(self):
        """Clear all active skills.

        This is typically called when resetting conversation state.
        """
        self.active_skills.clear()

    def get_skills_context(self) -> str:
        """Get context about active skills for the LLM.

        Returns:
            Formatted context string
        """
        if not self.active_skills:
            return ""

        context = "\n=== Active Skills ===\n"
        for name, info in self.active_skills.items():
            skill_info = info['skill_info']
            context += f"\n{name} - {skill_info.get('title', name)}:\n"
            context += f"  Description: {skill_info.get('description', 'N/A')[:150]}...\n"
            context += f"  Task: {info['task']}\n"
            context += f"  Documentation: {skill_info.get('path', 'N/A')}\n"

        return context

    def reload_skills(self):
        """Reload skill definitions from disk.

        Useful for adding new skills without restarting the application.
        """
        self.available_skills.clear()
        self._load_skills()

    def get_skill_content(self, skill_name: str) -> Optional[str]:
        """Get full content of a skill's SKILL.md file.

        Args:
            skill_name: Name of the skill

        Returns:
            Full content of SKILL.md or None if not found
        """
        skill_info = self.get_skill_info(skill_name)
        if not skill_info or 'path' not in skill_info:
            return None

        try:
            with open(skill_info['path'], 'r', encoding='utf-8') as f:
                return f.read()
        except IOError:
            return None
