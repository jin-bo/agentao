"""``/skills`` — skill discovery and per-session activation state.

Extracted verbatim from the inline ``elif command == "skills"`` branch in
``input_loop.run_loop``; it was the last handler still living inside the
dispatch chain rather than beside its peers here. Messages and control
flow are unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .._globals import console, split_subcommand

if TYPE_CHECKING:
    from ..app import AgentaoCLI


def handle_skills_command(cli: AgentaoCLI, args: str) -> None:
    """Handle /skills command.

    Subcommands:
        /skills                       List available + active skills.
        /skills activate <name>       Activate for this session.
        /skills deactivate <name>     Deactivate for this session.
        /skills enable <name>         Re-enable a disabled skill.
        /skills disable <name>        Disable across sessions.
        /skills reload                Re-scan the skills directory.
    """
    if not args:
        cli.list_skills()
        return

    sub_cmd, sub_arg = split_subcommand(args)
    manager = cli.agent.skill_manager

    if sub_cmd == "activate":
        if not sub_arg:
            console.print("[warning]Usage: /skills activate <skill_name>[/warning]")
            return
        result = manager.activate_skill(
            sub_arg, "Manually activated via /skills activate"
        )
        if result.startswith("Error"):
            console.print(f"\n[warning]{result}[/warning]\n")
        else:
            console.print(f"\n[success]Skill '{sub_arg}' activated.[/success]\n")
        return

    if sub_cmd == "deactivate":
        if not sub_arg:
            console.print("[warning]Usage: /skills deactivate <skill_name>[/warning]")
            return
        if sub_arg not in manager.available_skills:
            available = ", ".join(sorted(manager.list_available_skills()))
            console.print(
                f"[warning]Unknown skill '{sub_arg}'. Available: {available}[/warning]"
            )
            return
        if manager.deactivate_skill(sub_arg):
            console.print(f"\n[success]Skill '{sub_arg}' deactivated.[/success]\n")
        else:
            console.print(f"\n[info]Skill '{sub_arg}' is not currently active.[/info]\n")
        return

    if sub_cmd == "disable":
        if not sub_arg:
            console.print("[warning]Usage: /skills disable <skill_name>[/warning]")
            return
        console.print(f"\n{manager.disable_skill(sub_arg)}\n")
        return

    if sub_cmd == "enable":
        if not sub_arg:
            console.print("[warning]Usage: /skills enable <skill_name>[/warning]")
            return
        console.print(f"\n{manager.enable_skill(sub_arg)}\n")
        return

    if sub_cmd == "reload":
        manager.reload_skills()
        count = len(manager.list_available_skills())
        console.print(f"\n[success]Skills reloaded. {count} available.[/success]\n")
        return

    console.print(
        f"[warning]Unknown subcommand '{sub_cmd}'. "
        f"Use: activate, deactivate, disable, enable, reload[/warning]"
    )
