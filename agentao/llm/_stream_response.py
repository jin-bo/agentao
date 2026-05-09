"""Duck-type response objects used by ``LLMClient.chat_stream``.

The streaming code path reconstructs a ChatCompletion-shaped response
from accumulated SSE deltas so ``agent.py`` (and tools further
downstream) can consume it identically to the non-streaming path. The
attribute surface here is the union of what those callers touch:

- ``response.choices[0].message.{content,tool_calls,reasoning_content}``
- ``response.usage``
- ``response.model``
- ``tc.id``, ``tc.function.{name,arguments,thought_signature}``

Nothing in this module talks to OpenAI or the network; it is pure data
structure plumbing kept separate so the network/retry path in
``client.py`` reads as one concern.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class _StreamFunction:
    def __init__(self, name: str, arguments: str, thought_signature: Optional[str] = None):
        self.name = name
        self.arguments = arguments
        if thought_signature is not None:
            self.thought_signature = thought_signature


class _StreamToolCall:
    def __init__(self, id: str, function: _StreamFunction):
        self.id = id
        self.type = "function"
        self.function = function


class _StreamMessage:
    def __init__(self, content, tool_calls, reasoning_content: Optional[str] = None):
        self.content = content
        self.tool_calls = tool_calls
        self.role = "assistant"
        # Mirrors the non-streaming `message.reasoning_content` attribute so
        # chat_loop / context_manager / sanitize all see thinking-model output
        # the same way regardless of streaming mode.
        self.reasoning_content = reasoning_content


class _StreamChoice:
    def __init__(self, message: _StreamMessage, finish_reason: str):
        self.message = message
        self.finish_reason = finish_reason


class _StreamResponse:
    """Duck-type replacement for ChatCompletion returned by the streaming path."""

    def __init__(
        self,
        model: str,
        content: Optional[str],
        tool_calls_data: Dict[int, Dict[str, str]],
        finish_reason: str,
        usage: Any = None,
        reasoning_content: Optional[str] = None,
    ):
        self.model = model
        self.usage = usage  # populated when provider supports stream_options include_usage

        tool_calls = None
        if tool_calls_data:
            tool_calls = [
                _StreamToolCall(
                    id=tool_calls_data[idx]["id"],
                    function=_StreamFunction(
                        name=tool_calls_data[idx]["name"],
                        arguments=tool_calls_data[idx]["arguments"],
                        thought_signature=tool_calls_data[idx].get("thought_signature"),
                    ),
                )
                for idx in sorted(tool_calls_data)
            ]

        message = _StreamMessage(
            content=content,
            tool_calls=tool_calls,
            reasoning_content=reasoning_content,
        )
        self.choices = [_StreamChoice(message=message, finish_reason=finish_reason)]
