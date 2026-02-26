"""Context window management: compression, summarization, and memory recall."""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional


class ContextManager:
    """Manages context window size, compression, and memory recall."""

    DEFAULT_MAX_TOKENS = 200_000
    COMPRESSION_THRESHOLD = 0.65  # Compress when exceeding 65% of max_tokens
    KEEP_RECENT_RATIO = 0.60      # Keep the most recent 60% of messages

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

    def estimate_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """Estimate token count for a list of messages.

        Uses character count / 4 as a rough approximation (~4 chars per token).
        Handles str content, list content (multimodal), and tool_calls dicts.

        Args:
            messages: List of message dicts

        Returns:
            Estimated token count
        """
        total_chars = 0
        for msg in messages:
            total_chars += self._content_chars(msg)
        return total_chars // 4

    def _content_chars(self, msg: Dict[str, Any]) -> int:
        """Count characters in a message."""
        chars = 0
        content = msg.get("content", "")
        if isinstance(content, str):
            chars += len(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    chars += len(block.get("text", ""))
        if "tool_calls" in msg:
            chars += len(str(msg["tool_calls"]))
        return chars

    def needs_compression(self, messages: List[Dict[str, Any]]) -> bool:
        """Check if conversation needs compression.

        Args:
            messages: Current conversation messages

        Returns:
            True if estimated tokens exceed COMPRESSION_THRESHOLD * max_tokens
        """
        return self.estimate_tokens(messages) > self.max_tokens * self.COMPRESSION_THRESHOLD

    def compress_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Compress conversation history by summarizing early messages.

        Algorithm:
        1. Keep the most recent 60% of messages (at least 4)
        2. Summarize early messages with LLM
        3. Save summary to memory
        4. Prepend summary as a system message

        On any error, returns original messages unchanged (graceful degradation).

        Args:
            messages: Current conversation messages

        Returns:
            Compressed messages list
        """
        if len(messages) < 5:
            return messages

        keep_count = max(4, int(len(messages) * self.KEEP_RECENT_RATIO))
        split_index = len(messages) - keep_count

        # Advance split_index to the next 'user' message boundary to avoid
        # orphaning tool results from their preceding assistant tool_calls.
        while split_index < len(messages) - 1 and messages[split_index].get("role") != "user":
            split_index += 1

        # Safety: if no user message found, keep everything
        if messages[split_index].get("role") != "user":
            return messages

        to_summarize = messages[:split_index]
        to_keep = messages[split_index:]

        if not to_summarize:
            return messages

        summary = self._summarize_messages(to_summarize)
        if not summary:
            return messages

        # Save summary to memory
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.memory_tool.execute(
                key=f"conversation_summary_{timestamp}",
                value=summary,
                tags=["auto", "conversation_summary"],
            )
        except Exception:
            pass  # Non-critical, continue anyway

        summary_msg = {
            "role": "system",
            "content": f"[Conversation Summary]\n{summary}",
        }
        return [summary_msg] + to_keep

    def _summarize_messages(self, messages: List[Dict[str, Any]]) -> str:
        """Call LLM (no tools) to summarize a list of messages.

        Args:
            messages: Messages to summarize

        Returns:
            Summary text, or empty string on failure
        """
        try:
            formatted = self._format_for_summary(messages)
            recall_messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a summarization assistant. Summarize the key decisions, "
                        "facts, and context from the conversation below. Be concise but complete. "
                        "Focus on: user preferences, decisions made, important facts, project context."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Please summarize this conversation:\n\n{formatted}",
                },
            ]
            response = self.llm_client.chat(messages=recall_messages, tools=None)
            return response.choices[0].message.content or ""
        except Exception as e:
            try:
                self.llm_client.logger.warning(f"Summarization failed: {e}")
            except Exception:
                pass
            return ""

    def _format_for_summary(self, messages: List[Dict[str, Any]]) -> str:
        """Format messages as readable text for summarization."""
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            if role == "tool":
                lines.append(f"[Tool Result - {msg.get('name', '')}]: {str(content)[:200]}")
            elif content:
                lines.append(f"[{role.upper()}]: {str(content)[:500]}")
        return "\n".join(lines)

    def recall_relevant_memories(
        self,
        user_message: str,
        all_memories: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Use LLM to identify memories relevant to the current user message.

        This is "Agentic RAG": the LLM itself decides relevance, no vector DB needed.

        Args:
            user_message: The current user message
            all_memories: All saved memories

        Returns:
            List of relevant memory dicts, or [] on failure/no match
        """
        if not all_memories:
            return []

        try:
            memory_lines = "\n".join(
                f"{i+1}. [{m['key']}]: {m['value']}"
                + (f" (tags: {', '.join(m['tags'])})" if m.get("tags") else "")
                for i, m in enumerate(all_memories)
            )

            recall_messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a memory relevance analyzer. Given a user message and a list "
                        "of saved memories, identify which memories are relevant to the current query. "
                        "Return ONLY a JSON array of memory keys that are relevant. "
                        'If none are relevant, return []. Example: ["key1", "key2"]'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"User message: {user_message}\n\n"
                        f"Saved memories:\n{memory_lines}\n\n"
                        "Which memory keys are relevant? Return JSON array only."
                    ),
                },
            ]

            response = self.llm_client.chat(messages=recall_messages, tools=None)
            content = response.choices[0].message.content or "[]"

            # Extract JSON array from response (handle extra text around it)
            content = content.strip()
            start = content.find("[")
            end = content.rfind("]")
            if start != -1 and end != -1:
                content = content[start:end + 1]

            relevant_keys = json.loads(content)
            if not isinstance(relevant_keys, list):
                return []

            return [m for m in all_memories if m.get("key") in relevant_keys]

        except Exception as e:
            try:
                self.llm_client.logger.warning(f"Memory recall failed: {e}")
            except Exception:
                pass
            return []

    def extract_memories_to_save(
        self,
        user_message: str,
        assistant_response: str,
        existing_memories: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Use LLM to extract important long-term memories from a conversation turn.

        Conservative strategy: only extract clearly valuable information.

        Returns:
            List of {key, value, tags} dicts to save, or [] if nothing worth saving.
        """
        existing_summary = "\n".join(
            f"- [{m['key']}]: {m['value'][:80]}"
            for m in existing_memories[:30]
        ) if existing_memories else "(none)"

        extract_messages = [
            {
                "role": "system",
                "content": (
                    "You are a memory extractor. Analyze this conversation turn and extract "
                    "ONLY clearly important information worth remembering for FUTURE conversations.\n\n"
                    "Save: user preferences, personal info, project facts, key decisions, work context.\n"
                    "Do NOT save: temporary info, vague observations, or anything already in existing memories.\n\n"
                    "Return a JSON array: [{\"key\": \"...\", \"value\": \"...\", \"tags\": [\"...\"]}]\n"
                    "Return [] if nothing important. Keys should be short snake_case identifiers."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"User message: {user_message}\n\n"
                    f"Assistant response: {assistant_response[:1000]}\n\n"
                    f"Existing memories (do not duplicate):\n{existing_summary}\n\n"
                    "Extract memories to save. Return JSON array only."
                ),
            },
        ]

        try:
            response = self.llm_client.chat(messages=extract_messages, tools=None)
            content = (response.choices[0].message.content or "[]").strip()

            # Extract JSON array (handle surrounding text)
            start = content.find("[")
            end = content.rfind("]")
            if start == -1 or end == -1:
                return []
            content = content[start:end + 1]

            result = json.loads(content)
            if not isinstance(result, list):
                return []

            # Validate structure
            valid = []
            for item in result:
                if isinstance(item, dict) and "key" in item and "value" in item:
                    valid.append(item)
            return valid

        except Exception as e:
            try:
                self.llm_client.logger.warning(f"Memory extraction failed: {e}")
            except Exception:
                pass
            return []

    def get_usage_stats(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Return context window usage statistics.

        Args:
            messages: Current conversation messages

        Returns:
            Dict with estimated_tokens, max_tokens, usage_percent, message_count
        """
        estimated = self.estimate_tokens(messages)
        usage_percent = (estimated / self.max_tokens * 100) if self.max_tokens > 0 else 0.0
        return {
            "estimated_tokens": estimated,
            "max_tokens": self.max_tokens,
            "usage_percent": round(usage_percent, 1),
            "message_count": len(messages),
        }


def is_context_too_long_error(exc: Exception) -> bool:
    """Return True if the exception is a 'prompt too long' / context overflow API error."""
    msg = str(exc).lower()
    return any(phrase in msg for phrase in [
        "prompt is too long",
        "context_length_exceeded",
        "maximum context length",
        "tokens > ",
        "reduce the length",
    ])
