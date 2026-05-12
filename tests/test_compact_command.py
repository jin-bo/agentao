"""Tests for the manual /compact slash command."""

from types import SimpleNamespace
from unittest.mock import Mock

from agentao.cli.commands import handle_compact_command


def _cli_with_messages(messages: list[dict], compacted: list[dict] | None = None):
    cm = Mock()
    cm.CIRCUIT_BREAKER_LIMIT = 3
    cm.estimate_tokens.side_effect = [1000, 250]
    cm.compress_messages.return_value = compacted if compacted is not None else messages
    cm.get_usage_stats.return_value = {
        "usage_percent": 12.5,
        "circuit_breaker_failures": 0,
    }
    agent = SimpleNamespace(
        messages=messages,
        context_manager=cm,
        _plugin_hook_rules=[],
        _last_session_summary_id=None,
        _build_system_prompt=Mock(return_value="system"),
        _emit_context_compressed=Mock(),
        _emit_session_summary_if_new=Mock(return_value="summary-id"),
    )
    cli = SimpleNamespace(agent=agent, _cached_ctx_pct=0.0)
    return cli, agent, cm


def test_compact_command_updates_history_and_emits_event():
    messages = [{"role": "user", "content": f"m{i}"} for i in range(10)]
    compacted = [
        {"role": "system", "content": "[Compact Boundary | auto=False]"},
        {"role": "system", "content": "[Conversation Summary]\nsummary"},
        {"role": "user", "content": "recent"},
    ]
    cli, agent, cm = _cli_with_messages(messages, compacted)

    handle_compact_command(cli, "")

    assert agent.messages == compacted
    cm.compress_messages.assert_called_once_with(messages, is_auto=False)
    agent._emit_context_compressed.assert_called_once()
    kwargs = agent._emit_context_compressed.call_args.kwargs
    assert kwargs["compression_type"] == "full"
    assert kwargs["reason"] == "manual_cli"
    assert kwargs["pre_msgs"] == 10
    assert kwargs["post_msgs"] == 3
    assert cli._cached_ctx_pct == 12.5


def test_compact_command_no_change_keeps_history():
    messages = [{"role": "user", "content": f"m{i}"} for i in range(10)]
    cli, agent, cm = _cli_with_messages(messages)  # compress_messages returns same list

    handle_compact_command(cli, "")

    assert agent.messages == messages
    agent._emit_context_compressed.assert_not_called()


def test_compact_command_treats_summarization_failure_as_no_change():
    # compress_messages can return a *new* list (microcompacted copy) without
    # ever producing a [Conversation Summary] — that must not count as success.
    messages = [{"role": "user", "content": f"m{i}"} for i in range(10)]
    failed = [dict(m) for m in messages]
    cli, agent, cm = _cli_with_messages(messages, failed)

    handle_compact_command(cli, "")

    assert agent.messages == messages
    agent._emit_context_compressed.assert_not_called()


def test_compact_command_skips_short_history():
    messages = [{"role": "user", "content": "short"} for _ in range(4)]
    cli, agent, cm = _cli_with_messages(messages)

    handle_compact_command(cli, "")

    cm.compress_messages.assert_not_called()
    agent._emit_context_compressed.assert_not_called()
