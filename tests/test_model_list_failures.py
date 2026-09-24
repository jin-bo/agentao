"""``list_available_models`` against the real SDKs, with only the socket replaced.

Two defects: the catalog fetch ran on the SDK's defaults (600 s a try, three
tries) while a user waited on ``/model``, and its error was ``str(e)`` — which
for a status error is the endpoint's response body, handed verbatim to an ACP
client and through Rich markup to the CLI.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import anthropic
import httpx
import httpx2
import openai
import pytest

from agentao.runtime import model as runtime_model

_BODY_SECRET = "sk-live-this-must-not-be-echoed"


def _agent(client):
    return SimpleNamespace(
        llm=SimpleNamespace(client=client, logger=logging.getLogger("test-model-list"))
    )


def _openai(handler):
    return openai.OpenAI(
        api_key="x", base_url="http://models.test/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _anthropic(handler):
    return anthropic.Anthropic(
        api_key="x", base_url="http://models.test",
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )


def test_the_catalog_fetch_is_bounded_and_retried_once():
    seen = []

    def handler(request):
        seen.append(request.extensions.get("timeout"))
        return httpx.Response(200, json={"object": "list", "data": [
            {"id": "b", "object": "model", "created": 0, "owned_by": "t"},
            {"id": "a", "object": "model", "created": 0, "owned_by": "t"},
        ]})

    assert runtime_model.list_available_models(_agent(_openai(handler))) == ["a", "b"]
    assert len(seen) == 1
    # The timeout the SDK actually handed the transport, not the one we meant to set.
    assert seen[0]["read"] == runtime_model._MODEL_LIST_TIMEOUT_S


def test_a_timeout_is_named_and_stops_after_one_retry():
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(RuntimeError) as err:
        runtime_model.list_available_models(_agent(_openai(handler)))
    assert len(calls) == 1 + runtime_model._MODEL_LIST_MAX_RETRIES
    assert "did not answer within 10s" in str(err.value)


@pytest.mark.parametrize("make", [_openai, _anthropic], ids=["openai", "anthropic"])
def test_a_status_error_names_the_status_and_not_the_body(make, caplog):
    def handler(request):
        cls = httpx2.Response if make is _anthropic else httpx.Response
        return cls(403, json={"error": {"message": f"bad key {_BODY_SECRET}"}})

    with caplog.at_level(logging.WARNING, logger="test-model-list"):
        with pytest.raises(RuntimeError) as err:
            runtime_model.list_available_models(_agent(make(handler)))
    message = str(err.value)
    assert "HTTP 403" in message
    assert _BODY_SECRET not in message
    # The detail is still there for whoever debugs it — in the log, not the reply.
    assert _BODY_SECRET in caplog.text


def test_acp_passes_the_runtime_message_through_without_doubling_it(monkeypatch):
    from agentao.acp import session_list_models as acp_list_models

    session = SimpleNamespace(
        session_id="s",
        agent=SimpleNamespace(list_available_models=lambda: (_ for _ in ()).throw(
            RuntimeError("Could not fetch model list: the models endpoint answered HTTP 403")
        )),
        last_known_models=None,
    )
    monkeypatch.setattr(acp_list_models, "require_active_session", lambda *a: session)
    result = acp_list_models.handle_session_list_models(None, {"sessionId": "s"})
    assert result["warning"] == "Could not fetch model list: the models endpoint answered HTTP 403"


def test_acp_names_a_foreign_exception_by_type_only(monkeypatch):
    from agentao.acp import session_list_models as acp_list_models

    session = SimpleNamespace(
        session_id="s",
        agent=SimpleNamespace(list_available_models=lambda: (_ for _ in ()).throw(
            ValueError(_BODY_SECRET)
        )),
        last_known_models=["m1"],
    )
    monkeypatch.setattr(acp_list_models, "require_active_session", lambda *a: session)
    result = acp_list_models.handle_session_list_models(None, {"sessionId": "s"})
    assert result == {"models": ["m1"], "warning": "Could not fetch model list: ValueError"}
