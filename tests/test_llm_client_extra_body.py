"""Tests for the ``extra_body`` host LLM request-passthrough (finding "B").

A host needs to reach request-body params the closed request build does not
expose — ``reasoning_effort`` / ``top_p`` / ``seed`` / ``response_format`` and
provider-specific fields. v1 forwards a single ``extra_body`` dict verbatim to
the SDK's ``.create(extra_body=...)`` option. See
``docs/design/host-llm-extra-params.md``.

Coverage (design §9):
- forwarded to both ``chat()`` (non-streaming) and ``chat_stream()`` (streaming)
- back-compat: empty/omitted → key absent → request byte-identical to today
- structural-overlap warning fires ONCE at construction, not per request
- type guard at construction (``TypeError`` on non-dict)
- constructor mutual-exclusion guard (not a silent no-op)
- env tolerance: malformed / valid-but-non-object ``LLM_EXTRA_BODY`` → warn+skip
- log redaction: credential-like keys masked, benign ``*_tokens`` key untouched
- ``reconfigure()`` preserves ``extra_body``; latches still reset
"""

from __future__ import annotations

import inspect
import logging
from unittest.mock import MagicMock

import pytest

from agentao.embedding.factory import discover_llm_kwargs
from agentao.llm.client import LLMClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(**kwargs) -> LLMClient:
    return LLMClient(
        api_key="test-key",
        base_url="https://api.example.com/v1",
        model="gpt-test",
        log_file=None,
        logger=MagicMock(),
        **kwargs,
    )


def _make_completion(content: str = "ok"):
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
    response.usage = MagicMock(prompt_tokens=5, completion_tokens=3, total_tokens=8)
    return response


def _make_chunk(*, content: str | None = None, finish_reason: str | None = None):
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
# Forwarding — both paths
# ---------------------------------------------------------------------------


def test_extra_body_forwarded_to_chat_nonstreaming():
    client = _make_client(extra_body={"reasoning_effort": "high"})
    raw = MagicMock()
    raw.parse.return_value = _make_completion("hi")
    client.client.chat.completions.with_raw_response.create = MagicMock(return_value=raw)

    client.chat(messages=[{"role": "user", "content": "hi"}])

    call = client.client.chat.completions.with_raw_response.create.call_args
    assert call.kwargs["extra_body"] == {"reasoning_effort": "high"}


def test_extra_body_forwarded_to_chat_stream():
    client = _make_client(extra_body={"reasoning_effort": "high"})
    client.client.chat.completions.create = MagicMock(
        return_value=[_make_chunk(content="hi", finish_reason="stop")]
    )

    client.chat_stream(messages=[{"role": "user", "content": "hi"}])

    call = client.client.chat.completions.create.call_args
    assert call.kwargs["extra_body"] == {"reasoning_effort": "high"}


# ---------------------------------------------------------------------------
# Back-compat — empty/omitted is byte-identical to today
# ---------------------------------------------------------------------------


def test_no_extra_body_key_absent_both_paths():
    client = _make_client()  # no extra_body
    non_stream = client._build_request_kwargs(
        [{"role": "user", "content": "hi"}], None, 100, stream=False
    )
    stream = client._build_request_kwargs(
        [{"role": "user", "content": "hi"}], None, 100, stream=True
    )
    assert "extra_body" not in non_stream
    assert "extra_body" not in stream


def test_back_compat_request_kwargs_golden():
    """The non-streaming request dict is unchanged when extra_body is unset."""
    client = _make_client()
    kwargs = client._build_request_kwargs(
        [{"role": "user", "content": "hi"}], None, 100, stream=False
    )
    assert kwargs == {
        "model": "gpt-test",
        "messages": [{"role": "user", "content": "hi"}],
        "temperature": 0.2,
        "max_tokens": 100,
    }


# ---------------------------------------------------------------------------
# Structural-overlap warning (§3.3) — once at construction, never per request
# ---------------------------------------------------------------------------


def test_structural_overlap_warns_once_not_per_request():
    client = _make_client(extra_body={"messages": [{"role": "system", "content": "x"}]})
    # One warning at construction, naming the overlapping key.
    assert client.logger.warning.call_count == 1
    assert "messages" in str(client.logger.warning.call_args)

    raw = MagicMock()
    raw.parse.return_value = _make_completion("hi")
    client.client.chat.completions.with_raw_response.create = MagicMock(return_value=raw)
    client.chat(messages=[{"role": "user", "content": "a"}])
    client.chat(messages=[{"role": "user", "content": "b"}])

    # No per-request overlap warning.
    assert client.logger.warning.call_count == 1


def test_no_overlap_no_warning():
    client = _make_client(extra_body={"reasoning_effort": "high"})
    assert client.logger.warning.call_count == 0


# ---------------------------------------------------------------------------
# Type guard (§3.1)
# ---------------------------------------------------------------------------


def test_non_dict_extra_body_raises_typeerror():
    with pytest.raises(TypeError, match="extra_body must be a dict"):
        _make_client(extra_body=[("x", 1)])


def test_none_extra_body_accepted():
    client = _make_client(extra_body=None)
    assert client.extra_body == {}


def test_construction_deep_freezes_nested_extra_body():
    """Mutating a nested value the host still holds must not alter the client's
    frozen config (deepcopy, not a shallow dict() copy)."""
    headers = {"Authorization": "Bearer A"}
    client = _make_client(extra_body={"extra_headers": headers})
    headers["Authorization"] = "Bearer B"  # caller mutates after construction
    assert client.extra_body["extra_headers"]["Authorization"] == "Bearer A"


# ---------------------------------------------------------------------------
# Constructor mutual-exclusion guard (§4.1) — not a silent no-op
# ---------------------------------------------------------------------------


def test_agentao_rejects_extra_body_alongside_llm_client(tmp_path):
    from agentao import Agentao

    injected = _make_client()
    with pytest.raises(ValueError, match="extra_body"):
        Agentao(
            working_directory=tmp_path,
            llm_client=injected,
            extra_body={"reasoning_effort": "high"},
        )


def test_agentao_extra_body_is_keyword_only():
    """Codex P1: extra_body must be keyword-only so it does NOT shift the
    legacy positional callback args (api_key..plan_session are
    positional-or-keyword on Agentao.__init__). The 6th positional must still
    be confirmation_callback, not extra_body."""
    from agentao import Agentao

    init = Agentao.__init__
    # The autouse conftest fixture wraps __init__ in a (*args, **kwargs) shim;
    # reach the real function via its closure to inspect the true signature.
    if getattr(init, "__closure__", None):
        for cell in init.__closure__:
            c = cell.cell_contents
            if callable(c) and getattr(c, "__name__", "") == "__init__":
                init = c
                break

    params = inspect.signature(init).parameters
    assert params["extra_body"].kind is inspect.Parameter.KEYWORD_ONLY
    # The positional-or-keyword group's 6th entry (after self) is unchanged.
    pos = [
        n for n, p in params.items()
        if p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD and n != "self"
    ]
    assert pos[5] == "confirmation_callback"
    assert "extra_body" not in pos


# ---------------------------------------------------------------------------
# Env tolerance (§4 CLI/env) — discover_llm_kwargs
# ---------------------------------------------------------------------------


def test_env_parses_valid_extra_body(monkeypatch):
    monkeypatch.setenv("LLM_EXTRA_BODY", '{"reasoning_effort": "high", "seed": 7}')
    assert discover_llm_kwargs()["extra_body"] == {"reasoning_effort": "high", "seed": 7}


def test_env_skips_malformed_extra_body(monkeypatch, caplog):
    monkeypatch.setenv("LLM_EXTRA_BODY", "not json {")
    with caplog.at_level(logging.WARNING, logger="agentao.embedding.factory"):
        out = discover_llm_kwargs()
    assert "extra_body" not in out
    # The "warn" half of "warn + skip" must actually fire.
    assert any("LLM_EXTRA_BODY" in r.message for r in caplog.records)


@pytest.mark.parametrize("value", ["[]", '"x"', "3", "true", "null"])
def test_env_skips_non_object_extra_body(monkeypatch, caplog, value):
    monkeypatch.setenv("LLM_EXTRA_BODY", value)
    with caplog.at_level(logging.WARNING, logger="agentao.embedding.factory"):
        out = discover_llm_kwargs()
    assert "extra_body" not in out
    assert any("must be a JSON object" in r.message for r in caplog.records)


@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_env_empty_extra_body_is_unset_and_silent(monkeypatch, caplog, value):
    """Empty / whitespace-only is treated as unset — skipped with NO warning
    (a common 'disable this var' idiom must not log on every startup)."""
    monkeypatch.setenv("LLM_EXTRA_BODY", value)
    with caplog.at_level(logging.WARNING, logger="agentao.embedding.factory"):
        out = discover_llm_kwargs()
    assert "extra_body" not in out
    assert not any("LLM_EXTRA_BODY" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Logging redaction (§7) — exact key-name match, recursive
# ---------------------------------------------------------------------------


def test_log_request_redacts_extra_body_credentials():
    client = _make_client(
        extra_body={
            "reasoning_effort": "high",
            "api_key": "sk-secret",
            "extra_headers": {"Authorization": "Bearer sk-nested"},
            "num_tokens": 5,  # benign *_tokens key — exact match, must NOT redact
        }
    )
    kwargs = client._build_request_kwargs(
        [{"role": "user", "content": "hi"}], None, None, stream=False
    )
    client._log_request("req_x", kwargs)

    body_lines = [
        str(call.args[0])
        for call in client.logger.info.call_args_list
        if call.args and "Extra Body" in str(call.args[0])
    ]
    assert body_lines, "no Extra Body line logged"
    line = body_lines[0]
    assert "high" in line                      # non-sensitive value preserved
    assert "sk-secret" not in line             # top-level credential masked
    assert "sk-nested" not in line             # nested credential masked
    assert "***" in line
    assert "num_tokens" in line and "5" in line  # benign *_tokens not redacted


@pytest.mark.parametrize(
    "key", ["x-api-key", "X-Api-Key", "proxy-authorization", "x-auth-token", "client_secret"]
)
def test_log_request_redacts_header_style_credentials(key):
    """Codex P2: header-style credential names (e.g. gateway X-Api-Key passed
    via extra_headers) must be redacted, not just body-style api_key."""
    client = _make_client(extra_body={"extra_headers": {key: "sk-leak"}})
    kwargs = client._build_request_kwargs(
        [{"role": "user", "content": "hi"}], None, None, stream=False
    )
    client._log_request("req_x", kwargs)
    body_lines = [
        str(call.args[0])
        for call in client.logger.info.call_args_list
        if call.args and "Extra Body" in str(call.args[0])
    ]
    assert body_lines
    assert "sk-leak" not in body_lines[0]
    assert "***" in body_lines[0]


def test_log_request_redacts_credential_inside_tuple():
    """A credential nested inside a tuple value is still masked (the redactor
    recurses tuples, not just dict/list)."""
    client = _make_client(extra_body={"creds": ({"api_key": "sk-in-tuple"},)})
    kwargs = client._build_request_kwargs(
        [{"role": "user", "content": "hi"}], None, None, stream=False
    )
    client._log_request("req_x", kwargs)
    body_lines = [
        str(call.args[0])
        for call in client.logger.info.call_args_list
        if call.args and "Extra Body" in str(call.args[0])
    ]
    assert body_lines
    assert "sk-in-tuple" not in body_lines[0]
    assert "***" in body_lines[0]


def test_log_request_omits_extra_body_when_unset():
    client = _make_client()
    kwargs = client._build_request_kwargs(
        [{"role": "user", "content": "hi"}], None, None, stream=False
    )
    client._log_request("req_x", kwargs)
    assert not any(
        call.args and "Extra Body" in str(call.args[0])
        for call in client.logger.info.call_args_list
    )


# ---------------------------------------------------------------------------
# reconfigure() / model-switch semantics (§5)
# ---------------------------------------------------------------------------


def test_reconfigure_preserves_extra_body_and_resets_latches():
    client = _make_client(extra_body={"reasoning_effort": "high"})
    client._use_max_completion_tokens = True
    client.omit_temperature = True

    client.reconfigure(api_key="new-key", model="new-model")

    assert client.extra_body == {"reasoning_effort": "high"}  # host config survives
    assert client._use_max_completion_tokens is False         # latch cleared
    assert client.omit_temperature is False                   # latch cleared


# ---------------------------------------------------------------------------
# Sub-agent inheritance — extra_body must flow through the _llm_config snapshot
# ---------------------------------------------------------------------------


def test_llm_config_snapshot_includes_extra_body(tmp_path):
    """Sub-agents are rebuilt from the parent's _llm_config() snapshot via the
    raw-config path; extra_body must be in it or sub-agent LLM calls silently
    drop reasoning_effort / provider-mandatory fields."""
    from agentao import Agentao

    agent = Agentao(
        working_directory=tmp_path,
        api_key="x",
        base_url="http://localhost:0",
        model="dummy",
        extra_body={"reasoning_effort": "high"},
    )
    try:
        assert agent._llm_config["extra_body"] == {"reasoning_effort": "high"}
    finally:
        agent.close()


def test_llm_config_snapshot_extra_body_none_when_unset(tmp_path):
    from agentao import Agentao

    agent = Agentao(
        working_directory=tmp_path,
        api_key="x",
        base_url="http://localhost:0",
        model="dummy",
    )
    try:
        # Empty/unset → None so the sub-agent raw-config build simply omits it.
        assert agent._llm_config["extra_body"] is None
    finally:
        agent.close()
