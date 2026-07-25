"""``/clear`` and ``/new`` — end the current session and start a fresh one.

The two differ in exactly one respect: ``/clear`` also wipes persistent
memories and session summaries, ``/new`` preserves them. They were
previously two inline blocks in ``run_loop`` whose reset sequences were
byte-identical apart from that pair of calls — so any future change to
what "reset" means had to be made twice, correctly, to keep them in step.
One implementation, one flag.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...permissions import PermissionMode
from .._globals import console

if TYPE_CHECKING:
    from ..app import AgentaoCLI


def _reset_session(cli: AgentaoCLI, *, clear_memories: bool) -> None:
    """Close the current session and open a new one.

    Order matters: ``on_session_end`` must run against the *old* session id
    (it saves the transcript), and ``on_session_start`` must run after the
    permission mode is reset so the new session records the mode it
    actually starts in.
    """
    cli.on_session_end()
    cli.current_session_id = None
    if cli._plan_session.is_active:
        cli._plan_controller.exit_plan_mode()

    cli.agent.clear_history()
    if clear_memories:
        cli.agent.memory_manager.clear()
        cli.agent.memory_manager.clear_all_session_summaries()

    cli._staged_images = []
    cli.last_response = None
    cli._cached_ctx_pct = 0.0
    cli._apply_mode(PermissionMode.WORKSPACE_WRITE)
    cli.on_session_start()


def handle_clear_command(cli: AgentaoCLI, args: str = "") -> None:
    """Handle /clear — reset the session *and* drop all memories."""
    _reset_session(cli, clear_memories=True)
    console.print("\n[success]Session and all memories cleared.[/success]")
    console.print("[info]Permission mode reset to workspace-write.[/info]\n")


def handle_new_command(cli: AgentaoCLI, args: str = "") -> None:
    """Handle /new — reset the session, keep long-term memories."""
    _reset_session(cli, clear_memories=False)
    console.print(
        "\n[success]New session started. Long-term memories preserved.[/success]"
    )
    console.print("[info]Permission mode reset to workspace-write.[/info]\n")
