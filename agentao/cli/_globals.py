"""Shared globals for the CLI package."""

import logging
from pathlib import Path

from rich.console import Console
from rich.theme import Theme

# Custom theme for the CLI
custom_theme = Theme({
    "info": "cyan",
    "warning": "yellow",
    "error": "bold red",
    "success": "bold green",
})

console = Console(theme=custom_theme)
logger = logging.getLogger("agentao.cli")

# Plugin inline dirs set from --plugin-dir in entrypoint(), consumed by
# AgentaoCLI and run_print_mode to wire plugins into sessions.
_plugin_inline_dirs: list[Path] = []

# Tool argument keys to display in the thinking step (priority order)
_TOOL_SUMMARY_KEYS = ("path", "file_path", "query", "description", "command", "url", "key", "pattern", "tag")
