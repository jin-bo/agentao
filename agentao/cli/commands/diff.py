"""``/diff`` — show the repository's current changes (#662).

Read-only and local: no LLM request, no change to the index or the working
tree. Collection lives in ``cli/git_changes.py`` so ``/review`` can reuse it;
this file only prints. Diff text and file names come from the repository, so
each line goes through ``sanitize_terminal_text`` before it reaches the
console. Diff lines and file names are printed as ``rich.text.Text``, not
markup: ``rich.markup.escape`` drops a backslash from a line that ends in
two or more of them (a LaTeX ``\\\\``) and, with three, prints the closing
tag as text.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from rich.markup import escape
from rich.text import Text

from ...security.terminal_text import sanitize_terminal_text
from .._globals import console
from .._utils import refuse_arguments
from ..git_changes import (
    MAX_COLLECT_BYTES,
    DiffSection,
    GitChangesError,
    collect_changes,
)

if TYPE_CHECKING:
    from ..app import AgentaoCLI


def _safe(text: str) -> str:
    return escape(sanitize_terminal_text(text))


def _name(name: str) -> str:
    # A file name can hold a newline or a tab (``ls-files -z`` keeps them),
    # which would print as a second, made-up entry. Show them escaped.
    return sanitize_terminal_text(
        name.replace("\n", "\\n").replace("\t", "\\t")
    )


def _style(line: str, in_hunk: bool) -> str:
    if not in_hunk and line.startswith(("+++", "---", "diff --git", "index ")):
        return "bold"
    if line.startswith("+"):
        return "green"
    if line.startswith("-"):
        return "red"
    if line.startswith("@@"):
        return "cyan"
    return ""


def _print_section(title: str, section: DiffSection, git_cmd: str) -> None:
    console.print(f"\n[bold]{title}[/bold]")
    if section.too_large:
        console.print(
            f"  [warning]Over {MAX_COLLECT_BYTES // (1024 * 1024)} MB of diff; not shown. "
            f"Run `{git_cmd}` in a shell to see it.[/warning]"
        )
        return
    if section.empty:
        console.print("  [dim](none)[/dim]")
        return
    in_hunk = False
    for line in section.text.splitlines():
        # File headers (``--- a/x``) come only before a file's first hunk.
        # Inside a hunk, ``---`` is a removed line that starts with ``--``.
        if line.startswith("diff "):
            in_hunk = False
        elif line.startswith("@@"):
            in_hunk = True
        style = _style(line, in_hunk)
        console.print(
            Text(sanitize_terminal_text(line), style=style),
            highlight=False, soft_wrap=True,
        )
    if section.omitted_lines:
        console.print(
            f"[warning]… {section.omitted_lines} more lines omitted. "
            f"Run `{git_cmd}` in a shell to see all.[/warning]"
        )


def handle_diff_command(cli: AgentaoCLI, args: str) -> None:
    """Handle ``/diff`` — staged, unstaged and untracked changes."""
    if refuse_arguments(args, "/diff takes no arguments. Run /diff on its own."):
        return
    cwd = Path(cli.agent.working_directory or Path.cwd())
    try:
        changes = collect_changes(cwd)
    except GitChangesError as exc:
        console.print()
        console.print(Text.assemble(("/diff: ", "error"), (sanitize_terminal_text(str(exc)), "error")))
        console.print()
        return
    if changes is None:
        console.print(f"\n[info]Not a Git repository: {_safe(str(cwd))}[/info]\n")
        return
    root = _safe(str(changes.root))
    if changes.clean:
        console.print(f"\n[success]Working tree clean[/success] [dim]({root})[/dim]\n")
        return

    console.print(f"\n[dim]Repository: {root}[/dim]")
    _print_section("Staged changes", changes.staged, "git diff --cached")
    _print_section("Unstaged changes", changes.unstaged, "git diff")
    console.print("\n[bold]Untracked files[/bold]")
    if changes.untracked_too_large:
        console.print(
            "  [warning]Too many to list. Run `git status` in a shell.[/warning]"
        )
    elif not changes.untracked:
        console.print("  [dim](none)[/dim]")
    for name in changes.untracked:
        console.print(Text("  " + _name(name)), highlight=False)
    if changes.omitted_untracked:
        console.print(f"  [warning]… and {changes.omitted_untracked} more[/warning]")
    console.print()
