"""``/context`` — context-window status and limit override."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .._globals import console

if TYPE_CHECKING:
    from ..app import AgentaoCLI


def handle_context_command(cli: AgentaoCLI, args: str) -> None:
    """Handle /context command."""
    args = args.strip()
    cm = cli.agent.context_manager

    if not args:
        stats = cm.get_usage_stats(cli.agent.messages)
        console.print("\n[info]Context Window Status:[/info]")
        console.print(f"  Estimated tokens: [cyan]{stats['estimated_tokens']:,}[/cyan]")
        console.print(f"  Max tokens:       [cyan]{stats['max_tokens']:,}[/cyan]")

        pct = stats["usage_percent"]
        color = "green" if pct < 55 else "yellow" if pct < 65 else "red"
        console.print(f"  Usage:            [{color}]{pct:.1f}%[/{color}]")
        console.print(f"  Messages:         {stats['message_count']}")

        failures = stats.get("circuit_breaker_failures", 0)
        if failures > 0:
            fb_color = "yellow" if failures < cm.CIRCUIT_BREAKER_LIMIT else "red"
            console.print(
                f"  Compact failures: [{fb_color}]{failures}/{cm.CIRCUIT_BREAKER_LIMIT}[/{fb_color}]"
                + (" [dim](circuit open — auto-compact disabled)[/dim]"
                   if failures >= cm.CIRCUIT_BREAKER_LIMIT else "")
            )

        lc = stats.get("last_compact")
        if lc:
            pre = lc.get("pre_compact_tokens", 0)
            post = lc.get("post_compact_tokens", 0)
            summarized = lc.get("messages_summarized", 0)
            kept = lc.get("messages_kept", 0)
            ts = lc.get("timestamp", "")[:19]
            console.print(
                f"  Last compact:     {ts}  "
                f"[dim]{pre:,} → {post:,} tokens | "
                f"{summarized} summarized, {kept} kept[/dim]"
            )
            files = lc.get("recently_read_files", [])
            if files:
                console.print(f"  Re-injected files: [dim]{', '.join(files[:5])}[/dim]")
        console.print()

    elif args.startswith("limit "):
        limit_str = args[6:].strip()
        try:
            new_limit = int(limit_str)
            if new_limit < 1000:
                console.print("\n[error]Context limit must be at least 1,000 tokens[/error]\n")
                return
            cm.max_tokens = new_limit
            console.print(f"\n[success]Context limit set to {new_limit:,} tokens[/success]\n")
        except ValueError:
            console.print(f"\n[error]Invalid number: {limit_str}[/error]\n")
    else:
        console.print("\n[error]Usage: /context  OR  /context limit <n>[/error]\n")
