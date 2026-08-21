"""Human-readable rendering of a :class:`DiagnosticReport`."""

from __future__ import annotations

from rich.markup import escape as _esc

from .._globals import console
from .models import DiagnosticReport

# Every interpolation below that carries a path, an env value, exception
# text, or a Finding message goes through ``_esc``. All of them are
# user-controlled, and Rich reads ``[...]`` as markup: a config key named
# ``[/oops]``, a repo checked out under ``~/[wip]/``, or an ``OSError``
# stringifying as ``[Errno 13] ...`` would otherwise raise ``MarkupError``
# out of the renderer — aborting the report at exactly the moment the user
# ran ``agentao doctor`` because something was already broken. Literal
# style tags stay outside the escaped spans.


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
        console.print(
            f"[bold]settings.json[/bold]: {_esc(str(status))}  "
            f"[dim]{_esc(str(s['path']))}[/dim]"
        )

    if "provider" in sections:
        s = sections["provider"]
        marker = "[green]yes[/green]" if s["api_key_present"] else "[red]no[/red]"
        console.print(
            f"[bold]LLM provider[/bold]: {_esc(str(s['provider']))} "
            f"(api_key={marker}, model={_esc(str(s.get('model') or '-'))}, "
            f"base_url={_esc(str(s.get('base_url') or '-'))})"
        )

    if "permissions" in sections:
        s = sections["permissions"]
        console.print(
            f"[bold]Permissions[/bold]: {_esc(str(s['user_status']))} "
            f"(rules={s['rule_count']}), project={_esc(str(s['project_status']))}"
        )

    if "mcp" in sections:
        s = sections["mcp"]
        console.print(
            f"[bold]MCP[/bold]: user={_esc(str(s['user_status']))} "
            f"(servers={s['user_server_count']}), "
            f"project={_esc(str(s['project_status']))} "
            f"(servers={s['project_server_count']})"
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
            console.print(
                f"[bold]ACP schema[/bold]: [red]error[/red] — "
                f"{_esc(str(s.get('error', '')))}"
            )

    if "memory" in sections:
        s = sections["memory"]
        console.print(
            f"[bold]Memory stores[/bold]: "
            f"project={_esc(str(s['project_status']))}, "
            f"user={_esc(str(s['user_status']))}"
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
            console.print(
                f"[bold]Plugins[/bold]: [red]error[/red] — "
                f"{_esc(str(s.get('error', '')))}"
            )

    if "optional_deps" in sections:
        deps = sections["optional_deps"]
        missing = [name for name, info in deps.items() if not info["present"]]
        if missing:
            console.print(
                f"[bold]Optional deps[/bold]: "
                f"missing {_esc(', '.join(missing))} "
                f"[dim](features may degrade)[/dim]"
            )
        else:
            console.print("[bold]Optional deps[/bold]: all probed packages present")

    if report.findings:
        console.print()
        console.print("[bold]Findings[/bold]:")
        for f in report.findings:
            tag = _FINDING_TAG.get(f.level, f.level.upper())
            src = f" [dim]({_esc(str(f.source))})[/dim]" if f.source else ""
            console.print(
                f"  {tag} \\[{_esc(str(f.area))}] {_esc(str(f.message))}{src}"
            )
    else:
        console.print()
        console.print("[green]No findings.[/green]")
