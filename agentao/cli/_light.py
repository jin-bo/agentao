"""The parts of the ``agentao`` command that run without the ``[cli]`` extras.

An ACP Registry client launches Agentao as ``uvx agentao@<version> --acp`` and
logs in with ``--login``. Neither needs ``rich`` or ``prompt_toolkit``, so
:func:`agentao.cli.entrypoint` routes them here before its extras check. This
module, the argument parser and everything they import must stay free of
those packages; ``tests/test_cli_bare_install.py`` holds that line.
"""

from __future__ import annotations

import sys
from typing import Any, List, Optional


def run_acp_mode(resume: Optional[str] = None) -> None:
    """Launch Agentao as an ACP stdio JSON-RPC server.

    ``resume`` forwards the ``--resume`` selector into ACP mode: ``None``
    starts fresh, ``""`` resumes the latest saved session on the first
    ``session/new``, and a string resumes that specific session.
    """
    from agentao.acp.__main__ import main as acp_main
    acp_main(resume=resume)


def _build_parser():
    """Build the top-level argument parser with subcommands."""
    import argparse

    parser = argparse.ArgumentParser(prog="agentao", add_help=False)
    parser.add_argument(
        "-h", "--help",
        dest="show_help",
        action="store_true",
        default=False,
        help="Show this help message and exit.",
    )
    parser.add_argument("-p", "--print", dest="prompt", nargs="?", const="", default=None)
    parser.add_argument(
        "--resume",
        dest="resume",
        nargs="?",
        const="",
        default=None,
        metavar="SESSION_ID",
        help=(
            "Resume a saved session. Omit SESSION_ID to resume the latest. "
            "With --acp, the first session/new resumes instead of starting blank."
        ),
    )
    parser.add_argument(
        "--acp",
        dest="acp",
        action="store_true",
        default=False,
        help="Launch Agentao as an Agent Client Protocol (ACP) server.",
    )
    parser.add_argument(
        "--stdio",
        dest="stdio",
        action="store_true",
        default=False,
        help=(
            "Use stdio transport for ACP mode (currently the only supported "
            "transport — implied by --acp)."
        ),
    )
    parser.add_argument(
        "--login",
        dest="login",
        action="store_true",
        default=False,
        help=(
            "Configure the LLM provider Agentao's ACP sessions use "
            "(~/.agentao/llm.json) and exit. Also how an ACP client runs "
            "Terminal Auth; takes precedence over --acp."
        ),
    )
    parser.add_argument(
        "--plugin-dir",
        dest="plugin_dirs",
        action="append",
        default=[],
        metavar="DIR",
        help="Load a plugin from DIR (repeatable).",
    )

    subparsers = parser.add_subparsers(dest="subcommand")

    subparsers.add_parser("init")

    from .run import add_run_subparser
    add_run_subparser(subparsers)

    _sub_plugin_dir_kwargs = dict(
        dest="sub_plugin_dirs", action="append", default=None,
        metavar="DIR", help="Load a plugin from DIR (repeatable).",
    )

    plugin_parser = subparsers.add_parser("plugin")
    plugin_parser.add_argument("--plugin-dir", **_sub_plugin_dir_kwargs)
    plugin_sub = plugin_parser.add_subparsers(dest="plugin_action")
    plugin_list_p = plugin_sub.add_parser("list", help="List loaded plugins")
    plugin_list_p.add_argument("--plugin-dir", **_sub_plugin_dir_kwargs)
    plugin_list_p.add_argument(
        "--json", dest="json_output", action="store_true",
        help="Output as JSON",
    )

    skill_parser = subparsers.add_parser("skill")
    skill_parser.add_argument("--plugin-dir", **_sub_plugin_dir_kwargs)
    skill_sub = skill_parser.add_subparsers(dest="skill_action")

    install_p = skill_sub.add_parser("install", help="Install a skill from GitHub")
    install_p.add_argument("ref", help="GitHub ref: owner/repo[:path][@ref]")
    install_p.add_argument(
        "--scope", choices=["global", "project"], default=None,
        help="Install scope (default: auto-detect)",
    )
    install_p.add_argument(
        "--force", action="store_true",
        help="Overwrite existing skill",
    )

    remove_p = skill_sub.add_parser("remove", help="Remove an installed skill")
    remove_p.add_argument("name", help="Skill name to remove")
    remove_p.add_argument(
        "--scope", choices=["global", "project"], default=None,
        help="Scope to remove from (default: auto-detect)",
    )

    list_p = skill_sub.add_parser("list", help="List installed skills")
    list_p.add_argument(
        "--installed", action="store_true",
        help="Show only managed installs",
    )
    list_p.add_argument(
        "--json", dest="json_output", action="store_true",
        help="Output as JSON",
    )

    update_p = skill_sub.add_parser("update", help="Update installed skill(s)")
    update_p.add_argument("name", nargs="?", default=None, help="Skill name to update")
    update_p.add_argument(
        "--all", dest="update_all", action="store_true",
        help="Update all managed skills",
    )
    update_p.add_argument(
        "--scope", choices=["global", "project"], default=None,
        help="Scope to update (default: auto-detect)",
    )

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Aggregate Agentao health signals (config, plugins, permissions, …).",
    )
    doctor_parser.add_argument(
        "--json", dest="json_output", action="store_true",
        help="Emit a single JSON document instead of a human-readable report.",
    )

    config_parser = subparsers.add_parser(
        "config",
        help="Inspect or validate Agentao configuration.",
    )
    config_sub = config_parser.add_subparsers(dest="config_action")
    config_validate_p = config_sub.add_parser(
        "validate",
        help="Report config errors in settings, permissions, MCP, replay, memory.",
    )
    config_validate_p.add_argument(
        "--json", dest="json_output", action="store_true",
        help="Emit a single JSON document instead of a human-readable report.",
    )

    return parser


def dispatch_acp_or_login(parser: Any, args: Any, extras: List[str]) -> bool:
    """Run ``--login`` or ``--acp`` if requested; ``False`` means neither.

    Login comes first: a Terminal Auth client may append ``--login`` to the
    ``--acp`` it launches with (the ACP spec) or replace it (the Registry's
    description), and either way it must not get a server. Both modes refuse
    leftovers, since ``parse_known_args`` would otherwise swallow a typo'd
    flag and start a server that reads stdin to EOF and exits 0, which a
    client reads as "login succeeded".

    Calls go through the ``agentao.cli`` package so tests can monkeypatch
    ``run_login`` / ``run_acp_mode`` there.
    """
    if not (args.login or args.acp):
        return False
    import agentao.cli as _cli

    if extras:
        parser.error("unrecognized arguments: " + " ".join(extras))
    if args.login:
        sys.exit(_cli.run_login())
    _cli.run_acp_mode(resume=args.resume)
    return True


def run_light(argv: List[str]) -> bool:
    """Handle *argv* if it asks for ``--acp`` / ``--login``; else ``False``.

    Only consulted when one of those tokens is present, so every other
    command still meets the extras check first. A command line that does not
    parse, or where the token belongs to something else (``plugin list
    --acp``), is left to the full entry point and its usual errors.
    """
    parser = _build_parser()
    try:
        args, extras = parser.parse_known_args(argv)
    except SystemExit:
        return False
    if not (args.login or args.acp):
        return False
    if getattr(args, "show_help", False):
        parser.print_help()
        sys.exit(0)
    return dispatch_acp_or_login(parser, args, extras)
