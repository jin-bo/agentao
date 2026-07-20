"""Entry points, argument parser, and non-interactive modes."""

from __future__ import annotations

import atexit
import os
import sys
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from dotenv import load_dotenv
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from ._globals import console, _plugin_inline_dirs

if TYPE_CHECKING:
    # Type-only: importing .app at module scope would pull prompt_toolkit and
    # defeat this module's import-lightness.
    from .app import AgentFactory


def run_print_mode(prompt: str) -> int:
    """Non-interactive print mode: thin shim over ``agentao run``.

    Equivalent to ``agentao run --format text --prompt <text>``.
    Inherits the unified exit-code table — notably, max-iterations is
    exit ``4`` (was ``2`` before 0.4.x; documented in release notes).
    """
    from .run import execute as _run_execute
    return _run_execute(["--format", "text", "--prompt", prompt])


def main(
    resume_session: Optional[str] = None,
    *,
    agent_factory: Optional["AgentFactory"] = None,
):
    """Main entry point.

    Args:
        resume_session: Session selector — ``None`` starts fresh, ``""``
            resumes the latest, a string resumes that session.
        agent_factory: Optional host-supplied runtime builder forwarded
            verbatim to :class:`~agentao.cli.app.AgentaoCLI`. See that
            class and ``docs/design/cli-host-agent-factory.md``.

    Note for embedders: a factory exception is caught by the broad handler
    below and rendered as a single ``Fatal error:`` line with no traceback,
    then ``sys.exit(1)``. Construct :class:`AgentaoCLI` directly if you need
    the exception to propagate to your own code.
    """
    try:
        import termios
        _HAS_TERMIOS = True
    except ImportError:
        _HAS_TERMIOS = False

    _saved_tc = None
    _tty_fd = None
    if _HAS_TERMIOS:
        try:
            _tty_fd = os.open('/dev/tty', os.O_RDWR | os.O_NOCTTY)
            _saved_tc = termios.tcgetattr(_tty_fd)
        except Exception:
            if _tty_fd is not None:
                try:
                    os.close(_tty_fd)
                except Exception:
                    pass
                _tty_fd = None
            try:
                if sys.stdin.isatty():
                    _saved_tc = termios.tcgetattr(sys.stdin.fileno())
            except Exception:
                pass

    def _restore_terminal():
        if _saved_tc is None:
            return
        fd = _tty_fd if _tty_fd is not None else (
            sys.stdin.fileno() if sys.stdin.isatty() else None
        )
        if fd is None:
            return
        if _HAS_TERMIOS:
            try:
                termios.tcsetattr(fd, termios.TCSANOW, _saved_tc)
            except Exception:
                pass

    atexit.register(_restore_terminal)

    try:
        from .app import AgentaoCLI
        cli = AgentaoCLI(agent_factory=agent_factory)
        if resume_session is not None:
            from .commands import resume_session as _resume
            _resume(cli, resume_session if resume_session else None)
        cli.run()
    except KeyboardInterrupt:
        console.print("\n\n[success]Goodbye![/success]\n")
        sys.exit(0)
    except Exception as e:
        console.print(f"\n[error]Fatal error: {str(e)}[/error]\n")
        sys.exit(1)


_PROVIDER_DEFAULTS = {
    "OPENAI":     {"base_url": "https://api.openai.com/v1",                                          "model": "gpt-5.4"},
    "DEEPSEEK":   {"base_url": "https://api.deepseek.com/v1",                                        "model": "deepseek-chat"},
    "GEMINI":     {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai",             "model": "gemini-flash-latest"},
    "ANTHROPIC":  {"base_url": "https://api.anthropic.com/v1",                                       "model": "claude-sonnet-4-6"},
}


def run_init_wizard() -> None:
    """Interactive first-run setup wizard."""
    from rich.rule import Rule

    console.print()
    console.print(Panel.fit(
        "[bold cyan]Agentao[/bold cyan] — setup wizard\n"
        "[dim]Configure your LLM provider and create the local .env file.[/dim]",
        border_style="cyan",
    ))
    console.print()

    env_path = Path(".env")
    if env_path.exists():
        console.print("[warning]A .env file already exists in this directory.[/warning]")
        if not Confirm.ask("Overwrite it?", default=False):
            console.print("[dim]Aborted. No changes made.[/dim]")
            return
        console.print()

    provider_choices = list(_PROVIDER_DEFAULTS.keys()) + ["CUSTOM"]
    console.print("[bold]Step 1 of 3 — LLM Provider[/bold]")
    for i, name in enumerate(provider_choices, 1):
        console.print(f"  [cyan]{i}[/cyan]  {name}")
    console.print()

    while True:
        raw = Prompt.ask(
            "Choose provider",
            default="1",
        ).strip()
        if raw.isdigit() and 1 <= int(raw) <= len(provider_choices):
            provider = provider_choices[int(raw) - 1]
            break
        upper = raw.upper()
        if upper in provider_choices:
            provider = upper
            break
        console.print("[error]Invalid choice — enter a number or provider name.[/error]")

    if provider == "CUSTOM":
        provider = Prompt.ask("Custom provider name (used as env var prefix, e.g. MYAPI)").strip().upper()

    defaults = _PROVIDER_DEFAULTS.get(provider, {"base_url": "", "model": ""})
    console.print()

    console.print("[bold]Step 2 of 3 — API Key[/bold]")
    while True:
        api_key = Prompt.ask(f"{provider}_API_KEY").strip()
        if api_key:
            break
        console.print("[error]API key is required.[/error]")
    console.print()

    console.print("[bold]Step 3 of 3 — Endpoint & Model[/bold]  [dim](press Enter to accept defaults)[/dim]")
    default_url = defaults["base_url"]
    default_model = defaults["model"]

    while True:
        base_url = Prompt.ask(
            f"{provider}_BASE_URL",
            default=default_url if default_url else None,
        ).strip()
        if base_url:
            break
        console.print("[error]Base URL is required.[/error]")

    while True:
        model = Prompt.ask(
            f"{provider}_MODEL",
            default=default_model if default_model else None,
        ).strip()
        if model:
            break
        console.print("[error]Model name is required.[/error]")
    console.print()

    lines = [
        "# Agentao configuration — generated by `agentao init`\n",
        "\n",
        f"LLM_PROVIDER={provider}\n",
        f"{provider}_API_KEY={api_key}\n",
        f"{provider}_BASE_URL={base_url}\n",
        f"{provider}_MODEL={model}\n",
    ]
    lines += [
        "\n",
        "# LLM Temperature (0.0-2.0, default: 0.2)\n",
        "# LLM_TEMPERATURE=0.2\n",
    ]

    env_path.write_text("".join(lines), encoding="utf-8")

    dot_dir = Path(".agentao")
    dot_dir.mkdir(exist_ok=True)

    console.print(Rule(style="green"))
    console.print(
        f"[success]Done![/success]  "
        f"[dim].env written with [bold]{provider}[/bold] configuration.[/dim]"
    )
    console.print()
    console.print("  Run [bold cyan]agentao[/bold cyan] to start.\n")


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


def entrypoint():
    """Unified entry point: -p for print mode, --resume for session restore,
    --acp --stdio for ACP server mode, skill management, or interactive.

    Note: all dispatch calls go through the ``agentao.cli`` package module
    (not local references) so that ``monkeypatch.setattr(cli, ...)`` in
    tests can intercept them.
    """
    import agentao.cli as _cli
    import agentao.cli._globals as _g

    parser = _build_parser()
    args, extras = parser.parse_known_args()

    if getattr(args, "show_help", False):
        parser.print_help()
        sys.exit(0)

    _top_dirs = getattr(args, "plugin_dirs", []) or []
    _sub_dirs = getattr(args, "sub_plugin_dirs", None) or []
    _g._plugin_inline_dirs[:] = [Path(d) for d in _top_dirs + _sub_dirs]

    if args.acp:
        _cli.run_acp_mode(resume=args.resume)
        return
    if args.stdio:
        sys.stderr.write(
            "agentao: --stdio requires --acp (no other transport mode uses stdio)\n"
        )
        sys.exit(2)

    if args.subcommand == "init":
        _cli.run_init_wizard()
    elif args.subcommand == "run":
        # ``agentao run`` is automation-oriented — silently accepting
        # an unknown flag (e.g. ``--max-iter`` instead of
        # ``--max-iterations``) would let CI run with the wrong value.
        # The top-level parser uses parse_known_args for legacy reasons,
        # so we surface those leftovers here.
        if extras:
            sys.stderr.write(
                "agentao run: unrecognized arguments: "
                + " ".join(extras) + "\n"
            )
            sys.exit(2)
        from .run import _execute_with_args
        sys.exit(_execute_with_args(args))
    elif args.subcommand == "plugin":
        _cli.handle_plugin_subcommand(args)
    elif args.subcommand == "skill":
        _cli.handle_skill_subcommand(args)
    elif args.subcommand == "doctor":
        # Automation-oriented like ``run``: a typo (``--jsno`` instead of
        # ``--json``) must fail loudly so CI surfaces the broken invocation
        # instead of exit-0 success against the wrong flag.
        if extras:
            sys.stderr.write(
                "agentao doctor: unrecognized arguments: "
                + " ".join(extras) + "\n"
            )
            sys.exit(2)
        _cli.handle_doctor_subcommand(args)
    elif args.subcommand == "config":
        if extras:
            sys.stderr.write(
                "agentao config: unrecognized arguments: "
                + " ".join(extras) + "\n"
            )
            sys.exit(2)
        _cli.handle_config_subcommand(args)
    elif args.prompt is not None:
        stdin_text = "" if sys.stdin.isatty() else sys.stdin.read()
        parts = [p for p in [args.prompt.strip(), stdin_text.strip()] if p]
        full_prompt = "\n".join(parts)
        sys.exit(_cli.run_print_mode(full_prompt))
    else:
        _cli.main(resume_session=args.resume)
