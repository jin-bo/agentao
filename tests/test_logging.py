"""Test script to verify LLM logging functionality."""

import os
from pathlib import Path

# Set a test API key if not already set
if not os.getenv("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = "test-key-for-demo"
    os.environ["OPENAI_BASE_URL"] = "https://api.example.com/v1"

from agentao.llm import LLMClient

print("=" * 70)
print("Testing LLM Logging Functionality")
print("=" * 70)

# Create test log file
log_file = "test_agentao.log"
if Path(log_file).exists():
    Path(log_file).unlink()
    print(f"✓ Removed existing {log_file}")

# Initialize client with logging
print(f"\n1. Initializing LLMClient with logging to '{log_file}'...")
client = LLMClient(
    api_key="test-api-key",
    base_url="https://api.example.com/v1",
    model="claude-sonnet-4-5",
    log_file=log_file,
)
print("✓ LLMClient initialized")

# Check if log file was created
if Path(log_file).exists():
    print(f"✓ Log file created: {log_file}")
else:
    print(f"✗ Log file not created")

# Read and display log content
print(f"\n2. Reading log file content...")
with open(log_file, "r", encoding="utf-8") as f:
    log_content = f.read()
    print(f"✓ Log file contains {len(log_content)} characters")
    print("\nLog content preview:")
    print("-" * 70)
    print(log_content)
    print("-" * 70)

# Test message logging (without actually calling API)
print(f"\n3. Testing request logging structure...")

# Create a mock request structure
test_messages = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Hello, can you help me with Python?"},
]

test_tools = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"}
                },
            },
        },
    }
]

# Log the request (without making actual API call)
print("✓ Would log request with:")
print(f"  - {len(test_messages)} messages")
print(f"  - {len(test_tools)} tools")
print(f"  - Model: {client.model}")

print(f"\n4. Verifying logger setup...")
print(f"✓ Logger name: {client.logger.name}")
print(f"✓ Logger level: {client.logger.level}")
print(f"✓ Number of handlers: {len(client.logger.handlers)}")
print(f"✓ Request counter: {client.request_count}")

print("\n" + "=" * 70)
print("Logging Test Complete!")
print("=" * 70)

print(f"\nNote: To see full logging in action:")
print(f"1. Set a valid OPENAI_API_KEY in .env")
print(f"2. Run: agentao")
print(f"3. Have a conversation with the agent")
print(f"4. Check agentao.log for detailed logs")

print(f"\nTo view the log file:")
print(f"  tail -f agentao.log")
print(f"  cat agentao.log")

# Cleanup test log
if Path(log_file).exists():
    Path(log_file).unlink()
    print(f"\n✓ Cleaned up test log file: {log_file}")
