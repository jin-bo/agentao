"""Plugin hooks: parsing, payload adapters, dispatch, user-turn prep.

Public surface (preserved from the pre-split single-file module):

- :class:`ClaudeHooksParser` — parse ``hooks.json`` / inline hook dicts
- :class:`ToolAliasResolver` — Agentao ↔ Claude tool-name mapping
- :class:`ClaudeHookPayloadAdapter` — build hook event payloads
- :class:`PluginHookDispatcher` — execute hooks (command / prompt)
- :func:`resolve_all_hook_rules` — gather rules across all plugins
- :func:`prepare_user_turn` — top-level entry for UserPromptSubmit

Layering (each row only depends on rows above):

    _matchers     ← _glob_match / _regex_match_full
    _alias        ← ToolAliasResolver / _TOOL_ALIASES
    _attachments  ← _make_attachment / _attachment_to_message
    _parser       ← ClaudeHooksParser
    _payload      ← ClaudeHookPayloadAdapter (deps _alias)
    _dispatcher   ← PluginHookDispatcher (deps _alias / _attachments / _matchers)
    _user_turn    ← resolve_all_hook_rules + prepare_user_turn
"""

from __future__ import annotations

from ._alias import ToolAliasResolver
from ._dispatcher import PluginHookDispatcher
from ._parser import ClaudeHooksParser
from ._payload import ClaudeHookPayloadAdapter
from ._user_turn import prepare_user_turn, resolve_all_hook_rules

__all__ = [
    "ClaudeHookPayloadAdapter",
    "ClaudeHooksParser",
    "PluginHookDispatcher",
    "ToolAliasResolver",
    "prepare_user_turn",
    "resolve_all_hook_rules",
]
