"""Dynamic recall: score memory records against query; return top-k RecallCandidates."""

import logging
import re
import traceback
from datetime import datetime, timezone
from typing import List, Optional, Set, TYPE_CHECKING

from .models import MemoryRecord, RecallCandidate

if TYPE_CHECKING:
    from .manager import MemoryManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Text processing constants
# ---------------------------------------------------------------------------

_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been",
    "have", "has", "had", "do", "does", "did", "will", "would",
    "can", "could", "should", "may", "might", "shall", "to", "of",
    "in", "for", "on", "with", "at", "by", "from", "as", "into",
    "and", "or", "but", "not", "no", "so", "if", "then", "that",
    "this", "it", "its", "i", "you", "we", "they", "my", "your",
})

# Split on whitespace, path separators, punctuation, and common operators.
_SPLIT_RE = re.compile(r'[\s/\\_\-.,:;|@#$%^&*()+=\[\]{}<>?!\'\"]+')

# CJK Unified Ideographs + Extension A + Compatibility Ideographs + Kana + Hangul
_CJK_RE = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]')

# Insert a space at Latin↔CJK boundaries so the Latin tokenizer splits them cleanly.
_CJK_BOUNDARY_RE = re.compile(
    r'(?<=[a-zA-Z\d])(?=[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af])'
    r'|(?<=[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af])(?=[a-zA-Z\d])'
)

# Characters of content used for content-path indexing.
_CONTENT_SNIPPET_LEN = 500


# ---------------------------------------------------------------------------
# Token normalization helpers
# ---------------------------------------------------------------------------

def _normalize_token(token: str) -> str:
    """Light normalization applied to each Latin token.

    Two rules (both additive — the original token is retained alongside):

    1. Version prefix: strip leading 'v' from purely numeric version tokens.
       ``v3`` → ``3``, ``v42`` → ``42``.  ``v3.11`` is already split by
       ``_SPLIT_RE`` before reaching here, so only the single-segment form
       arrives (e.g. ``v3`` or ``11``).

    2. Simple English plural: strip trailing ``s`` from tokens longer than
       3 chars that do not end in ``ss`` (avoids mangling ``class``,
       ``success``).  This handles the common ``keywords`` → ``keyword``
       case.  We intentionally keep this minimal to avoid false stems.
    """
    # Rule 1: version prefix (v3, v42, …)
    if len(token) >= 2 and token[0] == "v" and token[1:].isdigit():
        return token[1:]
    # Rule 2: trailing -s (not -ss), length guard avoids mangling short words
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _cjk_bigrams(text: str) -> set:
    """Extract overlapping bigrams from consecutive CJK characters in *text*.

    A single isolated CJK character (no neighbor) is added as-is so that
    single-character queries still match.
    """
    chars = [c for c in text if _CJK_RE.match(c)]
    result: set = set()
    if len(chars) == 1:
        result.add(chars[0])
    for i in range(len(chars) - 1):
        result.add(chars[i] + chars[i + 1])
    return result


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _days_since(iso_str: str) -> float:
    """Days since an ISO 8601 date string (treats naive datetimes as UTC)."""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400)
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Retriever
# ---------------------------------------------------------------------------

class MemoryRetriever:
    """Score memory records against a user query and return top-k candidates.

    Four index paths per record:

    - **tag path**     — exact overlap between query tokens and record tags
    - **title path**   — Jaccard similarity between query tokens and title tokens
    - **keyword path** — fraction of query tokens found in record keywords
    - **content path** — fraction of query tokens found in first 500 chars of content

    Plus: filepath context hint, recency bonus, stale penalty.

    Short-query dampening: when the query has ≤ 2 meaningful tokens the tag
    weight is reduced (4.0 → 2.5 → 1.5) to prevent a single matching tag from
    dominating the ranking.
    """

    def __init__(self, manager: "MemoryManager"):
        self._manager = manager
        self._recall_count: int = 0   # session-scoped hit counter for /memory status
        self._error_count: int = 0    # total recall errors this session
        self._last_error: str = ""    # one-line summary of the most recent error

    def tokenize(self, text: str) -> set:
        """Tokenize *text* with CJK bigram support and light normalization.

        Latin/ASCII path
        ~~~~~~~~~~~~~~~~
        Split on whitespace and punctuation (``_SPLIT_RE``), lowercase, remove
        stopwords and single-character tokens.  For each surviving token both
        the raw form and its ``_normalize_token()`` result are added so that
        exact matches and normalized matches both fire independently.

        CJK path
        ~~~~~~~~
        Extract overlapping bigrams from consecutive CJK characters (Chinese,
        Japanese kana, Korean Hangul).  A solitary CJK character with no
        neighbor is kept as a unigram so single-character queries work.

        The two paths are unioned; a mixed string like ``"Python版本"`` yields
        tokens from both paths.
        """
        tokens: set = set()

        # Insert spaces at Latin↔CJK boundaries before splitting, so that a
        # mixed string like "Python版本" splits into ["python", "版本"] rather
        # than staying as one token.
        prepped = _CJK_BOUNDARY_RE.sub(" ", text)

        # --- Latin/ASCII path ---
        for raw in _SPLIT_RE.split(prepped.lower()):
            if not raw or len(raw) <= 1 or raw in _STOPWORDS:
                continue
            tokens.add(raw)
            norm = _normalize_token(raw)
            if norm != raw:
                tokens.add(norm)

        # --- CJK bigram path (runs on original text to preserve char sequence) ---
        tokens.update(_cjk_bigrams(text))

        return tokens

    def score(
        self,
        record: MemoryRecord,
        query_tokens: set,
        hint_tokens: set,
    ) -> tuple:
        """Return ``(total_score, reasons, semantic_signal)`` for a record.

        Factors
        -------
        tag_w × exact_tag   tag_w=4.0 (|q|≥3) / 2.5 (|q|=2) / 1.5 (|q|=1)
        3.0  × title_j      Jaccard similarity, query vs title tokens
        2.0  × kw_match     fraction of query tokens found in *tokenized* keywords
                            (so a keyword like ``"agent.py"`` matches a query
                            token ``"agent"`` — symmetric with the title path)
        1.0  × content_m    fraction of query tokens in content snippet (500 chars)
        2.0  × fp_match     context-hint tokens intersect tokenized keywords
        1.0  × recency      1 / (1 + days_since/30)
        -2.0 × stale        penalty when record not updated for >90 days

        ``semantic_signal`` = sum of the five path hits (used as gating filter;
        a record with signal == 0 is excluded from results entirely).
        """
        tags = {t.lower() for t in record.tags}
        title_tokens = self.tokenize(record.title)
        content_tokens = self.tokenize(record.content[:_CONTENT_SNIPPET_LEN])

        # Tokenize keywords once and reuse for both kw_match and fp_match.
        # Raw keyword strings stay in the set so an exact stored keyword
        # (e.g. ``"fastapi"``) still matches a query token of the same form,
        # while sub-tokens of compound keywords (``"agent.py"`` →
        # ``{"agent", "py"}``) also become matchable.
        kw_tokens: set = {k.lower() for k in record.keywords}
        for k in record.keywords:
            kw_tokens.update(self.tokenize(k))

        q_len = len(query_tokens)
        tag_w = 4.0 if q_len >= 3 else (2.5 if q_len == 2 else 1.5)

        exact_tag  = len(query_tokens & tags) / max(q_len, 1)
        title_j    = _jaccard(query_tokens, title_tokens)
        kw_match   = len(query_tokens & kw_tokens) / max(q_len, 1)
        content_m  = len(query_tokens & content_tokens) / max(q_len, 1)
        fp_match   = 1.0 if (hint_tokens & kw_tokens) else 0.0
        recency    = 1.0 / (1.0 + _days_since(record.updated_at) / 30)
        stale      = 1.0 if _days_since(record.updated_at) > 90 else 0.0

        total = (tag_w * exact_tag + 3.0 * title_j + 2.0 * kw_match
                 + 1.0 * content_m + 2.0 * fp_match + 1.0 * recency
                 - 2.0 * stale)

        reasons: list = []
        if exact_tag > 0:  reasons.append("tag_match")
        if title_j > 0:    reasons.append("title_overlap")
        if kw_match > 0:   reasons.append("keyword_match")
        if content_m > 0:  reasons.append("content_match")
        if fp_match:        reasons.append("filepath_hint")
        if stale:           reasons.append("stale_penalty")
        if record.confidence == "explicit_user":
            reasons.append("explicit")

        semantic = exact_tag + title_j + kw_match + content_m + fp_match
        return total, reasons, semantic

    def recall_candidates(
        self,
        query: str,
        context_hints: Optional[List[str]] = None,
        top_k: int = 5,
        exclude_ids: Optional[Set[str]] = None,
    ) -> List[RecallCandidate]:
        """Score all memory records against query + hints; return top-k candidates.

        Steps:
        1. Tokenize query and context_hints
        2. Load all memory records (skip IDs in exclude_ids — already in stable block)
        3. Score every record; keep those with semantic > 0
        4. Sort descending; take top_k
        """
        if not query.strip():
            return []
        try:
            query_tokens = self.tokenize(query)
            hint_tokens = self.tokenize(" ".join(context_hints or []))

            records = self._manager.get_all_entries()
            if not records:
                return []

            _excluded = exclude_ids or set()
            scored = []
            for record in records:
                if record.id in _excluded:
                    continue
                s, reasons, semantic = self.score(record, query_tokens, hint_tokens)
                if semantic > 0:
                    scored.append((s, reasons, record))
            if not scored:
                return []

            scored.sort(key=lambda x: -x[0])
            hits = []
            for s, reasons, record in scored[:top_k]:
                excerpt = record.content[:200]
                hits.append(RecallCandidate(
                    memory_id=record.id,
                    scope=record.scope,
                    type=record.type,
                    title=record.title,
                    excerpt=excerpt,
                    score=s,
                    reasons=reasons,
                ))
            self._recall_count += len(hits)
            return hits
        except Exception as exc:
            self._error_count += 1
            self._last_error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "recall_candidates failed (error #%d): %s\n%s",
                self._error_count,
                self._last_error,
                traceback.format_exc().rstrip(),
            )
            return []
