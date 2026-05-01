"""CLI surface for Agentao.

Defining ``entrypoint()`` inline keeps ``agentao.cli`` import-light so a
core-only install (no ``[cli]`` extra) can print a friendly missing-dep
message instead of crashing with ``ModuleNotFoundError: rich``. All
other public names load lazily via PEP 562 ``__getattr__``.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    # Type checkers and IDEs see explicit names; the runtime path uses __getattr__.
    from .app import AgentaoCLI
    from ._globals import console
    from .entrypoints import (
        _build_parser,
        _PROVIDER_DEFAULTS,
        main,
        run_acp_mode,
        run_init_wizard,
        run_print_mode,
    )
    from .subcommands import (
        _handle_plugins_interactive,
        _load_and_register_plugins,
        _plugin_list_cli,
        _skill_install,
        _skill_list,
        _skill_remove,
        _skill_update,
        handle_plugin_subcommand,
        handle_skill_subcommand,
    )


def _names(mod: str, *names: str) -> dict[str, tuple[str, str]]:
    return {name: (mod, name) for name in names}


_LAZY_NAMES: dict[str, tuple[str, str]] = {
    "AgentaoCLI": (".app", "AgentaoCLI"),
    "console":    ("._globals", "console"),
    **_names(".entrypoints",
        "main", "run_print_mode", "run_init_wizard", "run_acp_mode",
        "_build_parser", "_PROVIDER_DEFAULTS",
    ),
    **_names(".subcommands",
        "handle_skill_subcommand", "handle_plugin_subcommand",
        "_skill_list", "_skill_remove", "_skill_install", "_skill_update",
        "_plugin_list_cli", "_load_and_register_plugins", "_handle_plugins_interactive",
    ),
}


__all__ = ["entrypoint", *sorted(_LAZY_NAMES.keys())]


_MISSING_DEP_MESSAGE = (
    "agentao CLI requires extra packages (missing: {missing}).\n"
    "  pip install 'agentao[cli]'   # CLI surface only\n"
    "  pip install 'agentao[full]'  # 0.3.x-equivalent closure\n"
    "See docs/migration/0.3.x-to-0.4.0.md for details.\n"
)

# Probed up-front by entrypoint() before dispatching. Keep in sync with
# `[project.optional-dependencies].cli` in pyproject.toml.
_CLI_EXTRA_PACKAGES: tuple[str, ...] = ("rich", "prompt_toolkit", "readchar", "pygments")


def entrypoint() -> None:
    """Console-script entry point with a friendly missing-dep guard.

    Pre-flights every [cli] dep before delegating. Wrapping the dispatch
    call alone is not enough: ``entrypoints.run_init_wizard`` has a
    broad ``except Exception`` that swallows deep ``ImportError`` and
    re-emits a generic "Fatal error" — so a partial install (rich
    present, prompt_toolkit missing) would otherwise bypass this guard.
    """
    import importlib.util

    for name in _CLI_EXTRA_PACKAGES:
        try:
            if importlib.util.find_spec(name) is not None:
                continue
        except (ImportError, ValueError):
            pass
        sys.stderr.write(_MISSING_DEP_MESSAGE.format(missing=name))
        sys.exit(2)

    from .entrypoints import entrypoint as _real_entrypoint
    _real_entrypoint()


def __getattr__(name: str) -> Any:
    if name in _LAZY_NAMES:
        from importlib import import_module

        mod_path, attr = _LAZY_NAMES[name]
        return getattr(import_module(mod_path, package=__name__), attr)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(list(globals().keys()) + __all__))
