"""What one ``LLM RESPONSE`` block in ``agentao.log`` says about a request (#550).

A log is how a run is tuned for cost and latency after the fact. Each block
states the attempt's duration and the prompt's cache counts, and the file
handler stamps lines to the millisecond, so a request can be timed and a
cache miss found without replay.
"""

from __future__ import annotations

import logging
import re
from types import SimpleNamespace
from unittest.mock import MagicMock

from agentao.llm.client import LLMClient


def _client_logging_to(records: list[str]) -> LLMClient:
    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    logger = logging.getLogger(f"test_llm_response_log.{id(records)}")
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.addHandler(_Collect())
    return LLMClient(
        api_key="test-key",
        base_url="https://api.example.com/v1",
        model="gpt-test",
        log_file=None,
        logger=logger,
    )


def _chunk(*, content=None, finish_reason=None, usage=None):
    delta = MagicMock()
    delta.content = content
    delta.reasoning_content = None
    delta.tool_calls = None
    choice = MagicMock()
    choice.delta = delta
    choice.finish_reason = finish_reason
    chunk = MagicMock()
    chunk.choices = [choice]
    chunk.model = "gpt-test"
    chunk.usage = usage
    return chunk


def test_a_response_block_states_duration_and_cache_counts():
    records: list[str] = []
    client = _client_logging_to(records)
    usage = SimpleNamespace(
        prompt_tokens=100, completion_tokens=5, total_tokens=105,
        prompt_tokens_details=SimpleNamespace(cached_tokens=80),
    )
    client.client.chat.completions.create = MagicMock(return_value=[
        _chunk(content="hi"),
        _chunk(finish_reason="stop", usage=usage),
    ])

    client.chat_stream(messages=[{"role": "user", "content": "hi"}])

    block = records[records.index("[req_1] LLM RESPONSE"):]
    assert any(re.fullmatch(r"Duration: \d+ ms", line) for line in block)
    assert "  Cache Read Tokens: 80" in block
    assert "  Cache Creation Tokens: 0" in block


def test_the_file_handler_stamps_milliseconds(tmp_path):
    logger = logging.getLogger("test_llm_response_log.file")
    handler = LLMClient._attach_file_handler(logger, str(tmp_path / "agentao.log"))
    assert handler is not None
    try:
        record = logging.LogRecord("x", logging.INFO, __file__, 1, "hello", None, None)
        line = handler.format(record)
    finally:
        logger.removeHandler(handler)
        handler.close()

    assert re.fullmatch(r"\d\d:\d\d:\d\d\.\d{3} hello", line)


def test_a_failed_attempt_logs_how_long_it_took():
    """A retried or failed request is the one most worth timing."""
    records: list[str] = []
    client = _client_logging_to(records)
    client.client.chat.completions.create = MagicMock(side_effect=ValueError("bad request"))

    try:
        client.chat_stream(messages=[{"role": "user", "content": "hi"}])
    except ValueError:
        pass

    assert any(
        re.match(r"\[req_1\] Streaming API call failed after \d+ ms: bad request", line)
        for line in records
    )
