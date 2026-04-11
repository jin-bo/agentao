"""CLI subcommand handlers for `agentao skill ...` and `agentao plugin ...`."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ._globals import console, logger, _plugin_inline_dirs


# ------------------------------------------------------------------
# Skill subcommand handlers
# ------------------------------------------------------------------

def _skill_list(args) -> None:
    """List skills.

    By default lists all discoverable skills (managed + unmanaged).
    With ``--installed`` shows only managed registry entries.
    """
    from rich.table import Table as RichTable

    from ..skills.registry import SkillRegistry, registry_path_for_scope

    installed_only = getattr(args, "installed", False)

    # Always collect managed records from both registries.
    managed_records = []
    managed_names: set[str] = set()
    for scope in ("global", "project"):
        reg_path = registry_path_for_scope(scope)
        if reg_path.exists():
            reg = SkillRegistry(reg_path)
            for rec in reg.list_all():
                managed_records.append(rec)
                managed_names.add(rec.name)

    if installed_only:
        if getattr(args, "json_output", False):
            import dataclasses as _dc
            print(json.dumps([_dc.asdict(r) for r in managed_records], indent=2))
            return

        if not managed_records:
            console.print("[dim]No managed skills installed.[/dim]")
            return

        table = RichTable(title="Managed Skills")
        table.add_column("Name", style="cyan")
        table.add_column("Version")
        table.add_column("Source")
        table.add_column("Scope", style="green")
        table.add_column("Status")

        for rec in managed_records:
            repo_skill = Path.cwd() / "skills" / rec.name
            status = "shadowed" if repo_skill.exists() else "ok"
            table.add_row(
                rec.name,
                rec.version or "-",
                f"{rec.source_type}:{rec.source_ref}",
                rec.install_scope,
                status,
            )
        console.print(table)
        return

    # Default: show all discoverable skills (managed + unmanaged).
    from ..skills.manager import SkillManager
    from ..skills.registry import _find_project_root
    project_root = _find_project_root() or Path.cwd()
    sm = SkillManager(working_directory=project_root)
    all_skills = sm.available_skills

    if getattr(args, "json_output", False):
        entries = []
        for name, info in sorted(all_skills.items()):
            entries.append({
                "name": name,
                "description": info.get("description", ""),
                "managed": name in managed_names,
            })
        print(json.dumps(entries, indent=2))
        return

    if not all_skills:
        console.print("[dim]No skills found.[/dim]")
        return

    table = RichTable(title="Skills")
    table.add_column("Name", style="cyan")
    table.add_column("Description")
    table.add_column("Managed", style="green")

    for name, info in sorted(all_skills.items()):
        managed_tag = "yes" if name in managed_names else "-"
        table.add_row(name, info.get("description", "")[:60], managed_tag)
    console.print(table)


def _skill_remove(args, scope: str) -> None:
    """Remove a managed skill installation."""
    import shutil

    from ..skills.registry import SkillRegistry, registry_path_for_scope

    reg_path = registry_path_for_scope(scope)
    registry = SkillRegistry(reg_path)
    record = registry.get(args.name)

    if record is None:
        other_scope = "global" if scope == "project" else "project"
        other_path = registry_path_for_scope(other_scope)
        if other_path.exists():
            other_reg = SkillRegistry(other_path)
            if other_reg.get(args.name):
                console.print(
                    f"[yellow]Skill '{args.name}' not found in {scope} scope, "
                    f"but exists in {other_scope} scope. "
                    f"Use --scope {other_scope} to remove it.[/yellow]"
                )
                sys.exit(1)
        console.print(f"[red]Skill '{args.name}' not found in any registry.[/red]")
        sys.exit(1)

    install_dir = Path(record.install_dir)
    if install_dir.exists():
        shutil.rmtree(install_dir)

    registry.remove(args.name)
    registry.save()
    console.print(f"[green]Removed skill '{args.name}' from {scope} scope.[/green]")


def _skill_install(args, scope: str) -> None:
    """Install a skill from a remote source."""
    from ..skills.installer import SkillInstallError, SkillInstaller
    from ..skills.registry import SkillRegistry, registry_path_for_scope
    from ..skills.sources import GitHubSkillSource

    registry = SkillRegistry(registry_path_for_scope(scope))
    source = GitHubSkillSource()
    installer = SkillInstaller(registry=registry, source=source, scope=scope)

    try:
        record = installer.install(args.ref, force=args.force)
        console.print(
            f"[green]Installed skill '{record.name}' "
            f"({record.source_ref}) into {scope} scope.[/green]"
        )
    except SkillInstallError as exc:
        console.print(f"[red]Error: {exc}[/red]")
        sys.exit(1)


def _skill_update(args, scope: str, *, explicit_scope: str | None = None) -> None:
    """Update one or all managed skills."""
    from ..skills.installer import SkillInstallError, SkillInstaller
    from ..skills.registry import SkillRegistry, registry_path_for_scope
    from ..skills.sources import GitHubSkillSource

    if args.update_all:
        scopes_to_update = [explicit_scope] if explicit_scope else ["global", "project"]
        updated, up_to_date, failed = [], [], []
        for update_scope in scopes_to_update:
            reg_path = registry_path_for_scope(update_scope)
            if not reg_path.exists():
                continue
            registry = SkillRegistry(reg_path)
            source = GitHubSkillSource()
            installer = SkillInstaller(registry=registry, source=source, scope=update_scope)
            for rec in registry.list_all():
                if rec.source_type == "manual":
                    continue
                try:
                    result = installer.update(rec.name)
                    if result:
                        updated.append(rec.name)
                    else:
                        up_to_date.append(rec.name)
                except SkillInstallError as exc:
                    failed.append((rec.name, str(exc)))
        if not updated and not up_to_date and not failed:
            console.print("[dim]No managed skills to update.[/dim]")
            return
        if updated:
            console.print(f"[green]Updated: {', '.join(updated)}[/green]")
        if up_to_date:
            console.print(f"[dim]Up-to-date: {', '.join(up_to_date)}[/dim]")
        if failed:
            for name, err in failed:
                console.print(f"[red]Failed {name}: {err}[/red]")
        return

    if not args.name:
        console.print("[red]Specify a skill name or use --all.[/red]")
        sys.exit(2)

    registry = SkillRegistry(registry_path_for_scope(scope))
    record = registry.get(args.name)

    if not record:
        other_scope = "global" if scope == "project" else "project"
        other_path = registry_path_for_scope(other_scope)
        if other_path.exists():
            other_reg = SkillRegistry(other_path)
            if other_reg.get(args.name):
                scope = other_scope
                registry = other_reg
                record = other_reg.get(args.name)
    if not record:
        console.print(f"[red]Skill '{args.name}' not found in any registry.[/red]")
        sys.exit(1)

    source = GitHubSkillSource()
    installer = SkillInstaller(registry=registry, source=source, scope=scope)

    try:
        result = installer.update(args.name)
        if result:
            console.print(
                f"[green]Updated '{args.name}' to revision "
                f"{result.revision[:12]}.[/green]"
            )
        else:
            console.print(f"Skill '{args.name}' is already up-to-date.")
    except SkillInstallError as exc:
        console.print(f"[red]Error updating '{args.name}': {exc}[/red]")
        sys.exit(1)


# ------------------------------------------------------------------
# Plugin subcommand handlers
# ------------------------------------------------------------------

def handle_plugin_subcommand(args) -> None:
    """Dispatch plugin subcommands (``agentao plugin list``)."""
    action = getattr(args, "plugin_action", None)

    if action == "list":
        _plugin_list_cli(args)
    else:
        sys.stderr.write("Usage: agentao plugin {list}\n")
        sys.exit(2)


def _plugin_list_cli(args) -> None:
    """``agentao plugin list`` — show loaded plugins with diagnostics."""
    from ..plugins.diagnostics import build_diagnostics
    from ..plugins.manager import PluginManager

    _top = getattr(args, "plugin_dirs", []) or []
    _sub = getattr(args, "sub_plugin_dirs", None) or []
    inline_dirs = [Path(d) for d in _top + _sub]
    mgr = PluginManager(inline_dirs=inline_dirs)
    loaded = mgr.load_plugins()

    # Simulate registration checks so the listing reflects post-load
    # failures (e.g. skill/agent name collisions) that would cause
    # _load_and_register_plugins() to reject a plugin at runtime.
    from ..plugins.skills import resolve_plugin_entries
    from ..plugins.agents import resolve_plugin_agents

    all_warnings = list(mgr.get_warnings())
    all_errors = list(mgr.get_errors())
    failed_plugins: set[str] = set()

    for plugin in loaded:
        entries, pw, pe = resolve_plugin_entries(plugin)
        all_warnings.extend(pw)
        all_errors.extend(pe)
        if pe:
            failed_plugins.add(plugin.name)

    for plugin in loaded:
        if plugin.name in failed_plugins:
            continue
        defs, aw, ae = resolve_plugin_agents(plugin)
        all_warnings.extend(aw)
        all_errors.extend(ae)
        if ae:
            failed_plugins.add(plugin.name)

    healthy = [p for p in loaded if p.name not in failed_plugins]
    diag = build_diagnostics(healthy, all_warnings, all_errors)

    if getattr(args, "json_output", False):
        import json as _json
        data = {
            "plugins": [
                {
                    "name": p.name,
                    "version": p.version,
                    "source": p.source,
                    "marketplace": p.marketplace,
                    "qualified_name": p.qualified_name,
                    "root_path": str(p.root_path),
                    "status": "ok" if p.name not in failed_plugins else "failed",
                }
                for p in loaded
            ],
            "warnings": [str(w) for w in diag.warnings],
            "errors": [str(e) for e in diag.errors],
        }
        print(_json.dumps(data, indent=2))
        return

    console.print(diag.format_report())


def _load_and_register_plugins(agent) -> None:
    """Load plugins and register their skills, agents, and MCP servers on *agent*."""
    from ..plugins.diagnostics import build_diagnostics
    from ..plugins.manager import PluginManager
    from ..plugins.skills import resolve_plugin_entries
    from ..plugins.agents import resolve_plugin_agents
    from ..plugins.mcp import merge_plugin_mcp_servers

    mgr = PluginManager(inline_dirs=_plugin_inline_dirs or None)
    loaded = mgr.load_plugins()
    if not loaded:
        return

    failed_plugins: set = set()
    for plugin in loaded:
        entries, warnings, errors = resolve_plugin_entries(plugin)
        if not errors and entries:
            try:
                reg_errors = agent.skill_manager.register_plugin_skills(entries)
                for err in reg_errors:
                    logger.warning("Plugin skill registration failed: %s", err)
                if reg_errors:
                    failed_plugins.add(plugin.name)
            except Exception as exc:
                logger.warning("Plugin skill registration error for '%s': %s", plugin.name, exc)
                failed_plugins.add(plugin.name)
        if errors:
            failed_plugins.add(plugin.name)
        for err in errors:
            logger.warning("Plugin skill resolution error: %s", err)

    _agents_added = False
    for plugin in loaded:
        if plugin.name in failed_plugins:
            continue
        defs, warnings, errors = resolve_plugin_agents(plugin)
        if not errors and defs:
            try:
                reg_errors = agent.agent_manager.register_plugin_agents(defs)
                for err in reg_errors:
                    logger.warning("Plugin agent registration failed: %s", err)
                if reg_errors:
                    failed_plugins.add(plugin.name)
                else:
                    _agents_added = True
            except Exception as exc:
                logger.warning("Plugin agent registration error for '%s': %s", plugin.name, exc)
                failed_plugins.add(plugin.name)
        if errors:
            failed_plugins.add(plugin.name)
        for err in errors:
            logger.warning("Plugin agent resolution error: %s", err)

    if _agents_added:
        agent._register_agent_tools()

    active_plugins = [p for p in loaded if p.name not in failed_plugins]

    from ..mcp.config import load_mcp_config
    base_mcp = load_mcp_config(project_root=agent.working_directory)
    merge_result = merge_plugin_mcp_servers(base_mcp, active_plugins)
    for err in merge_result.errors:
        logger.warning("Plugin MCP merge error: %s", err)

    plugin_servers = {k: v for k, v in merge_result.servers.items() if k not in base_mcp}
    if plugin_servers:
        agent._extra_mcp_servers.update(plugin_servers)
        if agent.mcp_manager is not None:
            try:
                agent.mcp_manager.disconnect_all()
            except Exception:
                pass
        agent.mcp_manager = agent._init_mcp()

    from ..plugins.hooks import (
        ClaudeHookPayloadAdapter,
        PluginHookDispatcher,
        resolve_all_hook_rules,
    )
    hook_rules, hook_warnings = resolve_all_hook_rules(active_plugins)
    for w in hook_warnings:
        logger.warning("Plugin hook warning: %s", w.message)
    agent._plugin_hook_rules = hook_rules
    agent._loaded_plugins = list(active_plugins)
    agent.tool_runner._plugin_hook_rules = hook_rules
    agent.tool_runner._working_directory = agent.working_directory

    diag = build_diagnostics(loaded, mgr.get_warnings(), mgr.get_errors())
    if diag.plugin_count:
        logger.info("Plugins: %s", diag.summary())


def _handle_plugins_interactive() -> None:
    """Handle the interactive ``/plugins`` command."""
    from ..plugins.diagnostics import build_diagnostics
    from ..plugins.manager import PluginManager

    mgr = PluginManager(inline_dirs=_plugin_inline_dirs or None)
    loaded = mgr.load_plugins()
    diag = build_diagnostics(loaded, mgr.get_warnings(), mgr.get_errors())
    console.print(diag.format_report())


def handle_skill_subcommand(args) -> None:
    """Dispatch skill subcommands."""
    from ..skills.registry import resolve_default_scope

    explicit_scope = getattr(args, "scope", None)
    scope = explicit_scope or resolve_default_scope()
    action = args.skill_action

    if action == "list":
        _skill_list(args)
    elif action == "remove":
        _skill_remove(args, scope)
    elif action == "install":
        _skill_install(args, scope)
    elif action == "update":
        _skill_update(args, scope, explicit_scope=explicit_scope)
    else:
        sys.stderr.write("Usage: agentao skill {install|remove|list|update}\n")
        sys.exit(2)
