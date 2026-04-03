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
      gpt-4o* / o1* / o3*                     -> o200k_base
      gpt-4* / gpt-3.5* / claude* / deepseek* -> cl100k_base
      gemini* / unknown                         -> None (CJK heuristic fallback)
    """
    if not _TIKTOKEN_AVAILABLE:
        return None
    m = model.lower()
    try:
        if any(m.startswith(p) for p in ("gpt-4o", "o1", "o3")):
            return _tiktoken.get_encoding("o200k_base")
        if any(m.startswith(p) for p in ("gpt-4", "gpt-3.5", "claude", "deepseek")):
            return _tiktoken.get_encoding("cl100k_base")
    except Exception:
        pass
    return None


def _heuristic_token_count(text: str) -> int:
    """CJK-aware token estimation (adapted from gemini-cli tokenCalculation.ts).

    Fast path for strings > 100K chars: len/4.
    Otherwise char-by-char:
      ASCII (0-127):    0.25 tokens/char
      non-ASCII / CJK:  1.3  tokens/char
    """
    n = len(text)
    if n > _FAST_PATH_CHARS:
        return n // 4
    tokens = 0.0
    for ch in text:
        tokens += 0.25 if ord(ch) < 128 else 1.3
    return int(tokens)


class ContextManager:
    """Manages context window size, compression, and memory recall."""

    DEFAULT_MAX_TOKENS = 200_000
    COMPRESSION_THRESHOLD = 0.65    # Full LLM compression at 65%
    MICROCOMPACT_THRESHOLD = 0.55   # Cheap tool-result clearing at 55%
    KEEP_RECENT_MESSAGES = 20       # Hard cap on verbatim-kept messages
    CIRCUIT_BREAKER_LIMIT = 3       # Stop auto-compact after N consecutive failures
    MICROCOMPACT_TOOL_LIMIT = 3_000 # Max chars kept from any old tool result in microcompact
    MICROCOMPACT_PRESERVE_RECENT = 5  # Keep the most recent N tool results at full fidelity

    def __init__(self, llm_client, memory_tool, max_tokens: int = DEFAULT_MAX_TOKENS):
        """Initialize ContextManager.

        Args:
            llm_client: LLMClient instance (borrowed from agent)
            memory_tool: SaveMemoryTool instance (borrowed from agent)
            max_tokens: Maximum context window tokens (default 200K)
        """
        self.llm_client = llm_client
        self.memory_tool = memory_tool
        self.max_tokens = max_tokens

        # Circuit breaker: stop auto-compact after too many consecutive failures
        self._consecutive_compact_failures: int = 0

        # Stats from the last completed compaction (surfaced via get_usage_stats)
        self._last_compact_stats: Optional[Dict[str, Any]] = None

        # Tier 1: last real prompt_tokens from API response (updated after each LLM call)
        self._last_api_prompt_tokens: Optional[int] = None
        # Tier 3: cached tiktoken encoding; None = CJK-aware heuristic fallback
        self._encoding = _get_tiktoken_encoding(self.llm_client.model)

    # -----------------------------------------------------------------------
    # Token estimation
    # -----------------------------------------------------------------------

    def record_api_usage(self, prompt_tokens: int) -> None:
        """Store real prompt_tokens from the latest API response (Tier 1)."""
        self._last_api_prompt_tokens = prompt_tokens

    def _count_tokens_in_text(self, text: str) -> int:
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
            tokens += self._count_tokens_in_text(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    tokens += self._count_tokens_in_text(block.get("text", ""))
        # reasoning_content is truncated to MAX_REASONING_HISTORY_CHARS before storage
        rc = msg.get("reasoning_content")
        if isinstance(rc, str) and rc:
            tokens += self._count_tokens_in_text(rc)
        if "tool_calls" in msg:
            tokens += self._count_tokens_in_text(str(msg["tool_calls"]))
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
            tools_tokens = self._count_tokens_in_text(tools_str)
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

    def needs_compression(self, messages: List[Dict[str, Any]]) -> bool:
        """Return True when full LLM compression is needed (>= 65%)."""
        return self.estimate_tokens(messages) > self.max_tokens * self.COMPRESSION_THRESHOLD

    def needs_microcompaction(self, messages: List[Dict[str, Any]]) -> bool:
        """Return True when cheap tool-result clearing is warranted (55-65%)."""
        tokens = self.estimate_tokens(messages)
        return (
            tokens > self.max_tokens * self.MICROCOMPACT_THRESHOLD
            and tokens <= self.max_tokens * self.COMPRESSION_THRESHOLD
        )

    # -----------------------------------------------------------------------
    # Microcompaction — cheap pass, no LLM call
    # -----------------------------------------------------------------------

    # Fraction of MICROCOMPACT_TOOL_LIMIT kept from the start of old tool results.
    # 20% head (command invoked, initial output) + 80% tail (errors, final results).
    MICROCOMPACT_HEAD_RATIO = 0.2

    def microcompact_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Truncate large tool results without calling the LLM.

        Keeps the most recent MICROCOMPACT_PRESERVE_RECENT tool results at full
        fidelity; older ones exceeding MICROCOMPACT_TOOL_LIMIT are shortened using
        a head+tail strategy: 20% from the start (command context) and 80% from
        the end (errors and final output tend to appear last).
        Returns a new list; does not mutate the original.
        """
        tool_indices = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
        preserve = set(tool_indices[-self.MICROCOMPACT_PRESERVE_RECENT:])

        head_chars = int(self.MICROCOMPACT_TOOL_LIMIT * self.MICROCOMPACT_HEAD_RATIO)
        tail_chars = self.MICROCOMPACT_TOOL_LIMIT - head_chars

        result = []
        mutated = 0
        for i, msg in enumerate(messages):
            if msg.get("role") == "tool" and i not in preserve:
                content = msg.get("content", "")
                if isinstance(content, str) and len(content) > self.MICROCOMPACT_TOOL_LIMIT:
                    msg = dict(msg)
                    omitted = len(content) - self.MICROCOMPACT_TOOL_LIMIT
                    msg["content"] = (
                        content[:head_chars]
                        + f"\n[… {omitted:,} chars omitted by microcompact …]\n"
                        + content[len(content) - tail_chars:]
                    )
                    mutated += 1
            result.append(msg)

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
          3. Advance split point to next 'user' boundary (tool_use/tool_result safety)
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
        if self._consecutive_compact_failures >= self.CIRCUIT_BREAKER_LIMIT:
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

        # --- Step 1: microcompact (free) ------------------------------------
        messages = self.microcompact_messages(messages)

        # --- Step 2: partial compaction split -------------------------------
        # Keep at most KEEP_RECENT_MESSAGES, but no more than 60% of total
        keep_count = min(
            self.KEEP_RECENT_MESSAGES,
            max(4, int(len(messages) * 0.60)),
        )
        split_index = len(messages) - keep_count

        # Advance to next 'user' boundary to avoid orphaned tool results
        while split_index < len(messages) - 1 and messages[split_index].get("role") != "user":
            split_index += 1

        if messages[split_index].get("role") != "user":
            return messages  # no safe split point found

        to_summarize = messages[:split_index]
        to_keep = messages[split_index:]

        if not to_summarize:
            return messages

        # --- Step 3: extract pinned messages --------------------------------
        pinned = [
            m for m in to_summarize
            if isinstance(m.get("content"), str) and m["content"].startswith("[PIN]")
        ]

        # --- Step 4: extract recently read files ----------------------------
        recently_read = self._extract_recently_read_files(to_summarize)

        # --- Step 5: LLM summarization --------------------------------------
        pre_tokens = self.estimate_tokens(messages)
        summary = self._summarize_messages(to_summarize)
        if not summary:
            self._consecutive_compact_failures += 1
            return messages  # graceful degradation

        self._consecutive_compact_failures = 0  # reset on success

        # --- Step 6: save to memory -----------------------------------------
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.memory_tool.execute(
                key=f"conversation_summary_{ts}",
                value=summary,
                tags=["auto", "conversation_summary"],
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
            "content": f"[Conversation Summary]\n{summary}",
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

    _HIGH_FIDELITY_TOOLS = {"write_file", "replace", "edit_file"}
    _TOOL_RESULT_TRUNCATION = 200
    _HIGH_FIDELITY_TRUNCATION = 1_000

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
        """Format messages as readable text for the summarization prompt."""
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    b.get("text", "")
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            if role == "tool":
                tool_name = msg.get("name", "")
                limit = (
                    self._HIGH_FIDELITY_TRUNCATION
                    if tool_name in self._HIGH_FIDELITY_TOOLS
                    else self._TOOL_RESULT_TRUNCATION
                )
                lines.append(f"[Tool Result - {tool_name}]: {str(content)[:limit]}")
            elif content:
                lines.append(f"[{role.upper()}]: {str(content)[:500]}")
        return "\n".join(lines)

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


def is_context_too_long_error(exc: Exception) -> bool:
    """Return True if the exception is a 'prompt too long' / context overflow API error."""
    msg = str(exc).lower()
    return any(phrase in msg for phrase in [
        "prompt is too long",
        "context_length_exceeded",
        "maximum context length",
        "tokens > ",
        "reduce the length",
        "range of input length",
        "internalerror.algo.invalidparameter",
    ])
