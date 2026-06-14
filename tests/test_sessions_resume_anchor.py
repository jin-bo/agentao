"""Regression: resuming a session must drop the Tier-1 token anchor.

``resume_session`` replaces ``agent.messages`` wholesale. The context
manager's anchored threshold estimate (``_threshold_token_estimate``) reuses
the last API ``prompt_tokens`` for the already-sent prefix; if the anchor from
the *previous* conversation survives the swap, the first post-resume threshold
check mis-sizes the new history (spurious or skipped compaction). The fix calls
``context_manager.invalidate_token_anchor()`` right after the swap.
"""

from unittest.mock import Mock

from agentao.context_manager import ContextManager


def _make_mock_llm():
    mock_llm = Mock()
    mock_llm.logger = Mock()
    mock_llm.model = "test-model"
    return mock_llm


def test_resume_session_invalidates_token_anchor(monkeypatch):
    from agentao.cli.commands import sessions as sess_mod

    cm = ContextManager(_make_mock_llm(), Mock(), max_tokens=1_000)
    # Stale anchor from a prior conversation in this process.
    cm.record_api_usage(600, message_count=3)
    assert cm._last_api_prompt_tokens == 600
    assert cm._api_anchor_msg_count == 3

    resumed = [{"role": "user", "content": "hello again"}]

    cli = Mock()
    cli.agent.context_manager = cm
    cli.agent.working_directory = "."

    monkeypatch.setattr(
        "agentao.embedding.sessions.list_sessions",
        lambda project_root=None: [{"id": "s1", "session_id": "s1", "title": "t"}],
    )
    monkeypatch.setattr(
        "agentao.embedding.sessions.load_session",
        lambda sid, project_root=None: (resumed, "saved-model", []),
    )

    sess_mod.resume_session(cli, "s1")

    assert cli.agent.messages == resumed
    # The prior conversation's anchor must not survive the swap.
    assert cm._last_api_prompt_tokens is None
    assert cm._api_anchor_msg_count is None
