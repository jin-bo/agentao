"""OpenAI-compatible LLM client."""

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from openai import OpenAI


class LLMClient:
    """OpenAI-compatible LLM client with comprehensive logging."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: str = "claude-sonnet-4-5",
        log_file: str = "chatagent.log",
    ):
        """Initialize LLM client.

        Args:
            api_key: API key for the LLM service
            base_url: Base URL for the API endpoint
            model: Model name to use
            log_file: Path to log file for LLM interactions
        """
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL")
        self.model = model or os.getenv("OPENAI_MODEL", "claude-sonnet-4-5")

        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
        )

        # Setup logging
        self.logger = logging.getLogger("chatagent.llm")
        self.logger.setLevel(logging.DEBUG)

        # Remove existing handlers to avoid duplicates
        self.logger.handlers.clear()

        # File handler for detailed logs
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)

        # Detailed format for file logs
        file_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler.setFormatter(file_formatter)

        self.logger.addHandler(file_handler)

        # Request counter for tracking
        self.request_count = 0

        self.logger.info(f"LLMClient initialized with model: {self.model}")

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> Any:
        """Send chat request to LLM.

        Args:
            messages: List of message dictionaries
            tools: Optional list of tool definitions
            temperature: Sampling temperature
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
            "temperature": temperature,
        }

        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        if max_tokens:
            kwargs["max_tokens"] = max_tokens

        # Log request
        self._log_request(request_id, kwargs)

        try:
            # Make API call
            response = self.client.chat.completions.create(**kwargs)

            # Log response
            self._log_response(request_id, response)

            return response

        except Exception as e:
            # Log error
            self.logger.error(f"[{request_id}] API call failed: {str(e)}")
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

        # Log messages with full content
        messages = kwargs.get('messages', [])
        self.logger.info(f"\nMessages ({len(messages)} total):")
        for i, msg in enumerate(messages):
            role = msg.get('role', 'unknown')
            content = msg.get('content', '')

            self.logger.info(f"\n  Message {i + 1} [{role}]:")

            # Log full content without truncation
            if isinstance(content, str):
                self.logger.info(f"    Content ({len(content)} chars):")
                # Log content line by line for better readability
                for line in content.split('\n'):
                    self.logger.info(f"      {line}")
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
                    self.logger.info(f"      Tool Call {j}:")
                    self.logger.info(f"        Function: {func_name}")
                    self.logger.info(f"        ID: {tc.get('id', 'N/A')}")
                    self.logger.info(f"        Arguments (full):")
                    # Pretty print the arguments
                    try:
                        args_dict = json.loads(func_args)
                        args_str = json.dumps(args_dict, indent=10, ensure_ascii=False)
                        for line in args_str.split('\n'):
                            self.logger.info(f"          {line}")
                    except json.JSONDecodeError:
                        self.logger.info(f"          {func_args}")

            # Log tool results if present
            if msg.get('role') == 'tool':
                tool_name = msg.get('name', 'unknown')
                tool_call_id = msg.get('tool_call_id', 'N/A')
                result = msg.get('content', '')
                self.logger.info(f"    Tool: {tool_name}")
                self.logger.info(f"    Tool Call ID: {tool_call_id}")
                self.logger.info(f"    Result ({len(result)} chars):")
                # Log full result
                for line in str(result).split('\n'):
                    self.logger.info(f"      {line}")

        # Log tools if present
        tools = kwargs.get('tools')
        if tools:
            self.logger.info(f"\nTools ({len(tools)} available):")
            for tool in tools:
                tool_name = tool.get('function', {}).get('name', 'unknown')
                self.logger.info(f"  - {tool_name}")

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
            self.logger.info(f"\nAssistant Response ({len(content)} chars):")
            # Log full content line by line
            for line in content.split('\n'):
                self.logger.info(f"  {line}")

        # Log reasoning_content if present (thinking-enabled APIs)
        reasoning_content = getattr(message, "reasoning_content", None)
        if reasoning_content:
            self.logger.info(f"\nReasoning Content ({len(reasoning_content)} chars):")
            for line in reasoning_content.split('\n'):
                self.logger.info(f"  {line}")

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
                    self.logger.info(f"  Arguments:\n{args_str}")
                except json.JSONDecodeError:
                    self.logger.info(f"  Arguments (raw): {func_args}")

        self.logger.info("=" * 80 + "\n")
