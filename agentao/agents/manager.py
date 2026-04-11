"""AgentManager — loads agent definitions and creates AgentToolWrapper instances."""

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from ..tools.base import Tool
from .tools import AgentToolWrapper, CheckBackgroundAgentTool


class AgentManager:
    """Discovers and manages agent definitions from Markdown files with YAML frontmatter."""

    def __init__(self):
        self.definitions: Dict[str, Dict[str, Any]] = {}
        self._load_definitions()

    def _load_definitions(self):
        # 1. Built-in definitions: agentao/agents/definitions/*.md
        builtin_dir = Path(__file__).parent / "definitions"
        self._scan_directory(builtin_dir)

        # 2. User-defined: .agentao/agents/*.md (project-level)
        user_dir = Path.cwd() / ".agentao" / "agents"
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
                # model: optional "Provider/model-name" or bare "model-name"; None = inherit parent
                model = frontmatter.get("model") or None
                # temperature: optional float; None = inherit parent
                raw_temp = frontmatter.get("temperature")
                temperature = float(raw_temp) if raw_temp is not None else None

                # Parse tools as list if it's a string
                if isinstance(tools_list, str):
                    tools_list = [t.strip() for t in tools_list.split(",")]

                self.definitions[name] = {
                    "name": name,
                    "description": description,
                    "tools": tools_list,
                    "max_turns": max_turns,
                    "system_instructions": body.strip() or None,
                    "model": model,
                    "temperature": temperature,
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

    # ------------------------------------------------------------------
    # Plugin agent registration
    # ------------------------------------------------------------------

    def register_plugin_agents(
        self,
        agent_defs: "list[PluginAgentDefinition]",
    ) -> "list[PluginLoadError]":
        """Register plugin-provided agent definitions.

        Returns a list of collision errors.  If the list is non-empty the
        caller should treat the plugin as failed.

        Imports are deferred to avoid a hard dependency when the plugin
        subsystem is not installed.
        """
        from agentao.plugins.agents import validate_no_external_collisions
        from agentao.plugins.models import PluginAgentDefinition, PluginLoadError

        plugin_name = agent_defs[0].plugin_name if agent_defs else ""
        errors = validate_no_external_collisions(
            plugin_name, agent_defs, set(self.definitions.keys())
        )
        if errors:
            return errors

        for defn in agent_defs:
            frontmatter, body = self._parse_yaml_frontmatter(defn.raw_markdown)

            tools_list = frontmatter.get("tools")
            if isinstance(tools_list, str):
                tools_list = [t.strip() for t in tools_list.split(",")]

            raw_temp = frontmatter.get("temperature")

            self.definitions[defn.runtime_name] = {
                "name": defn.runtime_name,
                "description": defn.description or frontmatter.get("description", ""),
                "tools": tools_list,
                "max_turns": int(frontmatter.get("max_turns", 15)),
                "system_instructions": body.strip() or None,
                "model": frontmatter.get("model") or None,
                "temperature": float(raw_temp) if raw_temp is not None else None,
                # Plugin metadata
                "plugin_name": defn.plugin_name,
                "source_path": str(defn.source_path),
            }

        return []

    def list_agents(self) -> Dict[str, str]:
        """Return {name: description} for all loaded agents."""
        return {name: defn["description"] for name, defn in self.definitions.items()}

    def create_agent_tools(
        self,
        all_tools: Dict[str, Tool],
        llm_config: Dict[str, Any],
        confirmation_callback: Optional[Callable] = None,
        step_callback: Optional[Callable] = None,
        output_callback: Optional[Callable] = None,
        tool_complete_callback: Optional[Callable] = None,
        ask_user_callback: Optional[Callable] = None,
        max_context_tokens: Optional[int] = None,
        parent_messages_getter: Optional[Callable] = None,
        cancellation_token_getter: Optional[Callable] = None,
        readonly_mode_getter: Optional[Callable[[], bool]] = None,
        permission_mode_getter: Optional[Callable] = None,
    ) -> List[Tool]:
        """Create an AgentToolWrapper for each agent definition plus CheckBackgroundAgentTool."""
        _readonly_getter = readonly_mode_getter or (lambda: False)
        wrappers = [
            AgentToolWrapper(
                definition=defn,
                all_tools=all_tools,
                llm_config=llm_config,
                confirmation_callback=confirmation_callback,
                step_callback=step_callback,
                output_callback=output_callback,
                tool_complete_callback=tool_complete_callback,
                ask_user_callback=ask_user_callback,
                max_context_tokens=max_context_tokens,
                parent_messages_getter=parent_messages_getter,
                cancellation_token_getter=cancellation_token_getter,
                readonly_mode_getter=_readonly_getter,
                permission_mode_getter=permission_mode_getter,
            )
            for defn in self.definitions.values()
        ]
        return wrappers + [CheckBackgroundAgentTool()]
