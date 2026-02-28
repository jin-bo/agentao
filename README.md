# ChatAgent

A powerful CLI chat agent with tools and skills support. Built with Python and designed to work with any OpenAI-compatible API.

## Features

### 🤖 Intelligent Agent
- Multi-turn conversations with context
- Function calling for tool usage
- Smart tool selection and execution
- **Context window management** - automatic sliding-window compression with LLM summarization
- **Dynamic memory recall** - Agentic RAG (no vector DB) recalls relevant memories per message
- **Live thinking display** - shows LLM reasoning and tool calls in real time
- **Complete logging** of all LLM interactions to `chatagent.log`
- **Auto-loading of project instructions** from `CHATAGENT.md` at startup
- **Tool confirmation** - user confirmation required for Shell, Web, and destructive Memory tools
- **Current date context** - system prompt includes current date and time
- **Multi-line paste support** - paste multi-line text and it's sent as one complete message
- **Reliability principles** - system prompt enforces read-before-assert, discrepancy reporting, and fact/inference distinction on every turn

### 🛠️ Comprehensive Tools

**File Operations:**
- `read_file` - Read file contents
- `write_file` - Write content to files (requires confirmation)
- `replace` - Edit files by replacing text
- `list_directory` - List directory contents

**Search & Discovery:**
- `glob` - Find files matching patterns (supports `**` for recursive search)
- `search_file_content` - Search text in files with regex support
- `codebase_investigator` - Analyze project structure

**Shell & Web:**
- `run_shell_command` - Execute shell commands (requires confirmation)
- `web_fetch` - Fetch and extract content from URLs (requires confirmation)
- `google_web_search` - Search the web via DuckDuckGo (requires confirmation)

**Special Features:**
- `activate_skill` - Activate specialized skills for specific tasks
- `cli_help` - Get help with CLI usage

### 🧠 Context Window Management

ChatAgent automatically manages long conversations to stay within LLM context limits:

- **Token estimation** - tracks approximate token usage (characters ÷ 4)
- **Sliding window compression** - when context exceeds 65% of the limit, early messages are summarized by the LLM and replaced with a compact `[Conversation Summary]` block; the split point always aligns to a `user` turn boundary so tool call sequences are never split mid-flight
- **Tool result truncation** - tool outputs larger than 80K characters (~20K tokens) are truncated before being added to messages, preventing a single large response (e.g. reading a big file) from consuming the entire context window
- **Auto-save summaries** - compression summaries are saved to memory with tag `conversation_summary` for future reference
- **Graceful degradation** - if compression fails, the original messages are preserved unchanged
- **Three-tier overflow recovery** - if the API returns a context-too-long error: (1) force-compress and retry; (2) if still too long, keep only the last 2 messages and retry; (3) only surfaces an error to the user if all three tiers fail

Default context limit is 200K tokens. Override with `CHATAGENT_CONTEXT_TOKENS` environment variable.

### 💾 Memory Recall (Agentic RAG)

Before each response, ChatAgent automatically identifies and injects memories relevant to your question — no vector database required:

1. All saved memories are listed for the LLM
2. The LLM returns a JSON array of relevant memory keys
3. You are shown the recalled memories and asked whether to inject them (single-key confirmation)
4. Confirmed memories are added to the system prompt for that turn

This means important context you've saved (preferences, facts, project details) surfaces automatically when relevant, without you having to ask.

### 💡 Live Thinking Display

The spinner updates in real time to show what the agent is doing:

- **"Thinking..."** - waiting for LLM response
- **"⚙ tool_name (arg)"** - executing a specific tool
- **Structured reasoning** - before each set of tool calls the agent prints (in blue) its **Action**, **Expectation**, and **If wrong** plan — a falsifiable prediction you can verify against the actual tool result

### 🎯 Dynamic Skills System

Skills are auto-discovered from the `skills/` directory. Each subdirectory contains a `SKILL.md` file with YAML frontmatter. Skills are listed in the system prompt and can be activated with the `activate_skill` tool.

Add new skills by creating a directory with a `SKILL.md` file — no code changes needed.

---

## Installation

### Prerequisites
- Python 3.12 or higher
- [uv](https://github.com/astral-sh/uv) (recommended) or pip
- An OpenAI API key or access to an OpenAI-compatible API

### Quick Start with uv (Recommended)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then set up ChatAgent:

```bash
cd chatagent
uv sync
cp .env.example .env
# Edit .env and add your API key
```

### Alternative: pip

```bash
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -e .
cp .env.example .env
```

---

## Configuration

Edit `.env` with your settings:

```env
# Required: Your API key
OPENAI_API_KEY=your-api-key-here

# Optional: Base URL for OpenAI-compatible APIs
# OPENAI_BASE_URL=https://api.openai.com/v1

# Optional: Model name
# OPENAI_MODEL=gpt-4-turbo-preview

# Optional: Context window limit in tokens (default: 200000)
# CHATAGENT_CONTEXT_TOKENS=200000
```

### Using with Different Providers

**OpenAI:**
```env
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4-turbo-preview
```

**Anthropic (via compatible endpoint):**
```env
OPENAI_API_KEY=your-key
OPENAI_BASE_URL=https://...
OPENAI_MODEL=claude-sonnet-4-5
```

**DeepSeek / other OpenAI-compatible APIs:**
```env
OPENAI_API_KEY=your-api-key
OPENAI_BASE_URL=https://api.your-provider.com/v1
OPENAI_MODEL=your-model-name
```

---

## Usage

### Starting the Agent

```bash
# Quick start
uv run chatagent

# Or via Python
uv run python main.py

# Or via convenience script
./run.sh
```

### Commands

All commands start with `/`:

| Command | Description |
|---------|-------------|
| `/help` | Show help message |
| `/clear` | Clear conversation history and reset confirmation mode |
| `/status` | Show message count, model, active skills, memory count, context usage |
| `/model` | Fetch and list available models from the configured API endpoint |
| `/model <name>` | Switch to specified model (e.g., `/model gpt-4`) |
| `/skills` | List available and active skills |
| `/memory` | List all saved memories with tag summary |
| `/memory search <query>` | Search memories (searches keys, tags, and values) |
| `/memory tag <tag>` | Filter memories by tag |
| `/memory delete <key>` | Delete a specific memory |
| `/memory clear` | Clear all memories (with confirmation) |
| `/context` | Show current context window usage (tokens and %) |
| `/context limit <n>` | Set context window limit (e.g., `/context limit 100000`) |
| `/reset-confirm` | Reset "allow all tools" mode (keeps conversation history) |
| `/exit` or `/quit` | Exit the program |

### Tool Confirmation (Safety Feature)

ChatAgent requires user confirmation before executing potentially dangerous tools:

**Tools requiring confirmation:**
- `run_shell_command` - Shell command execution
- `web_fetch` - Fetching web content
- `google_web_search` - Web search
- `write_file` - Writing/overwriting files
- `delete_memory` - Deleting a saved memory
- `clear_all_memories` - Clearing all memories

**How it works:**

1. Execution pauses and you see a menu with tool details
2. Press a single key (no Enter needed):
   - **1** - Yes, execute this tool once
   - **2** - Yes to all, allow all tools for this session
   - **3** - No, cancel execution
   - **Esc** - Cancel execution

### Memory Recall Confirmation

When relevant memories are recalled before a response:

1. A list of recalled memories is shown (key: value format)
2. Press a single key:
   - **1** - Inject these memories into the system prompt
   - **2** or **Esc** - Skip (memories are not injected)

When "allow all tools" is active, memory recall is auto-confirmed.

### Example Interactions

**Reading and analyzing files:**
```
You: Read the file main.py and explain what it does
You: Search for all Python files in this directory
You: Find all TODO comments in the codebase
```

**Working with code:**
```
You: Create a new Python file called utils.py with helper functions
You: Replace the old function in utils.py with an improved version
You: Run the tests using pytest
```

**Web and search:**
```
You: Fetch the content from https://example.com
You: Search for Python best practices
```

**Memory:**
```
You: Remember that I prefer tabs over spaces for indentation
You: Save this API endpoint URL for future use
You: What do you remember about my preferences?
```

**Context management:**
```
You: /context                     (check current token usage)
You: /context limit 100000        (set a lower context limit)
You: /status                      (see memory count and context %)
```

**Using skills:**
```
You: Activate the pdf skill to help me merge PDF files
You: Use the xlsx skill to analyze this spreadsheet
```

---

## Project Instructions (CHATAGENT.md)

ChatAgent automatically loads project-specific instructions from `CHATAGENT.md` if it exists in the current working directory. This file is injected into the system prompt at startup, allowing you to define:

- Code style and conventions
- Project structure and patterns
- Development workflows and testing approaches
- Common commands and best practices

If the file doesn't exist, the agent works normally with its default instructions.

Project instructions are injected at the top of the system prompt, before built-in agent instructions — making them the highest-priority guidance for the LLM. A good `CHATAGENT.md` includes code-style conventions, testing commands, and reliability rules such as requiring the agent to cite file and line number when making factual claims about the codebase.

---

## Project Structure

```
chatagent/
├── main.py                  # Entry point
├── pyproject.toml           # Project configuration
├── .env                     # Configuration (create from .env.example)
├── .env.example             # Configuration template
├── CHATAGENT.md             # Project-specific agent instructions
├── README.md                # This file
├── tests/                   # Test files
│   ├── test_context_manager.py      # ContextManager tests (22 tests, mock LLM)
│   ├── test_memory_management.py    # Memory tool tests
│   ├── test_reliability_prompt.py   # Reliability principles in system prompt (6 tests)
│   └── test_*.py                    # Other feature tests
├── docs/                    # Documentation
│   ├── features/            # Feature documentation
│   └── updates/             # Update logs
└── chatagent/
    ├── agent.py             # Core orchestration
    ├── cli.py               # CLI interface (Rich)
    ├── context_manager.py   # Context window management + Agentic RAG
    ├── llm/
    │   └── client.py        # OpenAI-compatible LLM client
    ├── tools/
    │   ├── base.py          # Tool base class + registry
    │   ├── file_ops.py      # Read, write, edit, list
    │   ├── search.py        # Glob, grep
    │   ├── shell.py         # Shell execution
    │   ├── web.py           # Fetch, search
    │   ├── memory.py        # Persistent memory (6 tools)
    │   ├── agents.py        # Helper agents
    │   └── skill.py         # Skill activation
    └── skills/
        └── manager.py       # Skill loading and management
```

---

## Testing

```bash
# Run all tests
uv run python -m pytest tests/ -v

# Run specific test files
uv run python -m pytest tests/test_context_manager.py -v
uv run python -m pytest tests/test_memory_management.py -v
```

Tests use `unittest.mock.Mock` for the LLM client — no real API calls required.

---

## Logging

All LLM interactions are logged to `chatagent.log`:

```bash
tail -f chatagent.log    # Real-time monitoring
grep "ERROR" chatagent.log
```

Logged data includes: full message content, tool calls with arguments, tool results, token usage, and timestamps.

---

## Development

### Adding a Tool

1. Create a tool class in `chatagent/tools/`:

```python
from .base import Tool

class MyTool(Tool):
    @property
    def name(self) -> str:
        return "my_tool"

    @property
    def description(self) -> str:
        return "Description for LLM"

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "param": {"type": "string", "description": "..."}
            },
            "required": ["param"],
        }

    @property
    def requires_confirmation(self) -> bool:
        return False  # Set True for dangerous operations

    def execute(self, param: str) -> str:
        return f"Result: {param}"
```

2. Register in `agent.py::_register_tools()`:

```python
tools_to_register.append(MyTool())
```

### Adding a Skill

1. Create `skills/my-skill/SKILL.md`:

```yaml
---
name: my-skill
description: Use when... (trigger conditions for LLM)
---

# My Skill

Documentation here...
```

2. Restart ChatAgent — skills are auto-discovered.

---

## Troubleshooting

**Model List Not Loading:** `/model` queries the live API endpoint. If it fails (invalid key, unreachable endpoint, no `models` endpoint), a clear error is shown. Verify your `OPENAI_API_KEY` and `OPENAI_BASE_URL` settings.

**API Key Issues:** Verify `.env` exists and contains a valid key with correct permissions.

**Context Too Long Errors:** ChatAgent handles these automatically with three-tier recovery (compress → minimal history → error). Common causes: very large tool results (e.g. reading huge files) or extremely long conversations. If errors persist, lower the limit with `/context limit <n>` or `CHATAGENT_CONTEXT_TOKENS`.

**Memory Recall Not Working:** Check that memories exist (`/memory`). Recall requires at least one memory saved. The LLM judges relevance — unrelated memories won't be recalled.

**Tool Execution Errors:** Check file permissions, path correctness, and that shell commands are valid for your OS.

---

## License

This project is open source. Feel free to use and modify as needed.

## Acknowledgments

- Built with [OpenAI Python SDK](https://github.com/openai/openai-python)
- CLI interface powered by [Rich](https://github.com/Textualize/rich)
- Inspired by [Claude Code](https://github.com/anthropics/claude-code)
