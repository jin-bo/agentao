"""Request/response logging mixin for :class:`agentao.llm.client.LLMClient`.

The two methods here render every LLM request and response to ``self.logger``
in full (no truncation), with incremental message logging and system-prompt
diffing. They are pure formatting logic, split out of ``client.py`` to keep the
chat / retry / streaming machinery readable.

**Incremental, but never blind to a rewrite (#635).** A request logs the
messages that are new *or changed*, not those past a count: a compaction can
rewrite a message in place (microcompaction, the ``api_overflow`` clip that
names a saved copy) or shorten the list, and the log must show what the model
was actually sent. Each channel remembers one fingerprint per history message
it logged. A changed one is logged again, marked ``(rewritten)``. The trailing
request-only messages (the volatile tail) are logged each time and not
remembered: they move every request.

Mixed into ``LLMClient``; relies on instance state set in ``LLMClient.__init__``:
``self.logger``, ``self._log_channels`` (per-channel bookkeeping) and
``self._last_tools_hash``.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .. import json_parse
from ._usage import cache_token_counts, reasoning_token_count


#: Keys whose values are redacted before logging ``extra_body``. ``extra_body``
#: can nest provider credentials (some gateways accept an API key in the body,
#: or carry gateway-routing headers under ``extra_headers``), and the logger
#: deliberately keeps secrets out of the log. Match is on the EXACT lower-cased
#: key name — NOT a substring — so a benign ``max_tokens`` / ``*_tokens`` key
#: is never over-redacted (``token`` != ``max_tokens``). Includes the common
#: header-style credential names (``x-api-key``, ``proxy-authorization``, …)
#: hosts use when passing gateway headers through.
_SENSITIVE_KEYS = frozenset({
    "authorization", "proxy-authorization",
    "api_key", "apikey", "api-key", "x-api-key",
    "api_token", "api-token", "x-api-token",
    "token", "access_token", "auth_token", "auth-token", "x-auth-token",
    "secret", "client_secret", "client-secret",
    "password", "cookie", "set-cookie",
})


def _redact_sensitive(value: Any) -> Any:
    """Return a deep copy of ``value`` with credential-like values masked.

    Recurses through dicts, lists, and tuples; at any depth, a dict entry
    whose key (lower-cased) is exactly one of :data:`_SENSITIVE_KEYS` has its
    value replaced with ``"***"``. Scalars pass through unchanged. Tuples are
    recursed (a credential nested inside a tuple value would otherwise be
    logged in the clear) and preserved as tuples.
    """
    if isinstance(value, dict):
        return {
            k: ("***" if isinstance(k, str) and k.lower() in _SENSITIVE_KEYS
                else _redact_sensitive(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_redact_sensitive(v) for v in value)
    return value


def _without_cache_markers(value: Any) -> Any:
    """``value`` with every ``cache_control`` key left out.

    On Chat Completions the logged list is the request, and the prompt-cache
    breakpoints move each request (``llm/_cache_control.py``). Marking turns a
    string ``content`` into one text part that carries the marker. Neither is a
    change to the message, so neither may log it again as rewritten.
    """
    if isinstance(value, dict):
        return {k: _without_cache_markers(v) for k, v in value.items() if k != "cache_control"}
    if isinstance(value, list):
        return [_without_cache_markers(v) for v in value]
    return value


def _fingerprint(msg: Any) -> int:
    """An in-process identity for one message's whole content."""
    canon = _without_cache_markers(msg)
    if isinstance(canon, dict):
        content = canon.get("content")
        if (
            isinstance(content, list) and len(content) == 1
            and isinstance(content[0], dict)
            and set(content[0]) == {"type", "text"} and content[0]["type"] == "text"
        ):
            canon = dict(canon, content=content[0]["text"])
    try:
        return hash(json.dumps(canon, sort_keys=True, default=repr, ensure_ascii=False))
    except Exception:
        return hash(repr(canon))


class _ChannelLog:
    """What one request channel has already written to the log."""

    __slots__ = ("fingerprints", "system")

    def __init__(self) -> None:
        self.fingerprints: List[int] = []
        self.system: Optional[str] = None


class _LoggingMixin:
    """LLM request/response logging for :class:`LLMClient`."""

    def _log_channel(self, channel: str) -> _ChannelLog:
        channels = getattr(self, "_log_channels", None)
        if not isinstance(channels, dict):
            channels = {}
            self._log_channels = channels
        state = channels.get(channel)
        if not isinstance(state, _ChannelLog):
            state = _ChannelLog()
            channels[channel] = state
        return state

    def _log_request(
        self,
        request_id: str,
        kwargs: Dict[str, Any],
        *,
        channel: str = "turn",
        request_only: Optional[int] = None,
    ) -> None:
        """Log LLM request details.

        Args:
            request_id: Unique request identifier
            kwargs: Request parameters
            channel: Whose history this is. The turn (``chat_stream``) and the
                one-off calls (``chat``: the summarizer, crystallize) keep
                separate state, so a side call does not make the turn's next
                request log its whole history again.
            request_only: How many trailing messages are request-only (the
                volatile tail). Logged each time, never remembered.
        """
        state = self._log_channel(channel)
        self.logger.info("=" * 80)
        self.logger.info(f"[{request_id}] LLM REQUEST")
        self.logger.info("=" * 80)

        # Log basic info
        self.logger.info(f"Model: {kwargs.get('model')}")
        # Read off the request, so "not sent" covers every reason it is
        # absent: unset, ``/temperature off``, a latched rejection, a wire
        # that has no such field.
        temperature = kwargs.get("temperature")
        self.logger.info(
            f"Temperature: {'(not sent)' if temperature is None else temperature}"
        )
        if kwargs.get('max_tokens'):
            self.logger.info(f"Max Tokens: {kwargs.get('max_tokens')}")

        # Host-supplied request-body passthrough (one known key, not arbitrary
        # kwargs). Values are recursively redacted — extra_body can nest
        # provider credentials, and logging it raw would reintroduce the leak
        # the logger otherwise avoids by keeping api_key out.
        extra_body = kwargs.get('extra_body')
        if extra_body:
            self.logger.info(f"Extra Body: {_redact_sensitive(extra_body)}")

        # Log what is new or rewritten since this channel's last request.
        messages = kwargs.get('messages', [])
        tail = request_only if type(request_only) is int and 0 < request_only <= len(messages) else 0
        history_len = len(messages) - tail
        fingerprints = [_fingerprint(m) for m in messages[:history_len]]
        previous = state.fingerprints
        selected = []  # (index, rewritten)
        new_count = rewritten_count = 0
        for i, msg in enumerate(messages):
            if i >= history_len or i >= len(previous):
                selected.append((i, False))
                new_count += 1
            elif fingerprints[i] != previous[i]:
                selected.append((i, True))
                if not (isinstance(msg, dict) and msg.get('role') == 'system'):
                    rewritten_count += 1
        state.fingerprints = fingerprints
        notes = f", {rewritten_count} rewritten" if rewritten_count else ""
        if history_len < len(previous):
            notes += f", history shortened from {len(previous)}"
        self.logger.info(
            f"Messages ({len(messages)} total, logging {new_count} new{notes}):"
        )

        # Always check system prompt for changes (it's messages[0], never in new_messages after first request)
        if messages and messages[0].get('role') == 'system':
            sys_content = messages[0].get('content', '')
            if isinstance(sys_content, str):
                if state.system is None:
                    self.logger.info("  Message 1 [system]:")
                    self.logger.info(f"    [system prompt initial: {len(sys_content)} chars]:\n" +
                                     "\n".join(f"      {line}" for line in sys_content.split('\n')))
                elif sys_content != state.system:
                    import re, difflib
                    _TS = re.compile(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \([A-Za-z]+\)')
                    if _TS.sub('<ts>', sys_content) != _TS.sub('<ts>', state.system):
                        diff = list(difflib.unified_diff(
                            state.system.splitlines(),
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
                state.system = sys_content

        for i, rewritten in selected:
            msg = messages[i]
            role = msg.get('role', 'unknown')
            content = msg.get('content', '')
            abs_index = i + 1

            # system message already handled above
            if role == 'system':
                continue

            mark = " (rewritten)" if rewritten else ""
            self.logger.info(f"  Message {abs_index} [{role}]{mark}:")
            if role == 'tool':
                # The content below is the result: logged once, by the same
                # path that summarizes a multimodal result's inline images.
                self.logger.info(
                    f"    Tool: {msg.get('name', 'unknown')} "
                    f"(call_id={msg.get('tool_call_id', 'N/A')})"
                )

            # Log full content as single write (only first line gets timestamp prefix)
            if isinstance(content, str):
                self.logger.info(f"    Content ({len(content)} chars):\n" +
                                 "\n".join(f"      {line}" for line in content.split('\n')))
            elif isinstance(content, list):
                # Multimodal content: summarize each part without dumping raw
                # data (an inline image is megabytes of base64).
                parts_summary = []
                for part in content:
                    if isinstance(part, dict):
                        ptype = part.get('type', 'unknown')
                        if ptype == 'text':
                            text = part.get('text', '')
                            parts_summary.append(f"text ({len(text)} chars)")
                        elif ptype == 'image_url':
                            image_url = part.get('image_url', {})
                            # ``image_url`` is normally {"url": ...} but external
                            # / MCP-sourced parts may use the relaxed bare-string
                            # shape; coerce to str so logging never crashes the
                            # (unguarded) request-logging path.
                            url = image_url.get('url', '') if isinstance(image_url, dict) else image_url
                            url = url if isinstance(url, str) else str(url)
                            if url.startswith('data:'):
                                parts_summary.append(f"image_url ({len(url)} chars, inline base64)")
                            else:
                                parts_summary.append(f"image_url: {url[:100]}")
                        else:
                            parts_summary.append(f"{ptype}")
                    else:
                        parts_summary.append(str(type(part)))
                self.logger.info(
                    f"    Content (multimodal, {len(content)} parts): "
                    f"[{', '.join(parts_summary)}]"
                )
                # The text parts are content like any other: logged in full.
                # Only the inline image data is left out.
                for k, part in enumerate(content, 1):
                    if isinstance(part, dict) and part.get('type') == 'text':
                        text = str(part.get('text', ''))
                        self.logger.info(
                            f"    Part {k} text ({len(text)} chars):\n" +
                            "\n".join(f"      {line}" for line in text.split('\n'))
                        )
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
                        args_dict = json_parse.loads(func_args)
                        args_str = json.dumps(args_dict, indent=10, ensure_ascii=False)
                    except json.JSONDecodeError:
                        args_str = func_args
                    self.logger.info(f"      Tool Call {j}: {func_name} (id={tc.get('id', 'N/A')})\n" +
                                     "\n".join(f"          {line}" for line in args_str.split('\n')))
                    if tc.get('function', {}).get('thought_signature') is not None:
                        sig = tc['function']['thought_signature']
                        self.logger.info(f"        Thought Signature ({len(str(sig))} chars): [preserved]")

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

    def _log_response(
        self, request_id: str, response: Any, *, duration_ms: int | None = None,
    ) -> None:
        """Log LLM response details.

        Args:
            request_id: Unique request identifier
            response: API response object
            duration_ms: How long the attempt that produced ``response``
                took, from send to the last chunk. A retried request's
                earlier attempts are not in it.
        """
        self.logger.info("=" * 80)
        self.logger.info(f"[{request_id}] LLM RESPONSE")
        self.logger.info("=" * 80)
        # Before the no-choices return: an empty response is the one a
        # duration most often has to explain.
        if duration_ms is not None:
            self.logger.info(f"Duration: {duration_ms} ms")

        # Extract response data
        choice = response.choices[0] if response.choices else None
        if not choice:
            self.logger.warning("No choices in response")
            return

        message = choice.message

        # Log basic info
        self.logger.info(f"Model: {response.model}")
        # When the provider never sent a finish_reason the value above is
        # agentao's own "stop" fallback, so the bare line would assert a clean
        # provider-confirmed stop for a stream that may have been cut off.
        # agentao.log is the documented first stop for debugging LLM behaviour
        # (CLAUDE.md), so the qualifier has to be here and not only on
        # ``TurnOutcome``.
        if getattr(response, "finish_reason_reported", True):
            self.logger.info(f"Finish Reason: {choice.finish_reason}")
        else:
            self.logger.info(
                f"Finish Reason: {choice.finish_reason} "
                "(NOT reported by provider — agentao fallback; the stream "
                "ended without one, so this response may be truncated)"
            )

        # Log usage stats if available
        if hasattr(response, 'usage') and response.usage:
            usage = response.usage
            self.logger.info(f"\nToken Usage:")
            self.logger.info(f"  Prompt Tokens: {usage.prompt_tokens}")
            self.logger.info(f"  Completion Tokens: {usage.completion_tokens}")
            self.logger.info(f"  Total Tokens: {usage.total_tokens}")
            # Parts of the prompt count, stated per request so a log shows
            # which requests missed the cache (#550). The rule that reads
            # them is the one the session totals use.
            cache_read, cache_creation = cache_token_counts(usage)
            self.logger.info(f"  Cache Read Tokens: {cache_read}")
            self.logger.info(f"  Cache Creation Tokens: {cache_creation}")
            # A part of the completion count. 0 on a wire that does not
            # state it (Anthropic counts thinking in ``output_tokens``).
            self.logger.info(f"  Reasoning Tokens: {reasoning_token_count(usage)}")

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
                    args_dict = json_parse.loads(func_args)
                    args_str = json.dumps(args_dict, indent=4, ensure_ascii=False)
                except json.JSONDecodeError:
                    args_str = func_args
                self.logger.info(f"  Arguments:\n{args_str}")

        self.logger.info("=" * 80 + "\n")
