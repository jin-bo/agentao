"""The classifiers read an API error's body, not only ``str(exc)``.

openai 3.38 shortened ``str()`` of an ``APIStatusError`` to the message, and
the error JSON — where a gateway names a refused field or puts the only
overflow signal — went with it. The SDK keeps it on ``exc.body``. These
errors are built with the real SDK class and the 3.38 text, so they pass or
fail the same on every installed openai.
"""

from unittest.mock import MagicMock

import httpx
import openai

from agentao.context_manager import is_context_too_long_error
from agentao.llm._retry import api_error_text


def _error(message: str, body):
    request = httpx.Request("POST", "https://gateway.example/v1/responses")
    response = httpx.Response(400, request=request)
    return openai.BadRequestError(message, response=response, body=body)


def test_the_body_joins_a_message_only_text():
    exc = _error("Error code: 400 - Invalid request.", {
        "message": "Invalid request.", "type": "invalid_request_error", "param": "include"})
    assert str(exc) == "Error code: 400 - Invalid request."
    assert "'param': 'include'" in api_error_text(exc)


def test_a_text_that_already_holds_the_body_is_left_as_is():
    # openai before 3.38 (and the anthropic SDK) put the JSON in the text.
    body = {"message": "Invalid request.", "param": "include"}
    exc = _error(f"Error code: 400 - {{'error': {body!r}}}", body)
    assert api_error_text(exc) == str(exc)


def test_a_body_that_is_not_json_adds_nothing():
    exc = _error("Error code: 400 - nope", None)
    assert api_error_text(exc) == str(exc)
    mock = MagicMock()
    mock.__str__.return_value = "boom"
    assert api_error_text(mock) == "boom"


def test_an_overflow_named_only_by_its_code_is_an_overflow():
    exc = _error("Error code: 400 - Bad request.", {
        "message": "Bad request.", "type": "invalid_request_error",
        "code": "context_length_exceeded"})
    assert is_context_too_long_error(exc)
