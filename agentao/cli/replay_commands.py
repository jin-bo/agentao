"""Slash-command handler for ``/replay`` (toggle + inspection).

Sits on top of :mod:`agentao.cli.replay_render` — everything here is
dispatch and argument parsing; formatting lives in the render module.

Subcommands:
- ``/replay`` / ``/replay list`` — list recorded instances (with status header)
- ``/replay on`` / ``/replay off`` — toggle recording (persisted to settings.json)
- ``/replay show <id> [--raw|--turn <tid>|--kind <k>|--errors]`` — render events
- ``/replay tail <id> [n]`` — flat tail of last N events
- ``/replay prune`` — delete instances beyond ``replay.max_instances``
- ``/replay delete <id>`` / ``/replay delete all`` — remove specific or all replay files
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import readchar
from rich.markup import escape as markup_escape

from ..session import strip_system_reminders
from ._globals import console
from .replay_render import (
    _format_ts_local,
    _parse_show_flags,
    _render_replay_grouped,
    _render_replay_raw,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ..replay import ReplayMeta
    from .app import AgentaoCLI


_USAGE = (
    "Usage: /replay [list | on | off | show <id> "
    "[--raw|--turn <tid>|--kind <k>|--errors] | tail <id> [n] | "
    "prune | delete <id> | delete all]"
)

_USER_MSG_PREVIEW_MAX = 80


def _summarize_user_message(text: str) -> str:
    """One-line preview of a captured user_message payload."""
    cleaned = " ".join(strip_system_reminders(text).split())
    if len(cleaned) > _USER_MSG_PREVIEW_MAX:
        return cleaned[: _USER_MSG_PREVIEW_MAX - 1] + "…"
    return cleaned


def _resolve_replay_or_print(
    target: str,
    project_root: "Path",
) -> Optional["ReplayMeta"]:
    """Resolve a replay-id prefix to a single match, or print and return None.

    Shared by every subcommand that takes ``<id>`` (show, tail, delete) so
    the no-match / ambiguous-prefix UI stays consistent.
    """
    from ..replay import find_replay_candidates

    candidates = find_replay_candidates(target, project_root)
    if not candidates:
        console.print(
            f"\n[error]No replay matches id '{target}'. "
            f"Use /replay list to see available instances.[/error]\n"
        )
        return None
    if len(candidates) > 1:
        console.print(
            f"\n[warning]Prefix '{target}' is ambiguous — "
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
        console.print("[dim]Type more characters to disambiguate.[/dim]\n")
        return None
    return candidates[0]


def handle_replay_command(cli: AgentaoCLI, args: str) -> None:
    """Dispatch ``/replay`` and its subcommands."""
    parts = args.strip().split(maxsplit=1)
    sub = parts[0].lower() if parts else ""

    if sub in ("on", "off"):
        _handle_toggle(cli, sub)
        return

    if sub in ("", "list"):
        _handle_list(cli)
        return

    if sub == "prune":
        _handle_prune(cli)
        return

    if sub == "delete":
        _handle_delete(cli, args)
        return

    if sub in ("show", "tail"):
        _handle_show_or_tail(cli, args, sub)
        return

    console.print(f"\n[warning]{_USAGE}[/warning]\n")


# ---------------------------------------------------------------------------
# /replay list — friendlier, /sessions-style multi-line layout
# ---------------------------------------------------------------------------


def _handle_list(cli: AgentaoCLI) -> None:
    from ..replay import list_replays

    project_root = cli.agent.working_directory
    metas = list_replays(project_root)
    cfg = cli.agent._replay_config
    state = "[green]on[/green]" if cfg.enabled else "[yellow]off[/yellow]"

    console.print(
        f"\n[info]Replay recording:[/info] {state}  "
        f"[dim](max_instances={cfg.max_instances})[/dim]"
    )

    if not metas:
        console.print("\n[warning]No saved replays found.[/warning]\n")
        console.print(
            "[dim]Enable recording with [cyan]/replay on[/cyan] and "
            "start a new turn.[/dim]\n"
        )
        return

    # Newest first to match what users usually want — list_replays returns
    # oldest-first (ls -tr style), so reverse here for the UI.
    metas = list(reversed(metas))

    console.print(f"\n[info]Saved Replays ({len(metas)}):[/info]\n")
    for meta in metas:
        err_tag = "  [warning]⚠ has errors[/warning]" if meta.has_errors else ""
        console.print(f"  • [cyan]{meta.short_id}[/cyan]")
        if meta.first_user_message:
            preview = _summarize_user_message(meta.first_user_message)
            console.print(
                f"    [bold]{markup_escape(preview)}[/bold]",
                no_wrap=True,
                overflow="ellipsis",
            )
        console.print(
            f"    [dim]{meta.event_count} events · "
            f"{meta.turn_count} turns[/dim]{err_tag}"
        )
        if meta.created_at:
            created = markup_escape(_format_ts_local(meta.created_at))
            updated = markup_escape(_format_ts_local(meta.updated_at))
            console.print(f"    Created: {created}  Updated: {updated}")
        else:
            updated = markup_escape(_format_ts_local(meta.updated_at))
            console.print(f"    Updated: {updated}")
        console.print(f"    [dim]File: {meta.path.name}[/dim]")
        console.print()

    console.print(
        f"[info]Usage:[/info] /replay show <id>  or  "
        f"/replay tail <id> {markup_escape('[n]')}  or  /replay prune\n"
    )


# ---------------------------------------------------------------------------
# /replay on | off — persisted toggle
# ---------------------------------------------------------------------------


def _handle_toggle(cli: AgentaoCLI, arg: str) -> None:
    from ..replay import save_replay_enabled

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
            "[dim]Existing replay files remain readable with /replay list.[/dim]\n"
        )


# ---------------------------------------------------------------------------
# /replay prune
# ---------------------------------------------------------------------------


def _handle_prune(cli: AgentaoCLI) -> None:
    from ..replay import ReplayRetentionPolicy

    project_root = cli.agent.working_directory
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


# ---------------------------------------------------------------------------
# /replay delete <id> | /replay delete all
# ---------------------------------------------------------------------------


def _active_replay_path(cli: AgentaoCLI):
    """Path of the currently-recording replay file, or None."""
    recorder = getattr(cli.agent, "_replay_recorder", None)
    if recorder is None:
        return None
    return getattr(recorder, "path", None)


def _handle_delete(cli: AgentaoCLI, args: str) -> None:
    raw_tokens = args.strip().split()[1:]  # drop leading "delete"
    if not raw_tokens:
        console.print(
            "\n[error]Usage: /replay delete <id>  or  /replay delete all[/error]\n"
        )
        return
    if raw_tokens[0] == "all":
        _handle_delete_all(cli)
    else:
        _handle_delete_one(cli, raw_tokens[0])


def _handle_delete_all(cli: AgentaoCLI) -> None:
    from ..replay import list_replays

    project_root = cli.agent.working_directory
    metas = list_replays(project_root)
    if not metas:
        console.print("\n[warning]No replay files to delete.[/warning]\n")
        return
    console.print(
        f"\n[warning]Delete all {len(metas)} replay file(s)? "
        "Press 1 to confirm, any other key to cancel.[/warning]"
    )
    if readchar.readkey() != "1":
        console.print("\n[info]Cancelled.[/info]\n")
        return
    active_path = _active_replay_path(cli)
    deleted = 0
    skipped_active = False
    for meta in metas:
        if active_path is not None and meta.path == active_path:
            skipped_active = True
            continue
        try:
            meta.path.unlink()
            deleted += 1
        except OSError as exc:
            console.print(
                f"  [error]Could not delete {meta.path.name}: {exc}[/error]"
            )
    console.print(f"\n[success]Deleted {deleted} replay file(s).[/success]")
    if skipped_active:
        console.print(
            "[dim]Skipped the currently-recording replay; it will be "
            "removed on the next /replay prune cycle after this session "
            "ends.[/dim]"
        )
    console.print()


def _handle_delete_one(cli: AgentaoCLI, target: str) -> None:
    project_root = cli.agent.working_directory
    meta = _resolve_replay_or_print(target, project_root)
    if meta is None:
        return
    active_path = _active_replay_path(cli)
    if active_path is not None and meta.path == active_path:
        console.print(
            f"\n[warning]Replay [cyan]{meta.short_id}[/cyan] is currently "
            "being recorded — finish the session (or run /clear) before "
            "deleting it.[/warning]\n"
        )
        return
    try:
        meta.path.unlink()
    except OSError as exc:
        console.print(
            f"\n[error]Could not delete {meta.path.name}: {exc}[/error]\n"
        )
        return
    console.print(
        f"\n[success]Deleted replay [cyan]{meta.short_id}[/cyan] "
        f"([dim]{meta.path.name}[/dim]).[/success]\n"
    )


# ---------------------------------------------------------------------------
# /replay show <id> | /replay tail <id> [n]
# ---------------------------------------------------------------------------


def _handle_show_or_tail(cli: AgentaoCLI, args: str, sub: str) -> None:
    from ..replay import open_replay

    project_root = cli.agent.working_directory
    raw_tokens = args.strip().split()[1:]  # drop leading "show"/"tail"
    if not raw_tokens:
        suffix = "[n]" if sub == "tail" else "[--raw] [--turn <id>] [--kind <k>] [--errors]"
        console.print(f"\n[warning]Usage: /replay {sub} <id> {suffix}[/warning]\n")
        return
    meta = _resolve_replay_or_print(raw_tokens[0], project_root)
    if meta is None:
        return
    reader = open_replay(meta.session_id, meta.instance_id, project_root)
    if reader is None:
        console.print(f"\n[error]Could not open replay {meta.short_id}.[/error]\n")
        return

    flags = _parse_show_flags(raw_tokens[1:])
    events = reader.events()

    if sub == "tail":
        try:
            n = int(flags.rest[0]) if flags.rest else 20
        except ValueError:
            n = 20
        n = max(1, n)
        _render_replay_raw(events[-n:], meta, console)
    elif flags.kind is not None:
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


__all__ = ["handle_replay_command"]
