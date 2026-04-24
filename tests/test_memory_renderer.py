"""Tests for MemoryPromptRenderer: stable and dynamic block rendering."""

import pytest

from agentao.memory.render import MemoryPromptRenderer
from agentao.memory.models import MemoryRecord, RecallCandidate


@pytest.fixture
def renderer():
    return MemoryPromptRenderer()


def _make_record(
    scope="project",
    type_="note",
    key="test_key",
    title="Test Entry",
    content="some content",
    tags=None,
    confidence="explicit_user",
    created_at="2026-04-08T10:00:00",
    record_id=None,
) -> MemoryRecord:
    return MemoryRecord(
        id=record_id or "abc12345",
        scope=scope,
        type=type_,
        key_normalized=key,
        title=title,
        content=content,
        tags=tags or [],
        confidence=confidence,
        sensitivity="normal",
        created_at=created_at,
        updated_at=created_at,
    )


def _make_candidate(
    scope="project",
    type_="note",
    title="Test Entry",
    excerpt="some excerpt",
    score=5.0,
    reasons=None,
) -> RecallCandidate:
    return RecallCandidate(
        memory_id="abc12345",
        scope=scope,
        type=type_,
        title=title,
        excerpt=excerpt,
        score=score,
        reasons=reasons or ["tag_match"],
    )


# ---------------------------------------------------------------------------
# render_stable_block
# ---------------------------------------------------------------------------


class TestRenderStableBlock:
    def test_empty_returns_empty(self, renderer):
        assert renderer.render_stable_block([]) == ""

    def test_basic_structure(self, renderer):
        records = [_make_record(title="my key", content="my value")]
        block = renderer.render_stable_block(records)
        assert block.startswith("<memory-stable>")
        assert block.endswith("</memory-stable>")
        assert "Saved facts for reference only" in block

    def test_contains_fact_tags(self, renderer):
        records = [_make_record(scope="project", type_="note")]
        block = renderer.render_stable_block(records)
        assert '<fact scope="project" type="note"' in block
        assert "</fact>" in block

    def test_content_escaped(self, renderer):
        records = [_make_record(content='value with <script> & "quotes"')]
        block = renderer.render_stable_block(records)
        assert "&lt;script&gt;" in block
        assert "&amp;" in block
        # xml.sax.saxutils.escape doesn't escape quotes in text content (only in attrs)
        assert '"quotes"' in block

    def test_title_escaped(self, renderer):
        records = [_make_record(title='key with <tags>')]
        block = renderer.render_stable_block(records)
        assert "&lt;tags&gt;" in block

    def test_tags_included(self, renderer):
        records = [_make_record(tags=["python", "backend"])]
        block = renderer.render_stable_block(records)
        assert "python, backend" in block

    def test_multiple_records(self, renderer):
        records = [
            _make_record(key="k1", title="first"),
            _make_record(key="k2", title="second"),
        ]
        block = renderer.render_stable_block(records)
        assert "first" in block
        assert "second" in block
        assert block.count("<fact") == 2

    def test_budget_truncation(self, renderer):
        records = [_make_record(content="x" * 500) for _ in range(20)]
        block = renderer.render_stable_block(records, budget=200)
        assert len(block) <= 220  # small overhead for closing tag
        assert block.endswith("</memory-stable>")

    def test_budget_eviction_preserves_newest_decision(self, renderer):
        """When the budget can't fit every record, the newest entry must
        survive — fresh user intent must not be crowded out by long-tail
        history. Records arrive in created_at-ASC order from
        get_stable_entries, so eviction walks newest-first.
        """
        records = [
            _make_record(
                key="old_pref_1", title="old preference one",
                content="x" * 80,
                created_at="2026-01-01T10:00:00",
                record_id="r1",
            ),
            _make_record(
                key="old_pref_2", title="old preference two",
                content="x" * 80,
                created_at="2026-01-02T10:00:00",
                record_id="r2",
            ),
            _make_record(
                key="new_decision", title="recent decision",
                type_="decision", content="use redis cluster",
                created_at="2026-04-08T10:00:00",
                record_id="r3",
            ),
        ]
        # Budget intentionally too small to fit all three.
        block = renderer.render_stable_block(records, budget=400)

        # The newest decision MUST be present
        assert "new_decision" in block
        assert "use redis cluster" in block
        # And not the entire ancient history
        assert block.count("<fact") < len(records)

    def test_budget_eviction_renders_kept_records_in_original_order(self, renderer):
        """Even though eviction walks newest-first, the kept records render
        in their original (oldest-first) order so the prompt-cache prefix
        stays stable across turns."""
        records = [
            _make_record(
                key="a_oldest", title="entry a",
                content="x" * 30,
                created_at="2026-01-01T10:00:00",
                record_id="r1",
            ),
            _make_record(
                key="b_middle", title="entry b",
                content="x" * 30,
                created_at="2026-02-01T10:00:00",
                record_id="r2",
            ),
            _make_record(
                key="c_newest", title="entry c",
                content="x" * 30,
                created_at="2026-03-01T10:00:00",
                record_id="r3",
            ),
        ]
        # Tight enough to evict the oldest, leaving b + c
        block = renderer.render_stable_block(records, budget=550)
        assert "b_middle" in block and "c_newest" in block
        # Order of kept facts in the rendered block: b before c (ASC)
        pos_b = block.index("b_middle")
        pos_c = block.index("c_newest")
        assert pos_b < pos_c

    def test_budget_eviction_drops_oldest_first(self, renderer):
        """Concretely: with two equally-sized records and budget for only one,
        the older one is dropped."""
        old = _make_record(
            key="old", title="old",
            content="x" * 50,
            created_at="2026-01-01T10:00:00",
            record_id="r_old",
        )
        new = _make_record(
            key="new", title="new",
            content="x" * 50,
            created_at="2026-04-08T10:00:00",
            record_id="r_new",
        )
        # Budget enough for header + close + one fact
        block = renderer.render_stable_block([old, new], budget=320)
        assert "new" in block
        assert "old" not in block

    def test_budget_eviction_fills_gap_with_smaller_older_entry(self, renderer):
        """Greedy fit: if the newest record doesn't fit but a smaller older
        record does, the older one slips in. This keeps utilization high
        without sacrificing recency priority overall."""
        small_old = _make_record(
            key="small_old", title="s",
            content="x" * 5,
            created_at="2026-01-01T10:00:00",
            record_id="r_small",
        )
        huge_new = _make_record(
            key="huge_new", title="h",
            content="x" * 1000,
            created_at="2026-04-08T10:00:00",
            record_id="r_huge",
        )
        # Budget fits the small one but not the huge one
        block = renderer.render_stable_block([small_old, huge_new], budget=350)
        assert "small_old" in block
        assert "huge_new" not in block

    def test_confidence_attribute(self, renderer):
        records = [_make_record(confidence="auto_summary")]
        block = renderer.render_stable_block(records)
        assert 'confidence="auto_summary"' in block


# ---------------------------------------------------------------------------
# session_tail rendering
# ---------------------------------------------------------------------------


class TestSessionTail:
    def test_session_tail_only_renders_block(self, renderer):
        """session_tail alone (no records) renders a stable block."""
        block = renderer.render_stable_block([], session_tail="Previous session summary.")
        assert "<memory-stable>" in block
        assert "<session>" in block
        assert "Previous session summary." in block
        assert "</memory-stable>" in block

    def test_session_tail_escaped(self, renderer):
        """session_tail content is XML-escaped."""
        block = renderer.render_stable_block([], session_tail="summary with <html> & stuff")
        assert "&lt;html&gt;" in block
        assert "&amp;" in block

    def test_session_tail_appended_after_facts(self, renderer):
        """<session> block appears after <fact> entries."""
        records = [_make_record(title="fact")]
        block = renderer.render_stable_block(records, session_tail="tail text")
        fact_pos = block.index("<fact")
        session_pos = block.index("<session>")
        assert fact_pos < session_pos

    def test_session_tail_reserved_within_budget(self, renderer):
        """session_tail is always present even when facts fill budget."""
        records = [_make_record(content="x" * 400) for _ in range(10)]
        tail = "Important cross-session summary."
        block = renderer.render_stable_block(records, session_tail=tail, budget=500)
        assert tail in block


# ---------------------------------------------------------------------------
# render_dynamic_block
# ---------------------------------------------------------------------------


class TestRenderDynamicBlock:
    def test_empty_returns_empty(self, renderer):
        assert renderer.render_dynamic_block([]) == ""

    def test_basic_structure(self, renderer):
        candidates = [_make_candidate()]
        block = renderer.render_dynamic_block(candidates)
        assert block.startswith("<memory-context>")
        assert block.endswith("</memory-context>")
        assert "Relevant saved facts for this turn" in block

    def test_contains_fact_tags(self, renderer):
        candidates = [_make_candidate(scope="user", type_="preference")]
        block = renderer.render_dynamic_block(candidates)
        assert '<fact scope="user" type="preference"' in block

    def test_score_included(self, renderer):
        candidates = [_make_candidate(score=3.75)]
        block = renderer.render_dynamic_block(candidates)
        assert 'score="3.75"' in block

    def test_reasons_included(self, renderer):
        candidates = [_make_candidate(reasons=["tag_match", "pinned"])]
        block = renderer.render_dynamic_block(candidates)
        assert "tag_match,pinned" in block

    def test_excerpt_escaped(self, renderer):
        candidates = [_make_candidate(excerpt='value with <html> & "quotes"')]
        block = renderer.render_dynamic_block(candidates)
        assert "&lt;html&gt;" in block
        assert "&amp;" in block

    def test_multiple_candidates(self, renderer):
        candidates = [
            _make_candidate(title="first"),
            _make_candidate(title="second"),
        ]
        block = renderer.render_dynamic_block(candidates)
        assert "first" in block
        assert "second" in block
        assert block.count("<fact") == 2

    def test_budget_limits_candidates(self, renderer):
        """render_dynamic_block drops candidates that exceed the char budget."""
        # Each candidate is ~300 chars; budget=700 fits ~1-2 but not all 10
        candidates = [_make_candidate(title=f"entry {i}", excerpt="x" * 200) for i in range(10)]
        block = renderer.render_dynamic_block(candidates, budget=700)
        # With a tight budget not all 10 candidates can fit
        assert 0 < block.count("<fact") < 10
        assert block.endswith("</memory-context>")

    def test_budget_zero_candidates_returns_empty(self, renderer):
        """If even one candidate exceeds the budget, return empty string."""
        candidates = [_make_candidate(excerpt="x" * 500)]
        block = renderer.render_dynamic_block(candidates, budget=50)
        assert block == ""


# ---------------------------------------------------------------------------
# Integration: renderer + agent prompt injection
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_injection_into_system_prompt(self, tmp_path):
        """Memory blocks appear in agent's system prompt as structured XML."""
        import tempfile
        from contextlib import contextmanager
        from pathlib import Path
        from unittest.mock import Mock, patch

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_proj = Path(tmpdir) / ".agentao"
            tmp_global = Path(tmpdir) / "global"
            with patch("agentao.agent.LLMClient") as mock_llm_cls, \
                 patch("agentao.tooling.mcp_tools.load_mcp_config", return_value=[]), \
                 patch("agentao.tooling.mcp_tools.McpClientManager"):
                mock_llm = Mock()
                mock_llm.logger = Mock()
                mock_llm.model = "test-model"
                mock_llm_cls.return_value = mock_llm

                from agentao.agent import Agentao
                from agentao.memory import MemoryManager
                from agentao.tools.memory import SaveMemoryTool

                agent = Agentao()
                agent.memory_manager = MemoryManager(
                    project_root=tmp_proj, global_root=tmp_global
                )
                agent.memory_tool = SaveMemoryTool(memory_manager=agent.memory_manager)

                # Save a memory
                agent.memory_tool.execute(key="test_key", value="test_value", tags=["test"])

                prompt = agent._build_system_prompt()
                assert "<memory-stable>" in prompt
                assert "test_key" in prompt
                assert "test_value" in prompt
                assert "</memory-stable>" in prompt

    def test_no_memory_no_block(self, tmp_path):
        """No memory block when there are no entries."""
        import tempfile
        from pathlib import Path
        from unittest.mock import Mock, patch

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_proj = Path(tmpdir) / ".agentao"
            tmp_global = Path(tmpdir) / "global"
            with patch("agentao.agent.LLMClient") as mock_llm_cls, \
                 patch("agentao.tooling.mcp_tools.load_mcp_config", return_value=[]), \
                 patch("agentao.tooling.mcp_tools.McpClientManager"):
                mock_llm = Mock()
                mock_llm.logger = Mock()
                mock_llm.model = "test-model"
                mock_llm_cls.return_value = mock_llm

                from agentao.agent import Agentao
                from agentao.memory import MemoryManager

                agent = Agentao()
                agent.memory_manager = MemoryManager(
                    project_root=tmp_proj, global_root=tmp_global
                )

                prompt = agent._build_system_prompt()
                assert "<memory-stable>" not in prompt


# ---------------------------------------------------------------------------
# Agentao._extract_context_hints — file path extraction from message blocks
# ---------------------------------------------------------------------------


class TestExtractContextHints:
    """Regression: file-path hint extraction must read text-block content
    from ``block["text"]`` (the canonical key used everywhere else in the
    codebase), not ``block["content"]``. The earlier bug silently dropped
    every list-shaped message and broke filepath_hint scoring."""

    def _make_agent(self, tmpdir):
        from pathlib import Path
        from unittest.mock import Mock, patch

        with patch("agentao.agent.LLMClient") as mock_llm_cls, \
             patch("agentao.tooling.mcp_tools.load_mcp_config", return_value=[]), \
             patch("agentao.tooling.mcp_tools.McpClientManager"):
            mock_llm = Mock()
            mock_llm.logger = Mock()
            mock_llm.model = "test-model"
            mock_llm_cls.return_value = mock_llm

            from agentao.agent import Agentao
            return Agentao()

    def test_extracts_paths_from_string_content(self, tmp_path):
        agent = self._make_agent(tmp_path)
        agent.messages = [
            {"role": "user", "content": "look at agent.py and tools/memory.py"},
        ]
        hints = agent._extract_context_hints()
        assert "agent.py" in hints
        assert "tools/memory.py" in hints

    def test_extracts_paths_from_list_text_blocks(self, tmp_path):
        """The bug: list-shaped messages were read via block['content'],
        not block['text'], so paths were never extracted."""
        agent = self._make_agent(tmp_path)
        agent.messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "edit retriever.py please"},
                ],
            },
        ]
        hints = agent._extract_context_hints()
        assert "retriever.py" in hints

    def test_extracts_paths_from_mixed_text_blocks(self, tmp_path):
        agent = self._make_agent(tmp_path)
        agent.messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "url": "x"},                  # ignored
                    {"type": "text", "text": "open render.py"},
                    {"type": "text", "text": "and storage.py"},
                ],
            },
        ]
        hints = agent._extract_context_hints()
        assert "render.py" in hints
        assert "storage.py" in hints

    def test_ignores_non_text_blocks(self, tmp_path):
        agent = self._make_agent(tmp_path)
        agent.messages = [
            {
                "role": "user",
                "content": [
                    {"type": "tool_use", "name": "x", "id": "no/path/here.py"},
                ],
            },
        ]
        # Tool-use block is not a text block; the path inside `id` must NOT
        # be extracted (it's not user intent — it's a tool routing identifier).
        hints = agent._extract_context_hints()
        assert "no/path/here.py" not in hints
