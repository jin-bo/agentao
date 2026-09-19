"""The LLM client: one retry / logging shell over a wire-protocol adapter.

``LLMClient`` speaks Chat Completions by default (``openai-completions``) and
Anthropic's Messages API when constructed with
``api_format="anthropic-messages"``. What differs per protocol lives in an
adapter (``_openai_completions``, ``_anthropic_messages``); the retry loop,
the logging and the token totals stay here, once.

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
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ._api_format import ANTHROPIC_MESSAGES, OPENAI_COMPLETIONS, resolve_api_format
from ._cache_control import resolve_cache_control
from ._openai_completions import OpenAICompletionsAdapter
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
    _mark_streamed,
    _parse_retry_after,
)
from ._stream_response import _StreamAccumulator, _StreamResponse
from ._usage import cache_token_counts, positive_int
from ._logging import _LoggingMixin
from ..paths import user_root
from ..security.secret_scan import redact

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


class _RedactingFormatter(logging.Formatter):
    """Formatter that strips credential-shaped strings from log records.

    ``agentao.log`` records full LLM requests/responses and full tool
    results with no truncation, so ``run_shell_command("env")`` or a
    ``cat .env`` writes live credentials to disk in plaintext. This is the
    single choke point for everything that reaches the log file.

    Implemented as a Formatter rather than a Filter deliberately: a Filter
    would have to mutate the shared ``LogRecord``, and that mutation would
    leak into every *other* handler on the logger (an embedded host's own
    handlers, the ACP stderr guard) in handler-registration order. A
    Formatter only shapes the bytes this handler writes.

    Only the file handler installs this. Redaction is pattern-based, so it
    is applied where the cost of a false positive is a slightly less
    readable log line — never to the tool result handed to the model,
    where a false positive corrupts the agent's working data.
    """

    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(record)
        try:
            return redact(formatted)
        except Exception:
            # Logging must never take down the caller. A scanner bug should
            # cost fidelity in the log, not the session.
            return formatted


class LLMClient(_LoggingMixin):
    """LLM client with comprehensive logging, over one configured wire protocol.

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
        prompt_cache: Optional[str] = None,
        prompt_cache_ttl: Optional[str] = None,
        api_format: Optional[str] = None,
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
            prompt_cache: Explicit prompt-cache breakpoint format for this
                endpoint — ``"anthropic"`` or ``None``/``"off"`` (default).
                Opt-in because agentao can verify the SDK forwards the key
                but not that *your* endpoint honours it; see
                :mod:`agentao.llm._cache_control`. Raises ``ValueError`` on
                an unknown value rather than quietly sending nothing.
            prompt_cache_ttl: Retention hint, ``"5m"`` (the provider default,
                same as ``None``) or ``"1h"``. Ignored when ``prompt_cache``
                is off.
            api_format: The wire protocol spoken to ``base_url`` —
                ``"openai-completions"`` (the default, same as ``None``) or
                ``"anthropic-messages"`` (Anthropic's Messages API).
                Configured, never inferred from the URL or the model name;
                only :meth:`reconfigure` changes it. An unknown value raises
                ``ValueError`` listing the valid ones. See
                :mod:`agentao.llm._api_format`.
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
        # Resolved before anything is built, so a misspelled format fails
        # without having opened a log file or an SDK client.
        self.api_format: str = resolve_api_format(api_format)
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

        # Explicit prompt-cache marker for this endpoint, or None when off.
        # Resolved (and validated) once at construction; which *calls* carry
        # markers is decided per call — see ``_build_request_kwargs``.
        self.cache_control: Optional[Dict[str, str]] = resolve_cache_control(
            prompt_cache, prompt_cache_ttl,
        )
        # The configured spellings, kept beside the resolved marker so a
        # sub-agent's raw-config build can inherit them (``_llm_config``).
        # Not derived back from ``cache_control``: one format resolves to one
        # marker today, and reversing that mapping would quietly pick the wrong
        # format the day there are two.
        self.prompt_cache: Optional[str] = prompt_cache
        self.prompt_cache_ttl: Optional[str] = prompt_cache_ttl

        # Set to True after detecting the model requires max_completion_tokens
        self._use_max_completion_tokens: bool = False

        # When True, 'temperature' is dropped from requests. Set either by the
        # user (/temperature off) or auto-latched once the model rejects the
        # parameter — see the one-shot fix-up in chat()/chat_stream(). Reasoning
        # models (o1/o3/gpt-5, …) reject any non-default temperature.
        self.omit_temperature: bool = False

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
                    _RedactingFormatter("%(asctime)s %(message)s", datefmt="%H:%M:%S")
                )
                file_handler._agentao_llm_file_handler = True  # type: ignore[attr-defined]
                pkg_logger.addHandler(file_handler)

        # Request counter for tracking
        self.request_count = 0
        # Cumulative token usage across all calls this session
        self.total_prompt_tokens: int = 0
        self.total_completion_tokens: int = 0
        # Parts *of* ``total_prompt_tokens``, not additions to it: the input
        # a provider bills at its cache rates (see ``_usage.py``).
        self.total_cache_read_tokens: int = 0
        self.total_cache_creation_tokens: int = 0
        # ``+=`` on an attribute is a read and a write. A background sub-agent
        # adds its usage from its own thread (``add_usage``) while this
        # client's turn adds its own, so every writer goes through the lock.
        self._usage_lock = threading.Lock()
        # Track how many messages have already been logged (for incremental logging)
        self._logged_message_count = 0
        # Track system prompt content to log full text on first call and diffs on changes
        self._last_system_content: Optional[str] = None
        # Track tools hash to avoid logging unchanged tool lists repeatedly
        self._last_tools_hash: Optional[int] = None

        # What the provider's Models API says about ``self.model``, when the
        # wire has one and the endpoint implements it (``anthropic-messages``:
        # ``GET /v1/models/{id}``). Filled by the adapter on its first request
        # and cleared with the capability latches; ``None`` means "not told",
        # never "unlimited". ``model_input_limit`` narrows the context window
        # (``ContextManager.effective_max_tokens``).
        self.model_input_limit: Optional[int] = None
        self.model_capabilities: Optional[Dict[str, Any]] = None

        # The adapter owns everything protocol-specific, the SDK client
        # included; ``self.client`` stays the live SDK object because
        # ``list_available_models`` and a good many tests reach for it.
        self._adapter = self._make_adapter()
        self.client = self._adapter.create_client()

        self.logger.info(f"LLMClient initialized with model: {self.model}")
        if self.api_format != OPENAI_COMPLETIONS:
            self.logger.info(f"LLMClient wire protocol: {self.api_format}")

        # Structural-overlap guard (§3.3): a key inside ``extra_body`` that the
        # SDK merges into the body could shadow a structural field the client
        # sets (``messages``/``model``/…). This is the host's explicit choice,
        # so it is not rejected — but shadowing ``messages`` is nasty to debug,
        # so warn ONCE here (not per request — that would spam the hot path).
        # Must run after logger init: ``self.logger`` does not exist until the
        # block above, so emitting it next to the §3.1 type-check would
        # AttributeError. The key set is the adapter's: which fields are
        # structural depends on the wire (``system`` is one on Messages, and
        # ``temperature`` is not).
        self._warn_structural_overlap()

    def _warn_structural_overlap(self) -> None:
        overlap = self._adapter.structural_body_keys & (self.extra_body or {}).keys()
        if overlap:
            self.logger.warning(
                "LLMClient.extra_body contains key(s) %s that the client "
                "sets as structural request fields; the SDK merges "
                "extra_body into the body last-wins, so these shadow the "
                "client's values.",
                ", ".join(sorted(overlap)),
            )

    def _make_adapter(self) -> Any:
        if self.api_format == ANTHROPIC_MESSAGES:
            # Imported here so the default wire never loads this module.
            from ._anthropic_messages import AnthropicMessagesAdapter

            return AnthropicMessagesAdapter(self)
        return OpenAICompletionsAdapter(self, _openai_client_cls)

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
        api_format: Optional[str] = None,
    ) -> None:
        """Reinitialize the SDK client with new provider credentials.

        Args:
            api_key: New API key
            base_url: New base URL. The default sentinel ``KEEP_BASE_URL``
                keeps the current endpoint; an explicit value (including
                ``None``, which clears it to the SDK default) replaces it.
                The None-clears path lets a cross-provider switch drop a
                previous provider's custom endpoint.
            model: New model name (None keeps existing)
            api_format: The new provider's wire protocol (None keeps the
                current one). A different value replaces the adapter, and
                with it that wire's own latches. Validated before anything
                is touched, so a refused value leaves the client as it was.
        """
        _new_format = (
            self.api_format if api_format is None else resolve_api_format(api_format)
        )
        _old_base = self.base_url
        # What a failed adapter/SDK-client build below puts back. Without it a
        # raise from ``create_client`` (the lazy ``anthropic`` import is the
        # reachable one) leaves ``api_format`` / ``_adapter`` on the new wire
        # and ``client`` on the old SDK object — and the next request calls
        # one protocol's method on the other protocol's client.
        _rollback = {
            name: getattr(self, name)
            for name in (
                "api_key", "base_url", "model", "api_format", "_adapter",
                "client", "cache_control", "prompt_cache", "prompt_cache_ttl",
            )
        }
        try:
            self._apply_reconfigure(api_key, base_url, model, _new_format, _old_base)
        except BaseException:
            for name, value in _rollback.items():
                setattr(self, name, value)
            raise

    def _apply_reconfigure(
        self,
        api_key: str,
        base_url: Any,
        model: Optional[str],
        _new_format: str,
        _old_base: Any,
    ) -> None:
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
        #
        # ``cache_control`` is a third category and **is** dropped on an
        # endpoint change. It is neither host passthrough nor a detected quirk:
        # agentao mints the markers itself on the strength of the operator
        # asserting that *this endpoint* honours them. A new base URL is a new
        # deployment, that assertion no longer covers it, and there is no latch
        # here — an endpoint that 400s on the key would 400 on every request
        # until someone noticed. Same family as the observed context limit and
        # the thinking-artifact purge, which also clear on an endpoint change.
        # A bare credential rotation (same base_url) keeps it. A wire-protocol
        # change counts as an endpoint change even on the same URL: the
        # assertion was made about the other protocol.
        if (
            self.base_url != _old_base or _new_format != self.api_format
        ) and self.cache_control is not None:
            self.logger.warning(
                "Endpoint changed (%s %s -> %s %s); dropping the explicit "
                "prompt-cache breakpoints configured for the old one. "
                "Re-set prompt_cache / LLM_PROMPT_CACHE once the new endpoint "
                "is verified.",
                _old_base, self.api_format, self.base_url, _new_format,
            )
            self.cache_control = None
            self.prompt_cache = None
            self.prompt_cache_ttl = None
        if _new_format != self.api_format:
            self.logger.info(
                "Wire protocol changed: %s -> %s", self.api_format, _new_format
            )
            self.api_format = _new_format
            self._adapter = self._make_adapter()
            # ``extra_body`` stays (above), and that is now a sharper edge: its
            # keys were written for the other wire's request body. Said once,
            # here, and the structural overlap is re-read against the new
            # adapter — ``system`` is inert on Chat Completions and replaces
            # the whole system prompt on Messages.
            if self.extra_body:
                self.logger.warning(
                    "extra_body key(s) %s were configured for the previous "
                    "wire protocol and go to %s unchanged.",
                    ", ".join(sorted(self.extra_body)), _new_format,
                )
                self._warn_structural_overlap()
        self.reset_capability_latches()
        self.client = self._adapter.create_client()
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
        self.model_input_limit = None
        self.model_capabilities = None
        self._adapter.reset_latches()

    def _build_request_kwargs(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        max_tokens: Optional[int],
        *,
        stream: bool,
        cache_boundary: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Assemble the ``.create(**kwargs)`` request dict for one call.

        Single source for both the non-streaming (``chat``) and streaming
        (``chat_stream``) paths — they used to duplicate this closed dict,
        which is how the ``extra_body`` passthrough gap went unnoticed.
        ``extra_body`` is itself a valid ``.create()`` argument, so adding it
        here forwards it through both call sites with no signature change;
        omitted when empty so the request stays byte-identical to the
        pre-passthrough build (back-compat).

        ``cache_boundary`` opts *this call* into explicit prompt-cache
        breakpoints (stage 0b) and says how many trailing messages are
        request-only, so the conversation breakpoint lands at the end of stable
        history. ``None`` — the default — marks nothing. Per call rather than
        per client because a cache *write* costs more than an ordinary read:
        the agent turn has a large prefix worth caching, while the one-shot
        summarizer prompt would pay the write premium for a prefix nothing
        reads back.

        This is also the last point before the wire. Marking here rather than
        in the chat loop is what keeps the markers out of the replay record and
        out of ``agent.messages``: everything above this line saw the unmarked
        request.
        """
        return self._adapter.build_request(
            messages, tools, max_tokens, stream=stream,
            cache_boundary=cache_boundary,
        )

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: Optional[int] = None,
        *,
        cache_boundary: Optional[int] = None,
    ) -> Any:
        """Send chat request to LLM.

        Args:
            messages: List of message dictionaries
            tools: Optional list of tool definitions
            max_tokens: Maximum tokens to generate
            cache_boundary: Opt this call into explicit prompt-cache
                breakpoints, and say how many trailing messages are
                request-only. See :meth:`_build_request_kwargs`.

        Returns:
            Response from the LLM
        """
        self.request_count += 1
        request_id = f"req_{self.request_count}"

        # Before the request is built, so what is logged is what is sent.
        self._adapter.prepare()
        # Build request parameters (single source — see _build_request_kwargs)
        kwargs = self._build_request_kwargs(
            messages, tools, max_tokens, stream=False,
            cache_boundary=cache_boundary,
        )

        # Log request
        self._log_request(request_id, self._adapter.log_view(kwargs, messages, tools))

        deadline = time.monotonic() + MAX_TOTAL_RETRY_SECONDS
        attempt = 0  # number of retries performed; first try is attempt 0
        while True:
            # Fresh per attempt, like ``chat_stream``'s: it is what the
            # attempt is counted from.
            acc = self._adapter.new_accumulator()
            try:
                try:
                    response = self._adapter.send(kwargs, acc)
                finally:
                    self._count_attempt(acc)

                self._log_response(request_id, response)
                return response

            except Exception as e:
                # A rejected parameter (max_tokens vs max_completion_tokens, an
                # unsupported temperature, …) is a one-shot fix-up, not a retry
                # — it does not consume retry budget. Each repair latches, so
                # it can fire at most once per model.
                if self._adapter.repair_request(str(e), kwargs, stream=False):
                    continue

                retryable, status, retry_after = self._adapter.classify_retry(e)
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
        *,
        cache_boundary: Optional[int] = None,
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
            cache_boundary: Opt this call into explicit prompt-cache
                breakpoints, and say how many trailing messages are
                request-only. See :meth:`_build_request_kwargs`.

        Returns:
            ChatCompletion (Pydantic) or duck-type ChatCompletion response compatible with agent.py
        """
        # Gemini: bypass streaming to preserve thought_signature on tool calls.
        # A quirk of Gemini *behind the Chat Completions wire* — an explicitly
        # configured native wire is dispatched first and never takes it.
        if self.api_format == OPENAI_COMPLETIONS and self._is_gemini():
            return self._emit_nonstreaming(
                messages, tools, max_tokens, on_text_chunk,
                cache_boundary=cache_boundary,
            )

        self.request_count += 1
        request_id = f"req_{self.request_count}"

        # The adapters first look at the token *inside* their event loop, which
        # is after the POST went out and the first event came back — so a turn
        # cancelled before that still sent the request and paid for the whole
        # prompt. ``prepare()`` is what makes the window worth closing: it can
        # hold this thread for seconds (a Models API lookup that is not
        # cancellable), and the runner's own check sits before this call.
        # Checked on both sides of it: before, so a cancelled turn does not
        # start the lookup; after, so one cancelled during it sends nothing.
        # The exit is the one a mid-stream ``break`` takes — an empty response
        # with ``finish_reason_reported`` False, which ``runtime/turn.py``
        # already classifies as cancelled — never a new exception.
        if self._cancelled_before_send(cancellation_token, request_id):
            return self._adapter.new_accumulator().build()
        self._adapter.prepare()
        if self._cancelled_before_send(cancellation_token, request_id):
            return self._adapter.new_accumulator().build()
        kwargs = self._build_request_kwargs(
            messages, tools, max_tokens, stream=True,
            cache_boundary=cache_boundary,
        )

        # Log without the stream flag (matches non-streaming log format)
        self._log_request(request_id, self._adapter.log_view(kwargs, messages, tools))

        deadline = time.monotonic() + MAX_TOTAL_RETRY_SECONDS
        attempt = 0  # number of retries performed; first try is attempt 0
        while True:
            acc = self._adapter.new_accumulator()
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

                # A rejected parameter — one-shot fix-up. Only safe at zero
                # progress (otherwise we'd re-emit content via on_text_chunk).
                # Each repair latches, which prevents loops.
                if not acc.progress_made and self._adapter.repair_request(
                    err_str, kwargs, stream=True,
                ):
                    continue

                # Status-based retry classification — done up front so a
                # retryable upstream/proxy failure (whose message often
                # contains "upstream") doesn't get mis-routed into the
                # streaming-unsupported fallback below.
                retryable, status, retry_after = self._adapter.classify_retry(e)

                # Provider rejected stream=True altogether — fall back to
                # non-streaming chat(). One-shot, only at zero progress, and
                # only for clearly non-retryable errors that explicitly say
                # streaming is unsupported (never bare "stream"/"streaming",
                # which also matches "upstream" in 502/503 proxy errors).
                # Chat Completions only: the ``anthropic-messages`` wire has
                # no non-streaming transport (``chat()`` streams too), so the
                # "fallback" there would re-send the request that just failed.
                if (
                    not acc.progress_made
                    and not retryable
                    and self.api_format == OPENAI_COMPLETIONS
                    and _is_streaming_unsupported(err_str)
                ):
                    self.logger.info(
                        f"[{request_id}] Streaming not supported by provider; "
                        "falling back to non-streaming"
                    )
                    try:
                        return self._emit_nonstreaming(
                            messages, tools, max_tokens, on_text_chunk,
                            cache_boundary=cache_boundary,
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

    def _count_attempt(self, acc: "_StreamAccumulator") -> None:
        """Add one attempt to the session totals — the only place that does.

        Called from a ``finally`` by both entry points, so on the way out
        **either way**: an attempt that raised was still a request, and
        ``acc`` holds whatever usage the server had reported by then, the same
        contract it keeps for ``progress_made``. An adapter's one duty here is
        to leave that on ``acc.usage_data``. ``acc`` is built fresh per
        attempt, so each is counted at most once; a failed attempt and the
        retry after it are two requests, and both count. Nothing reported,
        nothing added: Chat Completions states usage only with its last chunk
        (or on the response, when not streaming).
        """
        if acc.usage_data is not None:
            self._count_response_usage(acc.usage_data)

    def _count_response_usage(self, usage: Any) -> None:
        cache_read, cache_creation = cache_token_counts(usage)
        self.add_usage(
            getattr(usage, "prompt_tokens", 0), getattr(usage, "completion_tokens", 0),
            cache_read_tokens=cache_read, cache_creation_tokens=cache_creation,
        )

    def add_usage(
        self, prompt_tokens: int, completion_tokens: int, *,
        cache_read_tokens: int = 0, cache_creation_tokens: int = 0,
    ) -> None:
        """Add to the session totals. Safe from any thread.

        The one writer of the four ``total_*_tokens`` counters besides
        ``reset_usage``: this client's own requests, and a sub-agent's usage
        rolled up when it finishes (``agents/tools/_wrapper.py``) — a
        sub-agent has its own client, so without that its requests were in
        nobody's total. Anything that is not a non-negative ``int`` is
        ignored: a mocked response answers any attribute.
        """
        with self._usage_lock:
            self.total_prompt_tokens += positive_int(prompt_tokens)
            self.total_completion_tokens += positive_int(completion_tokens)
            self.total_cache_read_tokens += positive_int(cache_read_tokens)
            self.total_cache_creation_tokens += positive_int(cache_creation_tokens)

    def reset_usage(self) -> None:
        """Zero the session totals (a new conversation). Same lock as the adds."""
        with self._usage_lock:
            self.total_prompt_tokens = 0
            self.total_completion_tokens = 0
            self.total_cache_read_tokens = 0
            self.total_cache_creation_tokens = 0

    def usage_snapshot(self) -> Dict[str, int]:
        """The four session totals, read together under the lock the adds take.

        Read one attribute at a time and an ``add_usage`` on another thread
        can land between two of the reads: a prompt count from after it beside
        a completion count from before — a state that never existed. Keys are
        the ones ``agentao run``'s ``usage`` and ``SubagentUsage`` use.
        """
        with self._usage_lock:
            return {
                "prompt_tokens": self.total_prompt_tokens,
                "completion_tokens": self.total_completion_tokens,
                "cache_read_tokens": self.total_cache_read_tokens,
                "cache_creation_tokens": self.total_cache_creation_tokens,
            }

    def _cancelled_before_send(self, cancellation_token: Optional[Any], request_id: str) -> bool:
        if cancellation_token is None or not cancellation_token.is_cancelled:
            return False
        self.logger.info("%s: cancelled before the request went out; nothing sent", request_id)
        return True

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
        try:
            return self._adapter.consume_stream(
                kwargs, acc, on_text_chunk, cancellation_token,
            )
        finally:
            self._count_attempt(acc)

    def _emit_nonstreaming(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        max_tokens: Optional[int],
        on_text_chunk: Optional[Any],
        *,
        cache_boundary: Optional[int] = None,
    ) -> Any:
        """Run the non-streaming ``chat()`` and replay its full content
        through ``on_text_chunk`` so streaming callers behave identically.

        Shared by the Gemini bypass and the streaming-unsupported fallback.
        """
        response = self.chat(
            messages, tools=tools, max_tokens=max_tokens,
            cache_boundary=cache_boundary,
        )
        if on_text_chunk:
            content = response.choices[0].message.content
            if content:
                on_text_chunk(content)
        return response
