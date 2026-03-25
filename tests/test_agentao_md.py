"""Test that CHATAGENT.md is loaded correctly."""

from pathlib import Path
from unittest.mock import Mock, patch


def test_chatagent_md_loading():
    """Test that the agent loads CHATAGENT.md on initialization."""
    # Mock the LLMClient to avoid needing an API key
    with patch('chatagent.agent.LLMClient') as mock_llm_client:
        # Create a mock logger
        mock_logger = Mock()
        mock_llm_client.return_value.logger = mock_logger
        mock_llm_client.return_value.model = "gpt-4"

        # Import and create agent
        from chatagent.agent import ChatAgent

        agent = ChatAgent()

        # Check that project instructions were loaded
        assert agent.project_instructions is not None, "CHATAGENT.md should be loaded"
        assert len(agent.project_instructions) > 0, "CHATAGENT.md should have content"
        assert "ChatAgent Project Instructions" in agent.project_instructions, "Should contain header"

        print("✅ CHATAGENT.md loaded successfully")
        print(f"✅ Content length: {len(agent.project_instructions)} characters")

        # Test that system prompt includes project instructions
        system_prompt = agent._build_system_prompt()
        assert "=== Project Instructions ===" in system_prompt, "System prompt should include project instructions section"
        assert "ChatAgent Project Instructions" in system_prompt, "System prompt should include CHATAGENT.md content"

        print("✅ System prompt includes project instructions")
        print(f"✅ System prompt length: {len(system_prompt)} characters")


def test_chatagent_md_missing():
    """Test that the agent handles missing CHATAGENT.md gracefully."""
    # Mock the LLMClient
    with patch('chatagent.agent.LLMClient') as mock_llm_client:
        mock_logger = Mock()
        mock_llm_client.return_value.logger = mock_logger
        mock_llm_client.return_value.model = "gpt-4"

        # Mock Path.cwd() to return a directory without CHATAGENT.md
        with patch('chatagent.agent.Path.cwd') as mock_cwd:
            mock_cwd.return_value = Path('/nonexistent/path')

            from chatagent.agent import ChatAgent

            agent = ChatAgent()

            # Should handle missing file gracefully
            assert agent.project_instructions is None, "Should be None when file doesn't exist"

            # System prompt should still work
            system_prompt = agent._build_system_prompt()
            assert "You are ChatAgent" in system_prompt, "System prompt should work without CHATAGENT.md"
            assert "=== Project Instructions ===" not in system_prompt, "Should not have project instructions section"

            print("✅ Agent handles missing CHATAGENT.md gracefully")


if __name__ == "__main__":
    print("Testing CHATAGENT.md loading...")
    print()

    try:
        test_chatagent_md_loading()
        print()
        test_chatagent_md_missing()
        print()
        print("=" * 50)
        print("✅ All tests passed!")
    except AssertionError as e:
        print(f"❌ Test failed: {e}")
        exit(1)
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
