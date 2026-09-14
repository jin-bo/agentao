"""``/clear`` and ``/new`` — end the current session and start a fresh one.

The two differ in exactly one respect: ``/clear`` also wipes persistent
memories and session summaries, ``/new`` preserves them. They were
previously two inline blocks in ``run_loop`` whose reset sequences were
byte-identical apart from that pair of calls — so any future change to
what "reset" means had to be made twice, correctly, to keep them in step.
One implementation, one flag.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...permissions import PermissionMode
from .._globals import console
from .._utils import wipe_all_memories

if TYPE_CHECKING:
    from ..app import AgentaoCLI


@dataclass(frozen=True)
class _ResetOutcome:
    # Parts of the memory wipe still in place (``/clear`` only).
    not_cleared: list[str]
    # Background agents still pending or running, now detached from the
    # conversation: they finish, but report only through ``/agents``.
    detached_agents: int


def _reset_session(cli: AgentaoCLI, *, clear_memories: bool) -> _ResetOutcome:
    """Close the current session and open a new one.

    Order matters: ``on_session_end`` must run against the *old* session id
    (it saves the transcript), and ``on_session_start`` must run after the
    permission mode is reset so the new session records the mode it
    actually starts in.
    """
    # Both `/clear` and `/new` reach this one path, and upstream's vocabulary
    # has no value of its own for `/new` — `clear` is the nearest true one for
    # each, on both events. Stated here rather than left to the reader: the
    # default (`other` / `startup`) would report a *named* cause as an unnamed
    # one. See ``docs/design/session-lifecycle-source-vs-codex.md`` §6.1.
    cli.on_session_end(reason="clear")
    # The hook one-shot diagnostic registry is keyed by session id, and the old
    # id is about to go out of scope for good. Dropping its bucket here is what
    # keeps ``_diagnostics``'s stated lifetime honest — without it the entries
    # accumulate for the life of the process under a key nothing can reach.
    try:
        from ...plugins.hooks._diagnostics import clear_session
        clear_session(cli.current_session_id)
    except Exception:  # pragma: no cover - never block a reset
        pass
    cli.current_session_id = None
    if cli._plan_session.is_active:
        cli._plan_controller.exit_plan_mode()

    cli.agent.clear_history()
    # Counted at the cutoff itself: every task in flight *now* finishes
    # silently, including one that settles while SessionStart hooks run below.
    # ``getattr`` because ``bg_store`` is not in the agent-factory contract
    # (``app.py::_REQUIRED_AGENT_ATTRS``) — a runtime without it must not fail
    # here, after the session has already been reset.
    bg_store = getattr(cli.agent, "bg_store", None)
    detached_agents = 0 if bg_store is None else bg_store.count_in_flight()
    not_cleared: list[str] = []
    if clear_memories:
        _, _, not_cleared = wipe_all_memories(cli.agent.memory_manager)

    cli._staged_images = []
    cli.last_response = None
    cli._cached_ctx_pct = 0.0
    cli._apply_mode(PermissionMode.WORKSPACE_WRITE)
    cli.on_session_start(source="clear")
    return _ResetOutcome(
        not_cleared=not_cleared,
        detached_agents=detached_agents,
    )


def _print_detached(count: int) -> None:
    if count:
        console.print(
            f"[warning]{count} background agent(s) still running from the previous "
            f"session. They will not report into this one — check /agents.[/warning]"
        )


def handle_clear_command(cli: AgentaoCLI, args: str = "") -> None:
    """Handle /clear — reset the session *and* drop all memories."""
    outcome = _reset_session(cli, clear_memories=True)
    if outcome.not_cleared:
        console.print(
            f"\n[error]Session reset, but these could not be cleared: "
            f"{', '.join(outcome.not_cleared)}. They will still reach the next "
            f"prompt. See agentao.log.[/error]"
        )
    else:
        console.print("\n[success]Session and all memories cleared.[/success]")
    _print_detached(outcome.detached_agents)
    console.print("[info]Permission mode reset to workspace-write.[/info]\n")


def handle_new_command(cli: AgentaoCLI, args: str = "") -> None:
    """Handle /new — reset the session, keep long-term memories."""
    outcome = _reset_session(cli, clear_memories=False)
    console.print(
        "\n[success]New session started. Long-term memories and earlier "
        "session summaries preserved.[/success]"
    )
    _print_detached(outcome.detached_agents)
    console.print("[info]Permission mode reset to workspace-write.[/info]\n")
