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

import hashlib
from collections import Counter
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

from ._cache_control import apply_cache_control
from ._tool_ids import split_tool_id
from ._retry import (
    QUOTA_EXHAUSTED_CODES,
    RETRYABLE_STATUS_CODES,
    _classify_retry,
    _is_temperature_unsupported,
)
from ._stream_response import _StreamAccumulator

if TYPE_CHECKING:  # pragma: no cover - import-time only
    from .client import LLMClient

API_FORMAT = "openai-completions"

#: A chunk ``{"error": {...}}`` *inside* a 200 stream — what OpenAI-compatible
#: gateways send when the upstream fails after the stream opened. The ``openai``
#: SDK raises it as a bare ``APIError`` (not ``APIStatusError``: there is no
#: failing status), with the chunk's ``error`` object as ``body`` and its
#: ``code`` / ``type`` copied onto the exception, so a status-only classifier
#: calls it permanent. Mapped to the status the same error carries when it is
#: returned up front — the same repair the other two wires make
#: (``_anthropic_messages.py`` / ``_openai_responses.py`` ``_STREAM_ERROR_STATUS``).
#: OpenAI's own vocabulary only: ``server_error`` is its 500 ``type``,
#: ``rate_limit_exceeded`` its 429 ``code``.
_STREAM_ERROR_STATUS = {
    "server_error": 500,
    "rate_limit_exceeded": 429,
}

#: OpenAI's Chat Completions refuses a ``tool_calls[*].id`` longer than this.
#: Observed on api.openai.com, 2026-09-19: ``string_above_max_length``,
#: "Expected a string with maximum length 64, but got a string with length 83"
#: — for exactly the composite id this module rewrites. (pi-mono truncates to
#: 40; that number was copied here first, and it is not the API's.)
_TOOL_ID_MAX = 64


def _wire_tool_ids(messages: List[Dict[str, Any]]) -> Dict[str, str]:
    """History id → the id this request sends, for ids minted on ``openai-responses``.

    That wire keeps ``call_id|fc_…`` in history's one id slot, and the item id
    alone runs to 53 characters, the pair to 83 — past the 64 this API allows — so after a ``/provider`` switch back to
    this wire, every request carrying such a call is a 400, and stays one,
    because the id is in history. It goes out as its ``call_id``, which is what
    this API would have minted. Two things keep that one-to-one: a ``call_id``
    that is too long, or that another id in the request also spells (pi-mono
    records providers whose parallel calls share one ``call_id`` and differ
    only by item id), goes out as a prefix plus a hash of the whole history id
    — a hash rather than a counter, so the spelling does not depend on the
    order the ids appear in. (Whether an id is hashed at all does depend on
    its sibling being in the request: once compaction drops the other call,
    the survivor goes out as the bare ``call_id``. Each request stays
    self-consistent; only the cached prefix moves, once.)

    **Only composite ids are touched.** Any other id goes out byte for byte,
    whatever its length: this adapter also serves gateways that mint longer
    ids of their own and take them back, and the request is held byte-identical
    to the pre-extraction build. Outbound copy only — history keeps the
    original, which is what the Responses wire needs if the session returns.
    """
    raws: List[str] = []
    for message in messages:
        for call in message.get("tool_calls") or []:
            if isinstance(call, dict) and isinstance(call.get("id"), str):
                raws.append(call["id"])
        if message.get("role") == "tool" and isinstance(message.get("tool_call_id"), str):
            raws.append(message["tool_call_id"])
    ordered = list(dict.fromkeys(raws))
    composite = {
        raw: call_id
        for raw, (call_id, item_id) in ((r, split_tool_id(r)) for r in ordered)
        if item_id
    }
    if not composite:
        return {}
    spellings = Counter(composite.get(raw, raw) for raw in ordered)
    mapping: Dict[str, str] = {}
    for raw, call_id in composite.items():
        if len(call_id) <= _TOOL_ID_MAX and spellings[call_id] == 1:
            mapping[raw] = call_id
            continue
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
        mapping[raw] = f"{call_id[: _TOOL_ID_MAX - 9]}_{digest}"
    return mapping


def _with_wire_tool_ids(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """``messages`` with composite tool ids rewritten — **the same list** when
    there are none, and the same dict for every message that names none."""
    ids = _wire_tool_ids(messages)
    if not ids:
        return messages
    def wire(call: Any) -> Any:
        tool_id = call.get("id") if isinstance(call, dict) else None
        if isinstance(tool_id, str) and tool_id in ids:
            return {**call, "id": ids[tool_id]}
        return call

    out: List[Dict[str, Any]] = []
    for message in messages:
        calls = message.get("tool_calls")
        result_id = message.get("tool_call_id")
        if isinstance(calls, list):
            rewritten = [wire(c) for c in calls]
            if any(new is not old for new, old in zip(rewritten, calls)):
                message = {**message, "tool_calls": rewritten}
        elif message.get("role") == "tool" and isinstance(result_id, str) and result_id in ids:
            message = {**message, "tool_call_id": ids[result_id]}
        out.append(message)
    return out


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
        messages = _with_wire_tool_ids(messages)
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
        # Unset (``None``) is the default and sends nothing: the provider's
        # own default applies, and a model that rejects the field is never
        # asked to repair a request it did not need.
        if owner.temperature is not None and not owner.omit_temperature:
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
        error = _body_error(response)
        if error is not None:
            raise _api_error(error, raw.http_response.request)
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

            # Accumulate the thinking text. DeepSeek, MiniMax, Kimi and older
            # vLLM name the field ``reasoning_content``; Ollama, newer vLLM and
            # OpenRouter name it ``reasoning``. Some vLLM versions send both
            # with the same text, so ``reasoning`` is read only when
            # ``reasoning_content`` is absent. Either way it is kept as
            # ``reasoning_content``.
            if delta:
                reasoning = getattr(delta, "reasoning_content", None) or getattr(
                    delta, "reasoning", None
                )
                if isinstance(reasoning, str) and reasoning:
                    acc.reasoning_parts.append(reasoning)

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
        # temperature unsupported (reasoning models: o1/o3/gpt-5, …). Only a
        # request that carried it can have been rejected for it.
        if (
            "temperature" in kwargs
            and not owner.omit_temperature
            and _is_temperature_unsupported(err_text)
        ):
            owner.omit_temperature = True
            owner.logger.info(
                f"Model rejects 'temperature'; omitting it for this client{note}"
            )
            kwargs.pop("temperature", None)
            return True
        return False

    def classify_retry(self, exc: BaseException) -> Tuple[bool, Optional[int], Optional[str]]:
        """``(retryable, status, retry_after)``.

        HTTP failures go to the shared table. What it cannot see is an error
        chunk inside a 200 stream (:data:`_STREAM_ERROR_STATUS`), mapped here.
        The client retries only while nothing has reached the host, so a
        mapping here never re-shows text.
        """
        status = _stream_error_status(exc)
        if status is not None:
            return (True, status, None)
        return _classify_retry(exc)


def _body_error(response: Any) -> Optional[Dict[str, Any]]:
    """What to raise for a 200 body that carries no answer, else ``None``.

    A gateway that has sent ``200 OK`` before the upstream failed reports the
    failure in the body instead — OpenRouter documents it for non-streaming
    requests: a JSON body holding only an ``error`` object and no ``choices``.
    The SDK parses that into a ``ChatCompletion`` with ``choices=None`` and the
    error in ``model_extra``, and nothing raises: the chat loop then failed on
    ``response.choices[0]`` with a ``TypeError`` that named neither the
    provider nor its message.

    Any body without ``choices`` is raised, not only one with an ``error``
    object — the chat loop and the summarizer index ``choices[0]`` and have no
    other way to fail. An ``error`` object is raised as given (so a transient
    one keeps its status); a bare string becomes its message; nothing at all
    gets a message that says so. A body that answers is an answer, whatever
    else it carries.
    """
    if getattr(response, "choices", None):
        return None
    extra = getattr(response, "model_extra", None)
    error = extra.get("error") if isinstance(extra, dict) else None
    if isinstance(error, dict):
        return error
    if isinstance(error, str) and error:
        return {"message": error}
    return {"message": "The provider returned a response with no choices and no error"}


def _api_error(error: Dict[str, Any], request: Any) -> Exception:
    """The exception the SDK raises for the same ``error`` object in a stream.

    One shape for both entry points, so :func:`_stream_error_status` maps a
    transient one to its status on either — the summarizer and Gemini's turns
    come through ``chat()``, not ``chat_stream()``.
    """
    from openai import APIError

    message = error.get("message")
    if not isinstance(message, str) or not message:
        message = "The provider returned an error with no answer"
    return APIError(message, request, body=error)


def _stream_error_status(exc: BaseException) -> Optional[int]:
    """The retryable status an in-stream error chunk stands for, else ``None``."""
    try:
        from openai import APIConnectionError, APIError, APIStatusError
    except ImportError:  # pragma: no cover - openai is a core dependency
        return None
    # Only the bare ``APIError`` the stream raises: its subclasses carry a
    # real status or a transport failure, and the shared table reads those.
    if not isinstance(exc, APIError) or isinstance(exc, (APIStatusError, APIConnectionError)):
        return None
    # Strings only: the SDK copies ``code`` off the chunk without coercing a
    # non-string, and an unhashable one (a gateway's nested object) would make
    # the ``in`` test raise ``TypeError`` from inside the client's ``except``,
    # replacing the provider's error with ours.
    words = [v for v in (getattr(exc, "code", None), getattr(exc, "type", None))
             if isinstance(v, str)]
    # A balance does not refill while we wait — same codes, and the same
    # exact match, as the shared table applies to a 429.
    if any(v in QUOTA_EXHAUSTED_CODES for v in words):
        return None
    for value in words:
        if value in _STREAM_ERROR_STATUS:
            return _STREAM_ERROR_STATUS[value]
    # OpenRouter's documented mid-stream error puts the HTTP status in
    # ``code``, as a number. Read from ``body``: openai 2.x copies it onto
    # ``exc.code`` as given, 3.x coerces it to the string ``"503"``.
    body = getattr(exc, "body", None)
    raw = body.get("code") if isinstance(body, dict) else None
    if isinstance(raw, int) and not isinstance(raw, bool) and raw in RETRYABLE_STATUS_CODES:
        return raw
    return None
