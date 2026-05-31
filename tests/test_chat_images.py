"""End-to-end plumbing test for ``chat(images=...)`` (A1 / PR-1).

Verifies that an image attachment passed to ``chat()`` reaches conversation
history as an OpenAI-style multimodal content list (``text`` + ``image_url``
with an inline ``data:`` URL), and that a plain text turn is unchanged.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch


def _make_agent():
    with patch('agentao.agent.LLMClient') as mock_llm_class:
        mock_llm = Mock()
        mock_llm.logger = Mock()
        mock_llm.model = "gpt-4"
        mock_llm_class.return_value = mock_llm
        from agentao.agent import Agentao
        return Agentao(working_directory=Path.cwd())


def _stub_llm(agent):
    fake_response = MagicMock()
    fake_response.choices[0].message.tool_calls = None
    fake_response.choices[0].message.content = "ok"
    fake_response.choices[0].message.reasoning_content = None
    agent._llm_call = Mock(return_value=fake_response)
    return fake_response


def test_images_become_multimodal_content():
    agent = _make_agent()
    _stub_llm(agent)

    agent.chat(
        "describe this",
        images=[{"mimeType": "image/png", "data": "QUJD"}],
    )

    user_msgs = [m for m in agent.messages if m["role"] == "user"]
    assert user_msgs, "Should have a user message"
    content = user_msgs[0]["content"]

    assert isinstance(content, list), "Image turn content must be a list of parts"
    text_parts = [p for p in content if p.get("type") == "text"]
    image_parts = [p for p in content if p.get("type") == "image_url"]

    assert len(text_parts) == 1
    assert "describe this" in text_parts[0]["text"]
    assert "<system-reminder>" in text_parts[0]["text"]

    assert len(image_parts) == 1
    assert image_parts[0]["image_url"]["url"] == "data:image/png;base64,QUJD"


def test_no_images_stays_plain_string():
    agent = _make_agent()
    _stub_llm(agent)

    agent.chat("just text")

    user_msgs = [m for m in agent.messages if m["role"] == "user"]
    assert user_msgs
    content = user_msgs[0]["content"]
    assert isinstance(content, str), "Text-only turn content must stay a plain string"
    assert "just text" in content


def test_malformed_image_dict_raises_clear_error():
    import pytest

    agent = _make_agent()
    _stub_llm(agent)

    # snake_case key instead of the documented 'mimeType' — should fail at the
    # turn boundary with an actionable ValueError, not an opaque KeyError.
    with pytest.raises(ValueError, match="mimeType"):
        agent.chat("hi", images=[{"mime_type": "image/png", "data": "QUJD"}])


def test_empty_image_data_raises_clear_error():
    import pytest

    agent = _make_agent()
    _stub_llm(agent)

    # Empty data would build a malformed `data:...;base64,` URL — reject it.
    with pytest.raises(ValueError, match="non-empty"):
        agent.chat("hi", images=[{"mimeType": "image/png", "data": ""}])


def test_image_unsupported_falls_back_to_text_reference():
    agent = _make_agent()
    fake_response = _stub_llm(agent)
    agent._llm_call = Mock(side_effect=[
        ValueError("Invalid content type. image_url is only supported by vision models"),
        fake_response,
    ])

    response = agent.chat(
        "describe this",
        images=[{
            "mimeType": "image/png",
            "data": "QUJD",
            "_source": "/tmp/shot.png",
        }],
    )

    assert response == "ok"
    assert agent._llm_call.call_count == 2
    user_msgs = [m for m in agent.messages if m["role"] == "user"]
    assert user_msgs
    content = user_msgs[0]["content"]
    assert isinstance(content, str)
    assert "describe this" in content
    assert "/tmp/shot.png (image/png)" in content
    assert "data:image/png;base64" not in content


def test_image_fallback_replaces_image_message_before_background_note():
    agent = _make_agent()
    fake_response = _stub_llm(agent)
    agent.bg_store = SimpleNamespace(
        drain_notifications=Mock(return_value=["background finished"])
    )
    agent._llm_call = Mock(side_effect=[
        ValueError("Unexpected item type in content."),
        fake_response,
    ])

    response = agent.chat(
        "describe this",
        images=[{
            "mimeType": "image/png",
            "data": "QUJD",
            "_source": "/tmp/shot.png",
        }],
    )

    assert response == "ok"
    assert agent._llm_call.call_count == 2

    retry_messages = agent._llm_call.call_args_list[1].args[0]
    retry_user_contents = [
        msg["content"] for msg in retry_messages if msg.get("role") == "user"
    ]
    assert not any(isinstance(content, list) for content in retry_user_contents)
    assert any("/tmp/shot.png (image/png)" in content for content in retry_user_contents)
    assert any("background finished" in content for content in retry_user_contents)


def test_arun_forwards_images():
    """The async surface must thread images through to multimodal content."""
    import asyncio

    agent = _make_agent()
    _stub_llm(agent)

    asyncio.run(
        agent.arun("describe", images=[{"mimeType": "image/png", "data": "QUJD"}])
    )

    user_msgs = [m for m in agent.messages if m["role"] == "user"]
    assert user_msgs
    content = user_msgs[0]["content"]
    assert isinstance(content, list)
    assert any(p.get("type") == "image_url" for p in content)
