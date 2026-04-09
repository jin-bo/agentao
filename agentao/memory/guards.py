"""Guard pipeline: validation, sensitive-data detection, classification for memory writes."""

from __future__ import annotations

import re
from typing import List, Optional


class SensitiveMemoryError(ValueError):
    """Raised when memory content contains secrets or credentials."""
    pass


_STOPWORDS = frozenset({
    'a', 'an', 'the', 'is', 'in', 'of', 'to', 'and', 'or', 'for',
    'with', 'on', 'at', 'by', 'be', 'it', 'as', 'are', 'was', 'were',
})


class MemoryGuard:
    """Validates, classifies, and sanitizes memory writes."""

    SECRET_PATTERNS = [
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        re.compile(r"\bBearer\s+[A-Za-z0-9._\-]+"),
        re.compile(r"(?:api[_-]?key|password|token|secret)\s*[:=]\s*\S+", re.I),
        re.compile(r"ghp_[A-Za-z0-9]{36}"),           # GitHub personal access token
        re.compile(r"sk-[A-Za-z0-9]{20,}"),            # OpenAI-style key
        re.compile(r"AKIA[0-9A-Z]{16}"),               # AWS access key
    ]

    # ------------------------------------------------------------------
    # Key normalization
    # ------------------------------------------------------------------

    def normalize_key(self, key: str) -> str:
        """Normalize a memory key to snake_case for deduplication.

        Raises ValueError if the result is empty.
        """
        key = key.strip().lower()
        key = re.sub(r"\s+", "_", key)
        key = re.sub(r"[^a-z0-9_]+", "_", key)
        key = re.sub(r"_+", "_", key).strip("_")
        if not key:
            raise ValueError("Memory key is empty after normalization")
        return key[:80]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_title(self, title: str) -> str:
        """Validate and clean the memory title (single-line, max 120 chars).

        Raises ValueError if title contains newlines.
        """
        if "\n" in title or "\r" in title:
            raise ValueError("Memory key/title must be single-line")
        return title.strip()[:120]

    def validate_content(self, content: str) -> str:
        """Validate memory content (non-empty, max 4000 chars).

        Raises ValueError if content is empty or too long.
        """
        content = content.strip()
        if not content:
            raise ValueError("Memory content is empty")
        if len(content) > 4000:
            raise ValueError(f"Memory content exceeds max length ({len(content)} > 4000)")
        return content

    # ------------------------------------------------------------------
    # Sensitive-data detection
    # ------------------------------------------------------------------

    def detect_sensitive(self, content: str) -> None:
        """Raise SensitiveMemoryError if content matches secret patterns."""
        for pat in self.SECRET_PATTERNS:
            if pat.search(content):
                raise SensitiveMemoryError(
                    "Refused to save: content appears to contain sensitive credentials"
                )

    # ------------------------------------------------------------------
    # Scope and type classification
    # ------------------------------------------------------------------

    def classify_scope(self, key: str, tags: List[str], scope: Optional[str] = None) -> str:
        """Determine whether a memory belongs to 'user' or 'project' scope."""
        if scope in ("user", "project"):
            return scope
        tags_lower = {t.lower() for t in tags}
        if "user" in tags_lower or key.startswith("user_"):
            return "user"
        if {"preference", "profile", "personal"} & tags_lower:
            return "user"
        return "project"

    def classify_type(self, key: str, tags: List[str], type_: Optional[str] = None) -> str:
        """Determine the memory type from tags or explicit type."""
        if type_:
            return type_
        tags_lower = {t.lower() for t in tags}
        if "preference" in tags_lower:
            return "preference"
        if "workflow" in tags_lower:
            return "workflow"
        if "decision" in tags_lower:
            return "decision"
        if "constraint" in tags_lower:
            return "constraint"
        if "profile" in tags_lower:
            return "profile"
        if "project_fact" in tags_lower:
            return "project_fact"
        return "note"

    # ------------------------------------------------------------------
    # Keyword extraction
    # ------------------------------------------------------------------

    def extract_keywords(self, title: str, tags: List[str], content: str) -> List[str]:
        """Extract searchable keywords from title, tags, and content."""
        kws: set = set()
        # From title
        kws.update(w.lower() for w in re.findall(r'\w+', title) if len(w) > 2)
        # From tags
        kws.update(t.lower() for t in tags)
        # From content: file paths
        kws.update(re.findall(r'[\w/]+\.\w{2,4}', content))
        # From content: identifiers
        kws.update(w.lower() for w in re.findall(r'\b[a-z_]{3,}\b', content)[:30])
        kws -= _STOPWORDS
        return sorted(kws)[:20]
