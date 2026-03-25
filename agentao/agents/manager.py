"""AgentManager — loads agent definitions and creates AgentToolWrapper instances."""

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from ..tools.base import Tool
from .tools import AgentToolWrapper


class AgentManager:
    """Discovers and manages agent definitions from Markdown files with YAML frontmatter."""

    def __init__(self):
        self.definitions: Dict[str, Dict[str, Any]] = {}
        self._load_definitions()

    def _load_definitions(self):
        # 1. Built-in definitions: chatagent/agents/definitions/*.md
        builtin_dir = Path(__file__).parent / "definitions"
        self._scan_directory(builtin_dir)

        # 2. User-defined: .chatagent/agents/*.md (project-level)
        user_dir = Path.cwd() / ".chatagent" / "agents"
        self._scan_directory(user_dir)

    def _scan_directory(self, directory: Path):
        if not directory.exists():
            return

        for md_file in sorted(directory.glob("*.md")):
            try:
                content = md_file.read_text(encoding="utf-8")
                frontmatter, body = self._parse_yaml_frontmatter(content)

                name = frontmatter.get("name", md_file.stem)
                description = frontmatter.get("description", "")
                tools_list = frontmatter.get("tools")  # None means all tools
                max_turns = int(frontmatter.get("max_turns", 15))

                # Parse tools as list if it's a string
                if isinstance(tools_list, str):
                    tools_list = [t.strip() for t in tools_list.split(",")]

                self.definitions[name] = {
                    "name": name,
                    "description": description,
                    "tools": tools_list,
                    "max_turns": max_turns,
                    "system_instructions": body.strip() or None,
                }
            except Exception:
                continue

    @staticmethod
    def _parse_yaml_frontmatter(content: str) -> tuple:
        if not content.startswith("---"):
            return {}, content

        parts = content.split("---", 2)
        if len(parts) < 3:
            return {}, content

        try:
            frontmatter = yaml.safe_load(parts[1]) or {}
        except yaml.YAMLError:
            frontmatter = {}

        return frontmatter, parts[2]

    def list_agents(self) -> Dict[str, str]:
        """Return {name: description} for all loaded agents."""
        return {name: defn["description"] for name, defn in self.definitions.items()}

    def create_agent_tools(
        self,
        all_tools: Dict[str, Tool],
        llm_config: Dict[str, Any],
        confirmation_callback: Optional[Callable] = None,
        step_callback: Optional[Callable] = None,
    ) -> List[AgentToolWrapper]:
        """Create an AgentToolWrapper for each agent definition."""
        return [
            AgentToolWrapper(
                definition=defn,
                all_tools=all_tools,
                llm_config=llm_config,
                confirmation_callback=confirmation_callback,
                step_callback=step_callback,
            )
            for defn in self.definitions.values()
        ]
