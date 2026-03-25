"""CLI interface for Agentao."""

import warnings
warnings.filterwarnings("ignore", message="urllib3.*or chardet.*doesn't match")

import os
import sys
from typing import Optional

import readchar
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyBindings
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.theme import Theme
from dotenv import load_dotenv

from .agent import Agentao

# Custom theme for the CLI
custom_theme = Theme({
    "info": "cyan",
    "warning": "yellow",
    "error": "bold red",
    "success": "bold green",
})

console = Console(theme=custom_theme)

# Tool argument keys to display in the thinking step (priority order)
_TOOL_SUMMARY_KEYS = ("path", "file_path", "query", "description", "command", "url", "key", "pattern", "tag")


def _tool_args_summary(tool_name: str, args: dict) -> str:
    """Build a short human-readable summary of tool arguments for display."""
    if not args:
        return ""
    # Try priority keys first
    for key in _TOOL_SUMMARY_KEYS:
        if key in args:
            val = str(args[key])
            if len(val) > 50:
                val = val[:47] + "..."
            return f"({val})"
    # Fall back to first value
    first_val = str(next(iter(args.values())))
    if len(first_val) > 50:
        first_val = first_val[:47] + "..."
    return f"({first_val})"


_SLASH_COMMANDS = [
    '/agent', '/clear', '/confirm', '/confirm all', '/confirm prompt',
    '/context', '/context limit', '/exit', '/help',
    '/mcp', '/mcp add', '/mcp list', '/mcp remove',
    '/memory', '/memory clear', '/memory delete', '/memory list',
    '/memory search', '/memory tag', '/model', '/permission', '/provider', '/quit',
    '/reset-confirm', '/sessions', '/sessions delete', '/sessions delete all', '/sessions list', '/sessions resume',
    '/skills', '/skills disable', '/skills enable', '/skills reload', '/status',
]


class _SlashCompleter(Completer):
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith('/'):
            return
        for cmd in _SLASH_COMMANDS:
            if cmd.startswith(text):
                yield Completion(cmd, start_position=-len(text))


class AgentaoCLI:
    """CLI interface for Agentao."""

    def __init__(self):
        """Initialize CLI."""
        load_dotenv()

        # Track session-wide confirmation preferences
        self.allow_all_tools = False  # "Yes to all" mode
        self.current_status = None  # Track active status context
        self._streaming_output = False  # Track if we're in streaming shell output mode
        self._llm_streamed = False  # Track if LLM response text was already streamed
        provider = os.getenv("LLM_PROVIDER", "OPENAI").strip().upper()
        self.current_provider = provider  # Track active provider name

        context_limit = int(os.getenv("AGENTAO_CONTEXT_TOKENS", "200000"))

        from .permissions import PermissionEngine
        self.permission_engine = PermissionEngine()

        self.agent = Agentao(
            api_key=os.getenv(f"{provider}_API_KEY"),
            base_url=os.getenv(f"{provider}_BASE_URL"),
            model=os.getenv(f"{provider}_MODEL"),
            confirmation_callback=self.confirm_tool_execution,
            max_context_tokens=context_limit,
            step_callback=self.on_tool_step,
            thinking_callback=self.on_llm_thinking,
            ask_user_callback=self.ask_user,
            output_callback=self.on_tool_output,
            tool_complete_callback=self.on_tool_complete,
            llm_text_callback=self.on_llm_text,
            permission_engine=self.permission_engine,
        )

        # prompt_toolkit session: multiline=True captures full paste; Enter submits
        _kb = KeyBindings()

        @_kb.add('enter')
        def _pt_submit(event):
            event.current_buffer.validate_and_handle()

        @_kb.add('escape', 'enter')  # Meta/Alt+Enter → insert newline
        def _pt_newline(event):
            event.current_buffer.insert_text('\n')

        self._prompt_session = PromptSession(
            key_bindings=_kb,
            multiline=True,
            prompt_continuation='',
            completer=_SlashCompleter(),
        )

    def confirm_tool_execution(self, tool_name: str, tool_description: str, tool_args: dict) -> bool:
        """Prompt user to confirm tool execution with menu options.

        Args:
            tool_name: Name of the tool to execute
            tool_description: Description of the tool
            tool_args: Arguments to pass to the tool

        Returns:
            True if user confirms, False otherwise
        """
        # If "allow all" mode is enabled, automatically approve
        if self.allow_all_tools:
            console.print(f"[dim]✓ Auto-approved: {tool_name} (allow all mode)[/dim]")
            return True

        # Pause the "Thinking..." spinner during user confirmation
        if self.current_status:
            self.current_status.stop()

        try:
            # Display tool information
            console.print(f"\n[yellow]⚠️  Tool Confirmation Required[/yellow]")
            console.print(f"[info]Tool:[/info] [cyan]{tool_name}[/cyan]")
            console.print(f"[info]Arguments:[/info]")

            # Format arguments nicely
            for key, value in tool_args.items():
                console.print(f"  • {key}: {value}")

            # Display menu with better formatting
            console.print("\n[bold]Choose an option:[/bold]")
            console.print(" [green]1[/green]. Yes")
            console.print(" [green]2[/green]. Yes, allow all tools during this session")
            console.print(" [red]3[/red]. No")
            console.print("\n[dim]Press 1, 2, or 3 (single key, no Enter needed) · Esc to cancel[/dim]", end=" ")

            # Get single-key input using readchar
            while True:
                try:
                    key = readchar.readkey()

                    # Handle number keys
                    if key == "1":
                        console.print("\n[green]✓ Executing tool[/green]")
                        return True
                    elif key == "2":
                        self.allow_all_tools = True
                        console.print("\n[green]✓ Executing tool (allow all mode enabled for this session)[/green]")
                        return True
                    elif key == "3":
                        console.print("\n[red]✗ Cancelled[/red]")
                        return False
                    # Handle Esc key
                    elif key == readchar.key.ESC:
                        console.print("\n[red]✗ Cancelled[/red]")
                        return False
                    # Handle Ctrl+C
                    elif key == readchar.key.CTRL_C:
                        console.print("\n[red]✗ Cancelled[/red]")
                        return False
                    # Ignore other keys
                    else:
                        continue

                except KeyboardInterrupt:
                    console.print("\n[red]✗ Cancelled[/red]")
                    return False
                except Exception as e:
                    # Fallback to cancelled on any error
                    console.print(f"\n[red]✗ Cancelled (error: {e})[/red]")
                    return False

        finally:
            # Resume the "Thinking..." spinner after user makes a choice
            if self.current_status:
                self.current_status.start()

    def on_llm_thinking(self, reasoning: str) -> None:
        """Display LLM reasoning text produced before tool calls.

        Args:
            reasoning: The LLM's reasoning / thinking text
        """
        if not reasoning.strip():
            return

        # Pause the spinner while displaying reasoning
        if self.current_status:
            self.current_status.stop()

        console.rule("[dim]Thinking[/dim]", style="dim blue")
        for line in reasoning.strip().splitlines():
            console.print(f"  [dim italic]{line}[/dim italic]")
        console.print()

        # Resume spinner
        if self.current_status:
            self.current_status.start()

    def on_tool_step(self, tool_name: Optional[str], tool_args: dict) -> None:
        """Display tool call step.

        Called with tool_name=None to reset back to "Thinking..." display.

        Args:
            tool_name: Name of the tool being called, or None to reset
            tool_args: Arguments passed to the tool
        """
        if tool_name is None:
            # Reset to thinking state
            if self.current_status:
                self.current_status.update("[bold yellow]Thinking...[/bold yellow]")
            return

        # Stop spinner and print tool header as a visible line
        if self.current_status:
            self.current_status.stop()

        summary = _tool_args_summary(tool_name, tool_args)
        if summary:
            console.print(f"[bold yellow]⚙ {tool_name}[/bold yellow] [dim]{summary}[/dim]")
        else:
            console.print(f"[bold yellow]⚙ {tool_name}[/bold yellow]")

        # Restart spinner
        if self.current_status:
            self.current_status.start()

    def on_tool_output(self, tool_name: str, chunk: str) -> None:
        """Display streaming tool output in real-time.

        Args:
            tool_name: Name of the tool producing output
            chunk: Text chunk from tool stdout
        """
        if not self._streaming_output:
            self._streaming_output = True
            # Stop spinner before printing output
            if self.current_status:
                self.current_status.stop()
            console.rule("[dim]output[/dim]", style="dim")

        # Print raw chunk without Rich markup processing
        console.print(chunk, end="", markup=False, highlight=False)

    def on_tool_complete(self, tool_name: str) -> None:
        """Called after a tool finishes execution.

        Args:
            tool_name: Name of the completed tool
        """
        if self._streaming_output:
            # Ensure output ends with a newline before the closing rule
            console.print()
            console.rule(style="dim")
            self._streaming_output = False

        # Restart spinner for next tool or LLM call
        if self.current_status:
            self.current_status.start()

    def on_llm_text(self, chunk: str) -> None:
        """Display a streamed LLM text chunk in real-time.

        Called for each text delta from the LLM during the final response.
        Stops the spinner on first chunk and prints chunks as plain text.

        Args:
            chunk: Text chunk from LLM stream
        """
        if not chunk:
            return

        if not self._llm_streamed:
            self._llm_streamed = True
            # Stop spinner before printing streamed text
            if self.current_status:
                self.current_status.stop()

        console.print(chunk, end="", markup=False, highlight=False)

    def ask_user(self, question: str) -> str:
        """Pause spinner, display question, read free-form user response, resume spinner.

        Args:
            question: The question from the LLM to show the user

        Returns:
            User's text response, or fallback string on interrupt/EOF
        """
        if self.current_status:
            self.current_status.stop()
        try:
            console.print(f"\n[bold yellow]🤔 Agent Question[/bold yellow]")
            console.print(f"[yellow]{question}[/yellow]")
            response = console.input("[bold yellow]▶ [/bold yellow]").strip()
            return response if response else "(no response)"
        except (EOFError, KeyboardInterrupt):
            return "(user interrupted)"
        finally:
            if self.current_status:
                self.current_status.start()

    def print_welcome(self):
        """Print welcome message."""
        current_model = self.agent.get_current_model()
        welcome = f"""
# Agentao

A CLI chat agent with tools and skills support.

**Current Model:** `{current_model}`

**Commands:**
- `/help` - Show help message
- `/model` - List or switch models
- `/provider` - List or switch providers
- `/clear` - Clear conversation and reset confirmation
- `/status` - Show conversation status (memory count, context usage)
- `/skills` - List available skills
- `/memory [subcommand]` - Manage saved memories
- `/mcp` - List MCP servers and tools
- `/context` - Show context window usage
- `/context limit <n>` - Set context token limit
- `/reset-confirm` - Reset tool confirmation only
- `/sessions` - List, resume, or delete saved sessions (`/sessions delete all` to wipe all)
- `/permission` - Show active permission rules
- `/exit` or `/quit` - Exit the program

**Features:**
- Multi-turn conversations with context
- File operations (read, write, edit)
- Code search and analysis
- Web fetching and search
- Memory system
- Claude Skills support

Type your message to start chatting, or `/help` for more information!
"""
        console.print(Panel(Markdown(welcome), title="Welcome", border_style="cyan"))

    def print_help(self):
        """Print help message."""
        help_text = """
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
- `/clear` - Clear conversation history and reset confirmation mode
  - Also resets "allow all" mode to prompt for each tool
  - `/clear all` - Also clear all saved memories
- `/status` - Show conversation status
- `/skills` - List available skills
- `/memory [subcommand] [arg]` - Manage saved memories
  - `/memory` or `/memory list` - Show all saved memories (with tag summary)
  - `/memory search <query>` - Search memories by keyword (key, value, tags)
  - `/memory tag <tag>` - Filter memories by tag
  - `/memory delete <key>` - Delete a specific memory
  - `/memory clear` - Clear all memories (requires confirmation)
- `/context` - Show context window token usage and limit
  - `/context limit <n>` - Set max context tokens (default: 200,000)
- `/mcp [subcommand]` - Manage MCP servers
  - `/mcp` or `/mcp list` - List MCP servers with status and tools
  - `/mcp add <name> <command|url>` - Add an MCP server
  - `/mcp remove <name>` - Remove an MCP server
- `/confirm [all|prompt]` - Set tool confirmation mode
  - `/confirm` - Show current mode
  - `/confirm all` - Enable allow-all mode (skip prompts)
  - `/confirm prompt` - Restore prompt mode (ask each time)
- `/reset-confirm` - Reset tool confirmation to prompt mode (legacy alias)
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
- `google_web_search` - Search the web
- `activate_skill` - Activate Claude skills
- `cli_help` - Get CLI help
- `codebase_investigator` - Investigate codebases

**Skills:**
Type `/skills` to see available skills, or ask the agent to activate a specific skill.

**Examples:**
- "Read the file main.py"
- "Search for function definitions in Python files"
- "Fetch content from https://example.com"
- "Activate the pdf skill to help me work with PDF files"

**Note:** Regular messages (without `/`) are sent to the AI agent.
"""
        console.print(Markdown(help_text))

    def list_skills(self):
        """List available, disabled, and active skills."""
        sm = self.agent.skill_manager
        available = sm.list_available_skills()
        disabled = sorted(sm.disabled_skills & set(sm.available_skills.keys()))

        console.print(f"\n[info]Available Skills ({len(available)}):[/info]\n")
        for skill_name in sorted(available):
            skill_info = sm.get_skill_info(skill_name)
            title = skill_info.get('title', skill_name) if skill_info else skill_name
            desc = skill_info.get('description', 'No description')[:100] if skill_info else 'No description'
            console.print(f"  • [cyan]{skill_name}[/cyan] - {title}")
            if desc:
                console.print(f"    {desc}...")

        if disabled:
            console.print(f"\n[info]Disabled Skills ({len(disabled)}):[/info]\n")
            for skill_name in disabled:
                skill_info = sm.get_skill_info(skill_name)
                title = skill_info.get('title', skill_name) if skill_info else skill_name
                console.print(f"  • [dim]{skill_name}[/dim] - {title}")

        console.print("\n[info]Active Skills:[/info]")
        active = sm.get_active_skills()
        if active:
            for skill, info in active.items():
                console.print(f"  • [success]{skill}[/success]: {info['task']}")
        else:
            console.print("  None")
        console.print()

    def show_status(self):
        """Show conversation status."""
        summary = self.agent.get_conversation_summary()
        console.print(f"\n[info]Status:[/info]\n{summary}")

        # Show tool confirmation status
        if self.allow_all_tools:
            console.print("[info]Tool Confirmation:[/info] [green]Allow all mode enabled[/green]")
        else:
            console.print("[info]Tool Confirmation:[/info] [yellow]Prompt for each tool[/yellow]")
        console.print()

    def show_memories(self, subcommand: str = "", arg: str = ""):
        """Show saved memories.

        Args:
            subcommand: Subcommand (list, search, tag, delete, clear)
            arg: Argument for the subcommand
        """
        memory_tool = self.agent.memory_tool

        # Handle subcommands
        if subcommand in ["", "list"]:
            # List all memories
            memories = memory_tool.get_all_memories()

            if not memories:
                console.print("\n[warning]No memories saved yet.[/warning]\n")
                return

            console.print(f"\n[info]Saved Memories ({len(memories)} total):[/info]\n")
            for memory in memories:
                console.print(f"  • [cyan]{memory['key']}[/cyan]: {memory['value']}")
                if memory.get('tags'):
                    console.print(f"    Tags: {', '.join(memory['tags'])}")
                console.print(f"    Saved: {memory['timestamp']}")
                console.print()

            # Tag summary
            all_tags: dict = {}
            for mem in memories:
                for tag in mem.get("tags", []):
                    all_tags[tag] = all_tags.get(tag, 0) + 1
            if all_tags:
                console.print("[info]Tag Summary:[/info]")
                for tag, count in sorted(all_tags.items(), key=lambda x: -x[1]):
                    console.print(f"  [dim]#{tag}[/dim] ({count})")
                console.print()

        elif subcommand == "search":
            if not arg:
                console.print("\n[error]Usage: /memory search <query>[/error]\n")
                return

            results = memory_tool.search_memories(arg)

            if not results:
                console.print(f"\n[warning]No memories found matching '{arg}'[/warning]\n")
                return

            console.print(f"\n[info]Found {len(results)} memory(ies) matching '{arg}':[/info]\n")
            for memory in results:
                console.print(f"  • [cyan]{memory['key']}[/cyan]: {memory['value']}")
                if memory.get('tags'):
                    console.print(f"    Tags: {', '.join(memory['tags'])}")
                console.print(f"    Saved: {memory['timestamp']}")
                console.print()

        elif subcommand == "tag":
            if not arg:
                console.print("\n[error]Usage: /memory tag <tag_name>[/error]\n")
                return

            results = memory_tool.filter_by_tag(arg)

            if not results:
                console.print(f"\n[warning]No memories found with tag '{arg}'[/warning]\n")
                return

            console.print(f"\n[info]Found {len(results)} memory(ies) with tag '{arg}':[/info]\n")
            for memory in results:
                console.print(f"  • [cyan]{memory['key']}[/cyan]: {memory['value']}")
                if memory.get('tags'):
                    console.print(f"    Tags: {', '.join(memory['tags'])}")
                console.print(f"    Saved: {memory['timestamp']}")
                console.print()

        elif subcommand == "delete":
            if not arg:
                console.print("\n[error]Usage: /memory delete <key>[/error]\n")
                return

            if memory_tool.delete_memory(arg):
                console.print(f"\n[success]Successfully deleted memory: {arg}[/success]\n")
            else:
                console.print(f"\n[warning]Memory not found: {arg}[/warning]\n")

        elif subcommand == "clear":
            # Confirm before clearing
            if Confirm.ask("\n[warning]Are you sure you want to delete ALL memories? This cannot be undone.[/warning]", default=False):
                count = memory_tool.clear_all_memories()
                console.print(f"\n[success]Successfully cleared {count} memory(ies)[/success]\n")
            else:
                console.print("\n[info]Cancelled.[/info]\n")

        else:
            console.print(f"\n[error]Unknown subcommand: {subcommand}[/error]")
            console.print("[info]Available subcommands: list, search, tag, delete, clear[/info]\n")

    def _list_providers_from_env(self) -> list:
        """Return sorted list of provider names that have an API key in environment."""
        providers = []
        for key, value in os.environ.items():
            if key.endswith("_API_KEY") and value:
                provider = key[: -len("_API_KEY")]
                providers.append(provider)
        return sorted(providers)

    def handle_provider_command(self, args: str):
        """Handle /provider command.

        Args:
            args: Provider name to switch to, or empty to list providers
        """
        args = args.strip().upper()

        if not args:
            # Show current provider and list all available
            current_model = self.agent.get_current_model()
            console.print(f"\n[info]Current Provider:[/info] [cyan]{self.current_provider}[/cyan]  "
                          f"[dim](model: {current_model})[/dim]\n")

            providers = self._list_providers_from_env()
            if not providers:
                console.print("[warning]No providers found in .env (looking for XXXX_API_KEY entries)[/warning]\n")
                return

            console.print("[info]Available Providers:[/info]")
            for p in providers:
                marker = " [green]✓[/green]" if p == self.current_provider else ""
                console.print(f"  • {p}{marker}")
            console.print("\n[info]Usage:[/info] /provider <NAME>  (e.g. /provider GEMINI)\n")

        else:
            # Switch to specified provider
            api_key = os.getenv(f"{args}_API_KEY")
            if not api_key:
                console.print(f"\n[error]No API key found for provider '{args}' "
                               f"(expected env var: {args}_API_KEY)[/error]\n")
                return

            base_url = os.getenv(f"{args}_BASE_URL") or None
            model = os.getenv(f"{args}_MODEL") or None

            self.agent.set_provider(api_key=api_key, base_url=base_url, model=model)
            self.current_provider = args

            current_model = self.agent.get_current_model()
            console.print(f"\n[success]Switched to provider: {args}[/success]")
            console.print(f"[info]Model:[/info] [cyan]{current_model}[/cyan]\n")

    def handle_model_command(self, args: str):
        """Handle model command.

        Args:
            args: Command arguments (model name or empty for list)
        """
        args = args.strip()

        if not args:
            # Show current model and available models
            current = self.agent.get_current_model()
            console.print(f"\n[info]Current Model:[/info] [cyan]{current}[/cyan]\n")
            try:
                with console.status("[dim]Fetching available models…[/dim]"):
                    available = self.agent.list_available_models()
            except RuntimeError as e:
                console.print(f"[error]Failed to list models: {e}[/error]\n")
                return

            console.print("[info]Available Models:[/info]\n")

            # Group by provider
            claude_models = [m for m in available if m.startswith("claude-")]
            gpt_models = [m for m in available if m.startswith("gpt-")]
            other_models = [m for m in available if not m.startswith(("claude-", "gpt-"))]

            if claude_models:
                console.print("  [bold]Claude:[/bold]")
                for model in claude_models:
                    marker = " [green]✓[/green]" if model == current else ""
                    console.print(f"    • {model}{marker}")

            if gpt_models:
                console.print("\n  [bold]OpenAI GPT:[/bold]")
                for model in gpt_models:
                    marker = " [green]✓[/green]" if model == current else ""
                    console.print(f"    • {model}{marker}")

            if other_models:
                console.print("\n  [bold]Other:[/bold]")
                for model in other_models:
                    marker = " [green]✓[/green]" if model == current else ""
                    console.print(f"    • {model}{marker}")

            console.print("\n[info]Usage:[/info] /model <model_name>")
            console.print("Example: /model claude-sonnet-4-5\n")

        else:
            # Switch to specified model
            result = self.agent.set_model(args)
            console.print(f"\n[success]{result}[/success]\n")

    def handle_agent_command(self, args: str):
        """Handle /agent command.

        Args:
            args: '<name> <task>' to run an agent, or empty to list agents
        """
        args = args.strip()

        if not args:
            # List available agents
            if not self.agent.agent_manager:
                console.print("\n[warning]No agent manager available.[/warning]\n")
                return
            agents = self.agent.agent_manager.list_agents()
            if not agents:
                console.print("\n[warning]No agents defined.[/warning]\n")
                return
            console.print(f"\n[info]Available Agents ({len(agents)}):[/info]\n")
            for name, desc in agents.items():
                console.print(f"  - [cyan]{name}[/cyan]: {desc}")
            console.print("\n[info]Usage:[/info] /agent <name> <task>\n")
            return

        # Parse: first word is agent name, rest is the task
        parts = args.split(None, 1)
        agent_name = parts[0]
        if len(parts) < 2:
            console.print(f"\n[error]Usage: /agent {agent_name} <task description>[/error]\n")
            return

        task = parts[1]
        tool_name = f"agent_{agent_name.replace('-', '_')}"

        try:
            tool = self.agent.tools.get(tool_name)
        except KeyError:
            console.print(f"\n[error]Unknown agent: {agent_name}[/error]")
            available = ", ".join(self.agent.agent_manager.list_agents().keys()) if self.agent.agent_manager else ""
            console.print(f"[info]Available: {available}[/info]\n")
            return

        console.print(f"\n[bold green]Agent: {agent_name}[/bold green]")
        self.current_status = console.status(
            f"[bold yellow][{agent_name}] Thinking...", spinner="dots"
        )
        with self.current_status:
            result = tool.execute(task=task)

        console.print(Markdown(result))

    def handle_context_command(self, args: str):
        """Handle /context command.

        Args:
            args: Empty for status, 'limit <n>' to set token limit
        """
        args = args.strip()
        cm = self.agent.context_manager

        if not args:
            stats = cm.get_usage_stats(self.agent.messages)
            console.print("\n[info]Context Window Status:[/info]")
            console.print(f"  Estimated tokens: [cyan]{stats['estimated_tokens']:,}[/cyan]")
            console.print(f"  Max tokens:       [cyan]{stats['max_tokens']:,}[/cyan]")

            pct = stats["usage_percent"]
            color = "green" if pct < 60 else "yellow" if pct < 80 else "red"
            console.print(f"  Usage:            [{color}]{pct:.1f}%[/{color}]")
            console.print(f"  Messages:         {stats['message_count']}\n")

        elif args.startswith("limit "):
            limit_str = args[6:].strip()
            try:
                new_limit = int(limit_str)
                if new_limit < 1000:
                    console.print("\n[error]Context limit must be at least 1,000 tokens[/error]\n")
                    return
                cm.max_tokens = new_limit
                console.print(f"\n[success]Context limit set to {new_limit:,} tokens[/success]\n")
            except ValueError:
                console.print(f"\n[error]Invalid number: {limit_str}[/error]\n")
        else:
            console.print("\n[error]Usage: /context  OR  /context limit <n>[/error]\n")

    def handle_mcp_command(self, args: str):
        """Handle /mcp command for MCP server management.

        Args:
            args: Subcommand and arguments
        """
        from .mcp.config import load_mcp_config, save_mcp_config, _load_json_file
        from pathlib import Path

        args = args.strip()
        parts = args.split(None, 1) if args else []
        sub = parts[0] if parts else "list"
        sub_args = parts[1] if len(parts) > 1 else ""

        if sub == "list":
            manager = self.agent.mcp_manager
            if not manager or not manager.clients:
                console.print("\n[warning]No MCP servers configured.[/warning]")
                console.print("[info]Add servers to .agentao/mcp.json or use /mcp add[/info]\n")
                return

            statuses = manager.get_server_status()
            console.print(f"\n[info]MCP Servers ({len(statuses)}):[/info]\n")
            for s in statuses:
                color = "green" if s["status"] == "connected" else "red"
                trust_marker = " [dim](trusted)[/dim]" if s["trusted"] else ""
                console.print(
                    f"  [{color}]●[/{color}] [cyan]{s['name']}[/cyan] "
                    f"[dim]{s['transport']}[/dim] — "
                    f"[{color}]{s['status']}[/{color}], "
                    f"{s['tools']} tool(s){trust_marker}"
                )
                if s["error"]:
                    console.print(f"    [red]{s['error']}[/red]")
            console.print()

        elif sub == "add":
            # /mcp add <name> <command|url> [args...]
            add_parts = sub_args.split(None, 1) if sub_args else []
            if len(add_parts) < 2:
                console.print("\n[error]Usage: /mcp add <name> <command|url> [args...][/error]")
                console.print("[info]Examples:[/info]")
                console.print("  /mcp add github npx -y @modelcontextprotocol/server-github")
                console.print("  /mcp add remote https://api.example.com/sse\n")
                return

            name = add_parts[0]
            endpoint = add_parts[1]

            # Determine transport from endpoint
            if endpoint.startswith("http://") or endpoint.startswith("https://"):
                server_cfg = {"url": endpoint}
            else:
                # Stdio: split into command + args
                cmd_parts = endpoint.split()
                server_cfg = {"command": cmd_parts[0]}
                if len(cmd_parts) > 1:
                    server_cfg["args"] = cmd_parts[1:]

            # Load current project config and add
            project_path = Path.cwd() / ".agentao" / "mcp.json"
            existing = _load_json_file(project_path)
            servers = existing.get("mcpServers", {})
            servers[name] = server_cfg
            saved_path = save_mcp_config(servers)

            console.print(f"\n[success]Added MCP server '{name}' to {saved_path}[/success]")
            console.print("[info]Restart agentao to connect to the new server.[/info]\n")

        elif sub == "remove":
            name = sub_args.strip()
            if not name:
                console.print("\n[error]Usage: /mcp remove <name>[/error]\n")
                return

            project_path = Path.cwd() / ".agentao" / "mcp.json"
            existing = _load_json_file(project_path)
            servers = existing.get("mcpServers", {})
            if name not in servers:
                console.print(f"\n[warning]Server '{name}' not found in config.[/warning]\n")
                return

            del servers[name]
            save_mcp_config(servers)
            console.print(f"\n[success]Removed MCP server '{name}'.[/success]")
            console.print("[info]Restart agentao to apply changes.[/info]\n")

        else:
            console.print(f"\n[error]Unknown subcommand: {sub}[/error]")
            console.print("[info]Available: /mcp list, /mcp add, /mcp remove[/info]\n")

    def handle_permission_command(self, args: str):
        """Handle /permission command — show active permission rules."""
        console.print(f"\n{self.permission_engine.get_rules_display()}\n")

    def handle_sessions_command(self, args: str):
        """Handle /sessions command.

        Args:
            args: Subcommand: list | resume <id> | delete <id>
        """
        from .session import list_sessions, delete_session, delete_all_sessions

        args = args.strip()
        parts = args.split(None, 1) if args else []
        sub = parts[0] if parts else "list"
        sub_arg = parts[1].strip() if len(parts) > 1 else ""

        if sub in ("", "list"):
            sessions = list_sessions()
            if not sessions:
                console.print("\n[warning]No saved sessions found.[/warning]\n")
                return
            console.print(f"\n[info]Saved Sessions ({len(sessions)}):[/info]\n")
            for s in sessions:
                console.print(f"  • [cyan]{s['id']}[/cyan]")
                console.print(f"    Model: [dim]{s['model']}[/dim]  Messages: {s['message_count']}")
                console.print(f"    Saved: {s['timestamp']}")
                if s["active_skills"]:
                    console.print(f"    Skills: {', '.join(s['active_skills'])}")
                console.print()
            console.print("[info]Usage:[/info] /sessions resume <id>  or  /sessions delete <id>  or  /sessions delete all\n")

        elif sub == "resume":
            self.resume_session(sub_arg or None)

        elif sub == "delete":
            if sub_arg == "all":
                sessions = list_sessions()
                if not sessions:
                    console.print("\n[warning]No saved sessions to delete.[/warning]\n")
                    return
                console.print(f"\n[warning]Delete all {len(sessions)} session(s)? Press 1 to confirm, any other key to cancel.[/warning]")
                import readchar
                key = readchar.readkey()
                if key == "1":
                    count = delete_all_sessions()
                    console.print(f"\n[success]Deleted {count} session(s).[/success]\n")
                else:
                    console.print("\n[info]Cancelled.[/info]\n")
                return
            if not sub_arg:
                console.print("\n[error]Usage: /sessions delete <session-id>  or  /sessions delete all[/error]\n")
                return
            if delete_session(sub_arg):
                console.print(f"\n[success]Session '{sub_arg}' deleted.[/success]\n")
            else:
                console.print(f"\n[warning]Session '{sub_arg}' not found.[/warning]\n")

        else:
            console.print(f"\n[error]Unknown subcommand: {sub}[/error]")
            console.print("[info]Available: /sessions list | /sessions resume <id> | /sessions delete <id> | /sessions delete all[/info]\n")

    def resume_session(self, session_id: Optional[str] = None):
        """Load a previously saved session into the current agent.

        Args:
            session_id: Timestamp prefix to identify session, or None for latest.
        """
        from .session import load_session

        try:
            messages, model, active_skills = load_session(session_id)
        except FileNotFoundError as e:
            console.print(f"\n[error]Could not resume session: {e}[/error]\n")
            return

        self.agent.messages = messages
        if model:
            try:
                self.agent.set_model(model)
            except Exception:
                pass
        for skill_name in active_skills:
            try:
                self.agent.skill_manager.activate_skill(skill_name, "Restored from session")
            except Exception:
                pass

        msg_count = len(messages)
        console.print(f"\n[success]Session resumed: {msg_count} messages loaded.[/success]")
        if model:
            console.print(f"[dim]Model: {model}[/dim]")
        if active_skills:
            console.print(f"[dim]Active skills: {', '.join(active_skills)}[/dim]")
        console.print()

    def _save_session_on_exit(self):
        """Save current session to disk if there are messages."""
        if not self.agent.messages:
            return
        from .session import save_session
        try:
            active_skills = list(self.agent.skill_manager.get_active_skills().keys())
            session_file = save_session(
                messages=self.agent.messages,
                model=self.agent.get_current_model(),
                active_skills=active_skills,
            )
            console.print(f"[dim]Session saved → {session_file}[/dim]")
        except Exception:
            pass  # Non-critical

    def _get_user_input(self) -> str:
        """Read user input using prompt_toolkit.

        multiline=True captures pasted multi-line text in one shot.
        Enter submits; Meta/Alt+Enter inserts a literal newline.
        prompt_toolkit's wcwidth support correctly handles CJK characters on macOS.
        """
        return self._prompt_session.prompt(ANSI("\n\033[1;36mYou\033[0m: "))

    def run(self):
        """Run the CLI."""
        self.print_welcome()

        while True:
            try:
                # Get user input
                user_input = self._get_user_input()

                if not user_input.strip():
                    continue

                # Handle commands (all start with /)
                input_text = user_input.strip()

                # Check if it's a command
                if input_text.startswith('/'):
                    # Split command and arguments
                    parts = input_text[1:].split(maxsplit=1)
                    command = parts[0].lower()
                    args = parts[1] if len(parts) > 1 else ""

                    if command in ["exit", "quit"]:
                        self._save_session_on_exit()
                        console.print("\n[success]Goodbye![/success]\n")
                        break

                    elif command == "help":
                        self.print_help()
                        continue

                    elif command == "clear":
                        self.agent.clear_history()
                        self.allow_all_tools = False  # Reset confirmation mode
                        if args == "all":
                            mem_count = self.agent.memory_tool.clear_all_memories()
                            console.print("\n[success]Conversation history cleared.[/success]")
                            console.print(f"[success]Cleared {mem_count} memory(ies).[/success]")
                            console.print("[info]Tool confirmation reset to prompt mode.[/info]\n")
                        else:
                            console.print("\n[success]Conversation history cleared.[/success]")
                            console.print("[info]Tool confirmation reset to prompt mode.[/info]\n")
                        continue

                    elif command == "status":
                        self.show_status()
                        continue

                    elif command == "skills":
                        if not args:
                            self.list_skills()
                        else:
                            sub_parts = args.split(maxsplit=1)
                            sub_cmd = sub_parts[0]
                            sub_arg = sub_parts[1].strip() if len(sub_parts) > 1 else ""
                            if sub_cmd == "disable":
                                if not sub_arg:
                                    console.print("[warning]Usage: /skills disable <skill_name>[/warning]")
                                else:
                                    result = self.agent.skill_manager.disable_skill(sub_arg)
                                    console.print(f"\n{result}\n")
                            elif sub_cmd == "enable":
                                if not sub_arg:
                                    console.print("[warning]Usage: /skills enable <skill_name>[/warning]")
                                else:
                                    result = self.agent.skill_manager.enable_skill(sub_arg)
                                    console.print(f"\n{result}\n")
                            elif sub_cmd == "reload":
                                self.agent.skill_manager.reload_skills()
                                count = len(self.agent.skill_manager.list_available_skills())
                                console.print(f"\n[success]Skills reloaded. {count} available.[/success]\n")
                            else:
                                console.print(f"[warning]Unknown subcommand '{sub_cmd}'. Use: disable, enable, reload[/warning]")
                        continue

                    elif command == "memory":
                        # Parse subcommand and arguments
                        if args:
                            subcommand_parts = args.split(maxsplit=1)
                            subcommand = subcommand_parts[0]
                            subcommand_arg = subcommand_parts[1] if len(subcommand_parts) > 1 else ""
                            self.show_memories(subcommand, subcommand_arg)
                        else:
                            self.show_memories()
                        continue

                    elif command == "model":
                        self.handle_model_command(args)
                        continue

                    elif command == "provider":
                        self.handle_provider_command(args)
                        continue

                    elif command == "context":
                        self.handle_context_command(args)
                        continue

                    elif command == "mcp":
                        self.handle_mcp_command(args)
                        continue

                    elif command == "agent":
                        self.handle_agent_command(args)
                        continue

                    elif command == "confirm":
                        if args == "all":
                            self.allow_all_tools = True
                            console.print("\n[green]✓ Allow-all mode enabled. Tools will execute without prompting.[/green]\n")
                        elif args == "prompt":
                            self.allow_all_tools = False
                            console.print("\n[success]Prompt mode enabled. Will ask before each tool.[/success]\n")
                        elif args == "":
                            mode = "allow-all" if self.allow_all_tools else "prompt"
                            console.print(f"\n[info]Tool confirmation mode: {mode}[/info]\n")
                        else:
                            console.print("\n[warning]Usage: /confirm [all|prompt][/warning]\n")
                        continue

                    elif command == "reset-confirm":
                        if self.allow_all_tools:
                            self.allow_all_tools = False
                            console.print("\n[success]Tool confirmation reset. Will prompt for each tool.[/success]\n")
                        else:
                            console.print("\n[info]Tool confirmation is already in prompt mode.[/info]\n")
                        continue

                    elif command == "permission":
                        self.handle_permission_command(args)
                        continue

                    elif command == "sessions":
                        self.handle_sessions_command(args)
                        continue

                    else:
                        console.print(f"\n[error]Unknown command: /{command}[/error]")
                        console.print("Type [cyan]/help[/cyan] for available commands.\n")
                        continue

                # Process with agent
                console.rule("[bold green]Assistant[/bold green]", style="green")
                self._llm_streamed = False
                self.current_status = console.status("[bold yellow]Thinking...", spinner="dots")
                with self.current_status:
                    response = self.agent.chat(user_input)

                # Display response:
                # - If text was streamed in real-time, just add a trailing newline.
                # - Otherwise render as markdown.
                if self._llm_streamed:
                    console.print()
                    console.print()
                    self._llm_streamed = False
                else:
                    console.print()
                    console.print(Markdown(response))

            except KeyboardInterrupt:
                console.print("\n\n[warning]Interrupted. Type '/exit' to quit.[/warning]")
                continue

            except Exception as e:
                console.print(f"\n[error]Error: {str(e)}[/error]\n")
                continue


def run_print_mode(prompt: str) -> int:
    """Non-interactive print mode: send prompt, print response, exit. Returns exit code."""
    load_dotenv()
    agent = Agentao(
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL"),
        model=os.getenv("OPENAI_MODEL"),
    )
    try:
        response = agent.chat(prompt)
        print(response)
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def main(resume_session: Optional[str] = None):
    """Main entry point."""
    try:
        cli = AgentaoCLI()
        if resume_session is not None:
            # Empty string means "latest session"; non-empty is a session ID prefix
            cli.resume_session(resume_session if resume_session else None)
        cli.run()
    except KeyboardInterrupt:
        console.print("\n\n[success]Goodbye![/success]\n")
        sys.exit(0)
    except Exception as e:
        console.print(f"\n[error]Fatal error: {str(e)}[/error]\n")
        sys.exit(1)


def entrypoint():
    """Unified entry point: -p for print mode, --resume for session restore, otherwise interactive."""
    import argparse
    parser = argparse.ArgumentParser(prog="agentao", add_help=False)
    parser.add_argument("-p", "--print", dest="prompt", nargs="?", const="", default=None)
    parser.add_argument(
        "--resume",
        dest="resume",
        nargs="?",
        const="",
        default=None,
        metavar="SESSION_ID",
        help="Resume a saved session. Omit SESSION_ID to resume the latest.",
    )
    args, _ = parser.parse_known_args()

    if args.prompt is not None:
        stdin_text = "" if sys.stdin.isatty() else sys.stdin.read()
        parts = [p for p in [args.prompt.strip(), stdin_text.strip()] if p]
        full_prompt = "\n".join(parts)
        sys.exit(run_print_mode(full_prompt))
    else:
        main(resume_session=args.resume)


if __name__ == "__main__":
    entrypoint()
