"""Unit test for multimodal-content request logging (A1 / PR-1).

When a user turn carries an image, the message ``content`` is an OpenAI-style
list of parts (``text`` + ``image_url`` with an inline ``data:`` URL). The
request logger must **summarize** those parts — never dump the raw base64
blob, which is megabytes per image and would bloat ``agentao.log``.
"""

import logging
from pathlib import Path

import pytest

from agentao.llm import LLMClient


@pytest.fixture
def client(tmp_path: Path):
    log_file = tmp_path / "agentao.log"
    c = LLMClient(
        api_key="test-api-key",
        base_url="https://api.example.com/v1",
        model="claude-sonnet-4-5",
        log_file=str(log_file),
    )
    yield c, log_file
    # Release the file handlers so the temp file can be cleaned up on Windows.
    for handler in list(c.logger.handlers):
        c.logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass


def test_multimodal_content_is_summarized_not_dumped(client):
    c, log_file = client

    # A realistic blob: a long base64 string that must NOT appear verbatim.
    blob = "A" * 5000
    data_url = f"data:image/png;base64,{blob}"
    messages = [
        {"role": "system", "content": "sys"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "what is in this image?"},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]

    c._log_request("req-1", {"model": "m", "messages": messages})

    for handler in c.logger.handlers:
        handler.flush()
    log_text = log_file.read_text(encoding="utf-8")

    # Summary present...
    assert "Content (multimodal, 2 parts):" in log_text
    assert "text (22 chars)" in log_text
    assert "inline base64" in log_text
    # ...and the raw blob is NOT dumped.
    assert blob not in log_text


def test_relaxed_image_url_string_shape_does_not_crash(client):
    """External/MCP parts may use the bare-string ``image_url`` shape
    ({"image_url": "http://..."}) instead of {"url": ...}; the summarizer
    must not raise — _log_request runs before the API call, unguarded."""
    c, log_file = client
    messages = [
        {"role": "system", "content": "sys"},
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": "https://example.com/cat.png"},
                {"type": "image_url", "image_url": {"url": 123}},  # non-str url
            ],
        },
    ]

    c._log_request("req-3", {"model": "m", "messages": messages})  # must not raise

    for handler in c.logger.handlers:
        handler.flush()
    log_text = log_file.read_text(encoding="utf-8")
    assert "Content (multimodal, 2 parts):" in log_text


def test_plain_string_content_unaffected(client):
    c, log_file = client
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello world"},
    ]

    c._log_request("req-2", {"model": "m", "messages": messages})

    for handler in c.logger.handlers:
        handler.flush()
    log_text = log_file.read_text(encoding="utf-8")

    assert "Content (11 chars):" in log_text
    assert "hello world" in log_text
    assert "multimodal" not in log_text
