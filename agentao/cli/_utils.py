"""Utility functions and data structures for the CLI."""

from __future__ import annotations

from prompt_toolkit.completion import Completer, Completion

from ._globals import _TOOL_SUMMARY_KEYS, console


def _tool_args_summary(tool_name: str, args: dict) -> str:
    """Build a short human-readable summary of tool arguments for display."""
    if not args:
        return ""
    # Try priority keys first
    for key in _TOOL_SUMMARY_KEYS:
        if key in args:
            val = str(args[key])
            if len(val) > 50:
                val = val[:47] + "..."
            return f"({val})"
    # Fall back to first value
    first_val = str(next(iter(args.values())))
    if len(first_val) > 50:
        first_val = first_val[:47] + "..."
    return f"({first_val})"


_SLASH_COMMANDS = [
    '/acp', '/acp cancel', '/acp list', '/acp logs',
    '/acp restart', '/acp send',
    '/acp start', '/acp status', '/acp stop',
    '/agent', '/agent bg', '/agent cancel', '/agent dashboard', '/agent delete', '/agent list', '/agent status',
    '/agents',
    '/clear', '/copy', '/new',
    '/crystallize', '/crystallize clear', '/crystallize create',
    '/crystallize refine', '/crystallize status', '/crystallize suggest',
    '/plan', '/plan clear', '/plan history', '/plan implement', '/plan show',
    '/context', '/context limit', '/exit', '/help',
    '/mcp', '/mcp add', '/mcp list', '/mcp remove',
    '/markdown',
    '/memory', '/memory clear', '/memory delete', '/memory list',
    '/memory project', '/memory search', '/memory session', '/memory status',
    '/memory tag', '/memory user', '/mode', '/model', '/permission', '/provider', '/quit',
    '/sessions', '/sessions delete', '/sessions delete all', '/sessions list', '/sessions resume',
    '/plugins', '/plugins list',
    '/skills', '/skills activate', '/skills deactivate',
    '/skills disable', '/skills enable', '/skills reload', '/status', '/temperature',
    '/todos', '/tools',
]


_SLASH_COMMAND_HINTS = {
    '/acp cancel': '<name>',
    '/acp logs': '<name> [lines]',
    '/acp restart': '<name>',
    '/acp send': '<name> <message>',
    '/acp start': '<name>',
    '/acp status': '[name]',
    '/acp stop': '<name>',
    '/crystallize create': '[skill-name]',
    '/agent bg': '<agent-name> <task>',
    '/agent cancel': '<agent-id>',
    '/agent delete': '<agent-id>',
    '/agent status': '[agent-id]',
    '/mode': '[read-only|workspace-write|full-access]',
    '/model': '<model-name>',
    '/provider': '<provider-name>',
    '/memory search': '<keyword>',
    '/memory delete': '<key>',
    '/memory tag': '<tag>',
    '/skills activate': '<skill-name>',
    '/skills deactivate': '<skill-name>',
    '/skills enable': '<skill-name>',
    '/skills disable': '<skill-name>',
    '/context limit': '<tokens>',
    '/temperature': '<value>',
    '/sessions resume': '<session-id>',
    '/sessions delete': '<session-id>',
    '/mcp add': '<name> <command|url>',
    '/mcp remove': '<name>',
}


class _SlashCompleter(Completer):
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith('/'):
            return
        # If the typed text exactly matches a command, show its argument hint
        stripped = text.rstrip()
        if stripped in _SLASH_COMMAND_HINTS:
            hint = _SLASH_COMMAND_HINTS[stripped]
            yield Completion(f' {hint}', start_position=0, display_meta='arg')
            return
        # Prefix completion for command names
        for cmd in _SLASH_COMMANDS:
            if cmd.startswith(text):
                yield Completion(cmd, start_position=-len(text))


def _display_layered_entries(entries, header: str, console) -> None:
    """Display MemoryRecord list in a readable format."""
    if not entries:
        console.print(f"\n[warning]{header}: no entries.[/warning]\n")
        return
    console.print(f"\n[info]{header} ({len(entries)} total):[/info]\n")
    for e in entries:
        excerpt = e.content[:120] + "..." if len(e.content) > 120 else e.content
        console.print(f"  [dim]{e.id}[/dim] • [cyan]{e.title}[/cyan]: {excerpt}")
        if e.tags:
            console.print(f"    Tags: {', '.join(e.tags)}")
    console.print()
