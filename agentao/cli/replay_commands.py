"""Slash-command handlers for replay inspection (``/replays``, ``/replay``).

Sits on top of :mod:`agentao.cli.replay_render` — everything here is
dispatch and argument parsing; formatting lives in the render module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.markup import escape as markup_escape

from ._globals import console
from .replay_render import (
    _format_ts_local,
    _parse_show_flags,
    _render_replay_grouped,
    _render_replay_raw,
)

if TYPE_CHECKING:
    from .app import AgentaoCLI


def handle_replays_command(cli: AgentaoCLI, args: str) -> None:
    """Handle /replays (list/show/tail/prune) commands."""
    from ..replay import (
        ReplayRetentionPolicy,
        find_replay_candidates,
        list_replays,
        open_replay,
    )

    project_root = cli.agent.working_directory
    parts = args.strip().split(maxsplit=2)
    sub = parts[0].lower() if parts else ""

    if sub in ("", "list"):
        metas = list_replays(project_root)
        cfg = cli.agent._replay_config
        state = "[green]on[/green]" if cfg.enabled else "[yellow]off[/yellow]"
        console.print(
            f"\n[info]Replay recording:[/info] {state}  "
            f"[dim](max_instances={cfg.max_instances})[/dim]"
        )
        if not metas:
            console.print("\n[info]No replay instances found.[/info]\n")
            console.print(
                "[dim]Enable recording with [cyan]/replay on[/cyan] and "
                "start a new turn.[/dim]\n"
            )
            return
        console.print(f"\n[info]Replays ({len(metas)}):[/info]\n")
        for meta in metas:
            err = " [warning]⚠ errors[/warning]" if meta.has_errors else ""
            console.print(
                f"  [cyan]{meta.short_id}[/cyan]  "
                f"[dim]{meta.event_count} events, {meta.turn_count} turns[/dim]"
                f"{err}"
            )
            console.print(
                f"    [dim]{markup_escape(_format_ts_local(meta.updated_at))}  →  {meta.path.name}[/dim]"
            )
        console.print(
            "\n[dim]Use [cyan]/replays show <id>[/cyan] or "
            "[cyan]/replays tail <id> [n][/cyan].[/dim]\n"
        )
        return

    if sub == "prune":
        cfg = cli.agent._replay_config
        deleted = ReplayRetentionPolicy(max_instances=cfg.max_instances).prune(project_root)
        if deleted:
            console.print(
                f"\n[success]Pruned {len(deleted)} old replay(s).[/success]\n"
            )
            for path in deleted:
                console.print(f"  [dim]- {path.name}[/dim]")
            console.print()
        else:
            console.print("\n[info]Nothing to prune.[/info]\n")
        return

    if sub in ("show", "tail"):
        # Re-split to preserve flag-style tokens that the ``maxsplit=2``
        # split above collapsed into parts[2].
        raw_tokens = args.strip().split()[1:]  # drop leading "show"/"tail"
        if not raw_tokens:
            console.print(
                f"\n[warning]Usage: /replays {sub} <id> "
                f"{'[n]' if sub == 'tail' else '[--raw] [--turn <id>] [--kind <k>] [--errors]'}"
                f"[/warning]\n"
            )
            return
        requested = raw_tokens[0]
        candidates = find_replay_candidates(requested, project_root)
        if not candidates:
            console.print(
                f"\n[error]No replay matches id '{requested}'. "
                f"Use /replays to list available instances.[/error]\n"
            )
            return
        if len(candidates) > 1:
            console.print(
                f"\n[warning]Prefix '{requested}' is ambiguous — "
                f"{len(candidates)} replays match:[/warning]"
            )
            for m in candidates[:10]:
                console.print(
                    f"  [cyan]{m.short_id}[/cyan]  "
                    f"[dim]{markup_escape(_format_ts_local(m.updated_at))}  "
                    f"{m.event_count} events, {m.turn_count} turns[/dim]"
                )
            if len(candidates) > 10:
                console.print(f"  [dim]… and {len(candidates) - 10} more[/dim]")
            console.print(
                "[dim]Type more characters to disambiguate.[/dim]\n"
            )
            return
        meta = candidates[0]
        reader = open_replay(meta.session_id, meta.instance_id, project_root)
        if reader is None:
            console.print(
                f"\n[error]Could not open replay {meta.short_id}.[/error]\n"
            )
            return

        # --- Parse flags --------------------------------------------------
        flags = _parse_show_flags(raw_tokens[1:])
        events = reader.events()

        if sub == "tail":
            # ``tail`` keeps its legacy flat semantics: last N events,
            # one line each. Flags other than the N integer are ignored.
            try:
                n = int(flags.rest[0]) if flags.rest else 20
            except ValueError:
                n = 20
            n = max(1, n)
            _render_replay_raw(events[-n:], meta, console)
            return

        # --- show ---------------------------------------------------------
        #
        #   --raw                — flat event stream (legacy behavior)
        #   --kind <k>           — flat, filter to events of that kind
        #   --turn <tid>         — grouped, only the matching turn
        #   --errors             — grouped, only turns with errors
        #   (no flags)           — default grouped view
        if flags.kind is not None:
            filtered = [
                e for e in events if e.get("kind", "").startswith(flags.kind)
            ]
            _render_replay_raw(filtered, meta, console)
        elif flags.raw:
            _render_replay_raw(events, meta, console)
        else:
            _render_replay_grouped(
                events, meta, console,
                turn_filter=flags.turn,
                errors_only=flags.errors,
            )

        if meta.malformed_lines:
            console.print(
                f"\n[warning]Note: {meta.malformed_lines} malformed "
                f"line(s) skipped.[/warning]\n"
            )
        return

    console.print(
        "\n[warning]Usage: /replays [list|show <id> [--raw|--turn <tid>|--kind <k>|--errors]"
        "|tail <id> [n]|prune][/warning]\n"
    )


def handle_replay_toggle_command(cli: AgentaoCLI, args: str) -> None:
    """Handle /replay on|off — persists replay.enabled to settings.json."""
    from ..replay import save_replay_enabled

    arg = args.strip().lower()
    if arg not in ("on", "off"):
        current = "on" if cli.agent._replay_config.enabled else "off"
        console.print(
            f"\n[info]Replay recording: {current}[/info]\n"
            f"[dim]Use /replay on or /replay off to toggle.[/dim]\n"
        )
        return
    enabled = (arg == "on")
    try:
        cfg = save_replay_enabled(enabled, cli.agent.working_directory)
    except OSError as exc:
        console.print(f"\n[error]Could not persist replay setting: {exc}[/error]\n")
        return
    cli.agent.reload_replay_config()
    if enabled:
        console.print(
            "\n[success]Replay recording ON.[/success]  "
            f"[dim](max_instances={cfg.max_instances})[/dim]"
        )
        console.print(
            "[dim]Takes effect on the next new session. The currently "
            "running instance is not touched.[/dim]\n"
        )
    else:
        console.print(
            "\n[success]Replay recording OFF.[/success]\n"
            "[dim]Existing replay files remain readable with /replays.[/dim]\n"
        )


__all__ = ["handle_replays_command", "handle_replay_toggle_command"]
