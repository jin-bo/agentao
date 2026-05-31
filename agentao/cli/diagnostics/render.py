"""Human-readable rendering of a :class:`DiagnosticReport`."""

from __future__ import annotations

from .._globals import console
from .models import DiagnosticReport


_FINDING_TAG = {
    "error": "[red]ERROR[/red]",
    "warning": "[yellow]WARN[/yellow]",
    "info": "[cyan]INFO[/cyan]",
}


def _render_human(report: DiagnosticReport, *, header: str) -> None:
    """Print a human-readable summary to the rich console."""
    sections = report.sections

    console.print(f"[bold]{header}[/bold]")
    console.print()

    if "settings" in sections:
        s = sections["settings"]
        status = s.get("status", "absent")
        console.print(f"[bold]settings.json[/bold]: {status}  [dim]{s['path']}[/dim]")

    if "provider" in sections:
        s = sections["provider"]
        marker = "[green]yes[/green]" if s["api_key_present"] else "[red]no[/red]"
        console.print(
            f"[bold]LLM provider[/bold]: {s['provider']} "
            f"(api_key={marker}, model={s.get('model') or '-'}, "
            f"base_url={s.get('base_url') or '-'})"
        )

    if "permissions" in sections:
        s = sections["permissions"]
        console.print(
            f"[bold]Permissions[/bold]: user={s['user_status']} "
            f"(rules={s['rule_count']}), project={s['project_status']}"
        )

    if "mcp" in sections:
        s = sections["mcp"]
        console.print(
            f"[bold]MCP[/bold]: user={s['user_status']} "
            f"(servers={s['user_server_count']}), "
            f"project={s['project_status']} (servers={s['project_server_count']})"
        )

    if "replay" in sections:
        s = sections["replay"]
        enabled = "on" if s["enabled"] else "off"
        console.print(
            f"[bold]Replay[/bold]: {enabled}  "
            f"max_instances={s['max_instances']}, "
            f"deep_capture={'yes' if s['deep_capture_enabled'] else 'no'}"
        )

    if "acp_schema" in sections:
        s = sections["acp_schema"]
        if s.get("status") == "ok":
            console.print(
                f"[bold]ACP schema[/bold]: ok  "
                f"events_defs={s['events_defs']}, acp_defs={s['acp_defs']}"
            )
        else:
            console.print(f"[bold]ACP schema[/bold]: [red]error[/red] — {s.get('error','')}")

    if "memory" in sections:
        s = sections["memory"]
        console.print(
            f"[bold]Memory stores[/bold]: project={s['project_status']}, "
            f"user={s['user_status']}"
        )

    if "plugins" in sections:
        s = sections["plugins"]
        if s.get("status") == "ok":
            console.print(
                f"[bold]Plugins[/bold]: {s['count']} loaded, "
                f"warnings={len(s.get('warnings', []))}, "
                f"errors={len(s.get('errors', []))}"
            )
        else:
            console.print(f"[bold]Plugins[/bold]: [red]error[/red] — {s.get('error','')}")

    if "optional_deps" in sections:
        deps = sections["optional_deps"]
        missing = [name for name, info in deps.items() if not info["present"]]
        if missing:
            console.print(
                f"[bold]Optional deps[/bold]: missing {', '.join(missing)} "
                f"[dim](features may degrade)[/dim]"
            )
        else:
            console.print("[bold]Optional deps[/bold]: all probed packages present")

    if report.findings:
        console.print()
        console.print("[bold]Findings[/bold]:")
        for f in report.findings:
            tag = _FINDING_TAG.get(f.level, f.level.upper())
            src = f" [dim]({f.source})[/dim]" if f.source else ""
            console.print(f"  {tag} [{f.area}] {f.message}{src}")
    else:
        console.print()
        console.print("[green]No findings.[/green]")
