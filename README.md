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
- **Multi-line paste support** - paste multi-line text and the entire content enters the input buffer as one unit (prompt_toolkit native; no timing hacks); press Alt+Enter to insert a manual newline, Enter to submit
- **Slash command Tab completion** - type `/` and press Tab for an autocomplete menu of all `/` commands
- **Reliability principles** - system prompt enforces read-before-assert, discrepancy reporting, and fact/inference distinction on every turn
- **Operational guidelines** - system prompt includes tone & style rules, shell command efficiency patterns (temp file redirect), tool usage rules (parallelism, non-interactive flags, respect cancellations), and security guidelines (explain-before-act)

### 🛠️ Comprehensive Tools

**File Operations:**
- `read_file` - Read file contents
- `write_file` - Write content to files (requires confirmation)
- `replace` - Edit files by replacing text
- `list_directory` - List directory contents

**Search & Discovery:**
- `glob` - Find files matching patterns (supports `**` for recursive search)
- `search_file_content` - Search text in files with regex support

**Shell & Web:**
- `run_shell_command` - Execute shell commands (requires confirmation)
- `web_fetch` - Fetch and extract content from URLs (requires confirmation); uses [Crawl4AI](https://github.com/unclecode/crawl4ai) for clean Markdown output if installed, otherwise falls back to plain text extraction
- `google_web_search` - Search the web via DuckDuckGo (requires confirmation)

**Agents & Skills:**
- `agent_codebase_investigator` - Delegate read-only codebase exploration to a sub-agent
- `agent_generalist` - Delegate complex multi-step tasks to a sub-agent
- `activate_skill` - Activate specialized skills for specific tasks

**MCP Tools:**
- Dynamically discovered from connected MCP servers
- Named `mcp_{server}_{tool}` (e.g. `mcp_filesystem_read_file`)
- Require confirmation unless server is trusted

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

### 🤖 SubAgent System

ChatAgent can delegate tasks to independent sub-agents, each running its own LLM loop with scoped tools and turn limits. Inspired by [Gemini CLI](https://github.com/google-gemini/gemini-cli)'s "agent as tool" pattern.

**Built-in agents:**
- `codebase-investigator` — read-only codebase exploration (find files, search patterns, analyze structure)
- `generalist` — general-purpose agent with access to all tools for complex multi-step tasks

**Two trigger paths:**
1. **LLM-driven** — the parent LLM decides to delegate via `agent_codebase_investigator` / `agent_generalist` tools
2. **User-driven** — use `/agent <name> <task>` to call an agent directly

**Custom agents:** create `.chatagent/agents/my-agent.md` with YAML frontmatter (`name`, `description`, `tools`, `max_turns`) — auto-discovered at startup.

### 🔌 MCP (Model Context Protocol) Support

Connect to external MCP tool servers to dynamically extend the agent's capabilities:

- **Stdio transport** — spawn a local subprocess (e.g. `npx @modelcontextprotocol/server-filesystem`)
- **SSE transport** — connect to remote HTTP/SSE endpoints
- **Auto-discovery** — tools are discovered on startup and registered as `mcp_{server}_{tool}`
- **Confirmation** — MCP tools require user confirmation unless the server is marked `"trust": true`
- **Env var expansion** — `$VAR` and `${VAR}` syntax in config values
- **Two-level config** — project `.chatagent/mcp.json` overrides global `~/.chatagent/mcp.json`

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

### MCP Server Configuration

Create `.chatagent/mcp.json` in your project (or `~/.chatagent/mcp.json` for global servers):

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/dir"],
      "trust": true
    },
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": { "GITHUB_TOKEN": "$GITHUB_TOKEN" }
    },
    "remote-api": {
      "url": "https://api.example.com/sse",
      "headers": { "Authorization": "Bearer $API_KEY" },
      "timeout": 30
    }
  }
}
```

| Field | Description |
|-------|-------------|
| `command` | Executable for stdio transport |
| `args` | Command-line arguments |
| `env` | Extra environment variables (supports `$VAR` / `${VAR}` expansion) |
| `cwd` | Working directory for subprocess |
| `url` | SSE endpoint URL |
| `headers` | HTTP headers for SSE transport |
| `timeout` | Connection timeout in seconds (default: 60) |
| `trust` | Skip confirmation for this server's tools (default: false) |

MCP servers connect automatically on startup. Use `/mcp list` to check status.

### Using with Different Providers

ChatAgent supports switching between providers at runtime with `/provider`. Add credentials for each provider to your `.env` (or `~/.env`) using the naming convention `<NAME>_API_KEY`, `<NAME>_BASE_URL`, and `<NAME>_MODEL`:

```env
# OpenAI (default)
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4-turbo-preview

# Gemini
GEMINI_API_KEY=...
GEMINI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
GEMINI_MODEL=gemini-2.0-flash

# DeepSeek
DEEPSEEK_API_KEY=...
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-chat
```

Then switch at runtime:
```
/provider           # list detected providers
/provider GEMINI    # switch to Gemini
/model              # see available models on the new endpoint
```

The `/provider` command detects any `*_API_KEY` entry already loaded into the environment, so it works with `~/.env` and system environment variables — not just a local `.env` file.

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

### Non-Interactive (Print) Mode

Use `-p` / `--print` to send a single prompt, get a plain-text response on stdout, and exit — no UI, no confirmations. Useful for scripting and pipes.

```bash
# Basic usage
chatagent -p "What is 2+2?"

# Read from stdin
echo "Summarize this: hello world" | chatagent -p

# Combine -p argument with stdin (both are joined and sent as one prompt)
echo "Some context" | chatagent -p "Summarize the stdin"

# Pipe output to a file
chatagent -p "List 3 prime numbers" > output.txt

# Use in a pipeline
chatagent -p "Translate to French: Good morning" | pbcopy
```

In print mode all tools are auto-confirmed (no interactive prompts). The exit code is `0` on success and `1` on error.

### Commands

All commands start with `/`. Type `/` and press **Tab** for autocomplete.

| Command | Description |
|---------|-------------|
| `/help` | Show help message |
| `/clear` | Clear conversation history and reset confirmation mode |
| `/status` | Show message count, model, active skills, memory count, context usage |
| `/model` | Fetch and list available models from the configured API endpoint |
| `/model <name>` | Switch to specified model (e.g., `/model gpt-4`) |
| `/provider` | List available providers (detected from `*_API_KEY` env vars) |
| `/provider <NAME>` | Switch to a different provider (e.g., `/provider GEMINI`) |
| `/skills` | List available and active skills |
| `/memory` | List all saved memories with tag summary |
| `/memory search <query>` | Search memories (searches keys, tags, and values) |
| `/memory tag <tag>` | Filter memories by tag |
| `/memory delete <key>` | Delete a specific memory |
| `/memory clear` | Clear all memories (with confirmation) |
| `/mcp` | List MCP servers with status and tool counts |
| `/mcp add <name> <cmd\|url>` | Add an MCP server to project config |
| `/mcp remove <name>` | Remove an MCP server from project config |
| `/context` | Show current context window usage (tokens and %) |
| `/context limit <n>` | Set context window limit (e.g., `/context limit 100000`) |
| `/agent` | List available sub-agents |
| `/agent <name> <task>` | Run a sub-agent directly (e.g., `/agent codebase-investigator find all API endpoints`) |
| `/confirm` | Show current tool confirmation mode |
| `/confirm all` | Enable allow-all mode (tools execute without prompting) |
| `/confirm prompt` | Restore prompt mode (ask before each tool) |
| `/reset-confirm` | Reset "allow all tools" mode — legacy alias for `/confirm prompt` |
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
- `mcp_*` - All MCP server tools (unless server has `"trust": true`)

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

**Using agents:**
```
You: Analyze the project structure and find all API endpoints
     (LLM may auto-delegate to codebase-investigator)
/agent codebase-investigator find all TODO comments in this project
/agent generalist refactor the logging module to use structured output
```

**Using MCP tools:**
```
/mcp list                   (check connected servers and tools)
/mcp add fs npx -y @modelcontextprotocol/server-filesystem /tmp
You: List all files in the project     (LLM may use MCP filesystem tools)
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
    ├── agents/
    │   ├── __init__.py      # SubAgent exports
    │   ├── manager.py       # AgentManager — loads definitions, creates wrappers
    │   ├── tools.py         # TaskComplete, CompleteTaskTool, AgentToolWrapper
    │   └── definitions/     # Built-in agent definitions (.md with YAML frontmatter)
    │       ├── codebase-investigator.md
    │       └── generalist.md
    ├── mcp/
    │   ├── __init__.py      # MCP package exports
    │   ├── config.py        # Config loading + env var expansion
    │   ├── client.py        # McpClient + McpClientManager
    │   └── tool.py          # McpTool wrapper for Tool interface
    ├── tools/
    │   ├── base.py          # Tool base class + registry
    │   ├── file_ops.py      # Read, write, edit, list
    │   ├── search.py        # Glob, grep
    │   ├── shell.py         # Shell execution
    │   ├── web.py           # Fetch, search
    │   ├── memory.py        # Persistent memory (6 tools)
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

### Adding an Agent

Create a Markdown file with YAML frontmatter. Built-in agents go in `chatagent/agents/definitions/`, project-level agents go in `.chatagent/agents/`.

```yaml
---
name: my-agent
description: "When to use this agent (shown to LLM for delegation decisions)"
tools:                    # optional — omit for all tools
  - read_file
  - search_file_content
  - run_shell_command
max_turns: 10             # optional, default 15
---
You are a specialized agent. Instructions for the sub-agent go here.
When finished, call complete_task to return your result.
```

Restart ChatAgent — agents are auto-discovered and registered as `agent_my_agent` tools.

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

**Provider List Empty:** `/provider` scans the environment for `*_API_KEY` entries. Make sure your credentials are in `~/.env` or exported into the shell — a local `.env` in the project directory is not required.

**API Key Issues:** Verify `.env` exists and contains a valid key with correct permissions.

**Context Too Long Errors:** ChatAgent handles these automatically with three-tier recovery (compress → minimal history → error). Common causes: very large tool results (e.g. reading huge files) or extremely long conversations. If errors persist, lower the limit with `/context limit <n>` or `CHATAGENT_CONTEXT_TOKENS`.

**Memory Recall Not Working:** Check that memories exist (`/memory`). Recall requires at least one memory saved. The LLM judges relevance — unrelated memories won't be recalled.

**MCP Server Not Connecting:** Run `/mcp list` to see status and error messages. Verify the command exists and is executable, or that the SSE URL is reachable. Check `chatagent.log` for detailed connection errors.

**Tool Execution Errors:** Check file permissions, path correctness, and that shell commands are valid for your OS.

---

## License

This project is open source. Feel free to use and modify as needed.

## Acknowledgments

- Built with [OpenAI Python SDK](https://github.com/openai/openai-python)
- CLI interface powered by [Rich](https://github.com/Textualize/rich)
- Input handling powered by [prompt_toolkit](https://github.com/prompt-toolkit/python-prompt-toolkit)
- Optional enhanced web fetching via [Crawl4AI](https://github.com/unclecode/crawl4ai)
- MCP support via [Model Context Protocol SDK](https://github.com/modelcontextprotocol/python-sdk)
- MCP architecture inspired by [Gemini CLI](https://github.com/google-gemini/gemini-cli)
- Inspired by [Claude Code](https://github.com/anthropics/claude-code)
