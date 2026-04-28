#!/usr/bin/env python3
"""Test skill integration with Agentao."""

import os
from pathlib import Path

from agentao import Agentao


def test_skill_with_resources():
    """Test that agent can access skill resources."""
    # Create agent
    agent = Agentao(working_directory=Path.cwd())

    print("=" * 70)
    print("Testing Skill Resource Access")
    print("=" * 70)

    # First, activate the skill
    print("\n1. Activating standard-review skill...")
    response1 = agent.chat(
        "请激活 standard-review skill，我需要审查一个标准文件"
    )
    print("\nAgent response:")
    print(response1)

    # Then ask agent to read a reference file
    print("\n" + "=" * 70)
    print("\n2. Asking agent to read clause-types.md reference file...")
    response2 = agent.chat(
        "请读取 clause-types.md 文件，告诉我关于'要求'类型条款的能愿动词使用规则"
    )
    print("\nAgent response:")
    print(response2)

    print("\n" + "=" * 70)
    print("Test completed!")
    print("\nConversation summary:")
    print(agent.get_conversation_summary())


if __name__ == "__main__":
    # Check for API key
    if not os.getenv("OPENAI_API_KEY") and not os.getenv("API_KEY"):
        print("Warning: No API key found. Set OPENAI_API_KEY or API_KEY environment variable.")
        print("Skipping integration test.")
    else:
        test_skill_with_resources()
