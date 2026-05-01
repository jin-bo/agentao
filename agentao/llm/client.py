"""OpenAI-compatible LLM client."""

import json
import logging
import logging.handlers
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

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


# ---------------------------------------------------------------------------
# Duck-type response objects for the streaming path
# agent.py accesses: response.choices[0].message.{content,tool_calls}
# _serialize_tool_call accesses: tc.id, tc.function.name, tc.function.arguments
# ---------------------------------------------------------------------------

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


class LLMClient:
    """OpenAI-compatible LLM client with comprehensive logging.

    Pass ``logger=...`` to skip all ``agentao`` package-root mutation
    (handler attach, level set, marker eviction) — embedded hosts own
    their stack. Pass ``log_file=None`` to skip the file handler.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 65536,
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

        # Set to True after detecting the model requires max_completion_tokens
        self._use_max_completion_tokens: bool = False

        self.client = _openai_client_cls()(
            api_key=self.api_key,
            base_url=self.base_url,
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
            fallback = Path.home() / ".agentao" / "agentao.log"
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
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        """Reinitialize the OpenAI client with new provider credentials.

        Args:
            api_key: New API key
            base_url: New base URL (None keeps existing)
            model: New model name (None keeps existing)
        """
        self.api_key = api_key
        if base_url is not None:
            self.base_url = base_url
        if model is not None:
            self.model = model

        self.client = _openai_client_cls()(
            api_key=self.api_key,
            base_url=self.base_url,
        )
        self.logger.info(
            f"LLMClient reconfigured: model={self.model}, base_url={self.base_url}"
        )

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

        # Build request parameters
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }

        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        if max_tokens:
            kwargs["max_completion_tokens" if self._use_max_completion_tokens else "max_tokens"] = max_tokens

        # Log request
        self._log_request(request_id, kwargs)

        try:
            # Make API call
            raw = self.client.chat.completions.with_raw_response.create(**kwargs)
            response = raw.parse()

            # Accumulate session token totals
            if hasattr(response, "usage") and response.usage:
                self.total_prompt_tokens += response.usage.prompt_tokens or 0
                self.total_completion_tokens += response.usage.completion_tokens or 0

            # Log response
            self._log_response(request_id, response)

            return response

        except Exception as e:
            if not self._use_max_completion_tokens and "max_tokens" in str(e) and "max_completion_tokens" in str(e):
                # Model requires max_completion_tokens; switch and retry
                self._use_max_completion_tokens = True
                self.logger.info("Switching to max_completion_tokens for this model")
                if "max_tokens" in kwargs:
                    kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")
                try:
                    raw = self.client.chat.completions.with_raw_response.create(**kwargs)
                    response = raw.parse()
                    if hasattr(response, "usage") and response.usage:
                        self.total_prompt_tokens += response.usage.prompt_tokens or 0
                        self.total_completion_tokens += response.usage.completion_tokens or 0
                    self._log_response(request_id, response)
                    return response
                except Exception as retry_e:
                    import traceback
                    self.logger.error(f"[{request_id}] Retry failed: {str(retry_e)}\n{traceback.format_exc()}")
                    raise retry_e
            import traceback
            self.logger.error(f"[{request_id}] API call failed: {str(e)}\n{traceback.format_exc()}")
            raise

    def _log_request(self, request_id: str, kwargs: Dict[str, Any]) -> None:
        """Log LLM request details.

        Args:
            request_id: Unique request identifier
            kwargs: Request parameters
        """
        self.logger.info("=" * 80)
        self.logger.info(f"[{request_id}] LLM REQUEST")
        self.logger.info("=" * 80)

        # Log basic info
        self.logger.info(f"Model: {kwargs.get('model')}")
        self.logger.info(f"Temperature: {kwargs.get('temperature')}")
        if kwargs.get('max_tokens'):
            self.logger.info(f"Max Tokens: {kwargs.get('max_tokens')}")

        # Log only new messages since last request (incremental)
        messages = kwargs.get('messages', [])
        new_messages = messages[self._logged_message_count:]
        self.logger.info(f"Messages ({len(messages)} total, logging {len(new_messages)} new):")
        self._logged_message_count = len(messages)

        # Always check system prompt for changes (it's messages[0], never in new_messages after first request)
        if messages and messages[0].get('role') == 'system':
            sys_content = messages[0].get('content', '')
            if isinstance(sys_content, str):
                if self._last_system_content is None:
                    self.logger.info("  Message 1 [system]:")
                    self.logger.info(f"    [system prompt initial: {len(sys_content)} chars]:\n" +
                                     "\n".join(f"      {line}" for line in sys_content.split('\n')))
                elif sys_content != self._last_system_content:
                    import re, difflib
                    _TS = re.compile(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \([A-Za-z]+\)')
                    if _TS.sub('<ts>', sys_content) != _TS.sub('<ts>', self._last_system_content):
                        diff = list(difflib.unified_diff(
                            self._last_system_content.splitlines(),
                            sys_content.splitlines(),
                            lineterm='',
                            n=2,
                        ))
                        self.logger.info("  Message 1 [system]:")
                        self.logger.info(f"    [system prompt changed: {len(sys_content)} chars, diff]:\n" +
                                         "\n".join(f"      {line}" for line in '\n'.join(diff).split('\n')))
                    # else: timestamp-only change, skip logging entirely
                else:
                    self.logger.info("  Message 1 [system]:")
                    self.logger.info(f"    [system prompt unchanged: {len(sys_content)} chars]")
                self._last_system_content = sys_content

        for i, msg in enumerate(new_messages):
            role = msg.get('role', 'unknown')
            content = msg.get('content', '')
            abs_index = len(messages) - len(new_messages) + i + 1

            # system message already handled above
            if role == 'system':
                continue

            self.logger.info(f"  Message {abs_index} [{role}]:")

            # Log full content as single write (only first line gets timestamp prefix)
            if isinstance(content, str):
                self.logger.info(f"    Content ({len(content)} chars):\n" +
                                 "\n".join(f"      {line}" for line in content.split('\n')))
            else:
                self.logger.info(f"    Content: {content}")

            # Note if reasoning_content is preserved (thinking-enabled APIs)
            if 'reasoning_content' in msg:
                rc = msg['reasoning_content']
                self.logger.info(f"    Reasoning Content ({len(rc)} chars): [preserved]")

            # Log tool calls if present
            if 'tool_calls' in msg:
                self.logger.info(f"    Tool Calls: {len(msg['tool_calls'])}")
                for j, tc in enumerate(msg['tool_calls'], 1):
                    func_name = tc.get('function', {}).get('name', 'unknown')
                    func_args = tc.get('function', {}).get('arguments', '{}')
                    try:
                        args_dict = json.loads(func_args)
                        args_str = json.dumps(args_dict, indent=10, ensure_ascii=False)
                    except json.JSONDecodeError:
                        args_str = func_args
                    self.logger.info(f"      Tool Call {j}: {func_name} (id={tc.get('id', 'N/A')})\n" +
                                     "\n".join(f"          {line}" for line in args_str.split('\n')))
                    if tc.get('function', {}).get('thought_signature') is not None:
                        sig = tc['function']['thought_signature']
                        self.logger.info(f"        Thought Signature ({len(str(sig))} chars): [preserved]")

            # Log tool results if present
            if msg.get('role') == 'tool':
                tool_name = msg.get('name', 'unknown')
                tool_call_id = msg.get('tool_call_id', 'N/A')
                result = msg.get('content', '')
                self.logger.info(f"    Tool: {tool_name} (call_id={tool_call_id})\n" +
                                 f"    Result ({len(result)} chars):\n" +
                                 "\n".join(f"      {line}" for line in str(result).split('\n')))

        # Log tools if present
        tools = kwargs.get('tools')
        if tools:
            tools_hash = hash(tuple(
                t.get('function', {}).get('name', '') for t in tools
            ))
            if tools_hash != self._last_tools_hash:
                names = [t.get('function', {}).get('name', 'unknown') for t in tools]
                self.logger.info(f"Tools ({len(tools)} available, changed): {', '.join(names)}")
                self._last_tools_hash = tools_hash
            else:
                self.logger.info(f"Tools ({len(tools)} available, unchanged)")

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
            response = self.chat(messages, tools=tools, max_tokens=max_tokens)
            if on_text_chunk:
                content = response.choices[0].message.content
                if content:
                    on_text_chunk(content)
            return response

        self.request_count += 1
        request_id = f"req_{self.request_count}"

        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if max_tokens:
            kwargs["max_completion_tokens" if self._use_max_completion_tokens else "max_tokens"] = max_tokens

        # Log without the stream flag (matches non-streaming log format)
        log_kwargs = {k: v for k, v in kwargs.items() if k != "stream"}
        self._log_request(request_id, log_kwargs)

        try:
            stream = self.client.chat.completions.create(**kwargs)

            content_parts: List[str] = []
            reasoning_parts: List[str] = []
            tool_calls_data: Dict[int, Dict[str, str]] = {}
            finish_reason = "stop"
            response_model = self.model
            usage_data = None

            for chunk in stream:
                if cancellation_token and cancellation_token.is_cancelled:
                    break
                # Capture usage from final usage-only chunk (stream_options include_usage)
                if hasattr(chunk, "usage") and chunk.usage:
                    usage_data = chunk.usage
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta

                # Accumulate text content and fire callback
                if delta and delta.content:
                    content_parts.append(delta.content)
                    if on_text_chunk:
                        on_text_chunk(delta.content)

                # Accumulate reasoning_content (DeepSeek/MiniMax/Kimi-style thinking
                # field). Non-streaming exposes it on message.reasoning_content;
                # without this branch the streaming path would silently drop it.
                if delta and getattr(delta, "reasoning_content", None):
                    reasoning_parts.append(delta.reasoning_content)

                # Accumulate tool call deltas
                if delta and delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        if idx not in tool_calls_data:
                            tool_calls_data[idx] = {"id": "", "name": "", "arguments": ""}
                        if tc_delta.id:
                            tool_calls_data[idx]["id"] = tc_delta.id
                        if tc_delta.function:
                            if tc_delta.function.name:
                                tool_calls_data[idx]["name"] += tc_delta.function.name
                            if tc_delta.function.arguments:
                                tool_calls_data[idx]["arguments"] += tc_delta.function.arguments
                            # Gemini thinking models: preserve thought_signature
                            thought_sig = getattr(tc_delta.function, "thought_signature", None)
                            if thought_sig is not None:
                                tool_calls_data[idx]["thought_signature"] = thought_sig

                if choice.finish_reason:
                    finish_reason = choice.finish_reason

                if hasattr(chunk, "model") and chunk.model:
                    response_model = chunk.model

            # Accumulate session token totals
            if usage_data is not None:
                self.total_prompt_tokens += getattr(usage_data, "prompt_tokens", 0) or 0
                self.total_completion_tokens += getattr(usage_data, "completion_tokens", 0) or 0

            # Build a duck-type response that agent.py can consume like a ChatCompletion
            response = _StreamResponse(
                model=response_model,
                content="".join(content_parts) if content_parts else None,
                tool_calls_data=tool_calls_data,
                finish_reason=finish_reason,
                usage=usage_data,
                reasoning_content="".join(reasoning_parts) if reasoning_parts else None,
            )

            self._log_response(request_id, response)
            return response

        except Exception as e:
            err_str = str(e).lower()
            if not self._use_max_completion_tokens and "max_tokens" in err_str and "max_completion_tokens" in err_str:
                # Model requires max_completion_tokens; switch and retry via non-streaming fallback
                self._use_max_completion_tokens = True
                self.logger.info("Switching to max_completion_tokens for this model (stream retry)")
                if "max_tokens" in kwargs:
                    kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")
                try:
                    stream = self.client.chat.completions.create(**kwargs)
                    content_parts2: List[str] = []
                    reasoning_parts2: List[str] = []
                    tool_calls_data2: Dict[int, Dict[str, str]] = {}
                    finish_reason2 = "stop"
                    response_model2 = self.model
                    usage_data2 = None
                    for chunk in stream:
                        if hasattr(chunk, "usage") and chunk.usage:
                            usage_data2 = chunk.usage
                        if not chunk.choices:
                            continue
                        choice = chunk.choices[0]
                        delta = choice.delta
                        if delta and delta.content:
                            content_parts2.append(delta.content)
                            if on_text_chunk:
                                on_text_chunk(delta.content)
                        if delta and getattr(delta, "reasoning_content", None):
                            reasoning_parts2.append(delta.reasoning_content)
                        if delta and delta.tool_calls:
                            for tc_delta in delta.tool_calls:
                                idx = tc_delta.index
                                if idx not in tool_calls_data2:
                                    tool_calls_data2[idx] = {"id": "", "name": "", "arguments": ""}
                                if tc_delta.id:
                                    tool_calls_data2[idx]["id"] = tc_delta.id
                                if tc_delta.function:
                                    if tc_delta.function.name:
                                        tool_calls_data2[idx]["name"] += tc_delta.function.name
                                    if tc_delta.function.arguments:
                                        tool_calls_data2[idx]["arguments"] += tc_delta.function.arguments
                        if choice.finish_reason:
                            finish_reason2 = choice.finish_reason
                        if hasattr(chunk, "model") and chunk.model:
                            response_model2 = chunk.model
                    if usage_data2 is not None:
                        self.total_prompt_tokens += getattr(usage_data2, "prompt_tokens", 0) or 0
                        self.total_completion_tokens += getattr(usage_data2, "completion_tokens", 0) or 0
                    response = _StreamResponse(
                        model=response_model2,
                        content="".join(content_parts2) if content_parts2 else None,
                        tool_calls_data=tool_calls_data2,
                        finish_reason=finish_reason2,
                        usage=usage_data2,
                        reasoning_content="".join(reasoning_parts2) if reasoning_parts2 else None,
                    )
                    self._log_response(request_id, response)
                    return response
                except Exception as retry_e:
                    import traceback
                    self.logger.error(f"[{request_id}] Stream retry failed: {str(retry_e)}\n{traceback.format_exc()}")
                    raise retry_e
            # Provider doesn't support streaming — fall back to non-streaming chat()
            if "stream" in err_str or "streaming" in err_str:
                self.logger.info(f"[{request_id}] Streaming not supported by provider; falling back to non-streaming")
                try:
                    response = self.chat(messages, tools=tools, max_tokens=max_tokens)
                    if on_text_chunk:
                        content = response.choices[0].message.content
                        if content:
                            on_text_chunk(content)
                    return response
                except Exception as fallback_e:
                    import traceback
                    self.logger.error(f"[{request_id}] Non-streaming fallback also failed: {str(fallback_e)}\n{traceback.format_exc()}")
                    raise fallback_e
            import traceback
            self.logger.error(f"[{request_id}] Streaming API call failed: {str(e)}\n{traceback.format_exc()}")
            raise

    def _log_response(self, request_id: str, response: Any) -> None:
        """Log LLM response details.

        Args:
            request_id: Unique request identifier
            response: API response object
        """
        self.logger.info("=" * 80)
        self.logger.info(f"[{request_id}] LLM RESPONSE")
        self.logger.info("=" * 80)

        # Extract response data
        choice = response.choices[0] if response.choices else None
        if not choice:
            self.logger.warning("No choices in response")
            return

        message = choice.message

        # Log basic info
        self.logger.info(f"Model: {response.model}")
        self.logger.info(f"Finish Reason: {choice.finish_reason}")

        # Log usage stats if available
        if hasattr(response, 'usage') and response.usage:
            usage = response.usage
            self.logger.info(f"\nToken Usage:")
            self.logger.info(f"  Prompt Tokens: {usage.prompt_tokens}")
            self.logger.info(f"  Completion Tokens: {usage.completion_tokens}")
            self.logger.info(f"  Total Tokens: {usage.total_tokens}")

        # Log message content - FULL content without truncation
        if message.content:
            content = message.content
            self.logger.info(f"Assistant Response ({len(content)} chars):\n" +
                             "\n".join(f"  {line}" for line in content.split('\n')))

        # Log reasoning_content if present (thinking-enabled APIs)
        reasoning_content = getattr(message, "reasoning_content", None)
        if reasoning_content:
            self.logger.info(f"Reasoning Content ({len(reasoning_content)} chars):\n" +
                             "\n".join(f"  {line}" for line in reasoning_content.split('\n')))

        # Log tool calls if present
        if message.tool_calls:
            self.logger.info(f"\nTool Calls ({len(message.tool_calls)}):")
            for tc in message.tool_calls:
                func_name = tc.function.name
                func_args = tc.function.arguments

                self.logger.info(f"  Tool: {func_name}")
                self.logger.info(f"  ID: {tc.id}")

                # Pretty print arguments
                try:
                    args_dict = json.loads(func_args)
                    args_str = json.dumps(args_dict, indent=4, ensure_ascii=False)
                except json.JSONDecodeError:
                    args_str = func_args
                self.logger.info(f"  Arguments:\n{args_str}")

        self.logger.info("=" * 80 + "\n")
