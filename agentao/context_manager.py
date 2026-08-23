"""Context window management: compression, summarization, and memory recall."""

import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    import tiktoken as _tiktoken
    _TIKTOKEN_AVAILABLE = True
except ImportError:
    _tiktoken = None
    _TIKTOKEN_AVAILABLE = False

# Switch to fast len/4 approximation for very large strings (from gemini-cli)
_FAST_PATH_CHARS = 100_000


def _get_tiktoken_encoding(model: str):
    """Return tiktoken Encoding for model, or None if unsupported/unavailable.

    Mapping:
      gpt-5* / gpt-4o* / o1* / o3*            -> o200k_base
      gpt-4* / gpt-3.5* / claude* / deepseek* -> cl100k_base
      gemini* / unknown                         -> None (CJK heuristic fallback)
    """
    if not _TIKTOKEN_AVAILABLE:
        return None
    m = model.lower()
    try:
        if any(m.startswith(p) for p in ("gpt-5", "gpt-4o", "o1", "o3")):
            return _tiktoken.get_encoding("o200k_base")
        if any(m.startswith(p) for p in ("gpt-4", "gpt-3.5", "claude", "deepseek")):
            return _tiktoken.get_encoding("cl100k_base")
    except Exception:
        pass
    return None


def _heuristic_token_count(text: str) -> int:
    """CJK-aware token estimation (adapted from gemini-cli tokenCalculation.ts).

    Fast path for strings > 100K chars: len/4.
    Otherwise weighted by character class:
      ASCII (0-127):    0.25 tokens/char
      non-ASCII / CJK:  1.3  tokens/char

    The ASCII/CJK split is computed at C speed via ``encode("ascii", "ignore")``
    — which drops every non-ASCII char, so the encoded byte length is exactly
    the ASCII-char count — instead of a Python per-character loop. The weights
    are applied with integer arithmetic (``×100 // 100``) so the result is the
    exact floor of ``ascii×0.25 + cjk×1.3``: deterministic, and free of the
    upward drift a running float sum of the inexact ``1.3`` would accumulate.
    This removes the O(chars) interpreter loop from the no-tiktoken estimation
    fallback (T1.2).
    """
    n = len(text)
    if n > _FAST_PATH_CHARS:
        return n // 4
    ascii_count = len(text.encode("ascii", "ignore"))
    non_ascii = n - ascii_count
    return (ascii_count * 25 + non_ascii * 130) // 100


class ContextManager:
    """Manages context window size, compression, and memory recall."""

    DEFAULT_MAX_TOKENS = 200_000
    COMPRESSION_THRESHOLD = 0.65    # Full LLM compression at 65%
    MICROCOMPACT_THRESHOLD = 0.55   # Cheap tool-result clearing at 55%
    KEEP_RECENT_MESSAGES = 20       # Hard cap on verbatim-kept messages
    CIRCUIT_BREAKER_LIMIT = 3       # Stop auto-compact after N consecutive failures
    MICROCOMPACT_TOOL_LIMIT = 3_000 # Max chars kept from any old tool result in microcompact
    MICROCOMPACT_PRESERVE_RECENT = 5  # Keep the most recent N tool results at full fidelity
    SUMMARY_END_MARKER = "--- END OF CONTEXT SUMMARY ---"  # Closes the summary block

    def __init__(self, llm_client, memory_tool, max_tokens: int = DEFAULT_MAX_TOKENS, memory_manager=None):  # Optional[MemoryManager]
        """Initialize ContextManager.

        Args:
            llm_client: LLMClient instance (borrowed from agent)
            memory_tool: SaveMemoryTool instance (borrowed from agent)
            max_tokens: Maximum context window tokens (default 200K)
        """
        self.llm_client = llm_client
        self.memory_tool = memory_tool
        self.max_tokens = max_tokens
        self.memory_manager = memory_manager

        # Circuit breaker: stop auto-compact after too many consecutive failures
        self._consecutive_compact_failures: int = 0

        # True when the most recent ``_summarize_messages`` call ended without
        # the provider reporting a finish_reason. That call does not go through
        # ``ChatLoopRunner`` — it is issued straight against ``llm_client`` —
        # so the runner's detector never sees it, yet it is the one call whose
        # output *permanently replaces* conversation history. A truncated
        # summary is inherited by every later turn.
        #
        # Recorded here rather than written to the agent directly: this class
        # deliberately holds no agent reference (it borrows ``llm_client`` and
        # ``memory_tool``). The compaction call sites in
        # ``runtime/chat_loop/_compaction.py`` own turn state and fold it in.
        self.last_summary_finish_reason_missing: bool = False

        # True when the most recent ``microcompact_messages`` call actually
        # shortened something. It always returns a fresh list, so identity
        # cannot answer this — and the answer decides whether the Tier-1 token
        # anchor is stale. Dropping the anchor on a no-op pass forces a full
        # re-encode of the whole history every iteration in the 55-65% band,
        # which is exactly when the history is largest.
        self.last_microcompact_mutated: bool = False

        # Stats from the last completed compaction (surfaced via get_usage_stats)
        self._last_compact_stats: Optional[Dict[str, Any]] = None

        # Tier 1: last real prompt_tokens from API response (updated after each LLM call)
        self._last_api_prompt_tokens: Optional[int] = None
        # Length of the message list that produced _last_api_prompt_tokens, so the
        # hot-path threshold check can reuse the real count for the already-sent
        # prefix and only locally estimate messages appended since.
        self._api_anchor_msg_count: Optional[int] = None
        # Tier 3: cached tiktoken encoding; None = CJK-aware heuristic fallback
        self._encoding = _get_tiktoken_encoding(self.llm_client.model)

    # -----------------------------------------------------------------------
    # Token estimation
    # -----------------------------------------------------------------------

    def record_api_usage(self, prompt_tokens: int, message_count: Optional[int] = None) -> None:
        """Store real prompt_tokens from the latest API response (Tier 1).

        ``message_count`` is the length of the message list that produced this
        count (system + history, as sent). When provided it anchors the
        hot-path threshold estimate so subsequent turns reuse the real count
        for the already-sent prefix instead of re-encoding the whole history.
        Omitting it (legacy callers) clears the anchor and forces the full
        local estimate.
        """
        self._last_api_prompt_tokens = prompt_tokens
        self._api_anchor_msg_count = message_count

    def invalidate_token_anchor(self) -> None:
        """Drop the Tier-1 anchor after history is mutated in place.

        Both microcompaction and full compression rewrite the already-sent
        prefix, so the real prompt_tokens no longer describes it; the next API
        response re-establishes the anchor.
        """
        self._last_api_prompt_tokens = None
        self._api_anchor_msg_count = None

    def _threshold_token_estimate(self, messages: List[Dict[str, Any]]) -> int:
        """Token count for hot-path threshold checks.

        Reuses the real prompt_tokens from the last API response (Tier 1) for
        the already-sent prefix and locally estimates only the messages
        appended since, avoiding a full re-encode of the history every turn.
        Falls back to a full local estimate when no fresh anchor is available
        (no API count yet, or right after compaction).

        Note: ``messages[0]`` (the system prompt) lives inside the anchored
        prefix, but it is rebuilt every turn with volatile content (memory
        recall, todos, active skills, timestamp). The anchor therefore charges
        the *previous* turn's system-prompt size for one turn until the next
        API response re-anchors — a bounded, self-healing accuracy trade-off,
        not a correctness bug. Do not "fix" it by trusting the anchor harder.
        """
        anchor = self._last_api_prompt_tokens
        n = self._api_anchor_msg_count
        # Guard the anchor token count: a provider can return a malformed
        # ``usage`` field (null / non-numeric prompt_tokens). bool is excluded
        # explicitly since it is an int subclass. (``n`` comes from ``len()``,
        # so it is always a plain int when set.)
        if (
            not isinstance(anchor, int) or isinstance(anchor, bool)
            or not isinstance(n, int)
            or n > len(messages)
        ):
            return self.estimate_tokens(messages)
        return anchor + sum(self._count_message_tokens(m) for m in messages[n:])

    def count_tokens_in_text(self, text: str) -> int:
        """Count tokens via tiktoken; fall back to CJK-aware heuristic."""
        if self._encoding is not None:
            try:
                return len(self._encoding.encode(text, disallowed_special=()))
            except Exception:
                pass
        return _heuristic_token_count(text)

    def _count_message_tokens(self, msg: Dict[str, Any]) -> int:
        """Return estimated token count for a single message dict."""
        tokens = 0
        content = msg.get("content", "")
        if isinstance(content, str):
            tokens += self.count_tokens_in_text(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    tokens += self.count_tokens_in_text(block.get("text", ""))
        # reasoning_content is truncated to MAX_REASONING_HISTORY_CHARS before storage
        rc = msg.get("reasoning_content")
        if isinstance(rc, str) and rc:
            tokens += self.count_tokens_in_text(rc)
        if "tool_calls" in msg:
            tokens += self.count_tokens_in_text(str(msg["tool_calls"]))
        return tokens

    def estimate_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """Count tokens for a list of messages (local estimate).

        Uses tiktoken when available (cl100k_base / o200k_base per model family);
        falls back to CJK-aware heuristic (ASCII=0.25, non-ASCII=1.3 tok/char).
        Used in hot-path threshold checks. Signature unchanged for compatibility.
        """
        return sum(self._count_message_tokens(m) for m in messages)

    def estimate_tokens_breakdown(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, int]:
        """Per-component token breakdown (always local estimate).

        Returns dict with keys: system, messages, tools, total.
        Only called at reporting time, not in hot-path threshold checks.
        """
        system_tokens = 0
        message_tokens = 0
        for msg in messages:
            count = self._count_message_tokens(msg)
            if msg.get("role") == "system":
                system_tokens += count
            else:
                message_tokens += count
        tools_tokens = 0
        if tools is not None:
            try:
                tools_str = json.dumps(tools)
            except Exception:
                tools_str = str(tools)
            tools_tokens = self.count_tokens_in_text(tools_str)
        total = system_tokens + message_tokens + tools_tokens
        return {
            "system": system_tokens,
            "messages": message_tokens,
            "tools": tools_tokens,
            "total": total,
        }

    # -----------------------------------------------------------------------
    # Threshold checks
    # -----------------------------------------------------------------------

    def needs_compression(
        self, messages: List[Dict[str, Any]], tokens: Optional[int] = None
    ) -> bool:
        """Return True when full LLM compression is needed (>= 65%).

        ``tokens`` lets the caller pass a pre-computed
        :meth:`_threshold_token_estimate` so the same per-iteration estimate
        feeds both this and :meth:`needs_microcompaction` instead of being
        computed twice (T1.3). Omit it to estimate locally.
        """
        est = tokens if tokens is not None else self._threshold_token_estimate(messages)
        return est > self.max_tokens * self.COMPRESSION_THRESHOLD

    def needs_microcompaction(
        self, messages: List[Dict[str, Any]], tokens: Optional[int] = None
    ) -> bool:
        """Return True when cheap tool-result clearing is warranted (55-65%).

        See :meth:`needs_compression` for the ``tokens`` pre-computation hook.
        """
        est = tokens if tokens is not None else self._threshold_token_estimate(messages)
        return (
            est > self.max_tokens * self.MICROCOMPACT_THRESHOLD
            and est <= self.max_tokens * self.COMPRESSION_THRESHOLD
        )

    # -----------------------------------------------------------------------
    # Microcompaction — cheap pass, no LLM call
    # -----------------------------------------------------------------------

    # Fraction of MICROCOMPACT_TOOL_LIMIT kept from the start of old tool results.
    # 20% head (command invoked, initial output) + 80% tail (errors, final results).
    MICROCOMPACT_HEAD_RATIO = 0.2

    # Matches an omission notice this class wrote, so a re-clip can carry the
    # earlier count forward instead of reporting only its own slice.
    _OMISSION_NOTICE = re.compile(r"\[… ([\d,]+) chars [^\]]*?…\]")

    @classmethod
    def _prior_omissions(cls, text: str) -> int:
        """Total already reported as dropped by an earlier clip of ``text``."""
        return sum(
            int(m.group(1).replace(",", ""))
            for m in cls._OMISSION_NOTICE.finditer(text)
        )

    @classmethod
    def _head_tail_clip(cls, text: str, limit: int, note: str = "omitted") -> str:
        """Keep the head + tail of ``text`` and say how much fell out between.

        One copy, because the call sites — microcompaction and the summary
        transcript's failure tier — need the identical shape, and the notice is
        load-bearing at both: without it a reader (or the summarizing model)
        cannot tell a clipped result from a complete one and will quote half a
        command or half a path as fact.

        **The notice is budgeted *inside* ``limit``, which makes this a fixed
        point.** It used to be appended on top, so the output was
        ``limit + len(notice)`` characters — strictly longer than the limit
        that produced it. Everything downstream tests "is this over the limit?",
        so the clip re-selected its own output forever: a second pass cut the
        honest ``197,020 chars omitted`` notice out of the middle and wrote
        ``45 chars omitted`` in its place, and every later pass reported ``40``.
        That also pinned ``microcompact_would_mutate()`` at True and
        ``last_microcompact_mutated`` at True for the whole 55-65% band,
        silently defeating both stand-downs added in #181.

        A re-clip of already-clipped text carries the earlier count forward
        (:meth:`_prior_omissions`), so the figure the summarizer sees is the
        total lost, not the last slice.
        """
        if len(text) <= limit:
            return text
        # ``omitted`` can never exceed ``len(text)``, so a notice sized for that
        # worst case is an upper bound on the real one — which makes the final
        # result at most ``limit`` characters without a second pass.
        reserve = len(f"\n[… {len(text):,} chars {note} …]\n")
        avail = max(1, limit - reserve)
        head = int(avail * cls.MICROCOMPACT_HEAD_RATIO)
        tail = avail - head
        kept = text[:head] + text[len(text) - tail:] if tail else text[:head]
        omitted = (
            len(text) - avail
            + cls._prior_omissions(text)
            - cls._prior_omissions(kept)
        )
        return (
            text[:head]
            + f"\n[… {omitted:,} chars {note} …]\n"
            + (text[len(text) - tail:] if tail else "")
        )

    def _microcompactable_indices(self, messages: List[Dict[str, Any]]) -> set:
        """Indices of tool messages a microcompact pass would actually shorten.

        Factored out so the *predicate* and the *transform* can never disagree:
        callers that must decide before mutating (announcing ``PreCompact``)
        ask :meth:`microcompact_would_mutate`, and the transform below simply
        rewrites this exact index set.
        """
        tool_indices = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
        preserve = set(tool_indices[-self.MICROCOMPACT_PRESERVE_RECENT:])
        return {
            i for i in tool_indices
            if i not in preserve
            and isinstance(messages[i].get("content"), str)
            and len(messages[i]["content"]) > self.MICROCOMPACT_TOOL_LIMIT
        }

    def microcompact_would_mutate(self, messages: List[Dict[str, Any]]) -> bool:
        """True when :meth:`microcompact_messages` would shorten something.

        Cheap (lengths only, no encoding). Callers gate the whole microcompact
        step on this *before* announcing the imminent compaction: once every
        old tool result is already at or under ``MICROCOMPACT_TOOL_LIMIT``,
        every further pass in the 55-65% band is a no-op, and announcing one
        would fork a ``PreCompact`` hook subprocess per iteration and emit a
        ``CONTEXT_COMPRESSED`` reporting ``pre == post``.
        """
        return bool(self._microcompactable_indices(messages))

    def microcompact_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Truncate large tool results without calling the LLM.

        Keeps the most recent MICROCOMPACT_PRESERVE_RECENT tool results at full
        fidelity; older ones exceeding MICROCOMPACT_TOOL_LIMIT are shortened using
        a head+tail strategy: 20% from the start (command context) and 80% from
        the end (errors and final output tend to appear last).
        Returns a new list; does not mutate the original.

        Records whether anything was actually shortened on
        :attr:`last_microcompact_mutated`. The return value cannot carry it —
        a fresh list is always built, so ``result is not messages`` says
        nothing — and callers need it to decide whether the token anchor is
        now stale. Same out-of-band-flag pattern as
        ``last_summary_finish_reason_missing``, and for the same reason: this
        class holds no agent reference, so the call sites fold it in.
        """
        targets = self._microcompactable_indices(messages)

        result = []
        for i, msg in enumerate(messages):
            if i in targets:
                msg = dict(msg)
                msg["content"] = self._head_tail_clip(
                    msg["content"],
                    self.MICROCOMPACT_TOOL_LIMIT,
                    note="omitted by microcompact",
                )
            result.append(msg)

        mutated = len(targets)
        self.last_microcompact_mutated = bool(mutated)
        if mutated:
            try:
                self.llm_client.logger.info(
                    f"Microcompaction: truncated {mutated} large tool result(s)"
                )
            except Exception:
                pass
        return result

    # -----------------------------------------------------------------------
    # Full compression
    # -----------------------------------------------------------------------

    @property
    def compaction_circuit_open(self) -> bool:
        """True once :meth:`compress_messages` has given up on auto-compaction.

        Callers gate on this *before* announcing an imminent compaction
        (``PreCompact``): with the breaker open every attempt returns the
        history unchanged, so a caller that keeps announcing would fork a hook
        subprocess per iteration for a compaction that never happens.
        """
        return self._consecutive_compact_failures >= self.CIRCUIT_BREAKER_LIMIT

    @staticmethod
    def _find_split_index(
        messages: List[Dict[str, Any]], start: int
    ) -> Optional[int]:
        """First safe split point at or after ``start``; ``None`` if there is none.

        A split is unsafe only when it lands **on** a ``role: "tool"`` message:
        tool results are appended contiguously after the assistant message that
        requested them, so cutting anywhere else keeps every result with its
        call. Cutting on one strands it from its ``tool_calls`` and strict APIs
        reject the result.

        A ``user`` boundary is still *preferred* — the kept window then opens on
        a coherent request rather than mid-exchange — but it is no longer
        required. It used to be, and a tail with no user message (routine: 20
        consecutive assistant/tool messages is ~10 tool calls in one turn) made
        compaction a silent permanent no-op.

        Never returns ``len(messages)``: that would leave nothing to keep.
        """
        if start < 0:
            start = 0
        limit = len(messages) - 1
        preferred = None
        fallback = None
        for i in range(start, limit + 1):
            role = messages[i].get("role")
            if role == "tool":
                continue
            if role == "user":
                preferred = i
                break
            if fallback is None:
                fallback = i
        chosen = preferred if preferred is not None else fallback
        # ``to_summarize`` is ``messages[:chosen]``, so index 0 — a legitimate
        # find — would summarize nothing. Spelled out rather than left to
        # truthiness: ``chosen or None`` silently folds "found index 0" into
        # "no split exists", and the caller charges the difference to the
        # circuit breaker.
        if chosen is None or chosen == 0:
            return None
        return chosen

    def compress_messages(
        self,
        messages: List[Dict[str, Any]],
        is_auto: bool = True,
    ) -> List[Dict[str, Any]]:
        """Compress conversation history using partial compaction + structured summarization.

        Algorithm:
          1. Apply microcompact pass (strip oversized tool results cheaply)
          2. Partial compaction: keep last N messages verbatim (never more than
             KEEP_RECENT_MESSAGES or 60% of total, whichever is smaller)
          3. Advance the split point to the first *safe* boundary — any non-tool
             message, preferring a 'user' one (see :meth:`_find_split_index`);
             landing on a ``role: "tool"`` message is what orphans a result
          4. Extract recently read file paths from the to-summarize window
          5. Call LLM with structured 9-section prompt to summarize old messages
          6. Save summary to memory
          7. Build result: [boundary_marker, summary, file_hint?, pinned…, recent…]

        Circuit breaker: after CIRCUIT_BREAKER_LIMIT consecutive failures this
        method returns messages unchanged and logs a warning.

        Args:
            messages: Current conversation messages (without system prompt)
            is_auto: True for threshold-triggered compression, False for manual

        Returns:
            Compressed messages list
        """
        # --- Circuit breaker ------------------------------------------------
        if self.compaction_circuit_open:
            try:
                self.llm_client.logger.warning(
                    f"Compact circuit breaker open "
                    f"({self._consecutive_compact_failures} consecutive failures) — skipping"
                )
            except Exception:
                pass
            return messages

        if len(messages) < 5:
            return messages

        # --- Step 2: partial compaction split -------------------------------
        # Keep at most KEEP_RECENT_MESSAGES, but no more than 60% of total
        keep_count = min(
            self.KEEP_RECENT_MESSAGES,
            max(4, int(len(messages) * 0.60)),
        )
        split_index = self._find_split_index(messages, len(messages) - keep_count)

        if split_index is None:
            # Structural failure, not a summarization failure — but on the auto
            # path it has to count as one, or the caller re-enters every
            # iteration (firing PreCompact hook subprocesses each time) while
            # the context keeps growing, until the API rejects it and the
            # overflow ladder cuts history to the last two messages.
            #
            # Manual ``/compact`` is deliberately exempt: it is user-driven and
            # does not loop, so there is no runaway to arrest — and the breaker
            # it would trip disables *automatic* compaction for the rest of the
            # session with no reset path.
            if is_auto:
                self._consecutive_compact_failures += 1
                tally = f" ({self._consecutive_compact_failures} consecutive failures)"
            else:
                # The manual path does not increment, so reporting the counter
                # here would attribute the auto path's tally — usually 0 — to
                # this failure.
                tally = ""
            try:
                self.llm_client.logger.warning(
                    "Compaction found no safe split point — history has no non-tool "
                    f"boundary in the summarizable range; skipping{tally}"
                )
            except Exception:
                pass
            return messages

        # ``_find_split_index`` never returns 0, so ``to_summarize`` is
        # non-empty by construction — no second emptiness check needed here.
        to_summarize = messages[:split_index]
        # Microcompact only the half that survives — the summarized half is
        # discarded once the summary exists, so clipping it here is pure loss:
        # ``_format_for_summary`` applies its own content-aware budget, which
        # gives a failing command four times what this pass would have left and
        # keeps the tail where its diagnostic is. Running before the split (as
        # this did) cut the same text twice for no gain.
        to_keep = self.microcompact_messages(messages[split_index:])

        # --- Step 3: extract pinned messages --------------------------------
        pinned = [
            m for m in to_summarize
            if isinstance(m.get("content"), str) and m["content"].startswith("[PIN]")
        ]

        # --- Step 4: extract recently read files ----------------------------
        recently_read = self._extract_recently_read_files(to_summarize)

        # --- Step 4b: crystallize from raw user messages --------------------
        # Run *before* summarization so the rule-based extractor sees
        # authentic user text, never the LLM's narration of it. Best-effort —
        # must never break the compaction pipeline.
        if self.memory_manager is not None:
            try:
                self.memory_manager.crystallize_user_messages(to_summarize)
            except Exception:
                pass

        # --- Step 5: LLM summarization --------------------------------------
        pre_tokens = self.estimate_tokens(messages)
        summary = self._summarize_messages(to_summarize)
        if not summary:
            self._consecutive_compact_failures += 1
            return messages  # graceful degradation

        self._consecutive_compact_failures = 0  # reset on success

        # --- Step 6: save session summary to SQLite --------------------------
        if self.memory_manager is not None:
            try:
                self.memory_manager.save_session_summary(
                    summary=summary,
                    tokens_before=pre_tokens,
                    messages_summarized=len(to_summarize),
                )
            except Exception:
                pass

        # --- Step 7: assemble result ----------------------------------------
        boundary_msg = {
            "role": "system",
            "content": (
                f"[Compact Boundary | tokens_before={pre_tokens} | "
                f"messages_summarized={len(to_summarize)} | "
                f"messages_kept={len(to_keep)} | "
                f"timestamp={datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
                f"auto={is_auto}]"
            ),
        }

        summary_msg = {
            "role": "system",
            "content": (
                f"[Conversation Summary]\n{summary}\n"
                f"{self.SUMMARY_END_MARKER}\n"
                "(The above is historical context. Resume from the live messages "
                "below; do not re-execute already-completed work.)"
            ),
        }

        file_hint_msgs: List[Dict[str, Any]] = []
        if recently_read:
            file_list = "\n".join(f"  - {p}" for p in recently_read)
            file_hint_msgs.append({
                "role": "system",
                "content": (
                    "[Files accessed before this summary — re-read if details are needed:\n"
                    f"{file_list}\n]"
                ),
            })

        result = [boundary_msg, summary_msg] + file_hint_msgs + pinned + to_keep

        post_tokens = self.estimate_tokens(result)
        self._last_compact_stats = {
            "timestamp": datetime.now().isoformat(),
            "pre_compact_tokens": pre_tokens,
            "post_compact_tokens": post_tokens,
            "messages_summarized": len(to_summarize),
            "messages_kept": len(to_keep),
            "is_auto": is_auto,
            "recently_read_files": recently_read,
        }
        try:
            self.llm_client.logger.info(
                f"Compression complete: {pre_tokens} → {post_tokens} tokens, "
                f"{len(to_summarize)} messages summarized, {len(to_keep)} kept"
            )
        except Exception:
            pass

        return result

    # -----------------------------------------------------------------------
    # Recently read file extraction
    # -----------------------------------------------------------------------

    def _extract_recently_read_files(self, messages: List[Dict[str, Any]]) -> List[str]:
        """Extract file paths from read_file tool calls in the given messages."""
        seen: set = set()
        result: List[str] = []

        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            tool_calls = msg.get("tool_calls", [])
            if not tool_calls:
                continue
            for tc in tool_calls:
                # Handle both dict (serialized) and object forms
                if isinstance(tc, dict):
                    func = tc.get("function", {})
                    name = func.get("name", "")
                    raw_args = func.get("arguments", "{}")
                else:
                    func = getattr(tc, "function", None)
                    name = getattr(func, "name", "") if func else ""
                    raw_args = getattr(func, "arguments", "{}") if func else "{}"

                if name != "read_file":
                    continue
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    path = args.get("path") or args.get("file_path", "")
                    if path and path not in seen:
                        seen.add(path)
                        result.append(path)
                except Exception:
                    pass

        return result[-10:]  # most recent 10

    # -----------------------------------------------------------------------
    # Structured 9-section summarization
    # -----------------------------------------------------------------------

    # Per-entry ceilings for the summary transcript, in characters.
    #
    # These used to be 200 (tool results) / 500 (messages), with a 1000-char
    # carve-out for a ``_HIGH_FIDELITY_TOOLS`` name list. Measured against the
    # saved sessions in ``.agentao/sessions``: **12% of tool-result content
    # survived**, while the summary prompt below demands error messages
    # "verbatim" and calls Files/Errors "the most important" sections. The
    # prompt was asking for what the input pipeline had already deleted.
    #
    # The name-based carve-out was worse than useless. ``write_file`` and
    # ``replace`` return ``f"Successfully {action} {path}"`` /
    # ``f"Replaced {n} occurrence(s) in {path}"`` (``tools/file_ops.py:260,394``)
    # — confirmation strings bounded by a path length, measured median 114
    # chars. The 1000-char budget was structurally unreachable, and the file
    # content it was meant to preserve lives in the *call arguments*, which
    # ``_format_tool_calls`` now renders. So the tier is gone, replaced by one
    # keyed on content: a result carrying a failure is what the prompt actually
    # wants quoted, and failures are cheap — 23 of 167 measured results, 3% of
    # the bytes.
    _TOOL_RESULT_TRUNCATION = 1_000
    # Strictly above ``MICROCOMPACT_TOOL_LIMIT`` plus the notice line
    # microcompaction leaves behind (~3_050 chars worst case), and that margin
    # is the point, not slack. ``compress_messages`` microcompacts the whole
    # list *before* this runs, so at 3_000 the two head+tail clips used the
    # identical limit and the identical ratio: the second cut landed exactly on
    # the first one's ``[… 200,000 chars omitted by microcompact …]`` notice and
    # replaced it with a claim of 45. The summarizer was told a result lost 45
    # characters when it had lost two hundred thousand — and the summary is what
    # *permanently* replaces the history. Keeping this above the microcompact
    # limit makes an already-clipped result pass through untouched.
    _ERROR_RESULT_TRUNCATION = 4_000
    _MESSAGE_TRUNCATION = 2_000
    # A rehydrated ``[Conversation Summary]`` folded into a new one. Larger than
    # an ordinary message, and exempt from budget eviction in
    # :meth:`_join_within_budget`, because it is the only surviving record of
    # everything before the previous compaction — see :meth:`_clip_carry_summary`.
    _CARRY_SUMMARY_TRUNCATION = 8_000

    # Total ceiling on the assembled transcript, as a fraction of the context
    # window. Raising the per-entry caps without this would be trading one
    # defect for another: nothing bounded the transcript before, so a
    # tool-dense window could overflow the summarization call itself — and a
    # failed summarization increments the circuit breaker, turning a fidelity
    # improvement into a compaction outage. Budget is spent newest-first, so
    # what a long window loses is its oldest end rather than the tail of every
    # single message.
    _SUMMARY_INPUT_BUDGET_RATIO = 0.10
    _SUMMARY_INPUT_BUDGET_FLOOR = 2_000  # tokens

    # A tool result is "high value" when it reports a failure — that is the
    # text ``## 4. Errors and Fixes`` asks to be quoted verbatim.
    #
    # Anchored on *diagnostic shapes* (a traceback header, an exception line at
    # column 0, a non-zero exit, a runner's FAILED/ERROR column) rather than on
    # bare words. A bare-word scan is not the cheap over-approximation it looks
    # like: over-tiering does not cost "a few hundred characters", it costs a
    # 4x share of the transcript budget below, and that budget is spent
    # newest-first — so every mis-tiered success evicts *older* messages
    # wholesale. Measured on this repo, ``traceback|exception|\berror\b|…``
    # matched **169 of 272** source files, i.e. two thirds of ordinary
    # ``read_file`` results (and ``read_file`` is 173KB of the 239KB of tool
    # output measured in the sessions that motivated this change). The shapes
    # below match 9 of 272 while still hitting every one of: python traceback,
    # pytest FAILED/E-lines, ``command not found``, non-zero exit, permission
    # denied, ``No such file or directory``, git ``fatal:``, npm ``ERR!``,
    # ruff/mypy ``Found N error(s)``, ``Connection refused``, go ``exit status``.
    #
    # ``(?i:…)`` scopes case-insensitivity to the literals that need it; the
    # column-0 anchors must stay case-sensitive or every ``    error: ...``
    # docstring line and ``ERROR = "error"`` enum member matches again.
    _FAILURE_MARKERS = re.compile(
        r"Traceback \(most recent call last\)"
        r"|^[A-Za-z_][\w.]*(?:Error|Exception)\s*:"
        r"|^(?i:error|fatal|panic|abort)\s*:"
        r"|^(?:FAILED|ERROR|FAIL|E) "
        r"|ERR!"
        r"|(?i:\bexit (?:code|status) [1-9])"
        r"|(?i:\b(?:permission|access) denied\b)"
        r"|(?i:\bcommand not found\b)"
        r"|(?i:\bno such file or directory\b)"
        r"|(?i:\bconnection (?:refused|reset)\b)"
        r"|(?i:\b\d+ (?:failed|errors?)\b)",
        re.MULTILINE,
    )

    # Tool *invocations* get their own budget, separate from tool results.
    # A result says what came back; only the call says which file was read or
    # which command ran — and the summary prompt asks for exactly that
    # ("Every file examined, created, or modified"). Kept deliberately modest:
    # the per-message worst case is _MAX_TOOL_CALLS_RENDERED × ~300 chars.
    _TOOL_CALL_VALUE_TRUNCATION = 200   # per argument value
    _TOOL_CALL_ARGS_TRUNCATION = 300    # per call, after joining values
    _MAX_TOOL_CALLS_RENDERED = 8        # per assistant message

    _SUMMARIZE_SYSTEM_PROMPT = (
        "You are a conversation summarization assistant. Your task is to produce a "
        "detailed, structured summary of the conversation history provided below.\n\n"
        "CRITICAL: Do NOT call any tools. Respond with plain text only.\n\n"
        "Step 1 — write your private analysis inside <analysis> tags: walk through "
        "every message chronologically, identify all user requests, decisions made, "
        "files touched, code snippets, error messages, and the precise state of work "
        "at the end of the conversation.\n\n"
        "Step 2 — write the final summary inside <summary> tags with EXACTLY these "
        "9 sections (use the ## headings verbatim):\n\n"
        "## 1. Primary Request and Intent\n"
        "Every explicit goal, requirement, or task the user stated.\n\n"
        "## 2. Key Technical Concepts\n"
        "Frameworks, libraries, languages, APIs, patterns used or discussed.\n\n"
        "## 3. Files and Code Sections\n"
        "Every file examined, created, or modified. For each: filename, what changed, "
        "and key code snippets (function names, class names, important lines). "
        "Be thorough — this section is critical for seamless continuation.\n\n"
        "## 4. Errors and Fixes\n"
        "Every error encountered and how it was resolved. Quote error messages verbatim.\n\n"
        "## 5. Problem Solving\n"
        "Approaches tried, decisions made, and why. Both solved and unresolved issues.\n\n"
        "## 6. User Messages\n"
        "All non-trivial user messages (quote short ones exactly; paraphrase long ones).\n\n"
        "## 7. Pending Tasks\n"
        "Work explicitly requested but not yet completed.\n\n"
        "## 8. Current Work\n"
        "The precise state of work at the moment this summary was created: what was "
        "being done, which file, which function, which step. Be as specific as possible.\n\n"
        "## 9. Next Step\n"
        "The single most logical next action, directly aligned with the user's latest request.\n\n"
        "Sections 3, 4, and 8 are the most important — prioritize completeness there."
    )

    def _summarize_messages(self, messages: List[Dict[str, Any]]) -> str:
        """Call LLM to produce a structured 9-section summary.

        Uses an <analysis> thinking block (stripped from output) followed by
        a <summary> block for the final result.

        Returns:
            Formatted summary text, or empty string on failure.
        """
        # Per-call, so the flag describes *this* summarization and not one
        # from an earlier compaction. ``run_turn`` additionally clears it per
        # turn, for the case where ``compress_messages`` returns without ever
        # reaching this method.
        self.last_summary_finish_reason_missing = False
        try:
            to_summarize = [
                m for m in messages
                if not (
                    isinstance(m.get("content"), str)
                    and m["content"].startswith("[PIN]")
                )
            ]
            formatted = self._format_for_summary(to_summarize)
            recall_messages = [
                {"role": "system", "content": self._SUMMARIZE_SYSTEM_PROMPT},
                {"role": "user", "content": f"Summarize this conversation:\n\n{formatted}"},
            ]
            response = self.llm_client.chat(messages=recall_messages, tools=None)
            # Same two-producer test the chat loop applies (see
            # ``runtime/chat_loop/_runner.py``): a falsy finish_reason, or a
            # streamed response whose provider never sent one.
            _fr = getattr(response.choices[0], "finish_reason", None)
            if not _fr or not getattr(response, "finish_reason_reported", True):
                self.last_summary_finish_reason_missing = True
            raw = response.choices[0].message.content or ""
            return self._format_summary(raw)
        except Exception as e:
            try:
                self.llm_client.logger.warning(f"Summarization failed: {e}")
            except Exception:
                pass
            return ""

    @staticmethod
    def _format_summary(raw: str) -> str:
        """Strip <analysis> block and unwrap <summary> tags."""
        # Remove analysis scratchpad
        raw = re.sub(r"<analysis>.*?</analysis>", "", raw, flags=re.DOTALL).strip()
        # Unwrap <summary>…</summary> if present
        m = re.search(r"<summary>(.*?)</summary>", raw, re.DOTALL)
        if m:
            raw = m.group(1).strip()
        return raw

    def _format_for_summary(self, messages: List[Dict[str, Any]]) -> str:
        """Format messages as readable text for the summarization prompt.

        Each message renders to a *block* of lines (content and/or tool-call
        lines); ``_join_within_budget`` then decides how many blocks fit.
        """
        blocks: List[List[str]] = []
        carry_index: Optional[int] = None
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            is_carry = False
            if isinstance(content, list):
                content = " ".join(
                    b.get("text", "")
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            if (
                isinstance(content, str)
                and content.startswith("[Conversation Summary]")
                and self.SUMMARY_END_MARKER in content
            ):
                # Strip a prior summary's end-marker + framing note on rehydration
                # so it doesn't accumulate when an old summary is re-summarized.
                # Anchored on the summary prefix so an unrelated message that merely
                # contains the marker substring is never truncated.
                content = content.split(self.SUMMARY_END_MARKER)[0].rstrip()
                is_carry = True
            if role == "tool":
                tool_name = msg.get("name", "")
                blocks.append([
                    f"[Tool Result - {tool_name}]: {self._clip_tool_result(str(content))}"
                ])
                continue  # tool messages never carry tool_calls
            block = []
            if content:
                text = (
                    self._clip_carry_summary(str(content))
                    if is_carry
                    else str(content)[: self._MESSAGE_TRUNCATION]
                )
                block.append(f"[{role.upper()}]: {text}")
            # An assistant turn that only called tools has ``content == ""``
            # (``chat_loop/_runner.py`` stores ``content or ""``), so the branch
            # above skips it entirely. Render the calls separately — otherwise
            # the transcript shows results with no invocation and the summary
            # cannot name the file that was read or the command that failed.
            block.extend(self._format_tool_calls(msg.get("tool_calls")))
            if is_carry and block:
                # Newest wins: summary N was itself produced from a window
                # containing summary N-1, so it already subsumes it.
                carry_index = len(blocks)
            blocks.append(block)
        return self._join_within_budget(blocks, carry_index)

    @classmethod
    def _clip_tool_result(cls, text: str) -> str:
        """Clip one tool result, giving failures a larger head+tail window.

        Two things the flat head-truncation got wrong. A failing command's
        diagnostic is at the *end* of its output — the traceback, the non-zero
        exit, the assertion — which is why microcompaction already keeps a tail
        (``MICROCOMPACT_HEAD_RATIO``). Truncating a failure from the head alone
        would satisfy the larger budget while still dropping the exact text
        ``## 4. Errors and Fixes`` asks to be quoted verbatim, so the failure
        tier keeps both ends and marks the gap.

        And the marker scan runs over the *whole* string for the same reason:
        scanning only the first N characters would miss every command that runs
        fine and then fails, which is most of them.

        Both tiers mark the cut. An unmarked clip is the failure mode
        :meth:`_clip_args` already documents one layer down: the summarizer
        cannot tell a clipped result from a complete one, so it quotes the
        amputated path or command as if it were the whole thing.
        """
        if len(text) <= cls._TOOL_RESULT_TRUNCATION:
            return text
        if not cls._FAILURE_MARKERS.search(text):
            # Head-only: a success's useful part is its opening, and the two
            # disjoint fragments a head+tail split leaves read worse here.
            #
            # The count carries any earlier clip's forward. This is the second
            # cut by construction — full compression microcompacts the kept
            # window first, and the live 55% pass has usually already run — so
            # reporting only this slice would tell the summarizer a result lost
            # 2,000 characters when it lost 199,065.
            kept = text[: cls._TOOL_RESULT_TRUNCATION]
            omitted = (
                len(text) - cls._TOOL_RESULT_TRUNCATION
                + cls._prior_omissions(text)
                - cls._prior_omissions(kept)
            )
            return kept + f"\n[… {omitted:,} chars omitted …]"
        return cls._head_tail_clip(text, cls._ERROR_RESULT_TRUNCATION)

    def _clip_carry_summary(self, text: str) -> str:
        """Clip a prior ``[Conversation Summary]`` being folded into a new one.

        Capped at half the transcript budget, so the carried history can never
        starve the live messages it exists to give context to — and so a small
        ``max_tokens`` (where the budget is the 2_000-token floor) still gets a
        proportional carve-out rather than the flat 8_000 chars.
        """
        clipped = text[: self._CARRY_SUMMARY_TRUNCATION]
        reserve = max(1, self._summary_input_budget() // 2)
        cost = self.count_tokens_in_text(clipped)
        if cost > reserve:
            clipped = clipped[: max(1, len(clipped) * reserve // cost)]
        return clipped

    def _summary_input_budget(self) -> int:
        """Token ceiling for the assembled summary transcript."""
        return max(
            self._SUMMARY_INPUT_BUDGET_FLOOR,
            int(self.max_tokens * self._SUMMARY_INPUT_BUDGET_RATIO),
        )

    def _block_cost(self, block: List[str]) -> int:
        """Estimated token cost of one rendered message block.

        Goes through :meth:`count_tokens_in_text`, not ``_heuristic_token_count``
        directly, so the budget is denominated in the *same* unit as the
        ``max_tokens`` it is a fraction of — tiktoken where the model family is
        known, and the CJK-aware heuristic (non-ASCII 1.3 tok/char vs ASCII's
        0.25) exactly as before where it is not. Either way this must not be a
        character budget: that would under-count Chinese more than fivefold, on
        the very histories most likely to be long.
        """
        return self.count_tokens_in_text("\n".join(block))

    def _join_within_budget(
        self, blocks: List[List[str]], carry_index: Optional[int] = None
    ) -> str:
        """Join per-message blocks newest-first until the token budget runs out.

        Spending from the newest end means an over-long window loses its oldest
        messages whole, rather than losing the tail of every message — the
        recent end is both the more relevant half and the half the kept-verbatim
        window no longer covers. Because allocation runs strictly backwards, the
        survivors are a contiguous suffix, so the transcript stays chronological
        with one elision marker at the seam.

        ``carry_index`` is the one exception, and it is not a decoration. A
        rehydrated ``[Conversation Summary]`` is by construction the *oldest*
        block in the window — ``compress_messages`` puts it at position 1 of the
        list it returns — so plain newest-first spending drops it first, and
        every compaction after the first would amputate the entire accumulated
        history: sections 1 and 6 of the prompt ("every explicit goal the user
        stated", "all non-trivial user messages") describe exactly the content
        that only lives there. It is charged before anything else and never
        evicted; :meth:`_clip_carry_summary` bounds it to half the budget so it
        cannot starve the live tail.
        """
        total = self._summary_input_budget()
        budget = total
        indexed = [(i, b) for i, b in enumerate(blocks) if b]
        keep: set = set()
        if carry_index is not None and blocks[carry_index]:
            budget -= self._block_cost(blocks[carry_index])
            keep.add(carry_index)
        kept_recent = 0
        for i, block in reversed(indexed):
            if i in keep:
                continue
            cost = self._block_cost(block)
            if cost > budget and kept_recent:
                # Stop at the first block that does not fit — do not skip it
                # and keep spending on older ones. Skipping would punch a hole
                # in the middle of the transcript, handing the summarizer a
                # history that omits a step without saying where; the whole
                # point of spending backwards is that the survivors form a
                # contiguous suffix. (``kept_recent`` keeps the newest block
                # unconditionally — counted separately from ``keep`` so a
                # charged carry block cannot satisfy it: a transcript of
                # nothing but an elision marker summarizes to nothing, which
                # counts as a compaction failure and increments the circuit
                # breaker.)
                break
            budget -= cost
            keep.add(i)
            kept_recent += 1
        dropped = len(indexed) - len(keep)
        # One marker, placed immediately above the surviving suffix rather than
        # at index 0. With a carry block protected the gap opens *after* it, and
        # a marker at the head would read as if the summary itself was dropped.
        # With no carry the suffix starts at the first kept block, so this is
        # the head — the same position as before.
        seam = min((i for i in keep if i != carry_index), default=None)
        lines: List[str] = []
        for i, block in indexed:
            if dropped and i == seam:
                lines.append(
                    f"[… {dropped} earlier message(s) omitted — summary input "
                    f"budget ({total:,} tokens) exhausted …]"
                )
            if i in keep:
                lines.extend(block)
        return "\n".join(lines)

    @classmethod
    def _format_tool_call_args(cls, raw: Any) -> str:
        """Render one tool call's ``arguments`` blob for the summary transcript.

        Parses the JSON rather than truncating the raw string, and emits the
        **shortest values first**, so a single oversized argument (a
        ``write_file`` body) cannot evict the short high-value ones beside it
        (``file_path``). Truncating the raw blob would keep whichever key the
        model happened to emit first, which is not a property worth relying on.

        Falls back to raw-string truncation when the blob is not JSON or not an
        object — this is display text for another LLM, so a degraded render is
        always preferable to dropping the call.

        ``arguments`` already decoded to a ``dict`` is accepted as-is, the same
        way :meth:`_extract_recently_read_files` accepts it: ``str()``-ing it
        first would produce a Python repr that ``json.loads`` rejects, so the
        blob would take the raw-truncation path and lose exactly the
        shortest-values-first ordering this method exists to provide.
        """
        if isinstance(raw, dict):
            parsed: Any = raw
        else:
            if not isinstance(raw, str):
                raw = "" if raw is None else str(raw)
            if not raw:
                return ""
            try:
                parsed = json.loads(raw)
            except Exception:
                return cls._clip_args(raw)
            if not isinstance(parsed, dict):
                return cls._clip_args(str(parsed))
        if not parsed:
            return ""

        rendered: List[str] = []
        for key, value in parsed.items():
            if isinstance(value, str):
                text = value
            else:
                try:
                    text = json.dumps(value, ensure_ascii=False, default=str)
                except Exception:
                    text = str(value)
            if len(text) > cls._TOOL_CALL_VALUE_TRUNCATION:
                omitted = len(text) - cls._TOOL_CALL_VALUE_TRUNCATION
                text = f"{text[: cls._TOOL_CALL_VALUE_TRUNCATION]}…(+{omitted:,} chars)"
            rendered.append(f"{key}={text}")
        rendered.sort(key=len)
        return cls._clip_args(", ".join(rendered))

    @classmethod
    def _clip_args(cls, text: str) -> str:
        """Cut to the per-call budget, marking the cut so it reads as a cut.

        The per-*value* path already appends ``…(+N chars)``; without the same
        marker here the joined render can amputate a value mid-way and hand the
        summarizer a plausible-looking but wrong path or command.
        """
        if len(text) <= cls._TOOL_CALL_ARGS_TRUNCATION:
            return text
        return text[: cls._TOOL_CALL_ARGS_TRUNCATION] + "…"

    @classmethod
    def _format_tool_calls(cls, tool_calls: Any) -> List[str]:
        """Render an assistant message's tool calls as transcript lines.

        Returns one ``[Tool Call - name]`` line per call (capped), so the
        summarizer sees the invocation next to the result it produced.

        Accepts both the serialized dict form and the raw SDK object form —
        :meth:`_extract_recently_read_files` is fed the *same* ``to_summarize``
        list and already handles both, so dropping objects here would give an
        embedder-supplied history file hints with no matching invocation line.
        """
        if not isinstance(tool_calls, list) or not tool_calls:
            return []
        lines: List[str] = []
        for tc in tool_calls[: cls._MAX_TOOL_CALLS_RENDERED]:
            if isinstance(tc, dict):
                fn: Any = tc.get("function")
                fn = fn if isinstance(fn, dict) else {}
                name = fn.get("name") or tc.get("name") or "unknown"
                raw_args = fn.get("arguments")
            else:
                fn = getattr(tc, "function", None)
                name = getattr(fn, "name", None) or getattr(tc, "name", None) or "unknown"
                raw_args = getattr(fn, "arguments", None)
            args = cls._format_tool_call_args(raw_args)
            # Every entry in the window yields exactly one line — an
            # unrenderable one degrades to ``[Tool Call - unknown]`` rather
            # than vanishing, so the ``omitted`` count below stays truthful.
            lines.append(f"[Tool Call - {name}]" + (f": {args}" if args else ""))
        overflow = len(tool_calls) - cls._MAX_TOOL_CALLS_RENDERED
        if overflow > 0:
            lines.append(f"[… {overflow} more tool call(s) omitted …]")
        return lines

    # -----------------------------------------------------------------------
    # Usage stats
    # -----------------------------------------------------------------------

    def get_usage_stats(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Return context window usage statistics with token breakdown.

        Args:
            messages: Full message list (including system if present).
            tools: Optional serialized tools schema for breakdown reporting.

        Returns:
            Dict with estimated_tokens, token_count_source ("api"/"local"),
            token_breakdown (system/messages/tools/total), max_tokens,
            usage_percent, message_count, circuit_breaker_failures,
            and (if available) last_compact metadata.
        """
        breakdown = self.estimate_tokens_breakdown(messages, tools=tools)
        # Tier 1: prefer real count from last API response
        if self._last_api_prompt_tokens is not None:
            estimated = self._last_api_prompt_tokens
            source = "api"
        else:
            estimated = breakdown["total"]
            source = "local"
        usage_percent = (estimated / self.max_tokens * 100) if self.max_tokens > 0 else 0.0
        stats: Dict[str, Any] = {
            "estimated_tokens": estimated,
            "token_count_source": source,
            "max_tokens": self.max_tokens,
            "usage_percent": round(usage_percent, 1),
            "message_count": len(messages),
            "token_breakdown": breakdown,
            "circuit_breaker_failures": self._consecutive_compact_failures,
        }
        if self._last_compact_stats:
            stats["last_compact"] = self._last_compact_stats
        return stats


# Patterns whose presence means "context/prompt overflow" — the conversation is
# too long for the model and we should compress and retry. Covers the major
# OpenAI-compatible / Anthropic / Google / xAI / Bedrock / OpenRouter / local
# backends. Example error strings are kept inline so the table is auditable.
# Borrowed structure from pi-mono ai/utils/overflow.ts (two-tier positive + guard).
_OVERFLOW_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"prompt is too long",  # Anthropic: "prompt is too long: 213462 tokens > 200000 maximum"
        r"request_too_large",  # Anthropic 413 byte-size overflow
        r"exceeds the context window",  # OpenAI (Completions & Responses)
        r"maximum context length",  # OpenAI/LiteLLM/OpenRouter: "...of N tokens", "...is N tokens", "(N)" — broad; guard below filters throttling
        r"context[_ ]length[_ ]exceeded",  # generic OpenAI-compatible code
        r"input is too long for requested model",  # Amazon Bedrock
        r"input token count.*exceeds the maximum",  # Google (Gemini)
        r"maximum prompt length is \d+",  # xAI (Grok)
        r"reduce the length",  # Groq: "reduce the length of the messages or completion" (broad; guard filters rate limits)
        r"exceeds (?:the )?maximum allowed input length of [\d,]+ tokens?",  # OpenRouter/Poolside
        r"is longer than the model'?s context length",  # Together AI
        r"too large for model with \d+ maximum context length",  # Mistral
        r"exceeds the available context size",  # llama.cpp server
        r"greater than the context length",  # LM Studio
        r"exceeded model token limit",  # Kimi For Coding
        r"prompt too long; exceeded (?:max )?context length",  # Ollama
        r"too many tokens",  # generic fallback (guarded below)
        r"token limit exceeded",  # generic fallback
        r"tokens > ",  # Anthropic-style "X tokens > Y maximum"
        r"range of input length",  # Alibaba/DashScope-style
        r"internalerror\.algo\.invalidparameter",  # Alibaba/DashScope overflow code
    )
]

# Guard patterns — if any of these match, the error is NOT an overflow even when
# a positive pattern also matches (e.g. Bedrock formats throttling as
# "ThrottlingException: Too many tokens, please wait..." which would otherwise
# hit the "too many tokens" fallback). Checked first, short-circuits to False.
_NON_OVERFLOW_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"throttling",  # AWS Bedrock / generic throttling
        r"rate limit",  # generic rate limiting
        r"too many requests",  # HTTP 429
        r"service unavailable",  # 503
    )
]


def is_context_too_long_error(exc: Exception) -> bool:
    """Return True if the exception is a 'prompt too long' / context overflow API error.

    Two-tier match: a negative guard (rate-limit / throttling / 429 / 503) is
    checked first so a fallback overflow phrase can't misclassify a transient
    error as overflow and trigger a destructive history compaction.
    """
    msg = str(exc)
    if any(pat.search(msg) for pat in _NON_OVERFLOW_PATTERNS):
        return False
    return any(pat.search(msg) for pat in _OVERFLOW_PATTERNS)
