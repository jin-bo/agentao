"""Request/response logging mixin for :class:`agentao.llm.client.LLMClient`.

The two methods here render every LLM request and response to ``self.logger``
in full (no truncation), with incremental message logging and system-prompt
diffing. They are pure formatting logic, split out of ``client.py`` to keep the
chat / retry / streaming machinery readable.

Mixed into ``LLMClient``; relies on instance state set in ``LLMClient.__init__``:
``self.logger`` plus the incremental-logging bookkeeping fields
``self._logged_message_count`` / ``self._last_system_content`` /
``self._last_tools_hash``.
"""

from __future__ import annotations

import json
from typing import Any, Dict


class _LoggingMixin:
    """LLM request/response logging for :class:`LLMClient`."""

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
