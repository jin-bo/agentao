"""The ``anthropic-messages`` wire: Anthropic's Messages API.

Stage 1 of ``docs/design/llm-api-adapters.md``. History stays OpenAI-shaped
dicts (option A): this module translates an outbound **copy** into a Messages
request, and folds the response back into the same duck-type the Chat
Completions path returns, so nothing above ``llm/`` knows which wire ran.

Rules a naive translation gets wrong, each forced by agentao's own history
format (§6 of the design):

- **A mid-history ``role: "system"`` message has no target.** Compaction
  writes its summary as one. It goes out as user text, which the merge below
  then folds into the neighbouring user turn — so it can never land between a
  ``tool_use`` and its ``tool_result``.
- **Consecutive same-role messages are merged.** agentao appends one
  ``role: "tool"`` message per result, a persisted background notification
  after them, and (since stage 0a) a request-only tail after that: three or
  more user-side messages in a row. Inside a merged user turn every
  ``tool_result`` goes first, then the text in its original order.
- **History may open on an assistant message** — the minimal-history rung
  steps back to the assistant that made the calls. A synthetic user turn is
  put in front rather than relying on the endpoint accepting that.
- **Signed thinking goes back verbatim or not at all.** ``reasoning_content``
  is a 500-character display copy; a truncated signed block is rejected. The
  whole blocks ride on the assistant dict under
  :data:`~._stream_response.ANTHROPIC_THINKING_BLOCKS` and lead the turn.
- **Usage folds the cache fields in.** Anthropic's ``input_tokens`` is the
  *uncached* remainder; ``prompt_tokens`` here is the whole prompt, because
  the Tier-1 compaction anchor reads it as the size of what was sent.

**``temperature`` is not sent on this wire.** The SDK this is built on
(``anthropic`` 1.6.0) has no ``temperature`` / ``top_p`` / ``top_k`` parameter
on ``messages.create`` at all — passing one is a ``TypeError`` before anything
reaches the network — and the API rejects it next to extended thinking. So the
client's ``temperature`` and ``/temperature`` have no effect here; a host whose
endpoint does take one (an Anthropic-compatible gateway) sends it through
``extra_body``, which is merged into the request body as on the other wire.

Both ``chat()`` and ``chat_stream()`` run over the **streaming** transport.
That is not a shortcut: the SDK refuses a non-streaming request whose
``max_tokens`` implies more than ten minutes (``_calculate_nonstreaming_timeout``
— anything above ~21k), agentao's default is 65,536, and the summarizer calls
``chat()`` with no cap at all. ``chat()`` simply consumes the stream with no
callback.

The ``anthropic`` SDK is a core dependency, imported lazily — the default wire
never loads it, and importing this module costs nothing.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from ._api_format import ANTHROPIC_MESSAGES
from ._cache_control import apply_cache_control
from ._retry import RETRYABLE_STATUS_CODES
from ._stream_response import ANTHROPIC_THINKING_BLOCKS, _StreamAccumulator

if TYPE_CHECKING:  # pragma: no cover - import-time only
    from .client import LLMClient

API_FORMAT = ANTHROPIC_MESSAGES

#: ``stop_reason`` → the ``finish_reason`` vocabulary the runtime already
#: gates on. ``model_context_window_exceeded`` is output cut short by the
#: window, which is what ``length`` means to the truncation guard.
_FINISH_REASONS = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "pause_turn": "stop",
    "max_tokens": "length",
    "model_context_window_exceeded": "length",
    "tool_use": "tool_calls",
    "refusal": "content_filter",
}

#: An ``error`` event *inside* a stream arrives on an HTTP 200, so the SDK
#: raises a bare ``APIStatusError`` with ``status_code == 200`` and a
#: status-only classifier calls it permanent. Overload is the common one, and
#: it usually arrives before any content. Mapped to the status the same error
#: carries when it is returned up front.
_STREAM_ERROR_STATUS = {
    "overloaded_error": 529,
    "rate_limit_error": 429,
    "api_error": 500,
    "timeout_error": 504,
}

_DATA_URL = re.compile(r"^data:([^;,]+);base64,(.*)$", re.DOTALL)
_TOOL_ID_INVALID = re.compile(r"[^a-zA-Z0-9_-]")
_TOOL_ID_VALID = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
#: "max_tokens: 65536 > 64000, which is the maximum allowed number of output
#: tokens for <model>". Not verified against a live endpoint from here; when
#: the text differs nothing is repaired and the 400 surfaces as it would have.
_MAX_TOKENS_CAP = re.compile(r"max_tokens:\s*(\d+)\s*>\s*(\d+)")

#: The Models API lookup is a convenience; a slow endpoint must not hold the
#: first turn for the SDK's ten-minute default.
#: httpx applies it per phase (connect, write, read, pool), so it bounds each
#: one rather than the whole lookup, and the lookup is not cancellable.
#: ``LLMClient.chat_stream`` reads the token on both sides of it instead, so a
#: turn cancelled meanwhile is not continued once this returns.
_MODEL_INFO_TIMEOUT_S = 5.0
_MODEL_INFO_ATTEMPTS = 2


def _positive_int(value: Any) -> Optional[int]:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


_ASSISTANT_FIRST_PLACEHOLDER = (
    "(The conversation before this point is not available.)"
)


# ---------------------------------------------------------------------------
# Request: OpenAI-shaped history → Messages
# ---------------------------------------------------------------------------


def _raw_tool_id(tool_id: Any) -> str:
    return tool_id if isinstance(tool_id, str) else str(tool_id or "")


def _wire_tool_ids(messages: List[Dict[str, Any]]) -> Dict[str, str]:
    """History tool-call id → the id this request sends, one-to-one.

    Ids minted on this wire already match the API's pattern and pass through
    byte-for-byte. One from a session recorded on another provider may not —
    an id carrying ``.`` or ``:``, say — and the API rejects it, so it is
    rewritten. The rewrite alone is lossy (``call.1`` and ``call:1`` both
    become ``call_1``; so do two ids that differ past the 64th character), and
    a duplicate ``tool_use`` id is a 400 on every later request, because the
    ids are in history. So the map is built over the whole request: valid ids
    claim their own spelling first, then each rewritten id takes a free one,
    suffixed in order of first appearance. A call and its result look up the
    same history id, so they cannot drift apart. Outbound copy only — history
    keeps the original.
    """
    seen: List[str] = []
    for message in messages:
        for call in message.get("tool_calls") or []:
            if isinstance(call, dict):
                seen.append(_raw_tool_id(call.get("id")))
        if message.get("role") == "tool":
            seen.append(_raw_tool_id(message.get("tool_call_id")))
    ordered = list(dict.fromkeys(seen))

    mapping = {raw: raw for raw in ordered if _TOOL_ID_VALID.match(raw)}
    taken = set(mapping)
    for raw in ordered:
        if raw in mapping:
            continue
        base = _TOOL_ID_INVALID.sub("_", raw)[:64] or "call"
        wire, n = base, 1
        while wire in taken:
            n += 1
            suffix = f"_{n}"
            wire = base[: 64 - len(suffix)] + suffix
        mapping[raw] = wire
        taken.add(wire)
    return mapping


def _text_block(text: str, marker: Any = None) -> Dict[str, Any]:
    block: Dict[str, Any] = {"type": "text", "text": text}
    if marker is not None:
        block["cache_control"] = marker
    return block


def _content_blocks(content: Any) -> List[Dict[str, Any]]:
    """A ``content`` value (string or OpenAI part list) as Messages blocks.

    Empty and whitespace-only text is dropped — the API rejects an empty text
    block. An image is a base64 data URL (all agentao's own ``chat(images=...)``
    produces) or an ``http(s)`` URL, which the API fetches itself. Anything
    else raises: it can only come from a host writing history directly, and
    dropping it silently would send the model a different conversation than the
    host recorded.
    """
    if content is None:
        return []
    if isinstance(content, str):
        return [_text_block(content)] if content.strip() else []
    if not isinstance(content, list):
        raise ValueError(
            f"anthropic-messages: unsupported message content {type(content).__name__}"
        )
    blocks: List[Dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            raise ValueError("anthropic-messages: content parts must be objects")
        kind = part.get("type")
        marker = part.get("cache_control")
        if kind == "text":
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                blocks.append(_text_block(text, marker))
        elif kind == "image_url":
            url = (part.get("image_url") or {}).get("url", "")
            match = _DATA_URL.match(url) if isinstance(url, str) else None
            if match is not None:
                source: Dict[str, Any] = {
                    "type": "base64",
                    "media_type": match.group(1),
                    "data": match.group(2),
                }
            elif isinstance(url, str) and url.startswith(("https://", "http://")):
                # Passed through for the provider to fetch. Raising here would
                # be permanent: the part is in history, so every later request
                # would raise too, with nothing short of ``/clear`` to recover.
                source = {"type": "url", "url": url}
            else:
                raise ValueError(
                    "anthropic-messages: an image must be a base64 data URL "
                    "or an http(s) URL"
                )
            block: Dict[str, Any] = {"type": "image", "source": source}
            if marker is not None:
                block["cache_control"] = marker
            blocks.append(block)
        else:
            raise ValueError(
                f"anthropic-messages: unsupported content part type {kind!r}"
            )
    return blocks


def _thinking_blocks(carrier: Any) -> List[Dict[str, Any]]:
    """The signed blocks off an assistant dict, whole ones only.

    Rebuilt key by key rather than passed through: the carrier is persisted to
    session files a host can edit, and an unknown key is a 400.
    """
    if not isinstance(carrier, list):
        return []
    blocks: List[Dict[str, Any]] = []
    for item in carrier:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "thinking":
            thinking, signature = item.get("thinking"), item.get("signature")
            if isinstance(thinking, str) and isinstance(signature, str) and signature:
                blocks.append(
                    {"type": "thinking", "thinking": thinking, "signature": signature}
                )
        elif item.get("type") == "redacted_thinking":
            data = item.get("data")
            if isinstance(data, str) and data:
                blocks.append({"type": "redacted_thinking", "data": data})
    return blocks


def _assistant_blocks(message: Dict[str, Any], ids: Dict[str, str]) -> List[Dict[str, Any]]:
    # Thinking leads: with thinking on, the API requires the assistant turn of
    # a tool loop to *start* with its thinking block.
    blocks = _thinking_blocks(message.get(ANTHROPIC_THINKING_BLOCKS))
    blocks.extend(_content_blocks(message.get("content")))
    for call in message.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        fn = call.get("function") or {}
        try:
            arguments = json.loads(fn.get("arguments") or "{}")
        except (TypeError, ValueError):
            arguments = {}
        blocks.append({
            "type": "tool_use",
            "id": ids[_raw_tool_id(call.get("id"))],
            "name": fn.get("name") or "unknown",
            # ``input`` must be an object; history holds canonical JSON text,
            # and a call whose arguments never parsed was answered with an
            # error result rather than run.
            "input": arguments if isinstance(arguments, dict) else {},
        })
    return blocks


def _tool_result_block(message: Dict[str, Any], ids: Dict[str, str]) -> Dict[str, Any]:
    block: Dict[str, Any] = {
        "type": "tool_result",
        "tool_use_id": ids[_raw_tool_id(message.get("tool_call_id"))],
    }
    content = message.get("content")
    marker = None
    if isinstance(content, str):
        if content:
            block["content"] = content
    else:
        parts = _content_blocks(content)
        for part in parts:
            # A breakpoint placed on the result's text is hoisted onto the
            # result block, which is where the API documents it.
            marker = part.pop("cache_control", marker)
        if parts:
            block["content"] = parts
    if marker is not None:
        block["cache_control"] = marker
    return block


def _system_as_user_text(message: Dict[str, Any]) -> List[Dict[str, Any]]:
    blocks = _content_blocks(message.get("content"))
    for block in blocks:
        if block["type"] == "text":
            block["text"] = (
                "<system-reminder>\n" + block["text"] + "\n</system-reminder>"
            )
    return blocks


def translate_messages(
    messages: List[Dict[str, Any]],
) -> Tuple[Any, List[Dict[str, Any]]]:
    """``(system, messages)`` for a Messages request. The input is not mutated."""
    system: Any = None
    rest = messages
    if messages and messages[0].get("role") in ("system", "developer"):
        lifted = _content_blocks(messages[0].get("content"))
        rest = messages[1:]
        # A breakpoint the caller put on the system *message* moves onto its
        # last block, as for any other message below. It was already charged
        # against the budget and ``apply_cache_control`` leaves a marked
        # message alone, so dropping it here would lose the breakpoint outright.
        if lifted and messages[0].get("cache_control") is not None:
            lifted[-1].setdefault("cache_control", messages[0]["cache_control"])
        if lifted:
            # A plain string unless a cache breakpoint needs the block form.
            plain = len(lifted) == 1 and "cache_control" not in lifted[0]
            system = lifted[0]["text"] if plain else lifted

    ids = _wire_tool_ids(rest)
    turns: List[Dict[str, Any]] = []
    for message in rest:
        role = message.get("role")
        if role == "assistant":
            wire_role, blocks = "assistant", _assistant_blocks(message, ids)
        elif role == "tool":
            wire_role, blocks = "user", [_tool_result_block(message, ids)]
        elif role in ("system", "developer"):
            wire_role, blocks = "user", _system_as_user_text(message)
        else:
            wire_role, blocks = "user", _content_blocks(message.get("content"))
        if not blocks:
            continue
        # A breakpoint the caller put on the message itself has no message-level
        # home on this wire; it moves to the message's last block.
        # Never onto thinking, which takes none.
        if message.get("cache_control") is not None and blocks[-1]["type"] not in (
            "thinking", "redacted_thinking",
        ):
            blocks[-1].setdefault("cache_control", message["cache_control"])
        if turns and turns[-1]["role"] == wire_role:
            turns[-1]["content"].extend(blocks)
        else:
            turns.append({"role": wire_role, "content": blocks})

    for turn in turns:
        if turn["role"] == "user":
            # Stable partition: results first, everything else in its order.
            results = [b for b in turn["content"] if b["type"] == "tool_result"]
            if results and len(results) != len(turn["content"]):
                others = [b for b in turn["content"] if b["type"] != "tool_result"]
                turn["content"] = results + others

    if turns and turns[0]["role"] == "assistant":
        turns.insert(0, {
            "role": "user",
            "content": [_text_block(_ASSISTANT_FIRST_PLACEHOLDER)],
        })
    return system, turns


def translate_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Canonical function schemas → Messages tool definitions."""
    out: List[Dict[str, Any]] = []
    for tool in tools:
        fn = tool.get("function") or {}
        item: Dict[str, Any] = {"name": fn.get("name", "")}
        if fn.get("description"):
            item["description"] = fn["description"]
        item["input_schema"] = fn.get("parameters") or {
            "type": "object", "properties": {},
        }
        if "cache_control" in tool:
            item["cache_control"] = tool["cache_control"]
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# Response: stream events → the shared duck-type
# ---------------------------------------------------------------------------


class _Usage:
    """``response.usage`` in the shape the runtime reads.

    ``prompt_tokens`` is the **whole** prompt. Anthropic reports the uncached
    remainder as ``input_tokens`` and the cached parts beside it, and the
    Tier-1 compaction anchor takes ``prompt_tokens`` as the true size of the
    prefix already sent — mapping ``input_tokens`` alone would under-report it
    by exactly the cached amount, which on a working cache is most of it, and
    compaction would fire late or never. The two cache fields stay available,
    additively, for cost reporting.
    """

    def __init__(self, fields: Dict[str, int]) -> None:
        self.cache_creation_input_tokens = fields.get("cache_creation_input_tokens", 0)
        self.cache_read_input_tokens = fields.get("cache_read_input_tokens", 0)
        self.prompt_tokens = (
            fields.get("input_tokens", 0)
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )
        self.completion_tokens = fields.get("output_tokens", 0)
        self.total_tokens = self.prompt_tokens + self.completion_tokens


_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


def _merge_usage(fields: Dict[str, int], usage: Any) -> None:
    """Fold one event's usage in. Counts are cumulative, so later wins."""
    for name in _USAGE_FIELDS:
        value = getattr(usage, name, None)
        if isinstance(value, int) and not isinstance(value, bool):
            fields[name] = value


class AnthropicMessagesAdapter:
    """The Messages API over the official ``anthropic`` SDK."""

    api = API_FORMAT

    #: Body fields this adapter sets. ``extra_body`` is merged in last-wins, so
    #: one of these inside it replaces agentao's value — ``system`` is the
    #: whole system prompt. ``thinking`` and ``temperature`` are absent on
    #: purpose: ``extra_body`` is the supported way to send them on this wire.
    structural_body_keys = frozenset({
        "model", "messages", "system", "tools", "max_tokens", "stream",
    })

    def __init__(self, owner: "LLMClient") -> None:
        self._owner = owner
        # The model's output cap, learned from the rejection that states it.
        # Model-specific, so cleared with the other capability latches.
        self._max_output_tokens: Optional[int] = None
        # How many times the Models API may still be asked about the current
        # model. A definite answer — 200, or a 4xx such as the 404 compatible
        # gateways give — spends them all; a transient failure (429, 5xx, a
        # timeout) spends one, so a blip on the first turn does not cost the
        # session its limits and a stalling endpoint is not asked for ever.
        self._model_info_attempts = _MODEL_INFO_ATTEMPTS

    def create_client(self) -> Any:
        try:
            import anthropic
        except ImportError as exc:
            raise ImportError(
                "api_format='anthropic-messages' needs the 'anthropic' package, "
                "a core dependency of agentao that is missing from this "
                "environment: pip install 'anthropic>=1.6.0'"
            ) from exc
        base_url = self._owner.base_url
        # The SDK posts to ``<base_url>/v1/messages``. A base URL carried over
        # from the OpenAI-compatible endpoint ends in ``/v1`` and would 404 as
        # ``/v1/v1/messages``.
        # ``reconfigure(base_url=None)`` is a documented path ("clears it to
        # the SDK default"); the SDK takes ``None`` to mean exactly that.
        trimmed = base_url.rstrip("/") if isinstance(base_url, str) else None
        if trimmed is not None and trimmed.endswith("/v1"):
            trimmed = trimmed[: -len("/v1")]
            self._owner.logger.info(
                "anthropic-messages: dropped the trailing /v1 from base_url "
                "(the SDK appends /v1/messages itself)"
            )
        return anthropic.Anthropic(
            api_key=self._owner.api_key,
            base_url=trimmed,
            # Same reason as the Chat Completions wire: one retry policy, ours.
            max_retries=0,
        )

    def reset_latches(self) -> None:
        self._max_output_tokens = None
        self._model_info_attempts = _MODEL_INFO_ATTEMPTS

    def prepare(self) -> None:
        """Ask ``GET /v1/models/{id}`` and adopt what it states.

        ``max_tokens`` seeds the output-cap latch, so the first request is not
        spent learning it from a rejection; ``max_input_tokens`` and
        ``capabilities`` go on the client for the context manager and the CLI.
        Everything is optional twice over — the endpoint may not implement the
        route, and the SDK types every field ``Optional`` — so any failure and
        any field that is not a positive ``int`` leaves today's behaviour
        exactly as it was.

        ``LLMClient`` calls this on the send path, **before** it builds and
        logs the request, so the ``max_tokens`` in ``agentao.log`` is the one
        that went out. Never at construction, and never from
        ``build_request``, which tests and the golden call with no socket.
        """
        if self._model_info_attempts <= 0:
            return
        self._model_info_attempts -= 1
        owner = self._owner
        try:
            info = owner.client.models.retrieve(owner.model, timeout=_MODEL_INFO_TIMEOUT_S)
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if isinstance(status, int) and 400 <= status < 500 and status != 429:
                self._model_info_attempts = 0  # the endpoint has answered: no
            owner.logger.debug(
                "Models API gave nothing for %r (%s); keeping configured limits",
                owner.model, type(exc).__name__,
            )
            return
        out_cap = _positive_int(getattr(info, "max_tokens", None))
        in_cap = _positive_int(getattr(info, "max_input_tokens", None))
        self._model_info_attempts = 0
        if out_cap is not None:
            self._max_output_tokens = out_cap
        owner.model_input_limit = in_cap
        caps = getattr(info, "capabilities", None)
        dump = getattr(caps, "model_dump", None)
        try:
            dumped = dump(mode="json") if callable(dump) else None
        except Exception:
            dumped = None
        owner.model_capabilities = dumped if isinstance(dumped, dict) else None
        owner.logger.info(
            "Models API for %s: max_tokens=%s, max_input_tokens=%s",
            owner.model, out_cap, in_cap,
        )

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
        """The ``messages.create(**kwargs)`` dict.

        ``stream`` is accepted for the shared signature and ignored: both
        public calls run over the streaming transport (module docstring).
        """
        owner = self._owner
        if cache_boundary is not None and owner.cache_control is not None:
            # Marked in the canonical shape, then translated: one copy-on-mark
            # implementation, one budget, one retention-order rule.
            messages, tools = apply_cache_control(
                messages, tools, owner.cache_control,
                request_only_tail=cache_boundary,
            )
        system, wire_messages = translate_messages(messages)
        # ``max_tokens`` is required on this wire, so a call that names none
        # (the summarizer) takes the client's configured cap.
        cap = max_tokens or owner.max_tokens
        if self._max_output_tokens is not None:
            cap = min(cap, self._max_output_tokens)
        kwargs: Dict[str, Any] = {
            "model": owner.model,
            "max_tokens": cap,
            "messages": wire_messages,
            "stream": True,
        }
        if system is not None:
            kwargs["system"] = system
        # No ``temperature``: see the module docstring.
        if tools:
            kwargs["tools"] = translate_tools(tools)
        if owner.extra_body:
            kwargs["extra_body"] = owner.extra_body
        return kwargs

    def log_view(
        self,
        kwargs: Dict[str, Any],
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """What ``_log_request`` renders: the conversation as agentao holds it.

        The logger is incremental over message indices, and the translated
        request merges messages — so it renders the canonical list, plus the
        scalars that actually went out.
        """
        view: Dict[str, Any] = {
            "model": kwargs.get("model"),
            "max_tokens": kwargs.get("max_tokens"),
            "messages": messages,
        }
        if tools:
            view["tools"] = tools
        if "extra_body" in kwargs:
            view["extra_body"] = kwargs["extra_body"]
        return view

    def repair_request(self, err_text: str, kwargs: Dict[str, Any], *, stream: bool) -> bool:
        """One-shot fix-up of a rejected request. True → re-send now."""
        owner = self._owner
        match = _MAX_TOKENS_CAP.search(err_text)
        if match is not None:
            limit = int(match.group(2))
            if 0 < limit < kwargs.get("max_tokens", 0):
                self._max_output_tokens = limit
                kwargs["max_tokens"] = limit
                owner.logger.info(
                    "Model caps output at %d tokens; using that for this client",
                    limit,
                )
                return True
        return False

    def classify_retry(self, exc: BaseException) -> Tuple[bool, Optional[int], Optional[str]]:
        """``(retryable, status, retry_after)`` for this SDK's exceptions.

        Its own table rather than the shared one: the exception classes are
        different types, and OpenAI's quota codes would never match here.
        Anthropic reports an exhausted balance as a 400, which is already
        permanent by status.
        """
        try:
            from anthropic import APIConnectionError, APIStatusError, APITimeoutError
        except ImportError:
            return (False, None, None)

        if isinstance(exc, APIStatusError):
            status = getattr(exc, "status_code", None)
            if status in RETRYABLE_STATUS_CODES:
                retry_after = None
                if getattr(exc, "response", None) is not None:
                    retry_after = exc.response.headers.get("retry-after")
                return (True, status, retry_after)
            if status == 200:
                body = getattr(exc, "body", None)
                error = body.get("error") if isinstance(body, dict) else None
                kind = error.get("type") if isinstance(error, dict) else None
                mapped = _STREAM_ERROR_STATUS.get(kind) if isinstance(kind, str) else None
                if mapped is not None:
                    return (True, mapped, None)
            return (False, status, None)
        if isinstance(exc, (APITimeoutError, APIConnectionError)):
            return (True, None, None)
        return (False, None, None)

    # -- response -----------------------------------------------------------

    def send(self, kwargs: Dict[str, Any]) -> Any:
        """One ``chat()`` attempt — the stream, consumed with no callback."""
        return self.consume_stream(kwargs, self.new_accumulator(), None, None)

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

        A stream that is cancelled or simply ends early still builds a
        response, with ``finish_reason_reported`` left False — the same
        honesty the Chat Completions path keeps: a partial answer must not
        read as a finished one.
        """
        stream = self._owner.client.messages.create(**kwargs)
        # Per open content block, by index: a thinking block being assembled,
        # or a tool call's up-front ``input``.
        pending: Dict[int, Any] = {}
        usage: Dict[str, int] = {}
        try:
            for event in stream:
                if cancellation_token and cancellation_token.is_cancelled:
                    break
                kind = getattr(event, "type", None)
                if kind == "message_start":
                    message = event.message
                    if getattr(message, "model", None):
                        acc.response_model = message.model
                    _merge_usage(usage, getattr(message, "usage", None))
                elif kind == "content_block_start":
                    self._open_block(acc, pending, event.index, event.content_block)
                elif kind == "content_block_delta":
                    self._apply_delta(acc, pending, event.index, event.delta, on_text_chunk)
                elif kind == "content_block_stop":
                    self._close_block(acc, pending, event.index)
                elif kind == "message_delta":
                    _merge_usage(usage, getattr(event, "usage", None))
                    stop_reason = getattr(event.delta, "stop_reason", None)
                    if stop_reason:
                        acc.finish_reason = _FINISH_REASONS.get(stop_reason, "stop")
                        acc.finish_reason_reported = True
        finally:
            # In the ``finally``, not after it: a stream that raises part-way
            # has usually already said what it cost. ``message_start`` carries
            # the whole input count, so a request that died on its third event
            # was still a request of that size, and ``LLMClient`` counts what
            # ``acc`` holds whether or not this returns. What the server
            # *reported*, which is not a claim about what it billed.
            if usage:
                acc.usage_data = _Usage(usage)
            close = getattr(stream, "close", None)
            if callable(close):
                close()
        return acc.build()

    @staticmethod
    def _open_block(
        acc: _StreamAccumulator,
        pending: Dict[int, Any],
        index: int,
        block: Any,
    ) -> None:
        kind = getattr(block, "type", None)
        if kind == "tool_use":
            acc.tool_calls_data[index] = {
                "id": block.id, "name": block.name, "arguments": "",
            }
            # Kept until the block closes, for a gateway that sends the whole
            # input up front and no deltas.
            pending[index] = getattr(block, "input", None)
        elif kind == "thinking":
            pending[index] = {
                "type": "thinking",
                "thinking": getattr(block, "thinking", "") or "",
                "signature": getattr(block, "signature", "") or "",
            }
        elif kind == "redacted_thinking":
            data = getattr(block, "data", None)
            if isinstance(data, str) and data:
                acc.thinking_blocks.append({"type": "redacted_thinking", "data": data})

    @staticmethod
    def _apply_delta(
        acc: _StreamAccumulator,
        pending: Dict[int, Any],
        index: int,
        delta: Any,
        on_text_chunk: Optional[Any],
    ) -> None:
        kind = getattr(delta, "type", None)
        if kind == "text_delta":
            if delta.text:
                acc.content_parts.append(delta.text)
                if on_text_chunk:
                    on_text_chunk(delta.text)
                    acc.progress_made = True
        elif kind == "input_json_delta":
            call = acc.tool_calls_data.get(index)
            if call is not None and delta.partial_json:
                call["arguments"] += delta.partial_json
        elif kind == "thinking_delta":
            if delta.thinking:
                acc.reasoning_parts.append(delta.thinking)
                if index not in acc.tool_calls_data and isinstance(pending.get(index), dict):
                    pending[index]["thinking"] += delta.thinking
        elif kind == "signature_delta":
            if (
                delta.signature
                and index not in acc.tool_calls_data
                and isinstance(pending.get(index), dict)
            ):
                pending[index]["signature"] += delta.signature

    @staticmethod
    def _close_block(
        acc: _StreamAccumulator,
        pending: Dict[int, Any],
        index: int,
    ) -> None:
        call = acc.tool_calls_data.get(index)
        if call is not None:
            initial = pending.pop(index, None)
            if not call["arguments"]:
                # The raw text is kept whenever the model streamed any, so a
                # call cut off mid-argument reaches the runtime's own repair
                # and truncation handling unparsed.
                call["arguments"] = json.dumps(
                    initial if isinstance(initial, dict) else {},
                    ensure_ascii=False,
                )
            return
        block = pending.pop(index, None)
        # The signature arrives last. A block cut off before it is unsigned,
        # and an unsigned block is rejected — the display copy in
        # ``reasoning_content`` is all that survives of it.
        if block is not None and block["signature"]:
            acc.thinking_blocks.append(block)
