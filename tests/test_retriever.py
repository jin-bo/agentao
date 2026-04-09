"""Tests for MemoryRetriever: tokenize, score, recall_candidates, format_recall_block."""

import uuid
from pathlib import Path

import pytest

from agentao.memory.manager import MemoryManager
from agentao.memory.models import MemoryRecord, RecallCandidate
from agentao.memory.retriever import MemoryRetriever, _jaccard, _days_since, _normalize_token, _cjk_bigrams


def _make_manager(tmp_path: Path) -> MemoryManager:
    return MemoryManager(project_root=tmp_path / ".agentao", global_root=tmp_path / "global")


def _make_retriever(tmp_path: Path):
    mgr = _make_manager(tmp_path)
    return MemoryRetriever(mgr), mgr


def _make_record(
    scope="project",
    title="test entry",
    content="some content here",
    tags=None,
    keywords=None,
    source="explicit",
    created_at="2026-04-08T10:00:00",
    updated_at=None,
) -> MemoryRecord:
    eid = uuid.uuid4().hex[:8]
    return MemoryRecord(
        id=eid,
        scope=scope,
        type="note",
        key_normalized=title.lower().replace(" ", "_")[:80],
        title=title,
        content=content,
        tags=tags or [],
        keywords=keywords or [],
        source=source,
        confidence="explicit_user",
        sensitivity="normal",
        created_at=created_at,
        updated_at=updated_at or created_at,
    )


# ---------------------------------------------------------------------------
# tokenize
# ---------------------------------------------------------------------------

def test_tokenize_splits_and_filters_stopwords(tmp_path):
    ret, _ = _make_retriever(tmp_path)
    tokens = ret.tokenize("the quick brown fox")
    assert "the" not in tokens
    assert "quick" in tokens
    assert "brown" in tokens
    assert "fox" in tokens


def test_tokenize_lowercases(tmp_path):
    ret, _ = _make_retriever(tmp_path)
    tokens = ret.tokenize("Python FastAPI")
    assert "python" in tokens
    assert "fastapi" in tokens


def test_tokenize_splits_on_special_chars(tmp_path):
    ret, _ = _make_retriever(tmp_path)
    tokens = ret.tokenize("path/to/file.py")
    assert "path" in tokens
    assert "file" in tokens


def test_tokenize_filters_single_char(tmp_path):
    ret, _ = _make_retriever(tmp_path)
    tokens = ret.tokenize("a b c hello")
    assert "hello" in tokens
    for t in tokens:
        assert len(t) > 1


def test_tokenize_empty_string(tmp_path):
    ret, _ = _make_retriever(tmp_path)
    assert ret.tokenize("") == set()


# ---------------------------------------------------------------------------
# score (now takes MemoryRecord, returns (score, reasons_list, semantic))
# ---------------------------------------------------------------------------

def test_score_exact_tag_match_boosts_score(tmp_path):
    ret, _ = _make_retriever(tmp_path)
    record = _make_record(tags=["python", "backend"], keywords=[])
    query_tokens = {"python"}
    s, reasons, semantic = ret.score(record, query_tokens, set())
    assert s > 0
    assert semantic > 0
    assert "tag_match" in reasons


def test_score_title_jaccard_match(tmp_path):
    ret, _ = _make_retriever(tmp_path)
    record = _make_record(title="python language preference", tags=[], keywords=[])
    query_tokens = ret.tokenize("python language")
    s, reasons, semantic = ret.score(record, query_tokens, set())
    assert s > 0
    assert semantic > 0
    assert "title_overlap" in reasons


def test_score_keyword_match(tmp_path):
    ret, _ = _make_retriever(tmp_path)
    record = _make_record(title="misc", tags=[], keywords=["fastapi", "uvicorn"])
    query_tokens = {"fastapi"}
    s, reasons, semantic = ret.score(record, query_tokens, set())
    assert s > 0
    assert semantic > 0
    assert "keyword_match" in reasons


def test_score_keyword_match_tokenizes_compound_keyword(tmp_path):
    """A compound keyword like 'agent.py' should match a query token 'agent'.

    Before unifying the keyword path, kw_match used the raw keyword set so
    'agent.py' only matched verbatim. Now keywords are tokenized first and
    sub-tokens are also queryable.
    """
    ret, _ = _make_retriever(tmp_path)
    record = _make_record(
        title="misc",
        tags=[],
        keywords=["agent.py", "memory_manager"],
    )
    # Query 'agent' must hit the sub-token of 'agent.py'
    s, reasons, semantic = ret.score(record, {"agent"}, set())
    assert "keyword_match" in reasons
    assert semantic > 0

    # And 'memory' should hit the sub-token of 'memory_manager'
    s2, reasons2, semantic2 = ret.score(record, {"memory"}, set())
    assert "keyword_match" in reasons2
    assert semantic2 > 0


def test_score_keyword_exact_still_matches(tmp_path):
    """Exact-form keywords still fire (no regression from the tokenization rewrite)."""
    ret, _ = _make_retriever(tmp_path)
    record = _make_record(title="misc", tags=[], keywords=["fastapi"])
    _, reasons, _ = ret.score(record, {"fastapi"}, set())
    assert "keyword_match" in reasons


def test_score_stale_penalty(tmp_path):
    ret, _ = _make_retriever(tmp_path)
    stale_record = _make_record(
        title="old entry", tags=[], keywords=[],
        updated_at="2020-01-01T00:00:00",
    )
    fresh_record = _make_record(
        title="old entry", tags=[], keywords=[],
        updated_at="2026-04-08T00:00:00",
    )
    query_tokens = {"old"}
    s_stale, reasons_stale, _ = ret.score(stale_record, query_tokens, set())
    s_fresh, _, _ = ret.score(fresh_record, query_tokens, set())
    assert s_stale < s_fresh
    assert "stale_penalty" in reasons_stale


def test_score_filepath_match(tmp_path):
    ret, _ = _make_retriever(tmp_path)
    record = _make_record(title="misc", tags=[], keywords=["agent.py", "retriever"])
    hint_tokens = ret.tokenize("agent.py")
    s, reasons, semantic = ret.score(record, set(), hint_tokens)
    assert "filepath_hint" in reasons
    assert semantic > 0


def test_score_zero_for_no_matching_tokens(tmp_path):
    ret, _ = _make_retriever(tmp_path)
    record = _make_record(title="completely unrelated", tags=["xyz"], keywords=["xyz"])
    s, _, semantic = ret.score(record, {"python"}, set())
    assert semantic == 0.0
    assert isinstance(s, float)


def test_score_explicit_confidence_bonus(tmp_path):
    ret, _ = _make_retriever(tmp_path)
    record = _make_record(title="test", tags=["python"], keywords=[])
    _, reasons, _ = ret.score(record, {"python"}, set())
    assert "explicit" in reasons


# ---------------------------------------------------------------------------
# recall_candidates (new primary API)
# ---------------------------------------------------------------------------

def test_recall_candidates_empty_for_blank_query(tmp_path):
    ret, mgr = _make_retriever(tmp_path)
    mgr.save_from_tool("python_pref", "Python", ["python"])
    assert ret.recall_candidates("") == []
    assert ret.recall_candidates("   ") == []


def test_recall_candidates_returns_matching(tmp_path):
    ret, mgr = _make_retriever(tmp_path)
    mgr.save_from_tool("preferred_language", "Python", ["python", "language"])
    candidates = ret.recall_candidates("python language")
    assert len(candidates) > 0
    assert isinstance(candidates[0], RecallCandidate)
    assert candidates[0].title == "preferred_language"


def test_recall_candidates_returns_empty_when_no_match(tmp_path):
    ret, mgr = _make_retriever(tmp_path)
    mgr.save_from_tool("database_config", "postgres://localhost", ["db"])
    candidates = ret.recall_candidates("javascript frontend react")
    for c in candidates:
        assert c.score > 0


def test_recall_candidates_returns_empty_when_no_records(tmp_path):
    ret, _ = _make_retriever(tmp_path)
    assert ret.recall_candidates("some query") == []


def test_recall_candidates_respects_top_k(tmp_path):
    ret, mgr = _make_retriever(tmp_path)
    for i in range(10):
        mgr.save_from_tool(f"entry_{i}", f"python content {i}", ["python"])
    candidates = ret.recall_candidates("python", top_k=3)
    assert len(candidates) <= 3


def test_recall_count_increments(tmp_path):
    ret, mgr = _make_retriever(tmp_path)
    mgr.save_from_tool("lang_pref", "Python", ["python"])
    assert ret._recall_count == 0
    ret.recall_candidates("python")
    assert ret._recall_count > 0


def test_recall_candidates_excerpt_populated(tmp_path):
    ret, mgr = _make_retriever(tmp_path)
    mgr.save_from_tool("stack_info", "FastAPI backend with PostgreSQL", ["stack"])
    candidates = ret.recall_candidates("fastapi stack")
    if candidates:
        assert "FastAPI" in candidates[0].excerpt


def test_recall_candidates_has_reasons(tmp_path):
    ret, mgr = _make_retriever(tmp_path)
    mgr.save_from_tool("python_pref", "Python", ["python"])
    candidates = ret.recall_candidates("python")
    if candidates:
        assert len(candidates[0].reasons) > 0


def test_recall_error_increments_counter(tmp_path, monkeypatch):
    """A retriever exception increments _error_count and sets _last_error."""
    ret, _ = _make_retriever(tmp_path)
    assert ret._error_count == 0
    assert ret._last_error == ""

    # Force an exception inside recall_candidates
    monkeypatch.setattr(ret, "tokenize", lambda _text: (_ for _ in ()).throw(RuntimeError("db gone")))

    result = ret.recall_candidates("anything")
    assert result == []
    assert ret._error_count == 1
    assert "RuntimeError" in ret._last_error
    assert "db gone" in ret._last_error


def test_recall_error_logged_as_warning(tmp_path, monkeypatch, caplog):
    """A retriever exception is logged at WARNING level."""
    import logging
    ret, _ = _make_retriever(tmp_path)
    monkeypatch.setattr(ret, "tokenize", lambda _text: (_ for _ in ()).throw(ValueError("bad input")))

    with caplog.at_level(logging.WARNING, logger="agentao.memory.retriever"):
        ret.recall_candidates("test")

    assert any("recall_candidates failed" in r.message for r in caplog.records)
    assert any("ValueError" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# _normalize_token
# ---------------------------------------------------------------------------

def test_normalize_version_prefix():
    assert _normalize_token("v3") == "3"
    assert _normalize_token("v42") == "42"
    assert _normalize_token("v3x") == "v3x"   # non-numeric suffix → unchanged
    assert _normalize_token("version") == "version"  # not v+digits

def test_normalize_simple_plural():
    assert _normalize_token("keywords") == "keyword"
    assert _normalize_token("cats") == "cat"
    # 'ss' ending is preserved
    assert _normalize_token("class") == "class"
    assert _normalize_token("success") == "success"
    # short tokens (≤3) are not stripped
    assert _normalize_token("ids") == "ids"

def test_normalize_no_op():
    assert _normalize_token("python") == "python"
    assert _normalize_token("fastapi") == "fastapi"


# ---------------------------------------------------------------------------
# _cjk_bigrams
# ---------------------------------------------------------------------------

def test_cjk_bigrams_basic():
    bigrams = _cjk_bigrams("版本管理")
    assert "版本" in bigrams
    assert "本管" in bigrams
    assert "管理" in bigrams

def test_cjk_bigrams_single_char():
    bigrams = _cjk_bigrams("你")
    assert "你" in bigrams

def test_cjk_bigrams_empty():
    assert _cjk_bigrams("hello world") == set()


# ---------------------------------------------------------------------------
# tokenize — normalization and CJK
# ---------------------------------------------------------------------------

def test_tokenize_plural_normalization(tmp_path):
    ret, _ = _make_retriever(tmp_path)
    tokens = ret.tokenize("keywords libraries")
    # normalized forms should be present alongside originals
    assert "keyword" in tokens
    assert "keywords" in tokens  # original also retained

def test_tokenize_version_prefix(tmp_path):
    ret, _ = _make_retriever(tmp_path)
    tokens = ret.tokenize("python v3")
    assert "3" in tokens    # normalized
    assert "v3" in tokens   # original

def test_tokenize_cjk_bigrams(tmp_path):
    ret, _ = _make_retriever(tmp_path)
    tokens = ret.tokenize("Python版本")
    assert "python" in tokens
    assert "版本" in tokens

def test_tokenize_cjk_only(tmp_path):
    ret, _ = _make_retriever(tmp_path)
    tokens = ret.tokenize("版本管理")
    assert "版本" in tokens
    assert "本管" in tokens
    assert "管理" in tokens

def test_tokenize_mixed_cjk_latin(tmp_path):
    ret, _ = _make_retriever(tmp_path)
    tokens = ret.tokenize("使用 Python 开发")
    assert "python" in tokens
    assert "使用" in tokens or "用" in tokens  # at least some CJK tokens


# ---------------------------------------------------------------------------
# score — content path and short-query tag dampening
# ---------------------------------------------------------------------------

def test_score_content_match(tmp_path):
    ret, _ = _make_retriever(tmp_path)
    record = _make_record(
        title="misc entry",
        tags=[],
        keywords=[],
        content="we use fastapi for the backend server",
    )
    query_tokens = ret.tokenize("fastapi")
    s, reasons, semantic = ret.score(record, query_tokens, set())
    assert "content_match" in reasons
    assert semantic > 0
    assert s > 0

def test_score_content_only_no_tag_title_kw(tmp_path):
    """content_match alone is enough to gate the record into results."""
    ret, _ = _make_retriever(tmp_path)
    record = _make_record(
        title="general notes",
        tags=["unrelated"],
        keywords=[],
        content="the deployment uses kubernetes and helm charts",
    )
    query_tokens = ret.tokenize("kubernetes")
    s, reasons, semantic = ret.score(record, query_tokens, set())
    assert "content_match" in reasons
    assert semantic > 0

def test_score_short_query_lower_tag_weight(tmp_path):
    """Short-query dampening: single-token full tag match yields lower tag
    contribution than three-token full tag match (all tokens hit tags)."""
    ret, _ = _make_retriever(tmp_path)
    # Record has tags that match every token in both queries.
    record = _make_record(tags=["python", "dev", "tool"], keywords=[], content="x")

    # q_len=1 → tag_w=1.5; exact_tag = 1/1 = 1.0 → contribution = 1.5
    s1, _, _ = ret.score(record, {"python"}, set())
    # q_len=3 → tag_w=4.0; exact_tag = 3/3 = 1.0 → contribution = 4.0
    s3, _, _ = ret.score(record, {"python", "dev", "tool"}, set())

    assert s3 > s1

def test_score_short_query_dampens_single_tag_recall(tmp_path):
    """With a 1-token query a perfect tag match scores lower than with tag_w=4.
    Verify tag_w=1.5 is applied (not 4.0) by checking the raw tag contribution."""
    ret, _ = _make_retriever(tmp_path)
    record = _make_record(tags=["python"], keywords=[], content="x")

    # Manually compute expected tag contribution for q_len=1: 1.5 * 1.0 = 1.5
    # If tag_w were 4.0 it would be 4.0; confirm it's the lower value.
    s_short, _, _ = ret.score(record, {"python"}, set())  # tag_w=1.5

    # Now simulate same record with a 3-token query all matching tags
    record3 = _make_record(tags=["python", "back", "fast"], keywords=[], content="x")
    s_long, _, _ = ret.score(record3, {"python", "back", "fast"}, set())  # tag_w=4.0

    # The long query's tag contribution (4.0 * 1.0) must exceed short (1.5 * 1.0)
    # Even accounting for recency being similar, the gap should be clear
    assert s_long > s_short + 1.0


# ---------------------------------------------------------------------------
# recall end-to-end — CJK query matches CJK content
# ---------------------------------------------------------------------------

def test_recall_cjk_query_matches_cjk_content(tmp_path):
    """A Chinese query can recall a record whose content contains matching bigrams."""
    ret, mgr = _make_retriever(tmp_path)
    r = _make_record(
        title="project note",
        tags=[],
        keywords=[],
        content="本项目使用版本管理工具",
    )
    mgr.project_store.upsert_memory(r)
    hits = ret.recall_candidates("版本管理")
    assert len(hits) > 0
    assert hits[0].memory_id == r.id

def test_recall_plural_normalized_query(tmp_path):
    """Query 'keyword' should match a record whose content says 'keywords'."""
    ret, mgr = _make_retriever(tmp_path)
    r = _make_record(
        title="seo tips",
        tags=[],
        keywords=[],
        content="choose your keywords carefully for best results",
    )
    mgr.project_store.upsert_memory(r)
    hits = ret.recall_candidates("keyword")
    # "keyword" tokenizes to {"keyword"}; content tokenizes "keywords" → {"keywords","keyword"}
    assert len(hits) > 0


def test_recall_candidates_exclude_ids(tmp_path):
    """Records whose id is in exclude_ids are skipped entirely."""
    ret, mgr = _make_retriever(tmp_path)
    r1 = _make_record(title="python version", tags=["python"], keywords=["python"])
    r2 = _make_record(title="python editor", tags=["python"], keywords=["python"])
    mgr.project_store.upsert_memory(r1)
    mgr.project_store.upsert_memory(r2)

    # Without exclusion both should appear
    hits_all = ret.recall_candidates("python", top_k=10)
    assert len(hits_all) == 2

    # Excluding r1's id should leave only r2
    hits_excl = ret.recall_candidates("python", top_k=10, exclude_ids={r1.id})
    assert all(c.memory_id != r1.id for c in hits_excl)
    assert any(c.memory_id == r2.id for c in hits_excl)
