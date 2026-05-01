"""Offline smoke for the FastAPI background-task example.

Runs against a fake LLMClient + a stubbed ``_llm_call`` so no API key
is required. The test exercises the full request lifecycle: POST a
job, poll until done, and assert the fake reply lands in the result.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


def _make_fake_llm():
    """Stand-in LLMClient — only the attributes Agentao reads at construction."""
    fake = MagicMock()
    fake.logger = MagicMock()
    fake.model = "fake-model"
    fake.api_key = "fake-key"
    fake.base_url = "http://localhost:1"
    fake.temperature = 0.0
    fake.max_tokens = 100
    fake.total_prompt_tokens = 0
    fake.total_completion_tokens = 0
    return fake


def _fake_llm_response(content: str = "hello from fake llm"):
    response = MagicMock()
    response.choices[0].message.tool_calls = None
    response.choices[0].message.content = content
    response.choices[0].message.reasoning_content = None
    return response


@pytest.fixture
def app_with_fake_llm():
    """Return a TestClient where ``Agentao._llm_call`` returns a 1-shot reply.

    The chat loop calls ``agent._llm_call(...)``; replacing that method
    on the class is the smallest seam that yields a deterministic turn
    without spinning up an OpenAI connection. ``llm_client_factory``
    still gets exercised at construction so the injection seam itself
    is covered by the test.
    """
    with patch(
        "agentao.agent.Agentao._llm_call",
        lambda self, msgs, tools, token: _fake_llm_response(),
    ):
        app = create_app(llm_client_factory=_make_fake_llm)
        with TestClient(app) as client:
            yield client


def test_post_run_returns_job_id_and_completes(app_with_fake_llm) -> None:
    client = app_with_fake_llm
    post = client.post("/run", json={"prompt": "hi"})
    assert post.status_code == 202
    job_id = post.json()["job_id"]

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        r = client.get(f"/run/{job_id}")
        if r.status_code == 200:
            body = r.json()
            assert body["status"] == "ok"
            assert "hello from fake llm" in body["result"]
            return
        time.sleep(0.05)
    raise AssertionError("job never completed")


def test_run_rejects_empty_prompt(app_with_fake_llm) -> None:
    client = app_with_fake_llm
    assert client.post("/run", json={"prompt": ""}).status_code == 400
    assert client.post("/run", json={}).status_code == 400


def test_get_unknown_job_is_404(app_with_fake_llm) -> None:
    client = app_with_fake_llm
    assert client.get("/run/does-not-exist").status_code == 404


def test_cancel_reports_cancelled_status() -> None:
    """``POST /run/{id}/cancel`` makes ``GET /run/{id}`` report cancelled.

    Before the fix, ``_runner`` caught only ``Exception``; the
    ``CancelledError`` raised by ``task.cancel()`` left the job done
    with neither ``result`` nor ``error`` set, so the GET handler
    reported ``status: ok`` with ``result: null``. Now the job is
    flagged ``cancelled`` and the GET reports it as such.
    """

    def _slow_llm_call(self, msgs, tools, token):  # noqa: ANN001
        # ``_llm_call`` is sync — chat() runs in arun's executor thread
        # and never awaits the return value. A blocking sleep keeps the
        # asyncio task in a cancellable running state until the test
        # issues POST /cancel; the awaited future raises
        # CancelledError, propagates back through arun, and lands in
        # _runner's CancelledError handler.
        time.sleep(5.0)
        return _fake_llm_response()

    with patch("agentao.agent.Agentao._llm_call", _slow_llm_call):
        app = create_app(llm_client_factory=_make_fake_llm)
        with TestClient(app) as client:
            post = client.post("/run", json={"prompt": "stalled"})
            assert post.status_code == 202
            job_id = post.json()["job_id"]

            cancel = client.post(f"/run/{job_id}/cancel")
            assert cancel.status_code == 200
            assert cancel.json() == {"status": "cancelling"}

            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                r = client.get(f"/run/{job_id}")
                if r.status_code == 200:
                    assert r.json() == {"status": "cancelled"}
                    return
                time.sleep(0.05)
            raise AssertionError("cancelled job never resolved")
