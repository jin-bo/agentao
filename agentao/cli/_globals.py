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


def split_subcommand(
    args: str,
    *,
    default: str = "",
    lower: bool = False,
    strip_rest: bool = True,
) -> tuple[str, str]:
    """Split a slash-command argument string into ``(subcommand, rest)``.

    Centralizes the ``args.strip().split(None, 1)`` preamble shared by the
    ``/mcp`` / ``/sessions`` / ``/permission`` / ``/acp`` / ``/agent``
    handlers. The keyword flags preserve each handler's historical casing
    (the duplication had already drifted apart):

    - ``default`` — the subcommand returned for an empty argument string
      (``"list"`` for mcp/sessions, ``"status"`` for permission, ``""`` else).
    - ``lower`` — lower-case the subcommand (only ``/permission`` did this).
    - ``strip_rest`` — strip the remainder (``/mcp add`` historically did not).
    """
    parts = args.strip().split(None, 1)
    if parts:
        sub = parts[0].lower() if lower else parts[0]
    else:
        sub = default.lower() if lower else default
    if len(parts) > 1:
        rest = parts[1].strip() if strip_rest else parts[1]
    else:
        rest = ""
    return sub, rest


def unknown_subcommand(sub: str) -> str:
    """The standard ``Unknown subcommand`` error line shared by handlers."""
    return f"\n[error]Unknown subcommand: {sub}[/error]"
