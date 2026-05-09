"""Top-level entry: ``resolve_all_hook_rules`` + ``prepare_user_turn``.

These two functions are what ``agent.chat()`` and the CLI subcommands
actually call. Everything underneath — parser, payload adapter,
dispatcher, attachment helpers — is composed here into a single
``PreparedUserTurn`` value the chat loop can act on without thinking
about hook plumbing.
"""

from __future__ import annotations

from pathlib import Path

from ..models import (
    LoadedPlugin,
    ParsedHookRule,
    PluginWarning,
    PreparedTurnMessage,
    PreparedUserTurn,
)
from ._attachments import _attachment_to_message
from ._dispatcher import PluginHookDispatcher
from ._parser import ClaudeHooksParser
from ._payload import ClaudeHookPayloadAdapter


def resolve_all_hook_rules(
    plugins: list[LoadedPlugin],
) -> tuple[list[ParsedHookRule], list[PluginWarning]]:
    """Collect and parse hook rules from all loaded plugins.

    Returns ``(rules, warnings)``.
    """
    parser = ClaudeHooksParser()
    all_rules: list[ParsedHookRule] = []
    all_warnings: list[PluginWarning] = []

    for plugin in plugins:
        for spec in plugin.hook_specs:
            if isinstance(spec, str):
                # File path reference.
                hook_path = (plugin.root_path / spec).resolve()
                if hook_path.is_file():
                    rules, warns = parser.parse_file(hook_path, plugin_name=plugin.name)
                    all_rules.extend(rules)
                    all_warnings.extend(warns)
                else:
                    all_warnings.append(
                        PluginWarning(
                            plugin_name=plugin.name,
                            message=f"Hooks file not found: {hook_path}",
                            field="hooks",
                        )
                    )
            elif isinstance(spec, dict):
                # Inline hooks dict.
                rules, warns = parser.parse_dict(spec, plugin_name=plugin.name)
                all_rules.extend(rules)
                all_warnings.extend(warns)

    return all_rules, all_warnings


def prepare_user_turn(
    *,
    user_message: str,
    plugins: list[LoadedPlugin],
    session_id: str | None = None,
    cwd: Path | None = None,
) -> PreparedUserTurn:
    """Run UserPromptSubmit hooks and build a PreparedUserTurn.

    This is the single entry point that ``agent.chat()`` should call
    before processing the user's message.
    """
    rules, _warnings = resolve_all_hook_rules(plugins)
    ups_rules = [r for r in rules if r.event == "UserPromptSubmit" and r.is_supported]

    if not ups_rules:
        # No hooks — fast path.
        return PreparedUserTurn(
            original_user_message=user_message,
            should_query=True,
        )

    adapter = ClaudeHookPayloadAdapter()
    payload = adapter.build_user_prompt_submit(
        user_message=user_message,
        session_id=session_id,
        cwd=cwd,
    )

    dispatcher = PluginHookDispatcher(cwd=cwd)
    hook_result = dispatcher.dispatch_user_prompt_submit(
        payload=payload,
        rules=ups_rules,
    )

    # Build normalized messages.
    messages: list[PreparedTurnMessage] = []
    for attachment in hook_result.messages:
        messages.append(
            _attachment_to_message(attachment)
        )

    # Determine whether to proceed with the query.
    should_query = True
    stop_reason: str | None = None

    if hook_result.blocking_error:
        should_query = False
        stop_reason = f"Blocked by hook: {hook_result.blocking_error}"
        messages.append(
            PreparedTurnMessage(
                role="user",
                content=f"[Hook blocking error] {hook_result.blocking_error}",
                is_meta=True,
                source="hook",
            )
        )

    if hook_result.prevent_continuation:
        should_query = False
        stop_reason = stop_reason or hook_result.stop_reason or "Hook prevented continuation"

    # Inject additional contexts as meta user messages.
    for ctx in hook_result.additional_contexts:
        messages.append(
            PreparedTurnMessage(
                role="user",
                content=f"[Hook context] {ctx}",
                is_meta=True,
                source="hook",
            )
        )

    # Always include the original user message so the model sees the
    # actual prompt — unless hooks explicitly blocked continuation.
    if should_query:
        messages.append(
            PreparedTurnMessage(
                role="user",
                content=user_message,
                is_meta=False,
                source=None,
            )
        )

    return PreparedUserTurn(
        original_user_message=user_message,
        hook_attachments=hook_result.messages,
        normalized_messages=messages,
        should_query=should_query,
        stop_reason=stop_reason,
    )
