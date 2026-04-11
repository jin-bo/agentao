"""CLI interface for Agentao.

This package re-exports all public symbols that were previously available
from the monolithic ``agentao.cli`` module so that existing imports
(``from agentao.cli import entrypoint``, etc.) continue to work.
"""

from ._globals import console, logger, _plugin_inline_dirs
from .app import AgentaoCLI
from .entrypoints import (
    entrypoint,
    main,
    run_print_mode,
    run_init_wizard,
    run_acp_mode,
    _build_parser,
    _PROVIDER_DEFAULTS,
)
from .subcommands import (
    handle_skill_subcommand,
    handle_plugin_subcommand,
    _skill_list,
    _skill_remove,
    _skill_install,
    _skill_update,
    _plugin_list_cli,
    _load_and_register_plugins,
    _handle_plugins_interactive,
)

__all__ = [
    "AgentaoCLI",
    "console",
    "entrypoint",
    "main",
    "run_print_mode",
    "run_init_wizard",
    "run_acp_mode",
    "_build_parser",
    "_PROVIDER_DEFAULTS",
    "handle_skill_subcommand",
    "handle_plugin_subcommand",
    "_skill_list",
    "_skill_remove",
    "_skill_install",
    "_skill_update",
    "_plugin_list_cli",
    "_load_and_register_plugins",
    "_handle_plugins_interactive",
]
