"""FastAPI route that runs an Agentao turn in a background task.

The minimum 1-route shape:

    POST /run  body: {"prompt": "..."}    → 202 with a job_id
    GET  /run/{job_id}                    → result or 404 / 425 (still running)
    POST /run/{job_id}/cancel             → cancels the in-flight token

Per-request agent: each POST constructs an ``Agentao`` against the
``llm_client`` returned by :func:`get_llm_client` (overridable for
tests). Cancellation propagates from the asyncio task to the in-flight
``chat()`` token via ``arun``.

For multi-tenant SaaS shapes (per-tenant pool, SSE streaming), see
``examples/saas-assistant/`` instead — this file is intentionally the
narrowest sample.
"""

from __future__ import annotations

import asyncio
import tempfile
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import BackgroundTasks, FastAPI, HTTPException

from agentao import Agentao
from agentao.cancellation import CancellationToken
from agentao.llm import LLMClient


# ---------------------------------------------------------------------------
# LLM injection seam — tests override this dependency.
# ---------------------------------------------------------------------------


def get_llm_client() -> LLMClient:
    """Default: read from env, fail loud if missing.

    Tests override this dependency with a fake LLMClient so the example
    can be smoke-tested without any API key. See ``tests/test_smoke.py``.
    """
    import os

    return LLMClient(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        model=os.environ.get("OPENAI_MODEL", "gpt-5.5"),
    )


# ---------------------------------------------------------------------------
# In-memory job store
# ---------------------------------------------------------------------------


class _Job:
    __slots__ = ("token", "task", "result", "error", "cancelled")

    def __init__(self, token: CancellationToken) -> None:
        self.token = token
        self.task: asyncio.Task[str] | None = None
        self.result: str | None = None
        self.error: str | None = None
        self.cancelled: bool = False


_JOBS: dict[str, _Job] = {}


def create_app(llm_client_factory: Callable[[], LLMClient] = get_llm_client) -> FastAPI:
    """Construct the FastAPI app. ``llm_client_factory`` is the test seam."""
    app = FastAPI(title="agentao-fastapi-background-example")
    app.state.llm_client_factory = llm_client_factory

    async def _run_turn(job: _Job, prompt: str) -> str:
        agent = Agentao(
            working_directory=Path(tempfile.mkdtemp(prefix="agentao-job-")),
            llm_client=app.state.llm_client_factory(),
        )
        try:
            return await agent.arun(prompt, cancellation_token=job.token)
        finally:
            agent.close()

    @app.post("/run", status_code=202)
    async def run(body: dict[str, Any]) -> dict[str, Any]:
        prompt = body.get("prompt")
        if not isinstance(prompt, str) or not prompt:
            raise HTTPException(400, "prompt must be a non-empty string")
        job_id = uuid.uuid4().hex[:8]
        token = CancellationToken()
        job = _Job(token)
        _JOBS[job_id] = job

        async def _runner() -> str:
            try:
                result = await _run_turn(job, prompt)
                job.result = result
                return result
            except asyncio.CancelledError:
                # Distinct from a runtime error: the client used the
                # cancel endpoint. Record it so GET /run/{id} can
                # report ``status: cancelled`` instead of the default
                # ``status: ok`` with ``result: null``.
                job.cancelled = True
                raise
            except Exception as exc:
                job.error = str(exc)
                raise

        job.task = asyncio.create_task(_runner())
        return {"job_id": job_id}

    @app.get("/run/{job_id}")
    async def get_run(job_id: str) -> dict[str, Any]:
        job = _JOBS.get(job_id)
        if job is None:
            raise HTTPException(404, "no such job")
        if job.task and not job.task.done():
            raise HTTPException(425, "job still running")
        if job.cancelled:
            return {"status": "cancelled"}
        if job.error is not None:
            return {"status": "error", "error": job.error}
        return {"status": "ok", "result": job.result}

    @app.post("/run/{job_id}/cancel")
    async def cancel_run(job_id: str) -> dict[str, str]:
        job = _JOBS.get(job_id)
        if job is None:
            raise HTTPException(404, "no such job")
        job.token.cancel("client-cancel")
        if job.task and not job.task.done():
            job.task.cancel()
        return {"status": "cancelling"}

    return app


# Module-level app for ``uvicorn app.main:app``.
app = create_app()
