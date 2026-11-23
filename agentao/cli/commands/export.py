"""``/export <path>`` — save a conversation as a local Markdown file (#664).

``cli/session_export.py`` renders; this file parses the arguments, picks the
conversation (the current one, or a saved one by an unambiguous id prefix)
and writes the file. An existing file is replaced only with ``--force``.
"""

from __future__ import annotations

import os
import secrets
import shlex
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from rich.text import Text

from ...security.terminal_text import sanitize_terminal_text
from .._globals import console

if TYPE_CHECKING:
    from ..app import AgentaoCLI

USAGE = "Usage: /export <path> [--session <id>] [--tools] [--force]"


class _Refused(Exception):
    """A user-facing reason the export did not happen."""


def _parse(args: str) -> Tuple[str, Optional[str], bool, bool]:
    try:
        words = shlex.split(args, posix=os.name != "nt")
    except ValueError as exc:
        raise _Refused(f"{exc}. {USAGE}") from exc
    path: Optional[str] = None
    session: Optional[str] = None
    tools = force = False
    it = iter(words)
    for word in it:
        if word == "--tools":
            tools = True
        elif word == "--force":
            force = True
        elif word == "--session":
            session = next(it, None)
            if not session or session.startswith("--"):
                raise _Refused(f"--session needs a session id. {USAGE}")
        elif word.startswith("--"):
            raise _Refused(f"Unknown option {word}. {USAGE}")
        elif path is None:
            path = word
        else:
            raise _Refused(f"Only one path, please. {USAGE}")
    if path is None:
        raise _Refused(USAGE)
    if os.name == "nt" and len(path) >= 2 and path[0] == path[-1] and path[0] in "'\"":
        path = path[1:-1]
    return path, session, tools, force


def _saved_session(cli: AgentaoCLI, prefix: str) -> Tuple[List[Dict[str, Any]], str, str, str]:
    """``(messages, session_id, model, title)`` of the one saved session ``prefix`` names."""
    from ...embedding.sessions import load_session_record
    from ._session_match import NoUniqueSession, match_one_session

    root = cli.agent.working_directory
    try:
        sid, info = match_one_session(root, prefix)
    except NoUniqueSession as exc:
        raise _Refused(str(exc)) from exc
    try:
        loaded_sid, messages, model, _skills = load_session_record(sid, project_root=root)
    except (OSError, ValueError) as exc:
        raise _Refused(f"Could not read session {sid[:8]}: {exc}") from exc
    return messages, loaded_sid, model, info.get("title") or ""


def _write(target: Path, text: str, force: bool) -> None:
    if target.is_dir():
        raise _Refused(f"{target} is a directory.")
    if target.exists() and not force:
        raise _Refused(f"{target} already exists. Add --force to replace it.")
    if not target.parent.is_dir():
        raise _Refused(f"Folder {target.parent} does not exist.")
    # Written whole, then moved into place: a failed write leaves no half file.
    # Not ``mkstemp``: it creates 0600. ``os.open`` with 0o666 lets the kernel
    # apply the umask, as a plain write would, without touching the
    # process-wide umask (background threads create files too).
    tmp = target.parent / f".{target.name}.{secrets.token_hex(6)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        if target.exists():
            shutil.copymode(target, tmp)  # --force keeps the file's mode
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def handle_export_command(cli: AgentaoCLI, args: str) -> None:
    """Handle ``/export <path> [--session <id>] [--tools] [--force]``."""
    from ..session_export import first_request_text, render_markdown

    try:
        path, prefix, tools, force = _parse(args)
        if prefix:
            messages, sid, model, title = _saved_session(cli, prefix)
        else:
            messages = list(cli.agent.messages)
            if not messages:
                raise _Refused("Nothing to export yet: the conversation is empty.")
            sid = cli.current_session_id
            model = cli.agent.get_current_model()
            from ..session import session_name
            title = session_name(cli) or first_request_text(messages)
        target = Path(path).expanduser()
        if not target.is_absolute():
            target = Path(cli.agent.working_directory) / target
        text = render_markdown(
            messages, title=title, session_id=sid, model=model, include_tools=tools,
        )
        _write(target, text, force)
    except _Refused as exc:
        console.print()
        console.print(Text(sanitize_terminal_text(str(exc)), style="warning"))
        console.print()
        return
    except OSError as exc:
        console.print()
        console.print(Text(f"Export failed: {sanitize_terminal_text(str(exc))}", style="error"))
        console.print()
        return
    console.print()
    console.print(Text(f"Exported to {sanitize_terminal_text(str(target))}", style="success"))
    if not tools:
        console.print("[dim]Tool calls and results are left out; add --tools to include them.[/dim]")
    console.print()
