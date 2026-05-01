"""CLI interface for Agentao.

P0.10 — the console-script ``entrypoint()`` is intentionally defined
inline (no module-level imports of rich / prompt_toolkit / readchar /
pygments) so a core-only install can print a friendly missing-dep
message and exit 2 instead of crashing with an opaque
``ModuleNotFoundError: rich``. All other public names are loaded
lazily via PEP 562 ``__getattr__`` so existing imports
(``from agentao.cli import AgentaoCLI``, ``from agentao.cli import
_build_parser``, etc.) continue to resolve when the ``[cli]`` extra
is installed.
"""

from __future__ import annotations

import sys
from typing import Any


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


# Map each public name to the (relative-module, attribute) pair that
# resolves it. The lazy load happens on first attribute access so the
# module-level import of ``agentao.cli`` itself stays free of rich /
# prompt_toolkit / readchar / pygments.
_LAZY_NAMES: dict[str, tuple[str, str]] = {
    "AgentaoCLI":                   (".app", "AgentaoCLI"),
    "console":                      ("._globals", "console"),
    "main":                         (".entrypoints", "main"),
    "run_print_mode":               (".entrypoints", "run_print_mode"),
    "run_init_wizard":              (".entrypoints", "run_init_wizard"),
    "run_acp_mode":                 (".entrypoints", "run_acp_mode"),
    "_build_parser":                (".entrypoints", "_build_parser"),
    "_PROVIDER_DEFAULTS":           (".entrypoints", "_PROVIDER_DEFAULTS"),
    "handle_skill_subcommand":      (".subcommands", "handle_skill_subcommand"),
    "handle_plugin_subcommand":     (".subcommands", "handle_plugin_subcommand"),
    "_skill_list":                  (".subcommands", "_skill_list"),
    "_skill_remove":                (".subcommands", "_skill_remove"),
    "_skill_install":               (".subcommands", "_skill_install"),
    "_skill_update":                (".subcommands", "_skill_update"),
    "_plugin_list_cli":             (".subcommands", "_plugin_list_cli"),
    "_load_and_register_plugins":   (".subcommands", "_load_and_register_plugins"),
    "_handle_plugins_interactive":  (".subcommands", "_handle_plugins_interactive"),
}


_MISSING_DEP_MESSAGE = (
    "agentao CLI requires extra packages (missing: {missing}).\n"
    "  pip install 'agentao[cli]'   # CLI surface only\n"
    "  pip install 'agentao[full]'  # 0.3.x-equivalent closure\n"
    "See docs/migration/0.3.x-to-0.4.0.md for details.\n"
)


def entrypoint() -> None:
    """Console-script ``agentao`` entry point.

    Wraps the first heavy CLI import in try/except so a user with a
    core-only install (no ``[cli]`` extra) gets a one-line actionable
    error instead of an opaque ``ModuleNotFoundError``.
    """
    try:
        from .entrypoints import entrypoint as _real_entrypoint
    except ImportError as exc:
        missing = exc.name or "a CLI dependency"
        sys.stderr.write(_MISSING_DEP_MESSAGE.format(missing=missing))
        sys.exit(2)
    _real_entrypoint()


def __getattr__(name: str) -> Any:
    """PEP 562 — lazy-load public re-exports on first attribute access."""
    if name in _LAZY_NAMES:
        from importlib import import_module

        mod_path, attr = _LAZY_NAMES[name]
        mod = import_module(mod_path, package=__name__)
        return getattr(mod, attr)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
