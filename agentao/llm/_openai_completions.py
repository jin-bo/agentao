"""The ``openai-completions`` wire: OpenAI Chat Completions.

Stage 1 of ``docs/design/llm-api-adapters.md`` lifted this out of
``LLMClient`` so a second wire protocol could sit beside it. It is an
**extraction, not a rewrite**: every statement here ran inside
``client.py`` before, in the same order, and
``tests/test_llm_api_extraction_noop.py`` holds the request it builds
byte-identical to a capture taken from the pre-extraction build.

An adapter owns what differs per protocol — the request shape, the wire
call, the stream event loop, the one-shot request repairs and the retry
classification. ``LLMClient`` keeps what does not: the retry/backoff loop,
logging, the token totals, and the configuration the adapter reads back
through ``owner`` at request time (``/model``, ``/temperature`` and
``/thinking`` all mutate the live client between requests, so a snapshot
taken at construction would go stale).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

from ._cache_control import apply_cache_control
from ._retry import _classify_retry, _is_temperature_unsupported
from ._stream_response import _StreamAccumulator

if TYPE_CHECKING:  # pragma: no cover - import-time only
    from .client import LLMClient

API_FORMAT = "openai-completions"


class OpenAICompletionsAdapter:
    """Chat Completions over the official ``openai`` SDK."""

    api = API_FORMAT

    #: Request-body fields this adapter sets. ``extra_body`` is merged *into
    #: the body* by the SDK (last-wins), so a key here that also appears in
    #: ``extra_body`` would shadow the client's value. Used only for the
    #: one-time construction warning (§3.3 of host-llm-extra-params.md).
    structural_body_keys = frozenset({
        "model", "messages", "stream", "stream_options",
        "tools", "tool_choice", "temperature",
        "max_tokens", "max_completion_tokens",
    })

    def __init__(self, owner: "LLMClient", client_cls: Callable[[], Any]) -> None:
        self._owner = owner
        # A callable returning the SDK class, not the class: ``LLMClient``
        # resolves ``OpenAI`` through its own module namespace so that
        # ``patch("agentao.llm.client.OpenAI")`` keeps working, and that lookup
        # has to happen at construction time, not at import time.
        self._client_cls = client_cls

    def create_client(self) -> Any:
        # max_retries=0: defer retry policy to _classify_retry / _compute_backoff_delay
        # so 408/409/425/429/5xx/529 + Retry-After + cancellation are handled
        # uniformly across non-stream and stream paths. Two layers of retry
        # would otherwise compound (SDK default is 2) and ignore Retry-After
        # the way our caller expects.
        return self._client_cls()(
            api_key=self._owner.api_key,
            base_url=self._owner.base_url,
            max_retries=0,
        )

    def reset_latches(self) -> None:
        """Nothing of its own: this wire's two latches live on ``LLMClient``,
        where ``/temperature``, the sub-agent factory and the tests read them."""

    def prepare(self) -> None:
        """Called on the send path before the request is built. Nothing to
        learn here: Chat Completions has no route that states a model's limits."""

    def build_request(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        max_tokens: Optional[int],
        *,
        stream: bool,
        cache_boundary: Optional[int] = None,
    ) -> Dict[str, Any]:
        """The ``.create(**kwargs)`` dict. See ``LLMClient._build_request_kwargs``."""
        owner = self._owner
        if cache_boundary is not None and owner.cache_control is not None:
            messages, tools = apply_cache_control(
                messages, tools, owner.cache_control,
                request_only_tail=cache_boundary,
            )
        kwargs: Dict[str, Any] = {
            "model": owner.model,
            "messages": messages,
        }
        if stream:
            kwargs["stream"] = True
            kwargs["stream_options"] = {"include_usage": True}
        if not owner.omit_temperature:
            kwargs["temperature"] = owner.temperature
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if max_tokens:
            key = "max_completion_tokens" if owner._use_max_completion_tokens else "max_tokens"
            kwargs[key] = max_tokens
        if owner.extra_body:
            kwargs["extra_body"] = owner.extra_body
        return kwargs

    def log_view(
        self,
        kwargs: Dict[str, Any],
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """What ``_log_request`` renders: the request itself, minus ``stream``."""
        return {k: v for k, v in kwargs.items() if k != "stream"}

    def send(self, kwargs: Dict[str, Any], acc: _StreamAccumulator) -> Any:
        """One non-streaming attempt.

        ``acc`` carries nothing here but the usage the response stated: it is
        where ``LLMClient`` counts an attempt from, on every wire and on both
        entry points. A request that raised has no response, and so nothing
        to report.
        """
        raw = self._owner.client.chat.completions.with_raw_response.create(**kwargs)
        response = raw.parse()
        acc.usage_data = getattr(response, "usage", None)
        return response

    def new_accumulator(self) -> _StreamAccumulator:
        return _StreamAccumulator(self._owner.model)

    def consume_stream(
        self,
        kwargs: Dict[str, Any],
        acc: _StreamAccumulator,
        on_text_chunk: Optional[Any],
        cancellation_token: Optional[Any],
    ) -> Any:
        """One streaming attempt, accumulated into ``acc``."""
        stream = self._owner.client.chat.completions.create(**kwargs)

        for chunk in stream:
            if cancellation_token and cancellation_token.is_cancelled:
                break
            # Capture usage from final usage-only chunk (stream_options include_usage)
            if hasattr(chunk, "usage") and chunk.usage:
                acc.usage_data = chunk.usage
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta

            # Accumulate text content and fire callback
            if delta and delta.content:
                acc.content_parts.append(delta.content)
                if on_text_chunk:
                    on_text_chunk(delta.content)
                    acc.progress_made = True

            # Accumulate reasoning_content (DeepSeek/MiniMax/Kimi-style thinking
            # field). Non-streaming exposes it on message.reasoning_content;
            # without this branch the streaming path would silently drop it.
            if delta and getattr(delta, "reasoning_content", None):
                acc.reasoning_parts.append(delta.reasoning_content)

            # Accumulate tool call deltas. ``acc.tool_call_key`` resolves the
            # stream-stable key for this delta, tolerating providers that omit
            # the OpenAI ``index`` field (see _StreamAccumulator.tool_call_key
            # and goose #10023).
            if delta and delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = acc.tool_call_key(tc_delta)
                    if idx not in acc.tool_calls_data:
                        acc.tool_calls_data[idx] = {"id": "", "name": "", "arguments": ""}
                    if tc_delta.id:
                        acc.tool_calls_data[idx]["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            acc.tool_calls_data[idx]["name"] += tc_delta.function.name
                        if tc_delta.function.arguments:
                            acc.tool_calls_data[idx]["arguments"] += tc_delta.function.arguments
                        # Gemini thinking models: preserve thought_signature
                        thought_sig = getattr(tc_delta.function, "thought_signature", None)
                        if thought_sig is not None:
                            acc.tool_calls_data[idx]["thought_signature"] = thought_sig

            if choice.finish_reason:
                acc.finish_reason = choice.finish_reason
                acc.finish_reason_reported = True

            if hasattr(chunk, "model") and chunk.model:
                acc.response_model = chunk.model

        # Build a duck-type response that agent.py can consume like a ChatCompletion
        return acc.build()

    def repair_request(self, err_text: str, kwargs: Dict[str, Any], *, stream: bool) -> bool:
        """One-shot fix-up of a rejected request. True → re-send now.

        Not a retry: a repair spends none of the retry budget, and each one is
        latched on the client so it can fire at most once per model.
        """
        owner = self._owner
        note = " (stream retry)" if stream else ""
        # max_tokens vs max_completion_tokens param mismatch.
        if (
            not owner._use_max_completion_tokens
            and "max_tokens" in err_text
            and "max_completion_tokens" in err_text
        ):
            owner._use_max_completion_tokens = True
            owner.logger.info(f"Switching to max_completion_tokens for this model{note}")
            if "max_tokens" in kwargs:
                kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")
            return True
        # temperature unsupported (reasoning models: o1/o3/gpt-5, …).
        if not owner.omit_temperature and _is_temperature_unsupported(err_text):
            owner.omit_temperature = True
            owner.logger.info(
                f"Model rejects 'temperature'; omitting it for this client{note}"
            )
            kwargs.pop("temperature", None)
            return True
        return False

    def classify_retry(self, exc: BaseException) -> Tuple[bool, Optional[int], Optional[str]]:
        return _classify_retry(exc)
