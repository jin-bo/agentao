"""ACP explicit routing + inbox/interaction flushing for the CLI.

Split out from ``app.py`` to keep the CLI class slim. All functions take
the ``AgentaoCLI`` instance as their first argument and mutate its
``_acp_manager`` / ``_acp_load_error_shown`` / ``_acp_config_mtime``
state in place.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ._globals import console

if TYPE_CHECKING:
    from .app import AgentaoCLI


# Two shape filters with different confidence levels:
#   * high-confidence — starts with ``@…`` or a Chinese ``让/请 …`` verb.
#     These are very unlikely to be ordinary prose, so when the ACP
#     manager can't load we still consume the turn to avoid silently
#     re-routing the user's delegation intent to the main agent.
#   * ambiguous-colon — ``token:`` / ``token：`` forms.  Very common
#     in normal prose (``Note: …``, ``URL: …``).  When the ACP
#     manager can't load, we warn and fall through to the main agent.
# ``\S`` keeps the filter agnostic to non-ASCII / punctuated names
# like ``@设计师`` or ``qa.bot:`` — the router does strict matching
# against the configured name set.
# The ``让/请`` arms require at least one whitespace separator so
# ordinary Chinese prose (``请问这个报错怎么修复``, ``让我看看日志``)
# is not misclassified as ACP delegation.
_ACP_ROUTE_SHAPE_HIGH_CONF_RE = re.compile(
    r"^\s*(?:@\S|让\s+\S|请\s+\S)"
)
_ACP_ROUTE_SHAPE_COLON_RE = re.compile(
    r"^\s*\S+?\s*[:：]"
)


def try_acp_explicit_route(cli: "AgentaoCLI", user_input: str) -> bool:
    """Dispatch explicit ``@server`` / ``server:`` prefixes to ACP.

    Returns ``True`` if the input was consumed by ACP routing (caller
    must skip the normal agent.chat path).  Returns ``False`` to fall
    through to the normal agent path.

    Failure handling:
      - If the input does not match either explicit-route shape,
        returns ``False`` without touching the ACP manager.
      - If the ACP manager fails to load (e.g. malformed
        ``.agentao/acp.json``):
          * **High-confidence shapes** (``@name …`` / ``让 name …`` /
            ``请 name …``) surface the error and **consume** the turn
            so the user's delegation intent never silently falls
            through to the main agent.
          * **Ambiguous colon shape** (``token: …``) falls through
            to the main agent — these forms are common in ordinary
            prose (``Note: …``, ``URL: …``), and the first time it
            happens a one-shot warning is printed.
      - The load is retried on every call, so fixing the config
        mid-session recovers routing without a CLI restart.
    """
    if not user_input:
        return False
    high_conf = bool(_ACP_ROUTE_SHAPE_HIGH_CONF_RE.match(user_input))
    colon_shape = bool(_ACP_ROUTE_SHAPE_COLON_RE.match(user_input))
    if not high_conf and not colon_shape:
        return False

    # (Re)load when:
    #   - no manager yet, or
    #   - cached manager has zero servers (so creating the config
    #     mid-session is picked up), or
    #   - the on-disk ``.agentao/acp.json`` mtime changed since the
    #     last successful load (so adding / renaming servers
    #     mid-session is picked up as well).
    cfg_path = Path(".agentao") / "acp.json"
    try:
        disk_mtime: Optional[float] = cfg_path.stat().st_mtime
    except OSError:
        disk_mtime = None
    mtime_changed = (
        disk_mtime is not None
        and disk_mtime != cli._acp_config_mtime
    )
    needs_load = (
        cli._acp_manager is None
        or not cli._acp_manager.server_names
        or mtime_changed
    )
    if needs_load:
        try:
            from ..acp_client import ACPManager
            cli._acp_manager = ACPManager.from_project()
            cli._acp_config_mtime = disk_mtime
            cli._acp_load_error_shown = False
        except Exception as exc:
            if not cli._acp_load_error_shown:
                cli._acp_load_error_shown = True
                console.print(
                    f"\n[error]ACP routing: failed to load "
                    f".agentao/acp.json: {exc}[/error]"
                )
                console.print(
                    "[dim]Fix the config and continue, or remove the "
                    "routing prefix to send this as a normal "
                    "message.[/dim]\n"
                )
            if high_conf:
                return True
            return False

    mgr = cli._acp_manager
    if mgr is None:
        return False
    names = mgr.server_names
    if not names:
        return False

    from ..acp_client.router import detect_explicit_route
    route = detect_explicit_route(user_input, names)
    if route is None:
        return False

    if not route.task:
        console.print(
            f"\n[warning]ACP route detected → {route.server}, but no task "
            f"text was provided.[/warning]"
        )
        console.print(
            "[dim]Add a task after the server name, e.g. "
            f"'@{route.server} review the latest diff'.[/dim]\n"
        )
        return True

    console.print(
        f"\n[bold cyan]ACP Delegation → {route.server}[/bold cyan] "
        f"[dim]({route.syntax})[/dim]"
    )
    from .commands_ext import run_acp_prompt_inline
    run_acp_prompt_inline(cli, route.server, route.task)
    return True


def flush_acp_inbox(cli: "AgentaoCLI") -> None:
    """Drain and render ACP inbox messages at a safe idle point.

    Called before the input prompt, after slash command dispatch, and
    after the agent response is printed.  No-op when no ACP manager
    is configured.

    After rendering queued messages, checks for pending interactions
    (permission / input requests from ACP servers) and prints an
    actionable summary so the user knows how to respond.
    """
    if cli._acp_manager is None:
        return
    messages = cli._acp_manager.flush_inbox()
    if messages:
        from ..acp_client.render import flush_to_console
        flush_to_console(messages, console, markdown_mode=cli.markdown_mode)

    pending = cli._acp_manager.interactions.list_pending()
    if pending:
        from .commands_ext import _handle_inline_interaction
        for interaction in pending:
            _handle_inline_interaction(
                cli, cli._acp_manager, interaction.server, interaction
            )
