"""Shared CLI help text."""

CLI_HELP_TEXT = """
# Agentao Help

**Available Commands:**
All commands start with `/`:

- `/help` - Show this help message
- `/model` - List available models or switch model
  - `/model` - Show current model and available models
  - `/model <name>` - Switch to specified model
- `/provider` - List or switch API providers
  - `/provider` - Show current provider and available providers
  - `/provider <NAME>` - Switch to provider (reads XXXX_API_KEY, XXXX_BASE_URL, XXXX_MODEL from env)
- `/clear` - End current session (saves it) and start a new one
  - Clears conversation history, all memories, and resets permission mode to workspace-write
  - `/clear all` - Alias for `/clear` (backward compatible)
- `/new` - Alias for `/clear`; start a fresh session
- `/status` - Show conversation status
- `/temperature [value]` - Show or set LLM temperature (0.0-2.0)
- `/mode [read-only|workspace-write|full-access]` - Set permission mode
  - `/mode` - Show current mode
  - `/mode read-only` - Block all write & shell tools
  - `/mode workspace-write` - Allow file writes & safe shell; ask for web (default)
  - `/mode full-access` - Allow all tools without prompting
- `/plan` - Plan mode workflow (read-only; LLM plans, not executes)
  - `/plan` - Enter plan mode; if already on, shows current saved plan
  - `/plan show` - Display the saved plan file
  - `/plan implement` - Exit plan mode, restore prior permissions, show plan
  - `/plan clear` - Archive and clear the current plan
  - `/plan history` - List recent archived plans
- `/skills` - List available skills
- `/crystallize [subcommand]` - Draft a reusable skill from the current session
  - `/crystallize` or `/crystallize suggest` - Analyze the session and generate a skill draft
  - `/crystallize feedback <text>` - Add feedback and rewrite the current draft
  - `/crystallize revise` - Interactively enter feedback and rewrite the draft
  - `/crystallize refine` - Improve the current draft with skill-creator guidance
  - `/crystallize status` - Show current pending draft status
  - `/crystallize clear` - Clear the current pending draft
  - `/crystallize create [name]` - Save the draft into skills/ and reload
  - Recommended flow: `suggest` -> `feedback <text>` (repeatable) -> `refine` -> `create [name]`
- `/memory [subcommand] [arg]` - Manage saved memories
  - `/memory` or `/memory list` - Show all saved memories (with tag summary)
  - `/memory search <query>` - Search memories by keyword (key, value, tags)
  - `/memory tag <tag>` - Filter memories by tag
  - `/memory delete <key>` - Delete a specific memory
  - `/memory clear` - Clear all memories (requires confirmation)
- `/context` - Show context window token usage and limit
  - `/context limit <n>` - Set max context tokens (default: 200,000)
- `/plugins` - List loaded plugins with diagnostics
- `/mcp [subcommand]` - Manage MCP servers
  - `/mcp` or `/mcp list` - List MCP servers with status and tools
  - `/mcp add <name> <command|url>` - Add an MCP server
  - `/mcp remove <name>` - Remove an MCP server
- `/sandbox [subcommand]` - Control macOS sandbox-exec for shell commands (macOS only)
  - `/sandbox` or `/sandbox status` - Show current sandbox state
  - `/sandbox on` / `/sandbox off` - Toggle for this session
  - `/sandbox profile <name>` - Switch to a built-in or user profile
  - `/sandbox profiles` - List available profiles
- `/acp [subcommand]` - Manage ACP servers
  - `/acp` or `/acp list` - List ACP servers with state
  - `/acp start/stop/restart <name>` - Control server lifecycle
  - `/acp send <name> <message>` - Send a prompt (permission/input handled inline)
  - `/acp cancel <name>` - Cancel active turn
  - `/acp status <name>` - Detailed server status
  - `/acp logs <name> [lines]` - View server stderr
- `/replay [subcommand]` - Persistent replay recording: toggle, list, inspect, prune
  - `/replay` or `/replay list` - List replay instances (with recording status)
  - `/replay on` / `/replay off` - Toggle recording (writes `.agentao/settings.json`)
  - `/replay show <id>` - Render events in sequence order
  - `/replay tail <id> [n]` - Show last n events (default 20)
  - `/replay prune` - Delete replays beyond `replay.max_instances`
- `/copy` - Copy the last assistant response (Markdown) to clipboard
- `/markdown` - Toggle Markdown rendering ON/OFF (default: ON)
- `/exit` or `/quit` - Exit the program

**Available Tools:**
The agent has access to the following tools:
- `read_file` - Read file contents with line numbers (supports offset/limit)
- `write_file` - Write/append content to files
- `replace` - Edit files by replacing text (supports replace_all)
- `list_directory` - List directory contents
- `glob` - Find files matching patterns
- `search_file_content` - Search text in files
- `run_shell_command` - Execute shell commands
- `web_fetch` - Fetch web content
- `web_search` - Search the web
- `save_memory` - Save important information
- `activate_skill` - Activate skills
- `ask_user` - Ask the user for clarification
- `todo_write` - Track current task state

**Skills:**
Type `/skills` to see available skills, or ask the agent to activate a specific skill.

**Examples:**
- "Read the file main.py"
- "Search for function definitions in Python files"
- "Fetch content from https://example.com"
- "Activate the pdf skill to help me work with PDF files"

**Note:** Regular messages (without `/`) are sent to the AI agent.
"""
