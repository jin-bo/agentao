"""Shared test fixtures for tool-call shaped objects.

Three test files (``test_tool_argument_repair``, ``test_tool_name_repair``,
``test_outbound_sanitize``) all need to fabricate the OpenAI SDK's
``tool_call`` shape: an object exposing ``.id`` and ``.function.{name,
arguments}``. This helper builds that shape, with an optional
``pydantic`` flag for the ``model_dump()`` path some serialiser code
takes when the SDK returns Pydantic objects.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any


def make_tool_call(
    call_id: str,
    name: str,
    arguments: str = "{}",
    *,
    pydantic: bool = False,
) -> Any:
    """Build a fake ``tool_call`` matching the OpenAI SDK shape.

    ``pydantic=True`` returns an object with a ``model_dump()`` method
    instead of a SimpleNamespace, exercising the path that production
    serialisation code takes for SDK-emitted Pydantic models.
    """
    if pydantic:
        class _PydanticToolCall:
            def model_dump(self):
                return {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }
        return _PydanticToolCall()
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )
