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

from typing import Any, Dict, List, Optional


class _StreamAccumulator:
    """Mutable per-attempt state for one streaming chat completion.

    Holds the partial content / reasoning / tool-call data accumulated as
    stream chunks arrive, plus ``progress_made`` (set once ``on_text_chunk``
    has fired with non-empty text). ``chat_stream`` creates a fresh
    accumulator per retry attempt; ``_consume_stream`` fills it and
    ``build()`` materialises the duck-type response.
    """

    def __init__(self, model: str) -> None:
        self.content_parts: List[str] = []
        self.reasoning_parts: List[str] = []
        self.tool_calls_data: Dict[int, Dict[str, str]] = {}
        # Streaming tool-call keying state. The OpenAI streaming spec tags
        # every tool_call delta with an ``index`` so fragments reassemble
        # across chunks; some OpenAI-compatible providers (local / self-hosted
        # inference servers, gateways) omit it. We assign each call a synthetic,
        # monotonically-increasing key — never the provider's raw index nor a
        # chunk-local position — so indexed and indexless calls can coexist
        # without colliding and ``sorted()`` at build time preserves arrival
        # order. ``tool_calls_data`` persists across chunks, so a per-chunk
        # ``enumerate`` position cannot identify a call. See goose #10023.
        self._tc_key_by_index: Dict[int, int] = {}
        self._tc_last_key: Optional[int] = None
        self._tc_next_key: int = 0
        self.finish_reason: str = "stop"
        self.response_model: str = model
        self.usage_data: Any = None
        # True only after on_text_chunk has fired with non-empty text — i.e.,
        # an LLM_TEXT event has reached the host. Role-only first chunks,
        # usage-only chunks, and tool-call/reasoning-only chunks all consume
        # iterations without exposing anything to the host, so they must NOT
        # block the pre-output retry path.
        self.progress_made: bool = False

    def tool_call_key(self, tc_delta: Any) -> int:
        """Stream-stable accumulation key for one streaming tool_call delta.

        Honours the provider's ``index`` when present: continuation deltas of
        the same call carry the same index and merge onto one key. When index
        is absent, a delta that carries an ``id`` starts a new call (fresh
        key); an arguments-only delta continues the call most recently opened.
        Keys are synthetic and assigned in arrival order, so indexed and
        indexless calls never share a key and ``sorted()`` preserves call
        order. ``index`` is read via ``getattr`` because a provider that truly
        omits the field may yield a delta without the attribute at all.
        """
        index = getattr(tc_delta, "index", None)
        if index is not None:
            key = self._tc_key_by_index.get(index)
            if key is None:
                key = self._tc_next_key
                self._tc_next_key += 1
                self._tc_key_by_index[index] = key
            self._tc_last_key = key
            return key
        # Indexless: an id announces a new call; an arguments-only continuation
        # appends to the call we last opened (or starts one if none is open).
        if getattr(tc_delta, "id", None) or self._tc_last_key is None:
            self._tc_last_key = self._tc_next_key
            self._tc_next_key += 1
        return self._tc_last_key

    def build(self) -> "_StreamResponse":
        """Materialise the accumulated deltas into a duck-type response."""
        return _StreamResponse(
            model=self.response_model,
            content="".join(self.content_parts) if self.content_parts else None,
            tool_calls_data=self.tool_calls_data,
            finish_reason=self.finish_reason,
            usage=self.usage_data,
            reasoning_content="".join(self.reasoning_parts) if self.reasoning_parts else None,
        )


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
