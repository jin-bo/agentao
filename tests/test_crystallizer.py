"""Tests for SkillCrystallizer and MemoryCrystallizer."""

from pathlib import Path

import pytest

from agentao.memory.crystallizer import (
    MemoryCrystallizer,
    SkillCrystallizer,
    SUGGEST_SYSTEM_PROMPT,
    suggest_prompt,
    REFINE_SYSTEM_PROMPT,
    refine_prompt,
    _extract_text,
)
from agentao.memory.manager import MemoryManager

_SAMPLE_SKILL_MD = """\
---
name: python-testing
description: Use when writing or debugging Python test suites with pytest.
---

# Python Testing Workflow

## When to use
- User asks to write tests for a Python module
- Tests are failing and need debugging
- Setting up a new test suite

## Steps
1. Identify the module under test
2. Create test file in tests/ directory
3. Write test cases using pytest fixtures
"""


# ---------------------------------------------------------------------------
# suggest_prompt
# ---------------------------------------------------------------------------

def test_suggest_prompt_includes_session_content():
    content = "user asked to refactor the auth module"
    result = suggest_prompt(content)
    assert content in result


def test_suggest_prompt_truncates_long_content():
    long_content = "x" * 5000
    result = suggest_prompt(long_content)
    # Truncated to last 3000 chars of the content
    assert len(result) < 4000


def test_suggest_prompt_short_content_unchanged():
    content = "short session"
    result = suggest_prompt(content)
    assert content in result


def test_suggest_system_prompt_contains_format():
    assert "---" in SUGGEST_SYSTEM_PROMPT
    assert "name:" in SUGGEST_SYSTEM_PROMPT
    assert "description:" in SUGGEST_SYSTEM_PROMPT
    assert "NO_PATTERN_FOUND" in SUGGEST_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# _extract_text
# ---------------------------------------------------------------------------

def test_extract_text_from_mock_response():
    class MockChoice:
        class message:
            content = "hello skill"
    class MockResponse:
        choices = [MockChoice()]
    assert _extract_text(MockResponse()) == "hello skill"


def test_extract_text_falls_back_to_str_on_error():
    result = _extract_text("raw string response")
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# SkillCrystallizer.create — project scope
# ---------------------------------------------------------------------------

def test_create_writes_project_skill(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    crystallizer = SkillCrystallizer()
    target = crystallizer.create("my-skill", "project", _SAMPLE_SKILL_MD)
    assert target.exists()
    assert target.name == "SKILL.md"
    assert target.read_text(encoding="utf-8") == _SAMPLE_SKILL_MD


def test_create_project_skill_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    crystallizer = SkillCrystallizer()
    target = crystallizer.create("test-skill", "project", _SAMPLE_SKILL_MD)
    expected = tmp_path / ".agentao" / "skills" / "test-skill" / "SKILL.md"
    assert target == expected


def test_create_creates_parent_directories(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    crystallizer = SkillCrystallizer()
    target = crystallizer.create("nested-skill", "project", "content")
    assert target.parent.is_dir()


def test_create_overwrites_existing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    crystallizer = SkillCrystallizer()
    crystallizer.create("my-skill", "project", "original")
    crystallizer.create("my-skill", "project", "updated")
    target = tmp_path / ".agentao" / "skills" / "my-skill" / "SKILL.md"
    assert target.read_text(encoding="utf-8") == "updated"


def test_create_uses_project_root_over_cwd(tmp_path, monkeypatch):
    """Project-scope writes must follow the explicit ``project_root`` —
    not the process cwd — so ACP / background sessions save skills under
    the agent's working directory instead of the launcher's shell cwd.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    elsewhere = tmp_path / "launcher_cwd"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    crystallizer = SkillCrystallizer()
    target = crystallizer.create(
        "my-skill", "project", _SAMPLE_SKILL_MD, project_root=repo,
    )
    expected = repo / ".agentao" / "skills" / "my-skill" / "SKILL.md"
    assert target == expected
    assert target.exists()
    # And must NOT have leaked into the process cwd.
    assert not (elsewhere / ".agentao").exists()


def test_create_project_root_none_falls_back_to_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    crystallizer = SkillCrystallizer()
    target = crystallizer.create(
        "my-skill", "project", _SAMPLE_SKILL_MD, project_root=None,
    )
    assert target == tmp_path / ".agentao" / "skills" / "my-skill" / "SKILL.md"


# ---------------------------------------------------------------------------
# REFINE prompt
# ---------------------------------------------------------------------------

def test_refine_system_prompt_constrains_output():
    p = REFINE_SYSTEM_PROMPT
    assert "SKILL.md" in p
    # Must instruct output to be frontmatter-valid and only the skill content
    assert "ONLY" in p or "only" in p
    assert "frontmatter" in p.lower() or "---" in p


def test_refine_prompt_includes_draft_and_transcript():
    draft = "---\nname: foo\ndescription: test\n---\nbody"
    transcript = "Recent discussion about foo"
    guidance = "Write triggering descriptions"
    out = refine_prompt(draft, transcript, guidance)
    assert draft in out
    assert transcript in out
    assert guidance in out


def test_refine_prompt_truncates_long_transcript():
    draft = "---\nname: foo\ndescription: d\n---\nbody"
    transcript = "y" * 5000
    out = refine_prompt(draft, transcript, "g")
    # transcript truncated to last 3000 chars — the full 5000 cannot be present
    assert transcript not in out
    assert "y" * 3000 in out


# ---------------------------------------------------------------------------
# SkillCrystallizer.create — global scope
# ---------------------------------------------------------------------------

def test_create_global_skill_path(tmp_path, monkeypatch):
    """Global scope uses ~/.agentao/skills/; patch home dir to avoid real writes."""
    monkeypatch.setenv("HOME", str(tmp_path))
    # Patch Path.home() to return tmp_path
    import agentao.memory.crystallizer as crys_mod
    original_home = Path.home
    try:
        monkeypatch.setattr(crys_mod, "_GLOBAL_SKILLS_DIR", tmp_path / ".agentao" / "skills")
        crystallizer = SkillCrystallizer()
        target = crystallizer.create("global-skill", "global", _SAMPLE_SKILL_MD)
        assert target.exists()
        assert "global-skill" in str(target)
    finally:
        pass  # monkeypatch cleanup is automatic


def test_create_global_skill_writes_content(tmp_path, monkeypatch):
    import agentao.memory.crystallizer as crys_mod
    monkeypatch.setattr(crys_mod, "_GLOBAL_SKILLS_DIR", tmp_path / "global_skills")
    crystallizer = SkillCrystallizer()
    target = crystallizer.create("my-global", "global", _SAMPLE_SKILL_MD)
    assert target.read_text(encoding="utf-8") == _SAMPLE_SKILL_MD


# ---------------------------------------------------------------------------
# Integration: create + verify SKILL.md is valid
# ---------------------------------------------------------------------------

def test_create_skill_md_has_frontmatter(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    crystallizer = SkillCrystallizer()
    target = crystallizer.create("python-testing", "project", _SAMPLE_SKILL_MD)
    content = target.read_text(encoding="utf-8")
    assert content.startswith("---")
    assert "name:" in content
    assert "description:" in content


# ===========================================================================
# MemoryCrystallizer — rule-based extraction + review queue
# ===========================================================================


def _make_manager(tmp_path: Path) -> MemoryManager:
    return MemoryManager(project_root=tmp_path / ".agentao", global_root=tmp_path / "global")


# ---------------------------------------------------------------------------
# extract_from_summary — pattern matching
# ---------------------------------------------------------------------------


class TestExtractFromSummary:
    def test_extract_preference_english(self):
        c = MemoryCrystallizer()
        out = c.extract_from_summary("I prefer dark mode for the editor")
        assert len(out) == 1
        assert out[0].type == "preference"
        assert out[0].scope == "user"
        assert "dark mode" in out[0].content.lower()

    def test_extract_constraint_english(self):
        c = MemoryCrystallizer()
        out = c.extract_from_summary("Never commit secrets to git")
        assert len(out) == 1
        assert out[0].type == "constraint"
        assert out[0].scope == "project"

    def test_extract_decision_english(self):
        c = MemoryCrystallizer()
        out = c.extract_from_summary("We decided to use PostgreSQL for storage")
        assert any(p.type == "decision" for p in out)

    def test_extract_workflow_english(self):
        c = MemoryCrystallizer()
        out = c.extract_from_summary("Workflow: write tests then implement")
        assert any(p.type == "workflow" for p in out)

    def test_extract_preference_chinese(self):
        c = MemoryCrystallizer()
        out = c.extract_from_summary("我喜欢使用 vim 编辑器")
        assert len(out) >= 1
        assert out[0].type == "preference"
        assert out[0].scope == "user"

    def test_extract_constraint_chinese(self):
        c = MemoryCrystallizer()
        out = c.extract_from_summary("不要直接 push 到 main 分支")
        assert any(p.type == "constraint" for p in out)

    def test_extract_decision_chinese(self):
        c = MemoryCrystallizer()
        out = c.extract_from_summary("我们决定使用 Docker 部署服务")
        assert any(p.type == "decision" for p in out)

    def test_extract_no_patterns_returns_empty(self):
        c = MemoryCrystallizer()
        out = c.extract_from_summary("Today we discussed the weather and lunch plans.")
        assert out == []

    def test_extract_empty_summary_returns_empty(self):
        c = MemoryCrystallizer()
        assert c.extract_from_summary("") == []
        assert c.extract_from_summary("   ") == []

    def test_extract_dedupes_within_summary(self):
        """Same captured phrase matched twice in one summary → only one proposal."""
        c = MemoryCrystallizer()
        # Both captures end at '.' → both yield identical "dark mode"
        summary = "I prefer dark mode. Honestly, I prefer dark mode."
        out = c.extract_from_summary(summary)
        prefs = [p for p in out if p.type == "preference"]
        assert len(prefs) == 1

    def test_extract_attaches_session_id(self):
        c = MemoryCrystallizer()
        out = c.extract_from_summary("I like vim", session_id="sess-xyz")
        assert out[0].source_session == "sess-xyz"

    def test_extract_includes_evidence(self):
        c = MemoryCrystallizer()
        out = c.extract_from_summary("Background. I prefer rust over go. End.")
        assert "prefer rust" in out[0].evidence.lower() or "prefer" in out[0].evidence.lower()


# ---------------------------------------------------------------------------
# extract_from_user_messages — role filtering, content normalization, repetition
# ---------------------------------------------------------------------------


class TestExtractFromUserMessages:
    def test_extracts_from_user_role_only(self):
        """Assistant/system/tool messages must be ignored — only user content
        can produce a proposal. This is the entire point of the refactor."""
        c = MemoryCrystallizer()
        msgs = [
            {"role": "assistant", "content": "I'd recommend you prefer ruff for linting"},
            {"role": "system",    "content": "I prefer to remind you about safety"},
            {"role": "tool",      "content": "I prefer text from a tool result"},
            {"role": "user",      "content": "I prefer dark mode"},
        ]
        out = c.extract_from_user_messages(msgs)
        assert len(out) == 1
        assert out[0].type == "preference"
        assert "dark mode" in out[0].content.lower()

    def test_assistant_narration_does_not_trigger(self):
        c = MemoryCrystallizer()
        msgs = [{"role": "assistant", "content": "Always commit secrets is a bad idea"}]
        assert c.extract_from_user_messages(msgs) == []

    def test_handles_list_content_blocks(self):
        """Multimodal/tool-use shape: content is a list of typed blocks."""
        c = MemoryCrystallizer()
        msgs = [{
            "role": "user",
            "content": [{"type": "text", "text": "I prefer fish shell"}],
        }]
        out = c.extract_from_user_messages(msgs)
        assert len(out) == 1

    def test_handles_mixed_content_blocks(self):
        c = MemoryCrystallizer()
        msgs = [{
            "role": "user",
            "content": [
                {"type": "image", "url": "x"},          # ignored
                {"type": "text", "text": "I prefer ruff"},
                {"type": "text", "text": "and fast feedback"},
            ],
        }]
        out = c.extract_from_user_messages(msgs)
        assert len(out) == 1

    def test_strips_pin_prefix(self):
        c = MemoryCrystallizer()
        msgs = [{"role": "user", "content": "[PIN] I prefer fish shell"}]
        out = c.extract_from_user_messages(msgs)
        assert len(out) == 1

    def test_dedupes_across_messages_with_occurrences(self):
        c = MemoryCrystallizer()
        msgs = [
            {"role": "user", "content": "I prefer fish shell"},
            {"role": "user", "content": "again, I prefer fish shell"},
            {"role": "user", "content": "yes, I prefer fish shell"},
        ]
        out = c.extract_from_user_messages(msgs)
        assert len(out) == 1
        assert out[0].occurrences == 3
        assert out[0].confidence == "inferred"

    def test_distinct_preferences_not_merged(self):
        c = MemoryCrystallizer()
        msgs = [
            {"role": "user", "content": "I prefer dark mode"},
            {"role": "user", "content": "I prefer fish shell"},
        ]
        out = c.extract_from_user_messages(msgs)
        assert len(out) == 2

    def test_single_occurrence_keeps_auto_summary_confidence(self):
        c = MemoryCrystallizer()
        msgs = [{"role": "user", "content": "I prefer golang"}]
        out = c.extract_from_user_messages(msgs)
        assert len(out) == 1
        assert out[0].confidence == "auto_summary"
        assert out[0].occurrences == 1

    def test_attaches_session_id(self):
        c = MemoryCrystallizer()
        msgs = [{"role": "user", "content": "I prefer vim"}]
        out = c.extract_from_user_messages(msgs, session_id="sess-xyz")
        assert out[0].source_session == "sess-xyz"

    def test_empty_messages_returns_empty(self):
        c = MemoryCrystallizer()
        assert c.extract_from_user_messages([]) == []

    def test_skips_messages_without_text(self):
        c = MemoryCrystallizer()
        msgs = [
            {"role": "user", "content": ""},
            {"role": "user"},               # missing content
            {"role": "user", "content": None},
            {"role": "user", "content": 42},  # unsupported type
        ]
        assert c.extract_from_user_messages(msgs) == []

    def test_user_message_text_helper_string(self):
        assert MemoryCrystallizer._user_message_text("hello") == "hello"

    def test_user_message_text_helper_list(self):
        text = MemoryCrystallizer._user_message_text([
            {"type": "text", "text": "hello"},
            {"type": "text", "text": "world"},
        ])
        assert text == "hello world"

    def test_user_message_text_helper_unsupported(self):
        assert MemoryCrystallizer._user_message_text(None) == ""
        assert MemoryCrystallizer._user_message_text(42) == ""


# ---------------------------------------------------------------------------
# submit_to_review and promote — review queue lifecycle
# ---------------------------------------------------------------------------


class TestReviewLifecycle:
    def test_submit_writes_to_review_queue_not_memories(self, tmp_path):
        mgr = _make_manager(tmp_path)
        c = MemoryCrystallizer()
        proposals = c.extract_from_summary("I prefer fish shell")
        items = c.submit_to_review(proposals, mgr)

        assert len(items) == 1
        # Review queue has the item
        assert len(mgr.list_review_items()) == 1
        # Live memories is still empty
        assert mgr.get_all_entries() == []

    def test_submit_assigns_pending_status(self, tmp_path):
        mgr = _make_manager(tmp_path)
        c = MemoryCrystallizer()
        proposals = c.extract_from_summary("Always run tests before pushing")
        items = c.submit_to_review(proposals, mgr)
        assert items[0].status == "pending"

    def test_promote_writes_to_memories_with_crystallized_source(self, tmp_path):
        mgr = _make_manager(tmp_path)
        c = MemoryCrystallizer()
        proposals = c.extract_from_summary("I prefer ruff for linting")
        items = c.submit_to_review(proposals, mgr)

        record = c.promote(items[0], mgr)
        assert record is not None
        assert record.source == "crystallized"

    def test_promote_marks_queue_item_approved(self, tmp_path):
        mgr = _make_manager(tmp_path)
        c = MemoryCrystallizer()
        proposals = c.extract_from_summary("Never use eval()")
        items = c.submit_to_review(proposals, mgr)
        c.promote(items[0], mgr)

        fetched = mgr.project_store.get_review_item(items[0].id)
        assert fetched.status == "approved"

    def test_promote_preserves_proposal_type(self, tmp_path):
        mgr = _make_manager(tmp_path)
        c = MemoryCrystallizer()
        proposals = c.extract_from_summary("Never log passwords")
        items = c.submit_to_review(proposals, mgr)
        rec = c.promote(items[0], mgr)
        assert rec.type == "constraint"


# ---------------------------------------------------------------------------
# Manager facade
# ---------------------------------------------------------------------------


def _user_msg(text: str) -> dict:
    return {"role": "user", "content": text}


class TestManagerCrystallizationFacade:
    def test_save_session_summary_no_longer_auto_extracts(self, tmp_path):
        """Regression guard: save_session_summary() must NOT crystallize.

        Even if the summary text contains the words 'I prefer …' (which a
        previous version would have extracted), the review queue stays empty.
        Crystallization now lives in compress_messages() and only sees raw
        user messages.
        """
        mgr = _make_manager(tmp_path)
        mgr.save_session_summary(
            "I prefer pytest over unittest",  # narration that contains pattern words
            tokens_before=10,
            messages_summarized=2,
        )
        assert len(mgr.get_recent_session_summaries()) == 1
        assert mgr.list_review_items() == []

    def test_save_session_summary_succeeds_when_no_patterns(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr.save_session_summary("Just a normal conversation", tokens_before=10, messages_summarized=2)
        assert len(mgr.get_recent_session_summaries()) == 1
        assert mgr.list_review_items() == []

    def test_crystallize_user_messages_aggregates(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr.crystallize_user_messages([
            _user_msg("I prefer fish"),
            _user_msg("I prefer fish"),
            _user_msg("I prefer fish"),
        ])
        items = mgr.list_review_items()
        assert len(items) == 1
        assert items[0].occurrences == 3
        assert items[0].confidence == "inferred"
        assert items[0].source_session == mgr._session_id

    def test_crystallize_user_messages_returns_empty_when_no_proposals(self, tmp_path):
        mgr = _make_manager(tmp_path)
        out = mgr.crystallize_user_messages([_user_msg("hello there")])
        assert out == []
        assert mgr.list_review_items() == []

    def test_crystallize_user_messages_ignores_assistant_role(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr.crystallize_user_messages([
            {"role": "assistant", "content": "I prefer dark mode"},
            {"role": "tool",      "content": "Always commit secrets"},
        ])
        assert mgr.list_review_items() == []

    def test_approve_review_item(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr.crystallize_user_messages([_user_msg("I prefer Vim")])
        items = mgr.list_review_items()
        assert len(items) == 1
        rec = mgr.approve_review_item(items[0].id)
        assert rec is not None
        assert rec.source == "crystallized"
        assert any(e.source == "crystallized" for e in mgr.get_all_entries())
        assert mgr.list_review_items(status="pending") == []

    def test_approve_missing_returns_none(self, tmp_path):
        mgr = _make_manager(tmp_path)
        assert mgr.approve_review_item("nope") is None

    def test_approve_already_approved_returns_none(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr.crystallize_user_messages([_user_msg("I prefer Vim")])
        items = mgr.list_review_items()
        mgr.approve_review_item(items[0].id)
        assert mgr.approve_review_item(items[0].id) is None

    def test_reject_review_item(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr.crystallize_user_messages([_user_msg("Never push directly to main")])
        items = mgr.list_review_items()
        assert len(items) == 1
        ok = mgr.reject_review_item(items[0].id)
        assert ok is True
        assert all(e.source != "crystallized" for e in mgr.get_all_entries())
        assert mgr.list_review_items(status="pending") == []
        assert len(mgr.list_review_items(status="rejected")) == 1

    def test_reject_missing_returns_false(self, tmp_path):
        mgr = _make_manager(tmp_path)
        assert mgr.reject_review_item("nope") is False
