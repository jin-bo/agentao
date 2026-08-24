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
        console.print(f"  Max tokens:       [cyan]{stats['max_tokens']:,}[/cyan] [dim](configured)[/dim]")
        observed = stats.get("observed_limit")
        effective = stats.get("effective_max_tokens", stats["max_tokens"])
        if observed is not None and effective != stats["max_tokens"]:
            # The mismatch is the whole point of showing this: budgets are
            # denominated in the effective window, and a user reading only
            # the configured one would not know why compaction fires early.
            console.print(
                f"  Effective:        [yellow]{effective:,}[/yellow] "
                f"[dim](provider asserted {observed:,} — "
                f"{stats.get('observed_limit_provenance')})[/dim]"
            )
        elif observed is not None:
            console.print(
                f"  Effective:        [cyan]{effective:,}[/cyan] "
                f"[dim](provider asserted {observed:,}, at or above configured)[/dim]"
            )

        pct = stats["usage_percent"]
        # Read the tiers off the constants — hard-coded 55/65 silently lied
        # about when compaction fires the moment a threshold moved.
        micro_pct = cm.MICROCOMPACT_THRESHOLD * 100
        full_pct = cm.COMPRESSION_THRESHOLD * 100
        color = "green" if pct < micro_pct else "yellow" if pct < full_pct else "red"
        console.print(f"  Usage:            [{color}]{pct:.1f}%[/{color}]")
        console.print(f"  Messages:         {stats['message_count']}")

        failures = stats.get("circuit_breaker_failures", 0)
        if failures > 0:
            is_open = stats.get("circuit_breaker_open", failures >= cm.CIRCUIT_BREAKER_LIMIT)
            fb_color = "red" if is_open else "yellow"
            state = "open" if is_open else "closed"
            why = stats.get("last_compaction_failure")
            console.print(
                f"  Compact breaker:  [{fb_color}]{state}[/{fb_color}] "
                f"[dim]({failures}/{cm.CIRCUIT_BREAKER_LIMIT} consecutive failures"
                + (f", last: {why}" if why else "")
                + ")[/dim]"
            )
            if is_open:
                # Say how to get out of it. The old line said "auto-compact
                # disabled" and stopped there, which was true and useless:
                # the state is recoverable and the user is the one who can
                # recover it.
                console.print(
                    "  [dim]                  automatic compaction is paused; "
                    "/compact runs as a probe and a success resets it "
                    "(so does /clear)[/dim]"
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
