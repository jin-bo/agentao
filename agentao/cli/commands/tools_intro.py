"""``/tools`` — introspect registered tools.

Named ``tools_intro`` (rather than ``tools``) to avoid stylistic
collision with :mod:`agentao.tools`, even though Python's package
namespacing would keep them distinct. Reading
``cli.commands.tools_intro`` makes the intent unambiguous: this is the
slash-command surface, not the tool implementations.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .._globals import console

if TYPE_CHECKING:
    from ..app import AgentaoCLI


def handle_tools_command(cli: AgentaoCLI, args: str) -> None:
    """Handle /tools command."""
    args = args.strip()
    all_tools = cli.agent.tools.list_tools()

    if not args:
        console.print(f"\n[info]Registered Tools ({len(all_tools)}):[/info]\n")
        for tool in sorted(all_tools, key=lambda t: t.name):
            confirm = "  [warning]⚠ confirm[/warning]" if tool.requires_confirmation else ""
            console.print(f"  [cyan]{tool.name}[/cyan]{confirm}")
            console.print(f"    [dim]{tool.description}[/dim]")
        console.print()
        console.print("[dim]Use /tools <name> to see parameter schema.[/dim]\n")
    else:
        try:
            tool = cli.agent.tools.get(args)
        except KeyError:
            console.print(f"\n[error]Tool '{args}' not found.[/error]\n")
            return
        console.print(f"\n[info]{tool.name}[/info]")
        console.print(f"[dim]{tool.description}[/dim]")
        if tool.requires_confirmation:
            console.print("[warning]Requires user confirmation before execution[/warning]")
        console.print("\n[dim]Parameters schema:[/dim]")
        console.print(json.dumps(tool.parameters, indent=2, ensure_ascii=False))
        console.print()
