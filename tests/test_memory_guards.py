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
    def test_basic(self, guard):
        assert guard.normalize_key("User Preference") == "user_preference"

    def test_special_chars(self, guard):
        assert guard.normalize_key("my-key.name!") == "my_key_name"

    def test_multiple_spaces(self, guard):
        assert guard.normalize_key("  lots   of   spaces  ") == "lots_of_spaces"

    def test_max_length(self, guard):
        long_key = "a" * 200
        assert len(guard.normalize_key(long_key)) <= 80

    def test_empty_raises(self, guard):
        with pytest.raises(ValueError, match="empty"):
            guard.normalize_key("   ")

    def test_only_special_chars_raises(self, guard):
        with pytest.raises(ValueError, match="empty"):
            guard.normalize_key("!@#$%")

    def test_preserves_numbers(self, guard):
        assert guard.normalize_key("version 3.2") == "version_3_2"

    def test_underscores_collapsed(self, guard):
        assert guard.normalize_key("a___b") == "a_b"

    def test_leading_trailing_underscores_stripped(self, guard):
        assert guard.normalize_key("_hello_") == "hello"


# ---------------------------------------------------------------------------
# validate_title
# ---------------------------------------------------------------------------


class TestValidateTitle:
    def test_basic(self, guard):
        assert guard.validate_title("  My Title  ") == "My Title"

    def test_newline_raises(self, guard):
        with pytest.raises(ValueError, match="single-line"):
            guard.validate_title("line1\nline2")

    def test_carriage_return_raises(self, guard):
        with pytest.raises(ValueError, match="single-line"):
            guard.validate_title("line1\rline2")

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
    def test_private_key(self, guard):
        with pytest.raises(SensitiveMemoryError):
            guard.detect_sensitive("-----BEGIN RSA PRIVATE KEY-----\nxxx")

    def test_bearer_token(self, guard):
        with pytest.raises(SensitiveMemoryError):
            guard.detect_sensitive("Authorization: Bearer eyJhbGciOi.token.here")

    def test_api_key_assignment(self, guard):
        with pytest.raises(SensitiveMemoryError):
            guard.detect_sensitive("api_key = sk-1234567890abcdef")

    def test_password_assignment(self, guard):
        with pytest.raises(SensitiveMemoryError):
            guard.detect_sensitive("password: myS3cretP@ss!")

    def test_github_token(self, guard):
        with pytest.raises(SensitiveMemoryError):
            guard.detect_sensitive("ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij")

    def test_openai_key(self, guard):
        with pytest.raises(SensitiveMemoryError):
            guard.detect_sensitive("sk-abcdefghijklmnopqrstuvwxyz1234567890")

    def test_aws_key(self, guard):
        with pytest.raises(SensitiveMemoryError):
            guard.detect_sensitive("AKIAIOSFODNN7EXAMPLE")

    def test_safe_content_passes(self, guard):
        # Should not raise
        guard.detect_sensitive("The user prefers Python 3.12")
        guard.detect_sensitive("Project uses FastAPI and PostgreSQL")

    def test_word_token_in_context_ok(self, guard):
        # "token" as a concept (not assignment) should be OK
        guard.detect_sensitive("The tokenizer splits text into tokens")

    def test_secret_in_assignment_form(self, guard):
        with pytest.raises(SensitiveMemoryError):
            guard.detect_sensitive("secret=my_super_secret_value")


# ---------------------------------------------------------------------------
# classify_scope
# ---------------------------------------------------------------------------


class TestClassifyScope:
    def test_explicit_user(self, guard):
        assert guard.classify_scope("any_key", [], "user") == "user"

    def test_explicit_project(self, guard):
        assert guard.classify_scope("any_key", [], "project") == "project"

    def test_user_tag(self, guard):
        assert guard.classify_scope("pref", ["user", "lang"], None) == "user"

    def test_user_key_prefix(self, guard):
        assert guard.classify_scope("user_language", [], None) == "user"

    def test_preference_tag(self, guard):
        assert guard.classify_scope("lang", ["preference"], None) == "user"

    def test_profile_tag(self, guard):
        assert guard.classify_scope("name", ["profile"], None) == "user"

    def test_personal_tag(self, guard):
        assert guard.classify_scope("style", ["personal"], None) == "user"

    def test_default_project(self, guard):
        assert guard.classify_scope("build_cmd", ["project"], None) == "project"

    def test_no_tags_default_project(self, guard):
        assert guard.classify_scope("something", [], None) == "project"


# ---------------------------------------------------------------------------
# classify_type
# ---------------------------------------------------------------------------


class TestClassifyType:
    def test_explicit_type(self, guard):
        assert guard.classify_type("key", [], "workflow") == "workflow"

    def test_preference_tag(self, guard):
        assert guard.classify_type("key", ["preference"], None) == "preference"

    def test_workflow_tag(self, guard):
        assert guard.classify_type("key", ["workflow"], None) == "workflow"

    def test_decision_tag(self, guard):
        assert guard.classify_type("key", ["decision"], None) == "decision"

    def test_constraint_tag(self, guard):
        assert guard.classify_type("key", ["constraint"], None) == "constraint"

    def test_default_note(self, guard):
        assert guard.classify_type("key", ["something"], None) == "note"

    def test_no_tags_default_note(self, guard):
        assert guard.classify_type("key", [], None) == "note"


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
