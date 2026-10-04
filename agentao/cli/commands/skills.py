"""``/skills`` — skill discovery and per-session activation state.

Extracted verbatim from the inline ``elif command == "skills"`` branch in
``input_loop.run_loop``; it was the last handler still living inside the
dispatch chain rather than beside its peers here. Messages and control
flow are unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .._globals import console, split_subcommand
from ..ui import _safe

if TYPE_CHECKING:
    from ..app import AgentaoCLI


def _record_mcp_activation(cli: AgentaoCLI, manager, name: str) -> None:
    """Leave a durable provenance record of a user's MCP skill activation.

    A model's activation leaves its ``<mcp-skill>`` result in the
    transcript; this path puts the skill into context only through the
    active-skills block, which is rebuilt per request and never saved. The
    record is what compaction and a session restore read (they see only
    ``agent.messages``), so derived content keeps its gates after either.
    """
    info = (getattr(manager, "active_skills", None) or {}).get(name)
    key = info.get("mcp_key") if isinstance(info, dict) else None
    messages = getattr(getattr(cli, "agent", None), "messages", None)
    if key is None or not isinstance(messages, list):
        return
    from ...skills.provenance import result_marker

    # A ``user`` message in ``<system-reminder>``: a mid-history ``system``
    # message is refused by strict chat templates (see sessions.py).
    messages.append({
        "role": "user",
        "content": (
            f"<system-reminder>\n[The user activated MCP skill {name}; its "
            f"instructions are in \"Active Skills\".]\n{result_marker([key[0]])}"
            "\n</system-reminder>"
        ),
    })


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
        # ``or ()`` as well as the default: a host-injected manager may carry
        # the attribute set to ``None``, and ``x in None`` is a TypeError.
        disabled = getattr(manager, "disabled_skills", None) or ()
        if result.startswith("Error") and sub_arg in disabled:
            # The manager answers "Unknown skill" for a disabled one on
            # purpose — to a caller trying to activate it, it is not there
            # (#266). That is right for the model, and wrong for the person
            # at the prompt: ``/skills`` lists this very name under "Disabled
            # Skills", so "unknown" reads as a bug and hides the one-word
            # remedy. The CLI knows which of the two it is, so it says so.
            result = (
                f"Error: skill '{sub_arg}' is disabled for this project, so it "
                f"cannot be activated. Run /skills enable {sub_arg} first."
            )
        if result.startswith("Error"):
            console.print(f"\n[warning]{_safe(result)}[/warning]\n")
        else:
            _record_mcp_activation(cli, manager, sub_arg)
            console.print(f"\n[success]Skill '{_safe(sub_arg)}' activated.[/success]\n")
        return

    if sub_cmd == "deactivate":
        if not sub_arg:
            console.print("[warning]Usage: /skills deactivate <skill_name>[/warning]")
            return
        # An MCP skill loaded by URI is active without a catalogue entry.
        active = getattr(manager, "active_skills", None) or {}
        if sub_arg not in manager.available_skills and sub_arg not in active:
            available = ", ".join(sorted(manager.list_available_skills()))
            console.print(
                f"[warning]Unknown skill '{_safe(sub_arg)}'. Available: {_safe(available)}[/warning]"
            )
            return
        if manager.deactivate_skill(sub_arg):
            console.print(f"\n[success]Skill '{_safe(sub_arg)}' deactivated.[/success]\n")
        else:
            console.print(f"\n[info]Skill '{_safe(sub_arg)}' is not currently active.[/info]\n")
        return

    if sub_cmd == "disable":
        if not sub_arg:
            console.print("[warning]Usage: /skills disable <skill_name>[/warning]")
            return
        console.print(f"\n{_safe(manager.disable_skill(sub_arg))}\n")
        return

    if sub_cmd == "enable":
        if not sub_arg:
            console.print("[warning]Usage: /skills enable <skill_name>[/warning]")
            return
        console.print(f"\n{_safe(manager.enable_skill(sub_arg))}\n")
        return

    if sub_cmd == "reload":
        count = manager.reload_skills()
        console.print(f"\n[success]Skills reloaded. {count} available.[/success]\n")
        return

    console.print(
        f"[warning]Unknown subcommand '{_safe(sub_cmd)}'. "
        f"Use: activate, deactivate, disable, enable, reload[/warning]"
    )
