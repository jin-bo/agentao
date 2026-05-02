"""Tests for HTTP-level retry policy in ``LLMClient``.

Covers what the OpenAI SDK's built-in retry no longer provides (we set
``max_retries=0``): explicit status-code allowlist, ``Retry-After``
honored when present, jittered exponential backoff with a wall-clock
budget, and the streaming path's progress-aware retry — i.e., **never**
retry once a chunk has been delivered to ``on_text_chunk`` because the
caller has already shown that text to the user.
"""

from __future__ import annotations

from typing import List
from unittest.mock import MagicMock

import httpx
import openai
import pytest

from agentao.llm import client as client_mod
from agentao.llm.client import (
    LLMClient,
    MAX_RETRY_ATTEMPTS,
    _classify_retry,
    _compute_backoff_delay,
    _interruptible_sleep,
    _parse_retry_after,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_status_error(status: int, *, retry_after: str | None = None, cls=None):
    """Build a real OpenAI ``APIStatusError`` subclass instance."""
    cls = cls or openai.APIStatusError
    headers = {"retry-after": retry_after} if retry_after is not None else {}
    req = httpx.Request("POST", "https://example.com/v1/chat/completions")
    resp = httpx.Response(status, headers=headers, request=req)
    return cls(message=f"status={status}", response=resp, body=None)


def _make_completion(content: str = "ok", prompt_tokens: int = 5, completion_tokens: int = 3):
    """Build a mock object shaped like ``ChatCompletion``."""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = None
    msg.reasoning_content = None
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = "stop"
    response = MagicMock()
    response.choices = [choice]
    response.model = "gpt-test"
    response.usage = MagicMock(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )
    return response


def _make_client():
    return LLMClient(
        api_key="test-key",
        base_url="https://api.example.com/v1",
        model="gpt-test",
        log_file=None,
        logger=MagicMock(),
    )


def _make_chunk(*, content: str | None = None, finish_reason: str | None = None):
    """Build a streaming chunk with the bits ``chat_stream`` reads."""
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
    chunk.usage = None
    return chunk


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestClassifyRetry:
    def test_rate_limit_error_is_retryable_with_429_and_header(self):
        err = _make_status_error(429, retry_after="3", cls=openai.RateLimitError)
        retryable, status, retry_after = _classify_retry(err)
        assert (retryable, status, retry_after) == (True, 429, "3")

    def test_500_is_retryable(self):
        retryable, status, _ = _classify_retry(_make_status_error(500))
        assert retryable is True
        assert status == 500

    def test_529_is_retryable(self):
        retryable, status, _ = _classify_retry(_make_status_error(529))
        assert retryable is True
        assert status == 529

    def test_400_is_not_retryable(self):
        retryable, status, _ = _classify_retry(_make_status_error(400))
        assert retryable is False
        assert status == 400

    def test_401_is_not_retryable(self):
        retryable, _, _ = _classify_retry(_make_status_error(401))
        assert retryable is False

    def test_api_connection_error_is_retryable(self):
        req = httpx.Request("POST", "https://example.com/v1/chat/completions")
        err = openai.APIConnectionError(message="boom", request=req)
        retryable, status, retry_after = _classify_retry(err)
        assert (retryable, status, retry_after) == (True, None, None)

    def test_unknown_exception_is_not_retryable(self):
        retryable, _, _ = _classify_retry(ValueError("nope"))
        assert retryable is False


class TestParseRetryAfter:
    def test_seconds(self):
        assert _parse_retry_after("5") == 5.0

    def test_zero(self):
        assert _parse_retry_after("0") == 0.0

    def test_negative_clamped_to_zero(self):
        # spec says non-negative; bad input shouldn't make us sleep "negative" time
        assert _parse_retry_after("-1") == 0.0

    def test_none_and_empty(self):
        assert _parse_retry_after(None) is None
        assert _parse_retry_after("") is None

    def test_http_date_in_future(self):
        # An HTTP-date 30s in the future should parse to ~30s
        from datetime import datetime, timedelta, timezone
        from email.utils import format_datetime
        future = datetime.now(timezone.utc) + timedelta(seconds=30)
        header = format_datetime(future, usegmt=True)
        seconds = _parse_retry_after(header)
        assert seconds is not None
        assert 25 < seconds <= 30  # allow some slack for test latency

    def test_garbage_string(self):
        assert _parse_retry_after("not a date") is None


class TestComputeBackoffDelay:
    def test_retry_after_takes_precedence(self):
        # exponential would be 1.5s for attempt=0; Retry-After=10 wins
        assert _compute_backoff_delay(0, "10") == 10.0

    def test_retry_after_capped_at_max(self):
        from agentao.llm.client import MAX_BACKOFF_SECONDS
        assert _compute_backoff_delay(0, "999") == MAX_BACKOFF_SECONDS

    def test_exponential_grows_then_caps(self, monkeypatch):
        # Force jitter to 0 for determinism
        monkeypatch.setattr(client_mod.random, "random", lambda: 0.0)
        d0 = _compute_backoff_delay(0)
        d1 = _compute_backoff_delay(1)
        d2 = _compute_backoff_delay(2)
        assert d0 == pytest.approx(1.5)
        assert d1 == pytest.approx(3.0)
        assert d2 == pytest.approx(6.0)

    def test_jitter_adds_to_base(self, monkeypatch):
        monkeypatch.setattr(client_mod.random, "random", lambda: 1.0)
        # base = 1.5, jitter = 1.5 * 0.3 * 1.0 = 0.45 → 1.95
        assert _compute_backoff_delay(0) == pytest.approx(1.95)

    def test_jitter_does_not_exceed_max_cap(self, monkeypatch):
        # Once the exponential base saturates at MAX_BACKOFF_SECONDS, adding
        # jitter on top would breach the per-step ceiling that the policy
        # advertises. Worst-case jitter (random()=1.0) must still be capped.
        from agentao.llm.client import MAX_BACKOFF_SECONDS
        monkeypatch.setattr(client_mod.random, "random", lambda: 1.0)
        # attempt large enough that base saturates at the cap
        assert _compute_backoff_delay(20) == pytest.approx(MAX_BACKOFF_SECONDS)


class TestInterruptibleSleep:
    def test_zero_delay_returns_true(self):
        assert _interruptible_sleep(0) is True

    def test_no_token_completes(self):
        assert _interruptible_sleep(0.05) is True

    def test_cancellation_aborts(self):
        token = MagicMock()
        token.is_cancelled = True
        assert _interruptible_sleep(5.0, cancellation_token=token) is False


# ---------------------------------------------------------------------------
# chat() retry behavior
# ---------------------------------------------------------------------------


class TestChatRetry:
    def test_retries_on_429_and_succeeds(self, monkeypatch):
        # No actual sleeping
        monkeypatch.setattr(client_mod.time, "sleep", lambda *_a, **_k: None)

        client = _make_client()
        ok_raw = MagicMock()
        ok_raw.parse.return_value = _make_completion("hello")

        err = _make_status_error(429, retry_after="0", cls=openai.RateLimitError)

        client.client.chat.completions.with_raw_response.create = MagicMock(
            side_effect=[err, ok_raw]
        )

        response = client.chat(messages=[{"role": "user", "content": "hi"}])

        assert response.choices[0].message.content == "hello"
        assert client.client.chat.completions.with_raw_response.create.call_count == 2

    def test_does_not_retry_on_400(self, monkeypatch):
        monkeypatch.setattr(client_mod.time, "sleep", lambda *_a, **_k: None)

        client = _make_client()
        err = _make_status_error(400)
        client.client.chat.completions.with_raw_response.create = MagicMock(side_effect=err)

        with pytest.raises(openai.APIStatusError):
            client.chat(messages=[{"role": "user", "content": "hi"}])

        assert client.client.chat.completions.with_raw_response.create.call_count == 1

    def test_gives_up_after_max_attempts(self, monkeypatch):
        monkeypatch.setattr(client_mod.time, "sleep", lambda *_a, **_k: None)

        client = _make_client()
        err = _make_status_error(503, retry_after="0")
        client.client.chat.completions.with_raw_response.create = MagicMock(side_effect=err)

        with pytest.raises(openai.APIStatusError):
            client.chat(messages=[{"role": "user", "content": "hi"}])

        # Exactly MAX_RETRY_ATTEMPTS attempts (first + retries)
        assert (
            client.client.chat.completions.with_raw_response.create.call_count
            == MAX_RETRY_ATTEMPTS
        )

    def test_retry_budget_exhausts_before_max_attempts(self, monkeypatch):
        # If Retry-After is very long, we should bail on the wall-clock budget
        # rather than sleeping ourselves into oblivion.
        monkeypatch.setattr(client_mod, "MAX_TOTAL_RETRY_SECONDS", 1.0)
        monkeypatch.setattr(client_mod.time, "sleep", lambda *_a, **_k: None)

        client = _make_client()
        # Retry-After=999 → first computed delay = 30s (capped) > 1s budget
        err = _make_status_error(429, retry_after="999", cls=openai.RateLimitError)
        client.client.chat.completions.with_raw_response.create = MagicMock(side_effect=err)

        with pytest.raises(openai.RateLimitError):
            client.chat(messages=[{"role": "user", "content": "hi"}])

        # First attempt fires, then budget exhausts before the second
        assert client.client.chat.completions.with_raw_response.create.call_count == 1

    def test_max_tokens_param_fixup_does_not_consume_retry_budget(self, monkeypatch):
        # The historical max_tokens / max_completion_tokens swap must still work
        # and must NOT count as a retry attempt.
        monkeypatch.setattr(client_mod.time, "sleep", lambda *_a, **_k: None)

        client = _make_client()
        ok_raw = MagicMock()
        ok_raw.parse.return_value = _make_completion("after-swap")

        # First call: param-mismatch error → triggers fix-up
        # Second call: succeeds
        param_err = ValueError(
            "max_tokens is not supported, use max_completion_tokens instead"
        )

        client.client.chat.completions.with_raw_response.create = MagicMock(
            side_effect=[param_err, ok_raw]
        )

        response = client.chat(
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=128,
        )

        assert response.choices[0].message.content == "after-swap"
        assert client._use_max_completion_tokens is True
        # Only two calls total (the fix-up is the retry, no additional retry loop)
        assert client.client.chat.completions.with_raw_response.create.call_count == 2

    def test_session_token_totals_only_count_successful_response(self, monkeypatch):
        # Failed retries shouldn't accidentally accumulate token counts.
        monkeypatch.setattr(client_mod.time, "sleep", lambda *_a, **_k: None)

        client = _make_client()
        ok_raw = MagicMock()
        ok_raw.parse.return_value = _make_completion(
            "ok", prompt_tokens=7, completion_tokens=11
        )

        err = _make_status_error(503, retry_after="0")
        client.client.chat.completions.with_raw_response.create = MagicMock(
            side_effect=[err, err, ok_raw]
        )

        client.chat(messages=[{"role": "user", "content": "hi"}])

        assert client.total_prompt_tokens == 7
        assert client.total_completion_tokens == 11


# ---------------------------------------------------------------------------
# chat_stream() retry behavior
# ---------------------------------------------------------------------------


class TestChatStreamRetry:
    def test_retries_on_429_before_any_chunk(self, monkeypatch):
        monkeypatch.setattr(client_mod.time, "sleep", lambda *_a, **_k: None)

        client = _make_client()

        # First create() raises immediately; second create() yields chunks.
        err = _make_status_error(429, retry_after="0", cls=openai.RateLimitError)

        ok_chunks = iter([
            _make_chunk(content="hel"),
            _make_chunk(content="lo", finish_reason="stop"),
        ])

        client.client.chat.completions.create = MagicMock(side_effect=[err, ok_chunks])

        seen: List[str] = []
        response = client.chat_stream(
            messages=[{"role": "user", "content": "hi"}],
            on_text_chunk=seen.append,
        )

        assert seen == ["hel", "lo"]
        assert response.choices[0].message.content == "hello"
        assert client.client.chat.completions.create.call_count == 2

    def test_does_not_retry_after_first_chunk(self, monkeypatch):
        # Mid-stream failure must propagate — retrying would re-fire on_text_chunk
        # for already-delivered text and the user would see duplicated output.
        monkeypatch.setattr(client_mod.time, "sleep", lambda *_a, **_k: None)

        client = _make_client()

        def _flaky_stream():
            yield _make_chunk(content="part-1 ")
            raise _make_status_error(503, retry_after="0")

        client.client.chat.completions.create = MagicMock(return_value=_flaky_stream())

        seen: List[str] = []
        with pytest.raises(openai.APIStatusError):
            client.chat_stream(
                messages=[{"role": "user", "content": "hi"}],
                on_text_chunk=seen.append,
            )

        # The chunk before the failure was already delivered; we keep it.
        # The crucial assertion is no retry fired.
        assert seen == ["part-1 "]
        assert client.client.chat.completions.create.call_count == 1

    def test_does_not_retry_on_non_retryable_status(self, monkeypatch):
        monkeypatch.setattr(client_mod.time, "sleep", lambda *_a, **_k: None)

        client = _make_client()
        err = _make_status_error(401)
        client.client.chat.completions.create = MagicMock(side_effect=err)

        with pytest.raises(openai.APIStatusError):
            client.chat_stream(messages=[{"role": "user", "content": "hi"}])

        assert client.client.chat.completions.create.call_count == 1

    def test_upstream_proxy_error_does_not_trigger_streaming_fallback(self, monkeypatch):
        # Bug guard: a 502 with body "upstream connect error" used to match the
        # bare "stream" substring and silently fall back to non-streaming,
        # bypassing the retry policy and Retry-After. It must now go through
        # the normal status-based retry path instead.
        monkeypatch.setattr(client_mod.time, "sleep", lambda *_a, **_k: None)

        client = _make_client()

        # Fabricate a 502 whose message contains "upstream"
        err = _make_status_error(502, retry_after="0")
        err.message = "Bad Gateway: upstream connect error or disconnect/reset"

        # Second attempt succeeds with a normal stream
        ok_chunks = iter([_make_chunk(content="ok", finish_reason="stop")])
        client.client.chat.completions.create = MagicMock(side_effect=[err, ok_chunks])

        # If the old behavior were still in place, the non-streaming chat()
        # path would be taken and with_raw_response.create would fire. Make
        # that explode so the test fails loudly if the regression returns.
        client.client.chat.completions.with_raw_response.create = MagicMock(
            side_effect=AssertionError("non-streaming fallback must not run")
        )

        seen: List[str] = []
        response = client.chat_stream(
            messages=[{"role": "user", "content": "hi"}],
            on_text_chunk=seen.append,
        )

        assert seen == ["ok"]
        assert response.choices[0].message.content == "ok"
        # Two streaming attempts, no non-streaming fallback
        assert client.client.chat.completions.create.call_count == 2

    def test_streaming_not_supported_falls_back_to_non_streaming(self, monkeypatch):
        # Pre-existing behavior preserved: a "stream not supported" error
        # routes to chat() once, with no status retry.
        monkeypatch.setattr(client_mod.time, "sleep", lambda *_a, **_k: None)

        client = _make_client()

        client.client.chat.completions.create = MagicMock(
            side_effect=ValueError("This provider does not support streaming")
        )

        ok_raw = MagicMock()
        ok_raw.parse.return_value = _make_completion("non-stream-result")
        client.client.chat.completions.with_raw_response.create = MagicMock(
            return_value=ok_raw
        )

        seen: List[str] = []
        response = client.chat_stream(
            messages=[{"role": "user", "content": "hi"}],
            on_text_chunk=seen.append,
        )

        assert response.choices[0].message.content == "non-stream-result"
        assert seen == ["non-stream-result"]

    def test_retries_after_role_only_chunk_then_error(self, monkeypatch):
        # Providers commonly emit a role-only / empty first chunk before any
        # text. That chunk consumes a loop iteration but does not fire
        # on_text_chunk, so the pre-output retry path must still apply when
        # the next chunk raises a retryable error.
        monkeypatch.setattr(client_mod.time, "sleep", lambda *_a, **_k: None)

        client = _make_client()

        def _flaky_with_role_chunk():
            yield _make_chunk(content=None)  # role-only: no LLM_TEXT emitted
            raise _make_status_error(503, retry_after="0")

        ok_chunks = iter([
            _make_chunk(content="recovered", finish_reason="stop"),
        ])

        client.client.chat.completions.create = MagicMock(
            side_effect=[_flaky_with_role_chunk(), ok_chunks]
        )

        seen: List[str] = []
        response = client.chat_stream(
            messages=[{"role": "user", "content": "hi"}],
            on_text_chunk=seen.append,
        )

        assert seen == ["recovered"]
        assert response.choices[0].message.content == "recovered"
        assert client.client.chat.completions.create.call_count == 2

    def test_role_only_chunk_then_error_marks_streamed_false(self, monkeypatch):
        # Same shape as above but with a non-retryable error: nothing reached
        # the host, so .streamed must be False even though the chunk loop ran
        # one iteration.
        monkeypatch.setattr(client_mod.time, "sleep", lambda *_a, **_k: None)

        client = _make_client()

        def _flaky_with_role_chunk():
            yield _make_chunk(content=None)
            raise _make_status_error(401)

        client.client.chat.completions.create = MagicMock(
            return_value=_flaky_with_role_chunk()
        )

        seen: List[str] = []
        with pytest.raises(openai.APIStatusError) as excinfo:
            client.chat_stream(
                messages=[{"role": "user", "content": "hi"}],
                on_text_chunk=seen.append,
            )

        assert seen == []
        assert getattr(excinfo.value, "streamed", None) is False

    def test_mid_stream_error_marks_streamed_true(self, monkeypatch):
        # The runtime's LLM_CALL_COMPLETED(error) payload needs to distinguish
        # "host already saw partial chunks" from "nothing reached the host" so
        # it can decide between regenerate-from-scratch and resume-style retry.
        # client.chat_stream attaches .streamed to the raised exception.
        monkeypatch.setattr(client_mod.time, "sleep", lambda *_a, **_k: None)

        client = _make_client()

        def _flaky_stream():
            yield _make_chunk(content="part-1 ")
            raise _make_status_error(503, retry_after="0")

        client.client.chat.completions.create = MagicMock(return_value=_flaky_stream())

        with pytest.raises(openai.APIStatusError) as excinfo:
            client.chat_stream(
                messages=[{"role": "user", "content": "hi"}],
                on_text_chunk=lambda _c: None,
            )

        assert getattr(excinfo.value, "streamed", None) is True

    def test_pre_stream_error_marks_streamed_false(self, monkeypatch):
        # Non-retryable error with no chunks delivered: .streamed must be False
        # so the host knows it's safe to regenerate without surfacing a
        # half-emitted message in the conversation.
        monkeypatch.setattr(client_mod.time, "sleep", lambda *_a, **_k: None)

        client = _make_client()
        err = _make_status_error(401)
        client.client.chat.completions.create = MagicMock(side_effect=err)

        with pytest.raises(openai.APIStatusError) as excinfo:
            client.chat_stream(messages=[{"role": "user", "content": "hi"}])

        assert getattr(excinfo.value, "streamed", None) is False

    def test_cancellation_during_retry_sleep_aborts(self, monkeypatch):
        # Long Retry-After with a cancelled token must not strand the user.
        monkeypatch.setattr(client_mod.time, "sleep", lambda *_a, **_k: None)

        client = _make_client()
        err = _make_status_error(429, retry_after="0", cls=openai.RateLimitError)
        client.client.chat.completions.create = MagicMock(side_effect=err)

        # Cancellation token reports cancelled → _interruptible_sleep returns False
        # immediately → chat_stream raises without making a second create() call.
        token = MagicMock()
        token.is_cancelled = True

        # Force any computed delay to be > 0 so _interruptible_sleep is exercised.
        monkeypatch.setattr(
            client_mod, "_compute_backoff_delay", lambda *_a, **_k: 5.0
        )

        with pytest.raises(openai.RateLimitError):
            client.chat_stream(
                messages=[{"role": "user", "content": "hi"}],
                cancellation_token=token,
            )

        # First create() fired; sleep was interrupted; no second create().
        assert client.client.chat.completions.create.call_count == 1


# ---------------------------------------------------------------------------
# Constructor side effect
# ---------------------------------------------------------------------------


class TestSDKRetriesDisabled:
    def test_openai_client_constructed_with_max_retries_zero(self, monkeypatch):
        captured = {}

        class _Recorder:
            def __init__(self, **kwargs):
                captured.update(kwargs)
                self.chat = MagicMock()

        monkeypatch.setattr(client_mod, "_openai_client_cls", lambda: _Recorder)
        # Wipe any cached OpenAI from prior PEP 562 access
        client_mod.__dict__.pop("OpenAI", None)

        LLMClient(
            api_key="test-key",
            base_url="https://api.example.com/v1",
            model="gpt-test",
            log_file=None,
            logger=MagicMock(),
        )

        assert captured.get("max_retries") == 0
