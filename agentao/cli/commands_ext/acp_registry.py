"""`/acp registry search|add` — add agents from the official ACP Registry.

Search and add never launch an agent. ``add`` converts the entry with
:func:`agentao.acp_client.registry.entry_to_server_config`, shows what it
will write, and only on confirmation writes ``.agentao/acp.json`` and
registers the server with the running manager — stopped, so the first
``/acp send`` (or ``/acp start``) is what launches it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.markup import escape
from rich.prompt import Confirm

from .._globals import console, split_subcommand


def _shown(value: Any) -> str:
    """*value* ready for Rich: control / bidi characters dropped, markup escaped.

    Registry metadata and agent-advertised auth methods are third-party text
    shown at an approval prompt, so both passes apply — escaping alone keeps
    the characters that reorder or hide what the user is approving.
    """
    from ...security.terminal_text import sanitize_terminal_text

    return escape(sanitize_terminal_text(str(value)))

if TYPE_CHECKING:
    from ..app import AgentaoCLI

_SEARCH_LIMIT = 20


def _fetch():
    from ...acp_client.registry import RegistryError, fetch_registry

    try:
        with console.status("[bold cyan]Reading the ACP Registry...[/bold cyan]", spinner="dots"):
            return fetch_registry()
    except RegistryError as exc:
        console.print(f"\n[error]{_shown(str(exc))}[/error]\n")
        return None


def _runner_label(agent) -> str:
    if agent.runners:
        return "/".join(agent.runners)
    kinds = ", ".join(agent.distribution_types) or "none"
    return f"[dim]{_shown(kinds)} (unsupported)[/dim]"


def _registry_search(query: str) -> None:
    from ...acp_client.registry import search_registry

    if not query:
        console.print("\n[error]Usage: /acp registry search <keyword>[/error]\n")
        return
    agents = _fetch()
    if agents is None:
        return
    matches = search_registry(agents, query)
    if not matches:
        console.print(f"\n[warning]No ACP Registry agent matches {_shown(query)!r}.[/warning]\n")
        return
    console.print(f"\n[info]ACP Registry — {len(matches)} match(es):[/info]\n")
    for agent in matches[:_SEARCH_LIMIT]:
        description = agent.description
        if len(description) > 90:
            description = description[:87] + "..."
        console.print(
            f"  [cyan]{_shown(agent.id)}[/cyan] {_shown(agent.version)}  "
            f"{_runner_label(agent)}\n    [dim]{_shown(agent.name)} — {_shown(description)}[/dim]"
        )
    if len(matches) > _SEARCH_LIMIT:
        console.print(f"\n[dim]… {len(matches) - _SEARCH_LIMIT} more; narrow the search.[/dim]")
    console.print("\n[info]Add one with /acp registry add <id> [name][/info]\n")


def _registry_add(cli: "AgentaoCLI", rest: str) -> None:
    from ...acp_client.config import add_server_entry
    from ...acp_client.models import AcpConfigError
    from ...acp_client.registry import RegistryError, entry_to_server_config, find_agent

    parts = rest.split() if rest else []
    if not parts or len(parts) > 2:
        console.print("\n[error]Usage: /acp registry add <id> [name][/error]\n")
        return
    agent_id = parts[0]
    name = parts[1] if len(parts) == 2 else agent_id

    mgr = cli._acp_manager
    if mgr is not None and mgr.get_handle(name) is not None:
        console.print(
            f"\n[error]An ACP server named '{_shown(name)}' is already configured; "
            f"pass another name: /acp registry add {_shown(agent_id)} <name>[/error]\n"
        )
        return

    agents = _fetch()
    if agents is None:
        return
    try:
        entry = entry_to_server_config(find_agent(agents, agent_id))
    except RegistryError as exc:
        console.print(f"\n[error]{_shown(str(exc))}[/error]\n")
        return

    config = entry.config
    console.print(
        f"\n[info]Agent:[/info]   {_shown(entry.agent.name)} ([cyan]{_shown(entry.agent.id)}[/cyan])"
        f"\n[info]Version:[/info] {_shown(entry.agent.version)} via {entry.runner}"
        f"\n[info]Command:[/info] {_shown(subprocess.list2cmdline(entry.command_line))}"
        f"\n[info]Name:[/info]    {_shown(name)}  [dim](cwd: project root; not started)[/dim]"
    )
    if config["env"]:
        console.print(f"[info]Env:[/info]     {_shown(', '.join(sorted(config['env'])))}")
    console.print(f"[dim]Requires `{entry.runner}` on PATH.[/dim]\n")
    if not Confirm.ask("Add it to .agentao/acp.json?", default=False):
        console.print("[dim]Not added.[/dim]\n")
        return

    config_path = Path.cwd() / ".agentao" / "acp.json"
    try:
        mtime_before = config_path.stat().st_mtime
    except OSError:
        mtime_before = None
    try:
        server_config = add_server_entry(name, config, project_root=Path.cwd())
    except AcpConfigError as exc:
        console.print(f"\n[error]{_shown(str(exc))}[/error]\n")
        return

    mgr = cli._acp_manager
    if mgr is not None:
        try:
            mgr.add_server(name, server_config)
        except ValueError as exc:
            console.print(f"\n[warning]Saved, but not loaded in this session: {_shown(str(exc))}[/warning]\n")
            return
        # If the live manager matched the file before this write, it matches
        # it now: record the new mtime, or the ``@server`` route sees acp.json
        # change and replaces the manager, dropping the servers it runs. If
        # the file had already changed under it, leave that reload pending.
        if mtime_before == getattr(cli, "_acp_config_mtime", None):
            try:
                cli._acp_config_mtime = config_path.stat().st_mtime
            except OSError:
                pass
    console.print(
        f"[success]Added '{_shown(name)}'.[/success] "
        f"[dim]Send it a task with /acp send {_shown(name)} <message>; the first "
        f"launch downloads the package.[/dim]\n"
    )


def acp_registry(cli: "AgentaoCLI", rest: str) -> None:
    """``/acp registry search <keyword>`` / ``/acp registry add <id> [name]``."""
    sub, args = split_subcommand(rest)
    if sub == "search":
        _registry_search(args.strip())
        return
    if sub == "add":
        _registry_add(cli, args)
        return
    console.print(
        "\n[error]Usage: /acp registry search <keyword> | "
        "/acp registry add <id> [name][/error]\n"
    )
