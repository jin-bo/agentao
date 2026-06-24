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
- `/clear` - Save current session, clear conversation + all memories, and start a new one
  - Resets permission mode to workspace-write
  - `/clear all` - Alias for `/clear` (backward compatible)
- `/new` - Save current session and start a fresh conversation
  - Preserves long-term memories; resets permission mode to workspace-write
- `/status` - Show conversation status
- `/sessions [subcommand]` - Manage saved sessions
  - `/sessions` or `/sessions list` - List saved sessions
  - `/sessions resume <id>` - Resume a saved session by id prefix
  - `/sessions delete <id>` - Delete a saved session
  - `/sessions delete all` - Delete all saved sessions (requires confirmation)
- `/temperature [value|off|on]` - Show/set LLM temperature (0.0-2.0); `off` omits it for models that reject it
- `/mode [read-only|workspace-write|full-access]` - Set permission mode
  - `/mode` - Show current mode
  - `/mode read-only` - Block all write & shell tools
  - `/mode workspace-write` - Allow file writes & safe shell; ask for web (default)
  - `/mode full-access` - Allow all tools without prompting
- `/goal [subcommand|<objective>]` - Long-task goal with a time/turn budget (auto-continuation)
  - `/goal <objective>` - Set a goal and drive it to completion (confirms if one is in progress)
  - `/goal <objective> --for 30m --turns 10` - Cap by active wall-clock and/or continuation turns (first to trip wins; `--turns` is NOT `max_iterations`); `--unbounded` opts out of default caps
  - `/goal` or `/goal show` - Show the current goal (status, objective, used/cap)
  - `/goal budget [--for <d>] [--turns <n>]` - Set/replace caps on the live goal (`--clear` removes caps)
  - `/goal pause` / `/goal resume` - Pause / resume (paused time is not counted; resume also revives a blocked goal)
  - `/goal edit <objective>` - Re-edit the objective (keeps status + caps)
  - `/goal clear` - Remove the goal
- `/plan` - Plan mode workflow (read-only; LLM plans, not executes)
  - `/plan` - Enter plan mode; if already on, shows current saved plan
  - `/plan show` - Display the saved plan file
  - `/plan implement` - Exit plan mode, restore prior permissions, show plan
  - `/plan clear` - Archive and clear the current plan
  - `/plan history` - List recent archived plans
- `/skills [subcommand]` - List and manage skills in the current session
  - `/skills` - List available and active skills
  - `/skills activate <name>` - Activate a skill for this session
  - `/skills deactivate <name>` - Deactivate a skill for this session
  - `/skills disable <name>` - Persistently disable a skill for this project
  - `/skills enable <name>` - Re-enable a disabled skill
  - `/skills reload` - Re-scan skill directories
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
  - `/memory user` / `/memory project` - Show one memory scope
  - `/memory session` - Show recent session summaries
  - `/memory status` - Show memory diagnostics
  - `/memory crystallize` - Extract review candidates from this session
  - `/memory review [approve|reject <id>]` - Review crystallized memory candidates
- `/context` - Show context window token usage and limit
  - `/context limit <n>` - Set max context tokens (default: 200,000)
- `/compact` - Summarize older history now into a compact block (manual compaction)
- `/image <path>` - Attach an image to your next message (vision models)
  - `/image` - List images staged for the next message
  - `/image clear` - Discard all staged images
- `/plugins` - List loaded plugins with diagnostics
- `/permission` - Show active permission rules
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
- `/replay [subcommand]` - Persistent replay recording: toggle, list, inspect, prune, delete
  - `/replay` or `/replay list` - List replay instances (with recording status)
  - `/replay on` / `/replay off` - Toggle recording (writes `.agentao/settings.json`)
  - `/replay show <id>` - Render events in sequence order
  - `/replay tail <id> [n]` - Show last n events (default 20)
  - `/replay prune` - Delete replays beyond `replay.max_instances`
  - `/replay delete <id>` - Delete a single replay file by id (prefix match)
  - `/replay delete all` - Delete all replay files (requires confirmation; skips the active one)
- `/copy` - Copy the last assistant response (Markdown) to clipboard
- `/markdown` - Toggle Markdown rendering ON/OFF (default: ON)
- `/agent [subcommand]` - List or run sub-agents
  - `/agent` or `/agent list` - List available sub-agents
  - `/agent <name> <task>` - Run a sub-agent in the foreground
  - `/agent bg <name> <task>` - Run a sub-agent in the background
  - `/agent status [id]` - Show background agent status or result
  - `/agent dashboard` - Show a live background-agent dashboard
  - `/agent cancel <id>` - Cancel a background agent
  - `/agent delete <id>` - Delete a background-agent record
- `/agents` - Alias for `/agent dashboard`
- `/todos` - Show the current task list
- `/tools [name]` - List registered tools or show one tool's schema
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
