"""Exercise the real decision adapter against a scripted HTTP transport."""
import json
import threading
import time

import httpx
import pytest


def _response(criteria, winner=None, confidence=0.95):
    winner = winner or next(key for key in criteria if key != "none")
    return {"model": "jev-1.13.0", "answers": {"skill": {
        "type": "choice", "choice": winner, "confidence": confidence,
        "probabilities": {key: float(key == winner) for key in criteria},
    }}, "usage": {"input_tokens": 100, "output_tokens": 20}}


def _service(handler, **config):
    from agentao.recommendations import JevConfig, JevSkillRecommender

    return JevSkillRecommender(JevConfig(enabled=True, **config), "test-jev-key",
                               transport=httpx.MockTransport(handler))


def _candidates(count=2):
    from agentao.recommendations import SkillCandidate

    return [SkillCandidate(f"skill-{i}", f"description {i}", f"instructions {i}")
            for i in range(count)]


def test_two_stage_protocol_returns_only_offered_name():
    requests = []

    def handler(req):
        body = json.loads(req.content)
        requests.append(body)
        assert str(req.url) == "https://api.typesafe.ai/v1/systemone"
        assert req.headers["Authorization"] == "Bearer test-jev-key"
        return httpx.Response(200, json=_response(body["questions"]["skill"]["criteria"]))

    service = _service(handler)
    result = service.recommend("Please help", _candidates())
    assert result.skill_name == "skill-0"
    assert len(requests) == 2
    assert "instructions 0" in json.dumps(requests[1])
    assert "test-jev-key" not in repr(service)
    assert service.last_status == "recommended"


@pytest.mark.parametrize("mode", ["disabled", "no-key", "no-candidates", "explicit"])
def test_skip_paths_never_contact_network(mode):
    from dataclasses import replace

    calls = []
    service = _service(lambda req: calls.append(req))
    candidates = _candidates()
    prompt = "help"
    if mode == "disabled":
        service.config = replace(service.config, enabled=False)
    elif mode == "no-key":
        service.api_key = ""
    elif mode == "no-candidates":
        candidates = []
    else:
        prompt = "Use $skill-1 for this task"
    assert service.recommend(prompt, candidates) is None
    assert calls == []


@pytest.mark.parametrize("mode", ["none", "low", "unknown", "nan", "bad-sum", "http", "json"])
def test_unusable_results_fall_back_without_raw_error_output(mode, caplog):
    def handler(req):
        if mode == "http":
            return httpx.Response(401, text="test-jev-key raw diagnostic")
        if mode == "json":
            return httpx.Response(200, text="broken test-jev-key")
        body = json.loads(req.content)
        result = _response(body["questions"]["skill"]["criteria"])
        answer = result["answers"]["skill"]
        if mode == "none":
            result = _response(body["questions"]["skill"]["criteria"], "none")
        elif mode == "low":
            answer["confidence"] = 0.1
        elif mode == "unknown":
            answer["choice"] = "not-offered"
        elif mode == "nan":
            answer["confidence"] = "NaN"
        elif mode == "bad-sum":
            answer["probabilities"] = {k: 0 for k in answer["probabilities"]}
        return httpx.Response(200, json=result)

    service = _service(handler)
    assert service.recommend("help", _candidates()) is None
    assert "test-jev-key" not in caplog.text
    assert "raw diagnostic" not in caplog.text
    expected = ("no-recommendation" if mode in {"none", "low"} else
                "authentication-error" if mode == "http" else "unavailable")
    assert service.last_status == expected


def test_candidate_batches_respect_api_limit_and_compare_winners():
    counts = []

    def handler(req):
        criteria = json.loads(req.content)["questions"]["skill"]["criteria"]
        counts.append(len(criteria))
        return httpx.Response(200, json=_response(criteria))

    assert _service(handler).recommend("help", _candidates(260)) is not None
    assert max(counts) <= 255
    assert len(counts) == 3  # two rank batches, one verification


def test_timeout_returns_promptly_and_late_result_cannot_replace_status():
    entered, release = threading.Event(), threading.Event()
    calls = []

    def handler(req):
        calls.append(req)
        entered.set()
        release.wait(2)
        criteria = json.loads(req.content)["questions"]["skill"]["criteria"]
        return httpx.Response(200, json=_response(criteria))

    service = _service(handler, timeout_ms=100)
    start = time.monotonic()
    try:
        assert service.recommend("help", _candidates()) is None
        assert time.monotonic() - start < 0.8
        assert entered.is_set()
        assert service.last_status == "timeout"
        assert service.recommend("next", _candidates()) is None
        assert len(calls) == 1  # no accumulating abandoned workers
    finally:
        release.set()
    time.sleep(0.05)
    assert service.last_status != "recommended"


def test_cancelled_call_and_close_do_not_start_network():
    from agentao.cancellation import CancellationToken

    calls = []
    service = _service(lambda req: calls.append(req))
    token = CancellationToken()
    token.cancel("stop")
    assert service.recommend("help", _candidates(), cancellation_token=token) is None
    service.close()
    assert service.recommend("help", _candidates()) is None
    assert calls == []


def test_cancellation_during_response_discards_result_and_keeps_cancel_status():
    from agentao.cancellation import CancellationToken

    token = CancellationToken()
    calls = []

    def handler(req):
        calls.append(req)
        token.cancel("stop")
        criteria = json.loads(req.content)["questions"]["skill"]["criteria"]
        return httpx.Response(200, json=_response(criteria))

    service = _service(handler)
    assert service.recommend("help", _candidates(), cancellation_token=token) is None
    assert service.last_status == "cancelled"
    assert len(calls) == 1
