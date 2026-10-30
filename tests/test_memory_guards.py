"""Tests for MemoryGuard: normalization, validation, sensitive detection, classification."""

import pytest

from agentao.memory.guards import MemoryGuard, SensitiveMemoryError


@pytest.fixture
def guard():
    return MemoryGuard()


# ---------------------------------------------------------------------------
# normalize_key
# ---------------------------------------------------------------------------


class TestNormalizeKey:
    @pytest.mark.parametrize("key, normalized", [
        pytest.param("User Preference", "user_preference", id="basic"),
        pytest.param("my-key.name!", "my_key_name", id="special-chars"),
        pytest.param("  lots   of   spaces  ", "lots_of_spaces", id="multiple-spaces"),
        pytest.param("version 3.2", "version_3_2", id="preserves-numbers"),
        pytest.param("a___b", "a_b", id="underscores-collapsed"),
        pytest.param("_hello_", "hello", id="leading-trailing-underscores-stripped"),
    ])
    def test_a_key_is_normalized(self, guard, key, normalized):
        assert guard.normalize_key(key) == normalized

    def test_max_length(self, guard):
        long_key = "a" * 200
        assert len(guard.normalize_key(long_key)) <= 80

    @pytest.mark.parametrize("key", [
        pytest.param("   ", id="blank"),
        pytest.param("!@#$%", id="only-special-chars"),
    ])
    def test_a_key_that_normalizes_to_nothing_raises(self, guard, key):
        with pytest.raises(ValueError, match="empty"):
            guard.normalize_key(key)


# ---------------------------------------------------------------------------
# validate_title
# ---------------------------------------------------------------------------


class TestValidateTitle:
    def test_basic(self, guard):
        assert guard.validate_title("  My Title  ") == "My Title"

    @pytest.mark.parametrize("title", [
        pytest.param("line1\nline2", id="newline"),
        pytest.param("line1\rline2", id="carriage-return"),
    ])
    def test_a_line_break_raises(self, guard, title):
        with pytest.raises(ValueError, match="single-line"):
            guard.validate_title(title)

    def test_max_length(self, guard):
        long_title = "x" * 200
        assert len(guard.validate_title(long_title)) <= 120


# ---------------------------------------------------------------------------
# validate_content
# ---------------------------------------------------------------------------


class TestValidateContent:
    def test_basic(self, guard):
        assert guard.validate_content("  hello  ") == "hello"

    def test_empty_raises(self, guard):
        with pytest.raises(ValueError, match="empty"):
            guard.validate_content("   ")

    def test_too_long_raises(self, guard):
        with pytest.raises(ValueError, match="max length"):
            guard.validate_content("x" * 5000)

    def test_at_limit(self, guard):
        content = "x" * 4000
        assert guard.validate_content(content) == content


# ---------------------------------------------------------------------------
# detect_sensitive
# ---------------------------------------------------------------------------


class TestDetectSensitive:
    @pytest.mark.parametrize("content", [
        pytest.param("-----BEGIN RSA PRIVATE KEY-----\nxxx", id="private-key"),
        pytest.param("Authorization: Bearer eyJhbGciOi.token.here", id="bearer-token"),
        pytest.param("api_key = sk-1234567890abcdef", id="api-key-assignment"),
        pytest.param("password: myS3cretP@ss!", id="password-assignment"),
        pytest.param("ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij", id="github-token"),
        pytest.param("sk-abcdefghijklmnopqrstuvwxyz1234567890", id="openai-key"),
        pytest.param("AKIAIOSFODNN7EXAMPLE", id="aws-key"),
        pytest.param("secret=my_super_secret_value", id="secret-assignment"),
    ])
    def test_sensitive_content_is_refused(self, guard, content):
        with pytest.raises(SensitiveMemoryError):
            guard.detect_sensitive(content)

    def test_safe_content_passes(self, guard):
        # Should not raise
        guard.detect_sensitive("The user prefers Python 3.12")
        guard.detect_sensitive("Project uses FastAPI and PostgreSQL")

    def test_word_token_in_context_ok(self, guard):
        # "token" as a concept (not assignment) should be OK
        guard.detect_sensitive("The tokenizer splits text into tokens")


# ---------------------------------------------------------------------------
# classify_scope
# ---------------------------------------------------------------------------


class TestClassifyScope:
    @pytest.mark.parametrize("key, tags, explicit, scope", [
        pytest.param("any_key", [], "user", "user", id="explicit-user"),
        pytest.param("any_key", [], "project", "project", id="explicit-project"),
        pytest.param("pref", ["user", "lang"], None, "user", id="user-tag"),
        pytest.param("user_language", [], None, "user", id="user-key-prefix"),
        pytest.param("lang", ["preference"], None, "user", id="preference-tag"),
        pytest.param("name", ["profile"], None, "user", id="profile-tag"),
        pytest.param("style", ["personal"], None, "user", id="personal-tag"),
        pytest.param("build_cmd", ["project"], None, "project", id="default-project"),
        pytest.param("something", [], None, "project", id="no-tags-default-project"),
    ])
    def test_a_scope_is_classified(self, guard, key, tags, explicit, scope):
        assert guard.classify_scope(key, tags, explicit) == scope


# ---------------------------------------------------------------------------
# classify_type
# ---------------------------------------------------------------------------


class TestClassifyType:
    @pytest.mark.parametrize("tags, explicit, type_", [
        pytest.param([], "workflow", "workflow", id="explicit-type"),
        pytest.param(["preference"], None, "preference", id="preference-tag"),
        pytest.param(["workflow"], None, "workflow", id="workflow-tag"),
        pytest.param(["decision"], None, "decision", id="decision-tag"),
        pytest.param(["constraint"], None, "constraint", id="constraint-tag"),
        pytest.param(["something"], None, "note", id="default-note"),
        pytest.param([], None, "note", id="no-tags-default-note"),
    ])
    def test_a_type_is_classified(self, guard, tags, explicit, type_):
        assert guard.classify_type("key", tags, explicit) == type_


# ---------------------------------------------------------------------------
# extract_keywords
# ---------------------------------------------------------------------------


class TestExtractKeywords:
    def test_from_title(self, guard):
        kws = guard.extract_keywords("FastAPI Setup", [], "content")
        assert "fastapi" in kws
        assert "setup" in kws

    def test_from_tags(self, guard):
        kws = guard.extract_keywords("title", ["python", "backend"], "content")
        assert "python" in kws
        assert "backend" in kws

    def test_from_file_paths(self, guard):
        kws = guard.extract_keywords("title", [], "Check agent.py for details")
        assert "agent.py" in kws

    def test_max_20(self, guard):
        content = " ".join(f"keyword_{i}" for i in range(50))
        kws = guard.extract_keywords("title", [], content)
        assert len(kws) <= 20

    def test_stopwords_removed(self, guard):
        kws = guard.extract_keywords("the quick fox", [], "content")
        assert "the" not in kws


# ---------------------------------------------------------------------------
# Integration: guard pipeline via MemoryManager
# ---------------------------------------------------------------------------


class TestGuardIntegration:
    def test_sensitive_content_refused_via_tool(self, tmp_path):
        from tests.support.memory import make_memory_manager
        mgr = make_memory_manager(tmp_path)
        result = mgr.save_from_tool("my_key", "api_key = sk-1234567890abcdefghij", [])
        assert "refused" in result.lower() or "sensitive" in result.lower()
        assert mgr.get_all_entries() == []

    def test_empty_content_refused(self, tmp_path):
        from tests.support.memory import make_memory_manager
        mgr = make_memory_manager(tmp_path)
        result = mgr.save_from_tool("my_key", "   ", [])
        assert "error" in result.lower()

    def test_multiline_key_refused(self, tmp_path):
        from tests.support.memory import make_memory_manager
        mgr = make_memory_manager(tmp_path)
        result = mgr.save_from_tool("line1\nline2", "value", [])
        assert "error" in result.lower()

    def test_scope_explicit_via_tool(self, tmp_path):
        from tests.support.memory import make_memory_manager
        mgr = make_memory_manager(tmp_path, with_user=True)
        mgr.save_from_tool("my_setting", "value", [], scope="user")
        user_entries = mgr.get_all_entries(scope="user")
        assert any(e.title == "my_setting" for e in user_entries)

    def test_type_explicit_via_tool(self, tmp_path):
        from tests.support.memory import make_memory_manager
        mgr = make_memory_manager(tmp_path)
        mgr.save_from_tool("my_workflow", "run tests first", [], type="workflow")
        entries = mgr.get_all_entries()
        assert entries[0].type == "workflow"

    def test_key_normalized_consistently(self, tmp_path):
        from tests.support.memory import make_memory_manager
        mgr = make_memory_manager(tmp_path)
        mgr.save_from_tool("My Key Name", "v1", [])
        mgr.save_from_tool("my_key_name", "v2", [])
        # Should be the same entry (upserted)
        entries = mgr.get_all_entries()
        assert len(entries) == 1
        assert entries[0].content == "v2"
