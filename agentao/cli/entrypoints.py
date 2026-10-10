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
# Re-exported: these live in light modules so ``agentao --acp`` and
# ``agentao --login`` run without the [cli] extras (see ``_light``).
from ._light import _build_parser, dispatch_acp_or_login, run_acp_mode  # noqa: F401
from ._llm_prompts import _PROVIDER_DEFAULTS, _prompt_llm_settings  # noqa: F401

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
            # ``at_launch``: no session has started yet, and ``cli.run()``
            # below dispatches the only ``SessionStart`` this launch gets.
            _resume(cli, resume_session if resume_session else None, at_launch=True)
        cli.run()
    except KeyboardInterrupt:
        console.print("\n\n[success]Goodbye![/success]\n")
        sys.exit(0)
    except Exception as e:
        # ``escape`` because this renders arbitrary exception text into
        # markup. ``PermissionConfigError`` quotes the offending config
        # key verbatim, so a rule named ``[/oops]`` would make Rich raise
        # ``MarkupError`` here — replacing the typed startup error the
        # user needs with a traceback from the handler meant to prevent
        # one. Same for ``OSError``, which stringifies as ``[Errno 13]``.
        from rich.markup import escape as _esc

        console.print(f"\n[error]Fatal error: {_esc(str(e))}[/error]\n")
        sys.exit(1)


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

    provider, api_key, base_url, model = _prompt_llm_settings()

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
        "# LLM Temperature (0.0-2.0). Unset = not sent; the provider's default applies\n",
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

    # ``--login`` / ``--acp`` normally never get here (``agentao.cli.entrypoint``
    # routes them first); this covers callers of this function directly.
    if dispatch_acp_or_login(parser, args, extras):
        return
    if args.stdio:
        sys.stderr.write(
            "agentao: --stdio requires --acp (no other transport mode uses stdio)\n"
        )
        sys.exit(2)

    if extras and args.subcommand not in ("run", "doctor", "mcp", "config"):
        # The interactive session, ``-p``, ``init``, ``plugin`` and ``skill``
        # would otherwise start with the leftover ignored: a typo
        # (``-p hi --jsno``) ran the turn anyway. The four automation
        # subcommands below keep their own, prefixed refusal.
        parser.error("unrecognized arguments: " + " ".join(extras))

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
    elif args.subcommand == "mcp":
        if extras:
            parser.error("unrecognized arguments: " + " ".join(extras))
        from .mcp_auth import handle_mcp_subcommand
        sys.exit(handle_mcp_subcommand(args))
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
