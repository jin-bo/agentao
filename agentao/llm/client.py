"""OpenAI-compatible LLM client.

The retry policy and streaming duck-types are split into sibling
modules (``_retry``, ``_stream_response``) and re-imported here so the
public + test-patch surface of ``agentao.llm.client`` is unchanged:
``LLMClient``, ``OpenAI`` (via PEP 562), retry constants
(``MAX_BACKOFF_SECONDS`` etc.), and the test-imported ``_classify_retry``
/ ``_compute_backoff_delay`` / ``_interruptible_sleep`` /
``_parse_retry_after`` helpers.

Constants are imported (not aliased) so they bind into this module's
namespace — that's load-bearing for ``monkeypatch.setattr(client_mod,
"MAX_TOTAL_RETRY_SECONDS", 1.0)`` to affect the deadline reads in
``chat()`` / ``chat_stream()`` (Python ``LOAD_GLOBAL`` resolves free
variables against the function's owning module).
"""

import copy
import logging
import logging.handlers
import random
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ._retry import (
    BASE_BACKOFF_SECONDS,
    JITTER_FRACTION,
    MAX_BACKOFF_SECONDS,
    MAX_RETRY_ATTEMPTS,
    MAX_TOTAL_RETRY_SECONDS,
    RETRYABLE_STATUS_CODES,
    _classify_retry,
    _compute_backoff_delay,
    _interruptible_sleep,
    _is_streaming_unsupported,
    _is_temperature_unsupported,
    _mark_streamed,
    _parse_retry_after,
)
from ._stream_response import _StreamAccumulator, _StreamResponse
from ._logging import _LoggingMixin
from ..paths import user_root

# `openai` is deferred (P0.5): merely importing ``LLMClient`` should not pull
# in the OpenAI SDK. Hosts that inject their own ``llm_client=`` never load
# it; hosts that use this default class load it on first construction.
#
# A PEP 562 ``__getattr__`` exposes ``OpenAI`` as a module attribute on first
# access so existing tests that ``patch("agentao.llm.client.OpenAI")`` keep
# working without forcing an import-time load. Construction sites use
# ``_openai_client_cls()`` so the patched class wins.
if TYPE_CHECKING:
    from openai import OpenAI as _OpenAIClient


def _openai_client_cls() -> "type[_OpenAIClient]":
    g = globals()
    if "OpenAI" not in g:
        from openai import OpenAI as _OpenAIImpl

        g["OpenAI"] = _OpenAIImpl
    return g["OpenAI"]


def __getattr__(name: str):
    if name == "OpenAI":
        return _openai_client_cls()
    raise AttributeError(f"module 'agentao.llm.client' has no attribute {name!r}")


#: Sentinel for ``reconfigure(base_url=...)`` / ``set_provider`` meaning "keep
#: the current base_url". Distinct from ``None``, which **clears** base_url to
#: the SDK default — needed so a provider switch can drop a previous provider's
#: custom endpoint instead of silently inheriting it.
KEEP_BASE_URL: Any = object()

#: Request-body fields the client owns from the normal request build. ``extra_body``
#: is merged *into the body* by the SDK (last-wins), so a key here that also appears
#: in ``extra_body`` would shadow the client's value. Used only for the one-time
#: construction warning (§3.3 of host-llm-extra-params.md) — not a hot-path check.
_STRUCTURAL_BODY_KEYS = frozenset({
    "model", "messages", "stream", "stream_options",
    "tools", "tool_choice", "temperature",
    "max_tokens", "max_completion_tokens",
})


class LLMClient(_LoggingMixin):
    """OpenAI-compatible LLM client with comprehensive logging.

    Pass ``logger=...`` to skip all ``agentao`` package-root mutation
    (handler attach, level set, marker eviction) — embedded hosts own
    their stack. Pass ``log_file=None`` to skip the file handler.

    The full-fidelity request/response logging (``_log_request`` /
    ``_log_response``) is provided by :class:`_LoggingMixin`.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 65536,
        extra_body: Optional[Dict[str, Any]] = None,
        log_file: Optional[str] = "agentao.log",
        logger: Optional[logging.Logger] = None,
    ):
        """Initialize LLM client.

        ``api_key`` / ``base_url`` / ``model`` are required keyword-only
        arguments. The client never reads environment variables on its
        own — embedded hosts construct it directly with explicit values,
        and CLI / ACP go through
        :func:`agentao.embedding.build_from_environment`, which is the
        single place that resolves ``LLM_PROVIDER`` / ``*_API_KEY`` /
        ``*_BASE_URL`` / ``*_MODEL`` / ``LLM_TEMPERATURE`` /
        ``LLM_MAX_TOKENS`` from the surrounding environment.

        Args:
            api_key: API key for the LLM service.
            base_url: Base URL for the API endpoint.
            model: Model name to use.
            temperature: Sampling temperature (default 0.2).
            max_tokens: Default per-call output token cap (default 65536).
            extra_body: Optional host-supplied request-body passthrough,
                forwarded verbatim to ``.create()`` as the SDK's
                ``extra_body`` option (merged into the JSON request body).
                The escape hatch for params the closed request build does
                not expose — ``reasoning_effort`` / ``top_p`` / ``seed`` /
                ``response_format`` and any provider-specific field. The
                SDK / provider validates the values; the host configures
                its own endpoint. ``None``/empty → not forwarded → request
                is byte-identical to today. Must be a dict or ``None``.
            log_file: Path to log file for LLM interactions. ``None`` skips
                the file handler entirely.
            logger: Optional injected logger. When provided, the client
                uses it as ``self.logger`` and does not mutate
                ``logging.getLogger("agentao")`` — no level set, no
                handler attach, no marker eviction. Embedded hosts that
                own their logging stack should pass this.
        """
        if not api_key:
            raise ValueError("LLMClient requires a non-empty api_key.")
        if not base_url:
            raise ValueError("LLMClient requires a non-empty base_url.")
        if not model:
            raise ValueError("LLMClient requires a non-empty model.")
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.temperature = temperature
        self.max_tokens: int = max_tokens

        # Host-supplied request-body passthrough, forwarded verbatim to
        # .create() (§3.1). Explicit isinstance guard: a bare
        # ``dict(extra_body or {})`` would silently accept a list-of-pairs
        # (``[("x", 1)]``) and raise ValueError (not TypeError) on other
        # malformed shapes — fail fast with a clear contract instead. The
        # type-check needs no logger, so it can sit here; the structural-
        # overlap *warning* is deferred until after logger init below.
        if extra_body is not None and not isinstance(extra_body, dict):
            raise TypeError("LLMClient.extra_body must be a dict or None.")
        # deepcopy (not a shallow ``dict(...)``) so construction truly freezes
        # the config: a host that retains and later mutates a NESTED value
        # (e.g. ``extra_body["extra_headers"]["Authorization"]``) cannot alter
        # in-flight requests through the shared reference.
        self.extra_body: Dict[str, Any] = copy.deepcopy(extra_body) if extra_body else {}

        # Set to True after detecting the model requires max_completion_tokens
        self._use_max_completion_tokens: bool = False

        # When True, 'temperature' is dropped from requests. Set either by the
        # user (/temperature off) or auto-latched once the model rejects the
        # parameter — see the one-shot fix-up in chat()/chat_stream(). Reasoning
        # models (o1/o3/gpt-5, …) reject any non-default temperature.
        self.omit_temperature: bool = False

        # max_retries=0: defer retry policy to _classify_retry / _compute_backoff_delay
        # so 408/409/425/429/5xx/529 + Retry-After + cancellation are handled
        # uniformly across non-stream and stream paths. Two layers of retry
        # would otherwise compound (SDK default is 2) and ignore Retry-After
        # the way our caller expects.
        self.client = _openai_client_cls()(
            api_key=self.api_key,
            base_url=self.base_url,
            max_retries=0,
        )

        # Injected logger → host owns the stack; skip package-root mutation.
        if logger is not None:
            self.logger = logger
        else:
            self.logger = logging.getLogger("agentao.llm")
            pkg_logger = logging.getLogger("agentao")
            pkg_logger.setLevel(logging.DEBUG)

            # Evict only our marker-tagged handlers so AcpServer's stderr
            # guard (and any other outsider handler) survives reconstruction.
            for h in list(pkg_logger.handlers):
                if getattr(h, "_agentao_llm_file_handler", False):
                    pkg_logger.removeHandler(h)
                    try:
                        h.close()
                    except Exception:
                        pass

            file_handler = self._build_file_handler(log_file) if log_file else None
            if file_handler is not None:
                file_handler.setLevel(logging.DEBUG)
                file_handler.setFormatter(
                    logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S")
                )
                file_handler._agentao_llm_file_handler = True  # type: ignore[attr-defined]
                pkg_logger.addHandler(file_handler)

        # Request counter for tracking
        self.request_count = 0
        # Cumulative token usage across all calls this session
        self.total_prompt_tokens: int = 0
        self.total_completion_tokens: int = 0
        # Track how many messages have already been logged (for incremental logging)
        self._logged_message_count = 0
        # Track system prompt content to log full text on first call and diffs on changes
        self._last_system_content: Optional[str] = None
        # Track tools hash to avoid logging unchanged tool lists repeatedly
        self._last_tools_hash: Optional[int] = None

        self.logger.info(f"LLMClient initialized with model: {self.model}")

        # Structural-overlap guard (§3.3): a key inside ``extra_body`` that the
        # SDK merges into the body could shadow a structural field the client
        # sets (``messages``/``model``/…). This is the host's explicit choice,
        # so it is not rejected — but shadowing ``messages`` is nasty to debug,
        # so warn ONCE here (not per request — that would spam the hot path).
        # Must run after logger init: ``self.logger`` does not exist until the
        # block above, so emitting it next to the §3.1 type-check would
        # AttributeError.
        if self.extra_body:
            overlap = _STRUCTURAL_BODY_KEYS & self.extra_body.keys()
            if overlap:
                self.logger.warning(
                    "LLMClient.extra_body contains key(s) %s that the client "
                    "sets as structural request fields; the SDK merges "
                    "extra_body into the body last-wins, so these shadow the "
                    "client's values.",
                    ", ".join(sorted(overlap)),
                )

    @staticmethod
    def _build_file_handler(log_file: str) -> Optional[logging.FileHandler]:
        """Open a FileHandler for ``log_file`` with an absolute path + fallback.

        Resolves a relative ``log_file`` to ``Path.cwd() / log_file`` so the
        target never depends on the process cwd at any later moment, then
        ``mkdir(parents=True, exist_ok=True)`` on its parent. If opening the
        handler still fails (read-only filesystem, permission denied, etc.),
        falls back to ``~/.agentao/agentao.log`` so headless launches like
        ACP — where the parent client may have spawned us with cwd="/" — can
        still start. Returns ``None`` only if even the home-dir fallback is
        unwritable, in which case the caller continues without a file handler.
        """
        primary = Path(log_file)
        if not primary.is_absolute():
            primary = Path.cwd() / primary

        try:
            primary.parent.mkdir(parents=True, exist_ok=True)
            return logging.handlers.RotatingFileHandler(
                primary, maxBytes=10_000_000, backupCount=5, encoding="utf-8"
            )
        except OSError as primary_err:
            fallback = user_root() / "agentao.log"
            if fallback == primary:
                # Already tried; nothing else to fall back to.
                print(
                    f"agentao: cannot open log file {primary}: {primary_err}; "
                    "continuing without file logging.",
                    file=sys.stderr,
                )
                return None
            try:
                fallback.parent.mkdir(parents=True, exist_ok=True)
                handler = logging.handlers.RotatingFileHandler(
                    fallback, maxBytes=10_000_000, backupCount=5, encoding="utf-8"
                )
                print(
                    f"agentao: log file {primary} is not writable "
                    f"({primary_err}); using {fallback} instead.",
                    file=sys.stderr,
                )
                return handler
            except OSError as fallback_err:
                print(
                    f"agentao: cannot open log file {primary} ({primary_err}) "
                    f"or fallback {fallback} ({fallback_err}); "
                    "continuing without file logging.",
                    file=sys.stderr,
                )
                return None

    def reconfigure(
        self,
        api_key: str,
        base_url: Any = KEEP_BASE_URL,
        model: Optional[str] = None,
    ) -> None:
        """Reinitialize the OpenAI client with new provider credentials.

        Args:
            api_key: New API key
            base_url: New base URL. The default sentinel ``KEEP_BASE_URL``
                keeps the current endpoint; an explicit value (including
                ``None``, which clears it to the SDK default) replaces it.
                The None-clears path lets a cross-provider switch drop a
                previous provider's custom endpoint.
            model: New model name (None keeps existing)
        """
        self.api_key = api_key
        if base_url is not KEEP_BASE_URL:
            self.base_url = base_url
        if model is not None:
            self.model = model

        # ``self.extra_body`` is intentionally NOT reset (§5): it is instance-
        # level host config, not a model-detected quirk. Unlike ``temperature``
        # (auto-recovered via the ``omit_temperature`` latch), a stale
        # ``extra_body`` key after a model switch has no latch — the host owns
        # dropping model-specific keys (e.g. ``reasoning_effort``) on switch.
        self.reset_capability_latches()
        self.client = _openai_client_cls()(
            api_key=self.api_key,
            base_url=self.base_url,
            max_retries=0,
        )
        self.logger.info(
            f"LLMClient reconfigured: model={self.model}, base_url={self.base_url}"
        )

    def reset_capability_latches(self) -> None:
        """Clear auto-detected, model-specific request quirks.

        ``_use_max_completion_tokens`` and ``omit_temperature`` are latched
        per model on first rejection. They must be cleared whenever the model
        or provider changes — otherwise a quirk detected for one model (e.g. a
        reasoning model that rejects ``temperature``) silently sticks to the
        next model that supports it. A user-set ``/temperature off`` is also
        cleared here; the model is being swapped, so its premise no longer
        holds and re-detection re-latches if the new model also rejects it.
        """
        self._use_max_completion_tokens = False
        self.omit_temperature = False

    def _build_request_kwargs(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        max_tokens: Optional[int],
        *,
        stream: bool,
    ) -> Dict[str, Any]:
        """Assemble the ``.create(**kwargs)`` request dict for one call.

        Single source for both the non-streaming (``chat``) and streaming
        (``chat_stream``) paths — they used to duplicate this closed dict,
        which is how the ``extra_body`` passthrough gap went unnoticed.
        ``extra_body`` is itself a valid ``.create()`` argument, so adding it
        here forwards it through both call sites with no signature change;
        omitted when empty so the request stays byte-identical to the
        pre-passthrough build (back-compat).
        """
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }
        if stream:
            kwargs["stream"] = True
            kwargs["stream_options"] = {"include_usage": True}
        if not self.omit_temperature:
            kwargs["temperature"] = self.temperature
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if max_tokens:
            key = "max_completion_tokens" if self._use_max_completion_tokens else "max_tokens"
            kwargs[key] = max_tokens
        if self.extra_body:
            kwargs["extra_body"] = self.extra_body
        return kwargs

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: Optional[int] = None,
    ) -> Any:
        """Send chat request to LLM.

        Args:
            messages: List of message dictionaries
            tools: Optional list of tool definitions
            max_tokens: Maximum tokens to generate

        Returns:
            Response from the LLM
        """
        self.request_count += 1
        request_id = f"req_{self.request_count}"

        # Build request parameters (single source — see _build_request_kwargs)
        kwargs = self._build_request_kwargs(messages, tools, max_tokens, stream=False)

        # Log request
        self._log_request(request_id, kwargs)

        deadline = time.monotonic() + MAX_TOTAL_RETRY_SECONDS
        attempt = 0  # number of retries performed; first try is attempt 0
        while True:
            try:
                raw = self.client.chat.completions.with_raw_response.create(**kwargs)
                response = raw.parse()

                if hasattr(response, "usage") and response.usage:
                    self.total_prompt_tokens += response.usage.prompt_tokens or 0
                    self.total_completion_tokens += response.usage.completion_tokens or 0

                self._log_response(request_id, response)
                return response

            except Exception as e:
                # max_tokens vs max_completion_tokens param mismatch is a one-shot
                # fix-up, not a retry — does not consume retry budget. The flag is
                # latched to True after the first hit so this branch can fire at
                # most once per LLMClient instance.
                if (
                    not self._use_max_completion_tokens
                    and "max_tokens" in str(e)
                    and "max_completion_tokens" in str(e)
                ):
                    self._use_max_completion_tokens = True
                    self.logger.info("Switching to max_completion_tokens for this model")
                    if "max_tokens" in kwargs:
                        kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")
                    continue

                # temperature unsupported (reasoning models: o1/o3/gpt-5, …) —
                # one-shot fix-up, same shape as the max_completion_tokens branch.
                if not self.omit_temperature and _is_temperature_unsupported(str(e)):
                    self.omit_temperature = True
                    self.logger.info("Model rejects 'temperature'; omitting it for this client")
                    kwargs.pop("temperature", None)
                    continue

                retryable, status, retry_after = _classify_retry(e)
                if not retryable or attempt >= MAX_RETRY_ATTEMPTS - 1:
                    import traceback
                    self.logger.error(
                        f"[{request_id}] API call failed: {str(e)}\n{traceback.format_exc()}"
                    )
                    raise

                delay = _compute_backoff_delay(attempt, retry_after)
                remaining = deadline - time.monotonic()
                if delay > remaining:
                    import traceback
                    self.logger.error(
                        f"[{request_id}] retry budget exhausted after "
                        f"{attempt + 1} attempt(s): {str(e)}\n{traceback.format_exc()}"
                    )
                    raise

                label = f"status={status}" if status is not None else type(e).__name__
                self.logger.info(
                    f"[{request_id}] retryable error ({label}); "
                    f"attempt {attempt + 1} sleeping {delay:.2f}s"
                )
                time.sleep(delay)
                attempt += 1

    def _is_gemini(self) -> bool:
        """Return True when the configured endpoint is Gemini.

        Gemini thinking models include a thought_signature on tool call objects
        that must be round-tripped back on subsequent requests.  The OpenAI SDK
        drops unknown fields from streaming delta objects, so we bypass the
        streaming path for Gemini and use the non-streaming path (which returns
        full Pydantic objects that preserve all extra fields via model_dump()).
        """
        if self.base_url and "googleapis.com" in self.base_url:
            return True
        if self.model and self.model.lower().startswith("gemini"):
            return True
        return False

    def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: Optional[int] = None,
        on_text_chunk: Optional[Any] = None,
        cancellation_token: Optional[Any] = None,
    ) -> Any:
        """Streaming variant of chat(). Calls on_text_chunk(chunk) for each text delta.

        For Gemini models, delegates to chat() to preserve thought_signature on
        tool call objects (the OpenAI SDK drops unknown fields from streaming
        deltas, making round-tripping impossible).  on_text_chunk is still called
        with the full content so callers behave identically.

        Uses create(stream=True) for cross-provider compatibility (works with OpenAI,
        Anthropic, DeepSeek, and any OpenAI-compatible endpoint). Reconstructs
        a duck-type ChatCompletion response from accumulated chunks so agent.py can
        consume it identically to the non-streaming path.

        Args:
            messages: List of message dictionaries
            tools: Optional list of tool definitions
            max_tokens: Maximum tokens to generate
            on_text_chunk: Optional callable(str) invoked for each text delta

        Returns:
            ChatCompletion (Pydantic) or duck-type ChatCompletion response compatible with agent.py
        """
        # Gemini: bypass streaming to preserve thought_signature on tool calls
        if self._is_gemini():
            return self._emit_nonstreaming(messages, tools, max_tokens, on_text_chunk)

        self.request_count += 1
        request_id = f"req_{self.request_count}"

        kwargs = self._build_request_kwargs(messages, tools, max_tokens, stream=True)

        # Log without the stream flag (matches non-streaming log format)
        log_kwargs = {k: v for k, v in kwargs.items() if k != "stream"}
        self._log_request(request_id, log_kwargs)

        deadline = time.monotonic() + MAX_TOTAL_RETRY_SECONDS
        attempt = 0  # number of retries performed; first try is attempt 0
        while True:
            acc = _StreamAccumulator(self.model)
            try:
                response = self._consume_stream(
                    kwargs, acc, on_text_chunk, cancellation_token,
                )
                self._log_response(request_id, response)
                return response

            except Exception as e:
                # The error handling stays lexically inside the ``except``
                # block on purpose: its bare ``raise`` statements rely on the
                # active exception bound here, and ``acc.progress_made`` (set
                # by ``_consume_stream`` before it propagated) decides whether
                # a retry would duplicate already-emitted content.
                err_str = str(e).lower()

                # max_tokens vs max_completion_tokens param mismatch — one-shot
                # fix-up. Only safe at zero progress (otherwise we'd re-emit
                # content via on_text_chunk). Latched flag prevents loops.
                if (
                    not acc.progress_made
                    and not self._use_max_completion_tokens
                    and "max_tokens" in err_str
                    and "max_completion_tokens" in err_str
                ):
                    self._use_max_completion_tokens = True
                    self.logger.info(
                        "Switching to max_completion_tokens for this model (stream retry)"
                    )
                    if "max_tokens" in kwargs:
                        kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")
                    continue

                # temperature unsupported — one-shot fix-up. Only safe at zero
                # progress (otherwise the retry would re-emit content).
                if (
                    not acc.progress_made
                    and not self.omit_temperature
                    and _is_temperature_unsupported(err_str)
                ):
                    self.omit_temperature = True
                    self.logger.info(
                        "Model rejects 'temperature'; omitting it for this client (stream retry)"
                    )
                    kwargs.pop("temperature", None)
                    continue

                # Status-based retry classification — done up front so a
                # retryable upstream/proxy failure (whose message often
                # contains "upstream") doesn't get mis-routed into the
                # streaming-unsupported fallback below.
                retryable, status, retry_after = _classify_retry(e)

                # Provider rejected stream=True altogether — fall back to
                # non-streaming chat(). One-shot, only at zero progress, and
                # only for clearly non-retryable errors that explicitly say
                # streaming is unsupported (never bare "stream"/"streaming",
                # which also matches "upstream" in 502/503 proxy errors).
                if (
                    not acc.progress_made
                    and not retryable
                    and _is_streaming_unsupported(err_str)
                ):
                    self.logger.info(
                        f"[{request_id}] Streaming not supported by provider; "
                        "falling back to non-streaming"
                    )
                    try:
                        return self._emit_nonstreaming(
                            messages, tools, max_tokens, on_text_chunk,
                        )
                    except Exception as fallback_e:
                        import traceback
                        self.logger.error(
                            f"[{request_id}] Non-streaming fallback also failed: "
                            f"{str(fallback_e)}\n{traceback.format_exc()}"
                        )
                        _mark_streamed(fallback_e, False)
                        raise fallback_e

                # Mid-stream failures cannot be retried safely —
                # on_text_chunk has already fired and the duck-type
                # response would otherwise duplicate content.
                # Attach .streamed so callers (and the runtime's
                # LLM_CALL_COMPLETED error payload) can distinguish "host
                # already saw partial chunks" from "nothing reached the host".
                _mark_streamed(e, acc.progress_made)
                if (
                    not retryable
                    or acc.progress_made
                    or attempt >= MAX_RETRY_ATTEMPTS - 1
                ):
                    import traceback
                    if acc.progress_made and retryable:
                        self.logger.error(
                            f"[{request_id}] mid-stream failure (cannot retry safely): "
                            f"{str(e)}\n{traceback.format_exc()}"
                        )
                    else:
                        self.logger.error(
                            f"[{request_id}] Streaming API call failed: "
                            f"{str(e)}\n{traceback.format_exc()}"
                        )
                    raise

                delay = _compute_backoff_delay(attempt, retry_after)
                remaining = deadline - time.monotonic()
                if delay > remaining:
                    import traceback
                    self.logger.error(
                        f"[{request_id}] retry budget exhausted after "
                        f"{attempt + 1} attempt(s): {str(e)}\n{traceback.format_exc()}"
                    )
                    raise

                label = f"status={status}" if status is not None else type(e).__name__
                self.logger.info(
                    f"[{request_id}] retryable streaming error ({label}); "
                    f"attempt {attempt + 1} sleeping {delay:.2f}s"
                )
                if not _interruptible_sleep(delay, cancellation_token):
                    self.logger.info(
                        f"[{request_id}] retry sleep interrupted by cancellation"
                    )
                    raise
                attempt += 1

    def _consume_stream(
        self,
        kwargs: Dict[str, Any],
        acc: "_StreamAccumulator",
        on_text_chunk: Optional[Any],
        cancellation_token: Optional[Any],
    ) -> "_StreamResponse":
        """Run one streaming attempt, accumulating chunks into ``acc``.

        Returns the built duck-type response on success. On error it
        propagates the exception after ``acc`` already reflects any partial
        progress (notably ``acc.progress_made``), so ``chat_stream``'s
        retry handler can tell whether a retry would duplicate
        already-emitted content.
        """
        stream = self.client.chat.completions.create(**kwargs)

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

            # Accumulate tool call deltas
            if delta and delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
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

            if hasattr(chunk, "model") and chunk.model:
                acc.response_model = chunk.model

        # Accumulate session token totals
        if acc.usage_data is not None:
            self.total_prompt_tokens += getattr(acc.usage_data, "prompt_tokens", 0) or 0
            self.total_completion_tokens += getattr(acc.usage_data, "completion_tokens", 0) or 0

        # Build a duck-type response that agent.py can consume like a ChatCompletion
        return acc.build()

    def _emit_nonstreaming(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        max_tokens: Optional[int],
        on_text_chunk: Optional[Any],
    ) -> Any:
        """Run the non-streaming ``chat()`` and replay its full content
        through ``on_text_chunk`` so streaming callers behave identically.

        Shared by the Gemini bypass and the streaming-unsupported fallback.
        """
        response = self.chat(messages, tools=tools, max_tokens=max_tokens)
        if on_text_chunk:
            content = response.choices[0].message.content
            if content:
                on_text_chunk(content)
        return response
