"""Utility functions and data structures for the CLI."""

from __future__ import annotations

from prompt_toolkit.completion import Completer, Completion
from rich.markup import escape as markup_escape

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
    '/crystallize feedback', '/crystallize refine', '/crystallize revise',
    '/crystallize status', '/crystallize suggest',
    '/goal', '/goal budget', '/goal clear', '/goal edit',
    '/goal pause', '/goal resume', '/goal show',
    '/plan', '/plan clear', '/plan history', '/plan implement', '/plan show',
    '/compact', '/context', '/context limit', '/exit', '/help',
    '/image', '/image clear',
    '/mcp', '/mcp add', '/mcp list', '/mcp remove',
    '/markdown',
    '/memory', '/memory clear', '/memory delete', '/memory list',
    '/memory project', '/memory search', '/memory session', '/memory status',
    '/memory tag', '/memory user', '/mode', '/model', '/permission', '/provider', '/quit',
    '/sandbox', '/sandbox off', '/sandbox on', '/sandbox profile',
    '/sandbox profiles', '/sandbox status',
    '/sessions', '/sessions delete', '/sessions delete all', '/sessions list', '/sessions resume',
    '/plugins', '/plugins list',
    '/replay', '/replay list', '/replay on', '/replay off',
    '/replay show', '/replay tail', '/replay prune',
    '/replay delete', '/replay delete all',
    '/skills', '/skills activate', '/skills deactivate',
    '/skills disable', '/skills enable', '/skills reload', '/status', '/temperature',
    '/thinking', '/todos', '/tools',
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
    '/crystallize feedback': '<text>',
    '/goal': '<objective> [--for 30m] [--turns 10] [--unbounded]',
    '/goal budget': '[--for <d>] [--turns <n>] | --clear',
    '/goal edit': '<new objective>',
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
    '/image': '<path>',
    '/temperature': '<value|off|on>',
    '/thinking': '<minimal|low|medium|high|off>',
    '/sessions resume': '<session-id>',
    '/sessions delete': '<session-id>',
    '/mcp add': '<name> <command|url>',
    '/mcp remove': '<name>',
    '/sandbox profile': '<name>',
    '/replay show': '<id>',
    '/replay tail': '<id> [n]',
    '/replay delete': '<id> | all',
}


class _SlashCompleter(Completer):
    """Slash-command completer that preserves draft tail.

    Two behaviors that matter for the "typed prose first, then prepended
    a slash command" workflow:

    - Exact match of an arg-taking command (e.g. ``/agent bg``) yields a
      display-only hint that inserts nothing. The previous version
      inserted the literal hint string (`` <agent-name> <task>``) into
      the buffer, which collided with any draft after the cursor.
    - Prefix completion of an arg-taking command appends a trailing
      space unless the cursor is already followed by whitespace. This
      keeps ``/ageplease refactor`` → ``/agent please refactor`` instead
      of the broken ``/agentplease refactor``.
    - The hint does **not** suppress subcommand completions. A command
      can be both arg-taking and a prefix of its own subcommands —
      ``/goal <objective>`` alongside ``/goal show``, ``/goal pause``,
      … — and returning after the hint hid every one of those. Only
      ``/goal`` and ``/image`` have that shape today, which is why the
      breakage was invisible next to ``/mcp`` and ``/replay`` (no hint
      entry, so they never took the early return).
    """

    def get_completions(self, document, complete_event):
        text_before = document.text_before_cursor
        if not text_before.startswith('/'):
            return

        text_after = document.text_after_cursor
        stripped = text_before.rstrip()

        # Exact match → display-only hint. Inserting ``''`` keeps the
        # popup informational without rewriting the buffer.
        showed_hint = stripped in _SLASH_COMMAND_HINTS
        if showed_hint:
            hint = _SLASH_COMMAND_HINTS[stripped]
            yield Completion(
                text='',
                start_position=0,
                display=hint,
                display_meta='arg',
            )

        # Prefix completion for command names. Fall through after the
        # hint so subcommands stay reachable, but skip re-offering the
        # exact command the hint just described.
        for cmd in _SLASH_COMMANDS:
            if not cmd.startswith(text_before):
                continue
            if showed_hint and cmd == stripped:
                continue
            takes_args = cmd in _SLASH_COMMAND_HINTS
            needs_space = takes_args and not (
                text_after and text_after[0].isspace()
            )
            suffix = ' ' if needs_space else ''
            yield Completion(
                text=cmd + suffix,
                start_position=-len(text_before),
            )


def _display_layered_entries(entries, header: str, console_=None) -> None:
    """Display MemoryRecord list in a readable format.

    The parameter is named ``console_``, not ``console``: the plain name
    would shadow the module-level import above (F402/F811), and the
    sibling renderers in ``replay_render/`` already solved the identical
    collision the same way. Deleting the parameter outright would work
    for the linter too, but it would also delete the injection point —
    a host or test that swaps ``console`` on the *calling* module to
    capture ``/memory user`` output would get the chrome and lose the
    entries themselves. Defaults to the ``_globals`` singleton so
    existing two-argument callers are unaffected.

    Entry titles and contents are LLM-written and routinely contain
    brackets (paths, quoted tags), so they are escaped before hitting
    rich's markup parser — an unescaped ``[/...]`` aborts the whole
    listing with ``MarkupError`` partway through.
    """
    out = console if console_ is None else console_
    if not entries:
        out.print(f"\n[warning]{markup_escape(header)}: no entries.[/warning]\n")
        return
    out.print(f"\n[info]{markup_escape(header)} ({len(entries)} total):[/info]\n")
    for e in entries:
        excerpt = e.content[:120] + "..." if len(e.content) > 120 else e.content
        out.print(
            f"  [dim]{markup_escape(str(e.id))}[/dim] • "
            f"[cyan]{markup_escape(e.title)}[/cyan]: {markup_escape(excerpt)}"
        )
        if e.tags:
            out.print(f"    Tags: {markup_escape(', '.join(e.tags))}")
    out.print()
