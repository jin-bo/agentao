"""The ``openai-responses`` wire: the OpenAI Responses API.

Stage 2 of ``docs/design/llm-api-adapters.md``; appendix A there is the
translation table this file implements. History stays OpenAI Chat-shaped dicts
on every wire — this adapter translates an outbound *copy* into ``input`` items
and folds the event stream back into the one duck-type in
``_stream_response.py``.

Four things that came from running the SDK over a scripted socket, not from
reading about the protocol:

* **An error inside a stream is an event, not an exception.** ``error`` and
  ``response.failed`` come out of the SDK's iterator like any other event
  (the ``anthropic`` SDK raises its ``error`` event; this one does not), so an
  adapter that only reads the events it knows returns an empty, apparently
  clean response. They are raised here, as :class:`ResponsesStreamError`.
* **Usage is stated once, by the terminal event** (``response.completed`` /
  ``.incomplete`` / ``.failed``); ``response.created`` carries ``usage: null``.
  A stream that dies before it has reported nothing, as on Chat Completions.
* **Both public calls stream.** ``chat()`` could use the non-streaming
  transport here, but one event loop means one place a function call, an
  incomplete response and a failure are read.
* **The tool definition is flat** — ``name`` / ``parameters`` beside ``type``,
  not under ``function`` — and ``strict`` is sent as ``False``: the API
  documents strict mode as the default for function tools, and strict mode
  rejects a schema that does not list every property as required, which
  agentao's tools and every MCP server's do not.

Stateless on purpose: ``store: false`` and no ``previous_response_id``.
agentao's history is the single source of truth — compaction rewrites it,
``/clear`` wipes it, replay replays it — and a server-side conversation would
silently diverge from all three.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

from ._retry import (
    QUOTA_EXHAUSTED_CODES,
    _classify_retry,
    _is_temperature_unsupported,
)
from ._stream_response import OPENAI_REASONING_ITEMS, _StreamAccumulator
from ._usage import positive_int

if TYPE_CHECKING:  # pragma: no cover - import-time only
    from .client import LLMClient

API_FORMAT = "openai-responses"

#: Below this the API rejects the request outright.
_MIN_OUTPUT_TOKENS = 16

#: One history slot, two wire ids. See :func:`compose_tool_id`.
_ID_SEPARATOR = "|"
#: The prefix OpenAI gives a function-call *item* id. :func:`split_tool_id`
#: reads the part after the separator as an item id only when it has it.
_ITEM_ID_PREFIX = "fc_"

#: What makes ``store: false`` workable for a reasoning model: the API returns
#: each reasoning item's content encrypted, and takes it back as input.
_ENCRYPTED_REASONING = "reasoning.encrypted_content"

#: ``incomplete_details.reason`` → the ``finish_reason`` the runtime reads.
_INCOMPLETE_REASONS = {
    "max_output_tokens": "length",
    "content_filter": "content_filter",
}

#: An in-stream error ``code`` worth another attempt → the HTTP status it
#: would have been. These two are the only transient members of the closed
#: enum the SDK types ``response.failed``'s ``error.code`` with; the rest of
#: it (``invalid_prompt``, the image codes) describes the request, and sending
#: the same request again fails the same way. The bare ``error`` event's code
#: is a free string, and anything not listed here is permanent — the safe side
#: to miss on is the one that ends a turn rather than the one that loops.
_STREAM_ERROR_STATUS = {
    "server_error": 500,
    "rate_limit_exceeded": 429,
}


class ResponsesStreamError(Exception):
    """A failure the API reported *inside* a 200 stream.

    ``str()`` carries the code and the provider's message: the runtime's
    context-overflow detection and the request repairs both read the text.
    """

    def __init__(self, code: Optional[str], message: Optional[str]) -> None:
        self.code = code if isinstance(code, str) and code else None
        self.message = message if isinstance(message, str) else ""
        super().__init__(f"{self.code or 'error'}: {self.message}")


# -- ids ----------------------------------------------------------------------


def compose_tool_id(call_id: str, item_id: Optional[str]) -> str:
    """The one id agentao's history keeps for a Responses function call.

    The wire has two: ``call_id`` correlates the output, and the item ``id``
    names the call item itself. History has one slot, and that id must
    round-trip byte for byte — a second key would have to survive sanitize,
    compaction, replay and session load, and ``tool_call_id`` is what the
    compaction pairing rules match on. So: ``call_id|item_id``.
    """
    if isinstance(item_id, str) and item_id.startswith(_ITEM_ID_PREFIX):
        return f"{call_id}{_ID_SEPARATOR}{item_id}"
    return call_id


def split_tool_id(tool_id: Any) -> Tuple[str, Optional[str]]:
    """``(call_id, item_id)`` back out of a history id.

    Split on the **last** separator, and only when what follows looks like an
    item id. History outlives a provider switch, so the id may have been
    minted on another wire: one that merely contains ``|`` stays whole, since
    a mis-split would send half of it as the ``call_id`` and the other half as
    an item id the API never issued.
    """
    text = tool_id if isinstance(tool_id, str) else ""
    head, sep, tail = text.rpartition(_ID_SEPARATOR)
    if sep and head and tail.startswith(_ITEM_ID_PREFIX):
        return head, tail
    return text, None


# -- request translation ------------------------------------------------------


def _input_parts(content: Any) -> List[Dict[str, Any]]:
    """Canonical user content → ``input_text`` / ``input_image`` parts."""
    if isinstance(content, str):
        return [{"type": "input_text", "text": content}] if content else []
    parts: List[Dict[str, Any]] = []
    for part in content if isinstance(content, list) else []:
        if not isinstance(part, dict):
            continue
        kind = part.get("type")
        if kind == "text":
            text = part.get("text")
            if isinstance(text, str) and text:
                parts.append({"type": "input_text", "text": text})
        elif kind == "image_url":
            url = (part.get("image_url") or {}).get("url")
            if not isinstance(url, str) or not url:
                raise ValueError("openai-responses: an image part needs a URL")
            parts.append({"type": "input_image", "image_url": url})
        else:
            raise ValueError(
                f"openai-responses: unsupported content part type {kind!r}"
            )
    return parts


def _text_of(content: Any) -> str:
    """Flatten content to text — what an assistant turn and a tool result are."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        part["text"] for part in content
        if isinstance(part, dict) and part.get("type") == "text"
        and isinstance(part.get("text"), str)
    )


def _reasoning_items(carrier: Any) -> List[Dict[str, Any]]:
    """The reasoning items off an assistant dict — all of them, or none.

    Rebuilt key by key rather than passed through: the carrier is persisted to
    session files a host can edit, and an unknown key is a 400. An item with
    no ``encrypted_content`` cannot go back: with ``store: false`` the provider
    kept nothing under that id, so naming it asks for an item that is not
    there.

    **One entry that cannot go back takes the whole carrier with it**, the
    same rule ``consume_stream`` applies when recording. Sending the rest
    would still count as "reasoning carried", and the turn's calls would name
    their ``fc_`` ids — one of which was produced beside the item just dropped.
    """
    items: List[Dict[str, Any]] = []
    for entry in carrier if isinstance(carrier, list) else []:
        if not isinstance(entry, dict):
            return []
        item_id, encrypted = entry.get("id"), entry.get("encrypted_content")
        if not (isinstance(item_id, str) and item_id
                and isinstance(encrypted, str) and encrypted):
            return []
        summary = [
            {"type": "summary_text", "text": part["text"]}
            for part in entry.get("summary") or []
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        ]
        items.append({"type": "reasoning", "id": item_id, "summary": summary,
                      "encrypted_content": encrypted})
    return items


def translate_messages(
    messages: List[Dict[str, Any]], *, reasoning: bool = True,
) -> List[Dict[str, Any]]:
    """Canonical history → Responses ``input`` items. The input is not mutated.

    Every item is rebuilt key by key: history dicts carry keys this wire has
    no field for (``reasoning_content``, another wire's thinking carrier, a
    ``cache_control`` marker), and an unknown key is a 400.

    ``system`` stays a message item **in place**, the leading one included,
    rather than being hoisted into ``instructions``: compaction leaves
    ``role: "system"`` summaries inside history, and one rule for all of them
    keeps the request's order the order of ``agent.messages``.

    ``reasoning=False`` leaves the reasoning items out, and with them every
    function-call item id: for an endpoint that has refused encrypted
    reasoning, which can no more take it back than issue it.
    """
    items: List[Dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role == "assistant":
            # Reasoning leads its turn, as it did in the response.
            carried = (
                _reasoning_items(message.get(OPENAI_REASONING_ITEMS)) if reasoning else []
            )
            text = _text_of(message.get("content"))
            # ...and only when the turn has something to lead. The API refuses
            # a reasoning item "provided without its required following item",
            # and a turn can be reasoning alone: one cut off at
            # ``max_output_tokens`` while still thinking records no text and
            # no call.
            if not text and not any(
                isinstance(call, dict) for call in message.get("tool_calls") or []
            ):
                carried = []
            items.extend(carried)
            if text:
                items.append({"role": "assistant", "content": text})
            for call in message.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                fn = call.get("function") or {}
                call_id, item_id = split_tool_id(call.get("id"))
                item: Dict[str, Any] = {
                    "type": "function_call",
                    "call_id": call_id,
                    "name": fn.get("name") or "unknown",
                    # JSON *text* on this wire, as history holds it.
                    "arguments": fn.get("arguments") or "{}",
                }
                # The item id goes back **only beside the reasoning it was
                # produced with**. The API tracks which ``fc_`` id belongs
                # with which ``rs_`` item and refuses a call that names one
                # without the other — and a reasoning item can be missing for
                # ordinary reasons: a switch purged it, a session file lost
                # it, the endpoint returned none. pi-mono drops the id on the
                # same ground (``openai-responses-shared.ts``, "avoid pairing
                # validation"). ``call_id`` alone still pairs the output.
                if item_id is not None and carried:
                    item["id"] = item_id
                items.append(item)
        elif role == "tool":
            call_id, _ = split_tool_id(message.get("tool_call_id"))
            items.append({
                "type": "function_call_output",
                "call_id": call_id,
                "output": _text_of(message.get("content")),
            })
        elif role in ("system", "developer"):
            text = _text_of(message.get("content"))
            if text:
                items.append({"role": role, "content": text})
        else:
            parts = _input_parts(message.get("content"))
            if parts:
                plain = len(parts) == 1 and parts[0]["type"] == "input_text"
                items.append({
                    "role": "user",
                    "content": parts[0]["text"] if plain else parts,
                })
    return items


def without_reasoning(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Already-translated input items, as ``translate_messages(...,
    reasoning=False)`` would have produced them: no reasoning items, and no
    function-call item id, which is only ever sent beside one.

    For ``repair_request``, which is handed the request and not the history it
    came from. A test holds the two spellings of the rule to the same output.
    """
    return [
        {k: v for k, v in item.items()
         if not (k == "id" and item.get("type") == "function_call")}
        for item in items if item.get("type") != "reasoning"
    ]


#: An endpoint refusing the ``include`` *parameter*, as opposed to the value
#: in it. Each needs a rejecting word bound to the name: a bare "include" is
#: ordinary English ("the request must include…") and would latch on nothing.
_INCLUDE_PARAMETER_REJECTED = re.compile(
    # OpenAI-style: "Unknown parameter: 'include'", "Unsupported parameter…",
    # "Unrecognized request argument supplied: include".
    r"(?:unknown|unsupported|unrecognized|unexpected|invalid)\s+"
    r"(?:request\s+)?(?:parameter|argument|field|key)s?(?:\s+supplied)?\s*:?\s*['\"`]?include\b"
    # The error object naming it as the offending parameter.
    r"|['\"]param['\"]\s*:\s*['\"]include['\"]"
    # A pydantic-validated gateway (vLLM, FastAPI): "Extra inputs are not
    # permitted" at ``loc: ('body', 'include')``.
    r"|['\"]loc['\"]\s*:\s*[\[(][^\])]*['\"]include['\"]"
)


def _rejects_the_include_field(err_text: str) -> bool:
    """Whether a 400 says the endpoint does not take
    ``include: [reasoning.encrypted_content]``.

    Two shapes, both from compatible gateways rather than OpenAI: the *value*
    is refused (the text names ``encrypted_content`` and ``include``), or the
    *parameter* is (``Unknown parameter: include`` — no mention of the value at
    all). Every request carries the field, so missing the second shape turns
    an endpoint that worked before this adapter asked into a permanent 400.

    Narrow in the other direction too, because the answer is latched for the
    client's life. ``invalid_encrypted_content`` names the same words and
    means something else entirely — *one item* could not be decrypted (a
    rotated key, a stale session) on an endpoint that supports the field
    perfectly well — and reading it as "unsupported" would end reasoning
    carry-over for the session.

    Lower-cased **here**, like ``_is_temperature_unsupported``: only
    ``chat_stream`` hands ``repair_request`` lower-cased text, and ``chat()``
    — the summarizer's entry — passes the exception's own words, capitals
    and all.
    """
    err_text = err_text.lower()
    if "invalid_encrypted_content" in err_text:
        return False
    if "encrypted_content" in err_text and "include" in err_text:
        return True
    return _INCLUDE_PARAMETER_REJECTED.search(err_text) is not None


def translate_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Canonical function schemas → flat Responses tool definitions."""
    out: List[Dict[str, Any]] = []
    for tool in tools:
        fn = tool.get("function") or {}
        item: Dict[str, Any] = {
            "type": "function",
            "name": fn.get("name", ""),
            "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
            "strict": False,  # module docstring
        }
        if fn.get("description"):
            item["description"] = fn["description"]
        out.append(item)
    return out


# -- response -----------------------------------------------------------------


class _PromptTokensDetails:
    def __init__(self, cached_tokens: int) -> None:
        self.cached_tokens = cached_tokens


class _Usage:
    """``response.usage`` in the shape the runtime reads.

    ``input_tokens`` already **includes** the cached part on this wire, so it
    maps to ``prompt_tokens`` as it stands — the opposite of Anthropic's
    ``input_tokens``, which is the uncached remainder. Do not copy either
    mapping across adapters. The cached count rides where Chat Completions
    puts it, so ``cache_token_counts`` reads both wires with one rule; there
    is no cache-write count.
    """

    def __init__(self, usage: Any) -> None:
        self.prompt_tokens = positive_int(getattr(usage, "input_tokens", None))
        self.completion_tokens = positive_int(getattr(usage, "output_tokens", None))
        self.total_tokens = self.prompt_tokens + self.completion_tokens
        details = getattr(usage, "input_tokens_details", None)
        self.prompt_tokens_details = _PromptTokensDetails(
            positive_int(getattr(details, "cached_tokens", None))
        )


class OpenAIResponsesAdapter:
    """The Responses API over the official ``openai`` SDK."""

    api = API_FORMAT

    #: Body fields this adapter sets; see the Chat Completions adapter.
    structural_body_keys = frozenset({
        "model", "input", "stream", "store", "include", "tools",
        "temperature", "max_output_tokens",
    })

    def __init__(self, owner: "LLMClient", client_cls: Callable[[], Any]) -> None:
        self._owner = owner
        self._client_cls = client_cls
        # Set when an endpoint rejects ``include: [reasoning.encrypted_content]``
        # — a compatible gateway, not OpenAI. Per model, like every latch.
        self._omit_encrypted_reasoning = False

    def create_client(self) -> Any:
        # Same SDK and the same reason as Chat Completions: one retry policy.
        return self._client_cls()(
            api_key=self._owner.api_key,
            base_url=self._owner.base_url,
            max_retries=0,
        )

    def reset_latches(self) -> None:
        """The temperature latch lives on ``LLMClient``; this one is ours."""
        self._omit_encrypted_reasoning = False

    def prepare(self) -> None:
        """Nothing to learn: ``GET /v1/models/{id}`` states no limits here."""

    # -- request ------------------------------------------------------------

    def build_request(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        max_tokens: Optional[int],
        *,
        stream: bool,
        cache_boundary: Optional[int] = None,
    ) -> Dict[str, Any]:
        """The ``responses.create(**kwargs)`` dict.

        ``stream`` and ``cache_boundary`` are accepted for the shared
        signature and ignored: both public calls stream, and this wire has no
        ``cache_control`` — its prefix caching is automatic.
        """
        owner = self._owner
        kwargs: Dict[str, Any] = {
            "model": owner.model,
            "input": translate_messages(
                messages, reasoning=not self._omit_encrypted_reasoning,
            ),
            "stream": True,
            "store": False,
        }
        if not self._omit_encrypted_reasoning:
            # Asked of every model: one that does not reason returns no
            # reasoning items and the field costs nothing, and there is no
            # way to know which kind this is — a model name is never read.
            kwargs["include"] = [_ENCRYPTED_REASONING]
        if not owner.omit_temperature:
            kwargs["temperature"] = owner.temperature
        if tools:
            kwargs["tools"] = translate_tools(tools)
        if max_tokens:
            kwargs["max_output_tokens"] = max(_MIN_OUTPUT_TOKENS, max_tokens)
        if owner.extra_body:
            kwargs["extra_body"] = owner.extra_body
        return kwargs

    def log_view(
        self,
        kwargs: Dict[str, Any],
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """The conversation as agentao holds it, plus the scalars that went
        out: the logger is incremental over *message* indices, and one
        assistant message becomes several input items."""
        view: Dict[str, Any] = {"model": kwargs.get("model"), "messages": messages}
        for key in ("temperature", "max_output_tokens", "extra_body"):
            if key in kwargs:
                view[key] = kwargs[key]
        if tools:
            view["tools"] = tools
        return view

    def repair_request(self, err_text: str, kwargs: Dict[str, Any], *, stream: bool) -> bool:
        """One-shot fix-up of a rejected request. True → re-send now."""
        owner = self._owner
        if not owner.omit_temperature and _is_temperature_unsupported(err_text):
            owner.omit_temperature = True
            owner.logger.info("Model rejects temperature; omitting it for this client")
            kwargs.pop("temperature", None)
            return True
        if not self._omit_encrypted_reasoning and _rejects_the_include_field(err_text):
            # This request goes again without the field, and without the
            # items it would have carried back: an endpoint that cannot issue
            # encrypted reasoning cannot take it either. Later requests get
            # the same from ``build_request``, which reads the latch.
            self._omit_encrypted_reasoning = True
            owner.logger.info(
                "Endpoint rejects reasoning.encrypted_content; reasoning will "
                "not be carried across turns on this client"
            )
            kwargs.pop("include", None)
            kwargs["input"] = without_reasoning(kwargs.get("input", []))
            return True
        return False

    def classify_retry(self, exc: BaseException) -> Tuple[bool, Optional[int], Optional[str]]:
        """``(retryable, status, retry_after)``.

        HTTP failures are the ``openai`` SDK's own exceptions, so the shared
        table reads them, quota codes included. What it cannot see is a
        failure reported inside a 200 stream, which is mapped here.
        """
        if isinstance(exc, ResponsesStreamError):
            # A balance does not refill while we wait — same codes, and the
            # same exact match, as the shared table applies to a 429.
            if exc.code in QUOTA_EXHAUSTED_CODES:
                return (False, None, None)
            status = _STREAM_ERROR_STATUS.get(exc.code or "")
            return (status is not None, status, None)
        return _classify_retry(exc)

    # -- response -----------------------------------------------------------

    def send(self, kwargs: Dict[str, Any], acc: _StreamAccumulator) -> Any:
        """One ``chat()`` attempt — the stream, consumed with no callback."""
        return self.consume_stream(kwargs, acc, None, None)

    def new_accumulator(self) -> _StreamAccumulator:
        return _StreamAccumulator(self._owner.model)

    def consume_stream(
        self,
        kwargs: Dict[str, Any],
        acc: _StreamAccumulator,
        on_text_chunk: Optional[Any],
        cancellation_token: Optional[Any],
    ) -> Any:
        """One streaming attempt, accumulated into ``acc``.

        A stream that is cancelled or simply stops builds a response with
        ``finish_reason_reported`` left False: only a terminal event says the
        response is over, and a partial answer must not read as a finished one.
        """
        stream = self._owner.client.responses.create(**kwargs)
        # Output indices whose text arrived as deltas — so the whole-item
        # events that follow do not append the same text a second time.
        streamed_text: set = set()
        # Reasoning items by id, as the item events stated them.
        reasoning: Dict[str, Dict[str, Any]] = {}
        try:
            for event in stream:
                if cancellation_token and cancellation_token.is_cancelled:
                    break
                kind = getattr(event, "type", None)
                if kind == "response.created":
                    model = getattr(event.response, "model", None)
                    if isinstance(model, str) and model:
                        acc.response_model = model
                elif kind in ("response.output_text.delta", "response.refusal.delta"):
                    # A refusal is the model's visible answer, not a failure.
                    if event.delta:
                        streamed_text.add(event.output_index)
                        acc.content_parts.append(event.delta)
                        if on_text_chunk:
                            on_text_chunk(event.delta)
                            acc.progress_made = True
                elif kind in (
                    "response.reasoning_summary_text.delta",
                    # What an open-weight model behind a compatible server
                    # (LM Studio, vLLM) sends instead of a summary.
                    "response.reasoning_text.delta",
                ):
                    if event.delta:
                        acc.reasoning_parts.append(event.delta)
                elif kind == "response.reasoning_summary_part.added":
                    # A summary is several parts; run together they read as
                    # one sentence ending mid-word into the next heading.
                    if acc.reasoning_parts:
                        acc.reasoning_parts.append("\n\n")
                elif kind == "response.output_item.added":
                    self._open_item(acc, event.output_index, event.item)
                elif kind == "response.function_call_arguments.delta":
                    call = acc.tool_calls_data.get(event.output_index)
                    if call is not None and event.delta:
                        call["arguments"] += event.delta
                elif kind == "response.output_item.done":
                    self._note_reasoning(reasoning, event.item)
                    self._close_item(
                        acc, event.output_index, event.item, streamed_text, on_text_chunk,
                    )
                elif kind in ("response.completed", "response.incomplete"):
                    # The terminal response restates every item, and is the
                    # only place some servers state ``encrypted_content`` at
                    # all (Azure, per pi-mono) — so it is read after the item
                    # events and fills what they left out.
                    for item in getattr(event.response, "output", None) or []:
                        self._note_reasoning(reasoning, item)
                    self._finish(acc, event.response, streamed_text, on_text_chunk)
                elif kind == "response.failed":
                    response = event.response
                    self._record_usage(acc, response)
                    error = getattr(response, "error", None)
                    raise ResponsesStreamError(
                        getattr(error, "code", None), getattr(error, "message", None),
                    )
                elif kind == "error":
                    raise ResponsesStreamError(
                        getattr(event, "code", None), getattr(event, "message", None),
                    )
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
        # Whole items only. One without ``encrypted_content`` cannot go back
        # (``_reasoning_items``), and half a carrier is worse than none: it
        # would send the ``fc_`` id its call was paired with.
        items = list(reasoning.values())
        if items and all(item.get("encrypted_content") for item in items):
            acc.reasoning_items = items
        return acc.build()

    @staticmethod
    def _note_reasoning(reasoning: Dict[str, Dict[str, Any]], item: Any) -> None:
        """Record a reasoning item, keeping whatever an earlier sighting had."""
        if getattr(item, "type", None) != "reasoning":
            return
        item_id = getattr(item, "id", None)
        if not isinstance(item_id, str) or not item_id:
            return
        seen = reasoning.setdefault(item_id, {"id": item_id, "summary": []})
        summary = [
            {"type": "summary_text", "text": part.text}
            for part in getattr(item, "summary", None) or []
            if isinstance(getattr(part, "text", None), str)
        ]
        if summary:
            seen["summary"] = summary
        encrypted = getattr(item, "encrypted_content", None)
        if isinstance(encrypted, str) and encrypted:
            seen["encrypted_content"] = encrypted

    @staticmethod
    def _record_usage(acc: _StreamAccumulator, response: Any) -> None:
        usage = getattr(response, "usage", None)
        if usage is not None:
            acc.usage_data = _Usage(usage)

    @staticmethod
    def _open_item(acc: _StreamAccumulator, index: int, item: Any) -> None:
        if getattr(item, "type", None) == "function_call":
            acc.tool_calls_data[index] = {
                "id": compose_tool_id(item.call_id, getattr(item, "id", None)),
                "name": item.name,
                "arguments": "",
            }

    @staticmethod
    def _close_item(
        acc: _StreamAccumulator,
        index: int,
        item: Any,
        streamed_text: set,
        on_text_chunk: Optional[Any],
    ) -> None:
        """Take what a *finished* item states over what the deltas built.

        For a function call the finished item is authoritative — a gateway
        that sends the whole call and no deltas, or no ``added`` event at all,
        still produces the call. For text it only fills a gap: text that was
        already delivered through the callback is not delivered again.
        """
        kind = getattr(item, "type", None)
        if kind == "function_call":
            call = acc.tool_calls_data.setdefault(index, {"id": "", "name": "", "arguments": ""})
            call["id"] = compose_tool_id(item.call_id, getattr(item, "id", None))
            call["name"] = item.name
            if isinstance(item.arguments, str) and item.arguments:
                call["arguments"] = item.arguments
        elif kind == "message" and index not in streamed_text:
            for part in getattr(item, "content", None) or []:
                text = getattr(part, "text", None) or getattr(part, "refusal", None)
                if isinstance(text, str) and text:
                    streamed_text.add(index)
                    acc.content_parts.append(text)
                    if on_text_chunk:
                        on_text_chunk(text)
                        acc.progress_made = True

    def _finish(
        self,
        acc: _StreamAccumulator,
        response: Any,
        streamed_text: set,
        on_text_chunk: Optional[Any],
    ) -> None:
        self._record_usage(acc, response)
        # Anything the item events did not deliver is in the terminal output.
        # A call the item events already closed is stated again with the same
        # values; one they only *opened* (``added`` and nothing after it) gets
        # its arguments here rather than going out as ``""``.
        for index, item in enumerate(getattr(response, "output", None) or []):
            self._close_item(acc, index, item, streamed_text, on_text_chunk)
        details = getattr(response, "incomplete_details", None)
        reason = getattr(details, "reason", None)
        if getattr(response, "status", None) == "incomplete":
            acc.finish_reason = _INCOMPLETE_REASONS.get(reason, "length")
        elif acc.tool_calls_data:
            acc.finish_reason = "tool_calls"
        else:
            acc.finish_reason = "stop"
        acc.finish_reason_reported = True
