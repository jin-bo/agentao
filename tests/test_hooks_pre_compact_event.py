"""End-to-end PreCompact helper test through ``_maybe_microcompact``.

Pins two contracts:

  - The PreCompact hook fires *before* the messages mutation
    (``PLUGIN_HOOK_FIRED`` for ``PreCompact`` precedes
    ``CONTEXT_COMPRESSED`` in the captured event stream).
  - Phase A is side-effect only: messages are still compacted regardless
    of hook outcome.
"""

from __future__ import annotations

from agentao.plugins.models import ParsedHookRule

from tests.support.stop_precompact import (
    make_runner_with_rules,
    write_capture_script,
)


def test_pre_compact_fires_before_mutation_and_message_still_compacts(
    tmp_path, monkeypatch,
):
    script, capture = write_capture_script(tmp_path)
    rule = ParsedHookRule(
        event="PreCompact",
        hook_type="command",
        command=f"sh '{script}'",
        plugin_name="t",
    )
    runner, transport = make_runner_with_rules(tmp_path, rules=[rule])
    agent = runner._agent

    # Pretend microcompaction is needed and reduces messages to a fixed
    # post-mutation list. Both stubs run on the agent's real
    # context_manager so the side-effect-only contract is observable.
    monkeypatch.setattr(
        agent.context_manager, "needs_microcompaction",
        lambda messages: True,
    )
    compacted_marker = [{"role": "user", "content": "[compacted]"}]
    monkeypatch.setattr(
        agent.context_manager, "microcompact_messages",
        lambda messages: list(compacted_marker),
    )

    # Seed agent.messages with something pre-mutation so we can detect
    # that the mutation actually ran.
    agent.messages = [
        {"role": "user", "content": "earlier turn"},
        {"role": "assistant", "content": "earlier reply"},
    ]
    pre_len = len(agent.messages)
    messages_with_system = [
        {"role": "system", "content": "sys"}
    ] + agent.messages

    runner._maybe_microcompact(messages_with_system, "sys")

    # Subprocess actually fired.
    assert capture.exists()

    # PreCompact emit precedes CONTEXT_COMPRESSED in the event stream.
    types_in_order = [e.type.value for e in transport.events]
    pre_idx = next(
        (i for i, e in enumerate(transport.events)
         if e.type.value == "plugin_hook_fired"
         and e.data.get("hook_name") == "PreCompact"),
        None,
    )
    cc_idx = next(
        (i for i, t in enumerate(types_in_order) if t == "context_compressed"),
        None,
    )
    assert pre_idx is not None, types_in_order
    assert cc_idx is not None, types_in_order
    assert pre_idx < cc_idx, types_in_order

    # Side-effect-only: messages were compacted regardless of hook outcome.
    assert agent.messages == compacted_marker
    assert len(agent.messages) != pre_len

    # Schema sanity for the PreCompact emit.
    pre_event = transport.events[pre_idx]
    assert pre_event.data["compaction_type"] == "microcompact"
    assert pre_event.data["trigger"] == "auto"
    assert pre_event.data["matched_rule_count"] == 1
    assert pre_event.data["outcome"] == "allow"
