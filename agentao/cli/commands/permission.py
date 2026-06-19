"""``/permission`` and ``/sandbox`` — execution-policy controls.

Both surfaces govern how the agent is allowed to *act* (filesystem,
shell, network) — ``/permission`` reads the active rule set
declaratively, ``/sandbox`` is the macOS sandbox-exec switch.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .._globals import console, split_subcommand

if TYPE_CHECKING:
    from ..app import AgentaoCLI


def handle_permission_command(cli: AgentaoCLI, args: str) -> None:
    """Handle /permission command — show active permission rules."""
    console.print(f"\n{cli.permission_engine.get_rules_display()}\n")


def handle_sandbox_command(cli: AgentaoCLI, args: str) -> None:
    """Handle /sandbox command — macOS sandbox-exec control.

    Subcommands:
        /sandbox [status]             Show current sandbox state.
        /sandbox on                   Enable for this session.
        /sandbox off                  Disable for this session.
        /sandbox profile <name>       Switch profile (session only).
        /sandbox profiles             List available profile names.
    """
    policy = getattr(cli.agent, "sandbox_policy", None)
    if policy is None:
        console.print("\n[warning]Sandbox policy not initialized on this agent.[/warning]\n")
        return

    sub, sub_arg = split_subcommand(args, default="status", lower=True)

    if sub in ("", "status"):
        supported = policy.platform_supported
        health = policy.health_error()
        if policy.enabled and health is None:
            state = "[green]enabled[/green]"
        elif policy.enabled and health is not None:
            state = f"[red]enabled but BROKEN[/red] [warning]({health})[/warning]"
        else:
            state = "[dim]disabled[/dim]"
        if not supported:
            state += "  [warning](platform not supported — macOS only)[/warning]"
        console.print(f"\n[info]Sandbox:[/info] {state}")
        console.print(f"  Default profile: [cyan]{policy.default_profile_name}[/cyan]")
        console.print(f"  Workspace root:  [dim]{policy.workspace_root}[/dim]")
        console.print(f"  Available:       {', '.join(policy.list_profiles()) or '(none)'}")
        rule_profile = policy.rule_profile_for("run_shell_command")
        if rule_profile is not None:
            console.print(
                f"  [dim]Note: a rule maps run_shell_command → "
                f"[cyan]{rule_profile}[/cyan] (overrides default_profile)[/dim]"
            )
        if policy.enabled and health is not None:
            console.print(
                f"\n[warning]Shell commands will FAIL while the sandbox is "
                f"broken (fail-closed). Fix the config or run /sandbox off.[/warning]"
            )
        console.print()
        return

    if sub == "on":
        if not policy.platform_supported:
            console.print("\n[warning]Sandbox is macOS-only. Cannot enable on this platform.[/warning]\n")
            return
        policy.set_enabled(True)
        health = policy.health_error()
        if health is not None:
            console.print(
                f"\n[warning]⚠ Sandbox marked enabled, but the config is broken: "
                f"{health}[/warning]"
            )
            console.print(
                f"[warning]Shell commands will FAIL (fail-closed) until this is "
                f"fixed. Use `/sandbox profile <name>` to pick a valid profile, "
                f"or `/sandbox off` to disable.[/warning]\n"
            )
            return
        console.print(
            f"\n[green]✓ Sandbox enabled[/green] "
            f"(profile: [cyan]{policy.default_profile_name}[/cyan], session only — "
            f"edit .agentao/sandbox.json to persist)\n"
        )
        return

    if sub == "off":
        policy.set_enabled(False)
        console.print("\n[cyan]Sandbox disabled for this session.[/cyan]\n")
        return

    if sub in ("profile", "profiles"):
        if sub == "profiles" or not sub_arg:
            console.print(f"\n[info]Available profiles:[/info] {', '.join(policy.list_profiles()) or '(none)'}\n")
            return
        # Preflight against sandbox-exec — not just file existence. A
        # malformed custom .sb would pass is_file() but every subsequent
        # run_shell_command would die with "Invalid sandbox profile", and
        # the user would have no idea the switch was bogus. Reject up
        # front, the same way /sandbox on and /sandbox status do.
        health = policy.profile_health_error(sub_arg)
        if health is not None:
            console.print(f"\n[warning]{health}[/warning]\n")
            return
        resolved = policy._locate_profile(sub_arg)  # type: ignore[attr-defined]
        policy.set_default_profile(sub_arg)
        console.print(f"\n[green]✓ Default profile → [cyan]{sub_arg}[/cyan][/green]  [dim]({resolved})[/dim]")
        # Warn if a per-tool rule shadows the default for shell commands —
        # otherwise the user thinks they switched profiles but resolve()
        # keeps returning the rule's profile.
        rule_profile = policy.rule_profile_for("run_shell_command")
        if rule_profile is not None and rule_profile != sub_arg:
            console.print(
                f"[warning]⚠ A rule in .agentao/sandbox.json maps "
                f"run_shell_command → '{rule_profile}' and takes precedence "
                f"over default_profile. Shell commands will keep using "
                f"'{rule_profile}'. Edit the rule to make this switch "
                f"effective.[/warning]"
            )
        console.print()
        return

    console.print(f"\n[error]Unknown subcommand: /sandbox {sub}[/error]")
    console.print("[info]Available: /sandbox status | on | off | profile <name> | profiles[/info]\n")
