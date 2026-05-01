"""Dynamic recall: score memory records against query; return top-k RecallCandidates."""

import logging
import re
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, TYPE_CHECKING

# `jieba` is deferred (P0.5): importing this module is on the path of
# constructing ``Agentao(...)``, but jieba's ~30 ms dict-init cost (and the
# wheel itself) only materializes on the recall scoring hot path. Embedded
# hosts that swap MemoryStore typically never reach it.
#
# A PEP 562 ``__getattr__`` exposes ``agentao.memory.retriever.jieba`` for
# tests / patching without loading the module at import time.
if TYPE_CHECKING:
    import jieba as _jieba_t


def __getattr__(name: str):
    if name == "jieba":
        import jieba as _jieba_module

        return _jieba_module
    raise AttributeError(f"module 'agentao.memory.retriever' has no attribute {name!r}")


from agentao.logging_utils import capture_third_party_output

from .models import MemoryRecord, RecallCandidate

if TYPE_CHECKING:
    from .manager import MemoryManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# jieba lazy initialization
# ---------------------------------------------------------------------------

_JIEBA_INITIALIZED = False
_USERDICT_PATH = Path.home() / ".agentao" / "userdict.txt"


def _initialize_jieba_with_logging() -> None:
    """Run jieba initialization without leaking its progress messages to the terminal."""
    import jieba

    capture_third_party_output(
        runner=jieba.initialize,
        source_logger_names=("jieba",),
        target_logger=logger,
        target_level=logging.DEBUG,
        prefix="jieba: ",
    )


def _ensure_jieba_ready() -> None:
    """Lazy-init jieba: load user dict on first call, idempotent.

    jieba.initialize() forces dict load up-front (~1s) instead of paying the
    cost on first cut. The optional ``~/.agentao/userdict.txt`` file lets users
    add project names, technical terms, and proper nouns that the default
    dictionary doesn't know.
    """
    import jieba

    global _JIEBA_INITIALIZED
    if _JIEBA_INITIALIZED:
        return
    _initialize_jieba_with_logging()
    if _USERDICT_PATH.exists():
        try:
            jieba.load_userdict(str(_USERDICT_PATH))
        except Exception as exc:
            logger.warning("Failed to load jieba userdict at %s: %s", _USERDICT_PATH, exc)
    _JIEBA_INITIALIZED = True

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


def _cjk_segment(text: str) -> set:
    """Segment CJK substrings with jieba; return set of multi-char CJK words.

    Runs jieba only on CJK character runs (skipping Latin/ASCII regions which
    are handled by the Latin path). A word is kept if its first character is
    CJK AND its length >= 2 -- single CJK characters are intentionally dropped
    (mirrors the Latin path's len > 1 filter, since single Chinese chars are
    too ambiguous to carry useful retrieval signal and would flood the inverted
    index with high-frequency function words like "的"/"了").
    """
    import jieba

    _ensure_jieba_ready()
    result: set = set()
    for match in re.finditer(
        r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]+',
        text,
    ):
        for word in jieba.lcut(match.group(), cut_all=False):
            if len(word) >= 2 and _CJK_RE.match(word):
                result.add(word)
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
# Inverted-index data structures
# ---------------------------------------------------------------------------

@dataclass
class _RecordTokenBundle:
    """Pre-tokenized fields for a single memory record, cached in the index."""
    title_tokens: Set[str] = field(default_factory=set)
    content_tokens: Set[str] = field(default_factory=set)
    kw_tokens: Set[str] = field(default_factory=set)
    tags: Set[str] = field(default_factory=set)
    all_tokens: Set[str] = field(default_factory=set)  # union, for inverted index


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

        # Inverted-index state — invalidated when manager.write_version advances.
        self._index_version: int = -1
        self._inverted: Dict[str, Set[str]] = {}              # token -> set of record IDs
        self._bundles_by_id: Dict[str, _RecordTokenBundle] = {}
        self._records_by_id: Dict[str, MemoryRecord] = {}

    # ------------------------------------------------------------------
    # Index management
    # ------------------------------------------------------------------

    def _build_bundle(self, record: MemoryRecord) -> _RecordTokenBundle:
        """Compute all token sets for a record (mirrors score()'s tokenization)."""
        title_tokens = self.tokenize(record.title)
        content_tokens = self.tokenize(record.content[:_CONTENT_SNIPPET_LEN])
        kw_tokens: Set[str] = {k.lower() for k in record.keywords}
        for k in record.keywords:
            kw_tokens.update(self.tokenize(k))
        tags = {t.lower() for t in record.tags}
        all_tokens = title_tokens | content_tokens | kw_tokens | tags
        return _RecordTokenBundle(
            title_tokens=title_tokens,
            content_tokens=content_tokens,
            kw_tokens=kw_tokens,
            tags=tags,
            all_tokens=all_tokens,
        )

    def _rebuild_index_if_stale(self) -> None:
        """Rebuild inverted index whenever ``manager.write_version`` advances.

        Cheap O(1) version check on hot path; full rebuild only when memories
        have been added/updated/deleted since the last build.
        """
        current = self._manager.write_version
        if current == self._index_version and self._records_by_id:
            return
        records = self._manager.get_all_entries()
        self._inverted.clear()
        self._bundles_by_id.clear()
        self._records_by_id.clear()
        for r in records:
            bundle = self._build_bundle(r)
            self._bundles_by_id[r.id] = bundle
            self._records_by_id[r.id] = r
            for tok in bundle.all_tokens:
                self._inverted.setdefault(tok, set()).add(r.id)
        self._index_version = current

    def tokenize(self, text: str) -> set:
        """Tokenize *text* with jieba CJK segmentation and light normalization.

        Latin/ASCII path
        ~~~~~~~~~~~~~~~~
        Split on whitespace and punctuation (``_SPLIT_RE``), lowercase, remove
        stopwords and single-character tokens.  For each surviving token both
        the raw form and its ``_normalize_token()`` result are added so that
        exact matches and normalized matches both fire independently.

        CJK path
        ~~~~~~~~
        Run jieba word segmentation on each run of consecutive CJK characters.
        Single-character CJK words are filtered out (same len > 1 rule as the
        Latin path) to avoid flooding the index with high-frequency function
        words.  Custom domain terms can be added via ``~/.agentao/userdict.txt``.

        The two paths are unioned; a mixed string like ``"Python版本管理"`` yields
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

        # --- CJK path (jieba segmentation on CJK runs in the original text) ---
        tokens.update(_cjk_segment(text))

        return tokens

    def score(
        self,
        record: MemoryRecord,
        query_tokens: set,
        hint_tokens: set,
        bundle: Optional[_RecordTokenBundle] = None,
    ) -> tuple:
        """Return ``(total_score, reasons, semantic_signal)`` for a record.

        When ``bundle`` is provided (the fast path used by ``recall_candidates``)
        the per-record token sets are reused from the inverted-index cache;
        otherwise they are computed on the fly. Direct callers (tests) can omit
        ``bundle`` and rely on lazy tokenization.

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
        if bundle is None:
            bundle = self._build_bundle(record)
        tags = bundle.tags
        title_tokens = bundle.title_tokens
        content_tokens = bundle.content_tokens
        kw_tokens = bundle.kw_tokens

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
        """Score memory records against query + hints; return top-k candidates.

        Steps:
        1. Tokenize query and context_hints
        2. Rebuild inverted index if memory store has changed since last call
        3. Look up candidate record IDs via the inverted index (token union)
        4. Score only the candidate subset; keep those with semantic > 0
        5. Sort descending; take top_k
        """
        if not query.strip():
            return []
        try:
            query_tokens = self.tokenize(query)
            hint_tokens = self.tokenize(" ".join(context_hints or []))
            if not query_tokens and not hint_tokens:
                return []

            self._rebuild_index_if_stale()
            if not self._records_by_id:
                return []

            # Candidate set = union of records hit by any query/hint token.
            candidate_ids: Set[str] = set()
            for tok in query_tokens | hint_tokens:
                ids = self._inverted.get(tok)
                if ids:
                    candidate_ids |= ids
            if not candidate_ids:
                return []

            _excluded = exclude_ids or set()
            scored = []
            for rid in candidate_ids:
                if rid in _excluded:
                    continue
                record = self._records_by_id.get(rid)
                if record is None:
                    continue
                bundle = self._bundles_by_id.get(rid)
                s, reasons, semantic = self.score(
                    record, query_tokens, hint_tokens, bundle=bundle
                )
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
