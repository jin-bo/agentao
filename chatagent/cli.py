"""CLI interface for ChatAgent."""

import os
import select
import sys
from typing import Optional

import readchar
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.theme import Theme
from dotenv import load_dotenv

from .agent import ChatAgent

# Custom theme for the CLI
custom_theme = Theme({
    "info": "cyan",
    "warning": "yellow",
    "error": "bold red",
    "success": "bold green",
})

console = Console(theme=custom_theme)

# Tool argument keys to display in the thinking step (priority order)
_TOOL_SUMMARY_KEYS = ("path", "file_path", "query", "command", "url", "key", "pattern", "tag")


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


class ChatAgentCLI:
    """CLI interface for ChatAgent."""

    def __init__(self):
        """Initialize CLI."""
        load_dotenv()

        # Track session-wide confirmation preferences
        self.allow_all_tools = False  # "Yes to all" mode
        self.current_status = None  # Track active status context

        context_limit = int(os.getenv("CHATAGENT_CONTEXT_TOKENS", "200000"))

        self.agent = ChatAgent(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL"),
            model=os.getenv("OPENAI_MODEL"),
            confirmation_callback=self.confirm_tool_execution,
            recall_callback=self.confirm_memory_recall,
            max_context_tokens=context_limit,
            step_callback=self.on_tool_step,
            thinking_callback=self.on_llm_thinking,
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
            console.print(f"[info]Description:[/info] {tool_description}")
            console.print(f"[info]Arguments:[/info]")

            # Format arguments nicely
            for key, value in tool_args.items():
                # Truncate long values
                value_str = str(value)
                if len(value_str) > 100:
                    value_str = value_str[:100] + "..."
                console.print(f"  • {key}: {value_str}")

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

    def confirm_memory_recall(self, recalled_memories: list) -> Optional[list]:
        """Prompt user to confirm injecting recalled memories into context.

        Args:
            recalled_memories: List of memory dicts recalled as relevant

        Returns:
            The same list if user confirms, None if user declines
        """
        if self.allow_all_tools:
            console.print(
                f"[dim]✓ Auto-injecting {len(recalled_memories)} recalled memory(ies)[/dim]"
            )
            return recalled_memories

        if self.current_status:
            self.current_status.stop()

        try:
            console.print(
                f"\n[cyan]Memory Recall[/cyan]: "
                f"Found {len(recalled_memories)} relevant memory(ies):\n"
            )
            for mem in recalled_memories:
                console.print(f"  • [cyan]{mem['key']}[/cyan]: {str(mem['value'])[:80]}")
                if mem.get("tags"):
                    console.print(f"    Tags: {', '.join(mem['tags'])}")

            console.print("\n[bold]Inject these memories into context?[/bold]")
            console.print(" [green]1[/green]. Yes, inject")
            console.print(" [red]2[/red]. No, skip")
            console.print(
                "\n[dim]Press 1 or 2 (single key, no Enter needed) · Esc to skip[/dim]",
                end=" ",
            )

            while True:
                try:
                    key = readchar.readkey()
                    if key == "1":
                        console.print("\n[green]✓ Memories injected into context[/green]")
                        return recalled_memories
                    elif key in ("2",):
                        console.print("\n[dim]Memory injection skipped[/dim]")
                        return None
                    elif key in (readchar.key.ESC, readchar.key.CTRL_C):
                        console.print("\n[dim]Memory injection skipped[/dim]")
                        return None
                    else:
                        continue
                except (KeyboardInterrupt, Exception):
                    console.print("\n[dim]Memory injection skipped[/dim]")
                    return None
        finally:
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

        console.print(f"[bold blue]Thinking[/bold blue]")
        # Indent each line for visual separation
        for line in reasoning.strip().splitlines():
            console.print(f"  [blue]{line}[/blue]")
        console.print()

        # Resume spinner
        if self.current_status:
            self.current_status.start()

    def on_tool_step(self, tool_name: Optional[str], tool_args: dict) -> None:
        """Display tool call step in the thinking spinner.

        Called with tool_name=None to reset back to "Thinking..." display.

        Args:
            tool_name: Name of the tool being called, or None to reset
            tool_args: Arguments passed to the tool
        """
        if not self.current_status:
            return

        if tool_name is None:
            self.current_status.update("[bold yellow]Thinking...[/bold yellow]")
            return

        # Build a short summary of the key argument
        summary = _tool_args_summary(tool_name, tool_args)
        if summary:
            label = f"[bold yellow]⚙ {tool_name}[/bold yellow] [dim]{summary}[/dim]"
        else:
            label = f"[bold yellow]⚙ {tool_name}[/bold yellow]"

        self.current_status.update(label)

    def print_welcome(self):
        """Print welcome message."""
        current_model = self.agent.get_current_model()
        welcome = f"""
# ChatAgent

A CLI chat agent with tools and skills support.

**Current Model:** `{current_model}`

**Commands:**
- `/help` - Show help message
- `/model` - List or switch models
- `/clear` - Clear conversation and reset confirmation
- `/status` - Show conversation status (memory count, context usage)
- `/skills` - List available skills
- `/memory [subcommand]` - Manage saved memories
- `/context` - Show context window usage
- `/context limit <n>` - Set context token limit
- `/reset-confirm` - Reset tool confirmation only
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
# ChatAgent Help

**Available Commands:**
All commands start with `/`:

- `/help` - Show this help message
- `/model` - List available models or switch model
  - `/model` - Show current model and available models
  - `/model <name>` - Switch to specified model
- `/clear` - Clear conversation history and reset confirmation mode
  - Also resets "allow all" mode to prompt for each tool
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
- `/reset-confirm` - Reset tool confirmation to prompt mode
  - Use this if you enabled "allow all" mode (without clearing history)
- `/exit` or `/quit` - Exit the program

**Available Tools:**
The agent has access to the following tools:
- `read_file` - Read file contents
- `write_file` - Write content to files
- `replace` - Edit files by replacing text
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
        """List available skills."""
        skills = self.agent.skill_manager.list_available_skills()

        console.print(f"\n[info]Available Skills ({len(skills)} loaded):[/info]\n")
        for skill_name in sorted(skills):
            skill_info = self.agent.skill_manager.get_skill_info(skill_name)
            title = skill_info.get('title', skill_name) if skill_info else skill_name
            desc = skill_info.get('description', 'No description')[:100] if skill_info else 'No description'
            console.print(f"  • [cyan]{skill_name}[/cyan] - {title}")
            if desc:
                console.print(f"    {desc}...")

        console.print("\n[info]Active Skills:[/info]")
        active = self.agent.skill_manager.get_active_skills()
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

    def _get_user_input(self) -> str:
        """Read user input, collecting all lines when multi-line text is pasted.

        When text is pasted from clipboard, the terminal writes all characters
        (including newlines) into stdin at once. After Prompt.ask() returns the
        first line, select.select() detects remaining buffered data and collects
        the rest, returning the full multi-line string.
        """
        first_line = Prompt.ask("\n[bold cyan]You[/bold cyan]")

        # Check if more data is buffered in stdin (multi-line paste scenario)
        extra_lines = []
        try:
            while select.select([sys.stdin], [], [], 0.05)[0]:
                line = sys.stdin.readline()
                if not line:
                    break
                extra_lines.append(line.rstrip("\n"))
        except Exception:
            pass  # select not available or stdin is not a real fd (e.g. in tests)

        if extra_lines:
            return first_line + "\n" + "\n".join(extra_lines)
        return first_line

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
                        console.print("\n[success]Goodbye![/success]\n")
                        break

                    elif command == "help":
                        self.print_help()
                        continue

                    elif command == "clear":
                        self.agent.clear_history()
                        self.allow_all_tools = False  # Reset confirmation mode
                        console.print("\n[success]Conversation history cleared.[/success]")
                        console.print("[info]Tool confirmation reset to prompt mode.[/info]\n")
                        continue

                    elif command == "status":
                        self.show_status()
                        continue

                    elif command == "skills":
                        self.list_skills()
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

                    elif command == "context":
                        self.handle_context_command(args)
                        continue

                    elif command == "reset-confirm":
                        if self.allow_all_tools:
                            self.allow_all_tools = False
                            console.print("\n[success]Tool confirmation reset. Will prompt for each tool.[/success]\n")
                        else:
                            console.print("\n[info]Tool confirmation is already in prompt mode.[/info]\n")
                        continue

                    else:
                        console.print(f"\n[error]Unknown command: /{command}[/error]")
                        console.print("Type [cyan]/help[/cyan] for available commands.\n")
                        continue

                # Process with agent
                console.print("\n[bold green]Assistant[/bold green]")
                self.current_status = console.status("[bold yellow]Thinking...", spinner="dots")
                with self.current_status:
                    response = self.agent.chat(user_input)

                # Display response
                console.print(Markdown(response))

            except KeyboardInterrupt:
                console.print("\n\n[warning]Interrupted. Type '/exit' to quit.[/warning]")
                continue

            except Exception as e:
                console.print(f"\n[error]Error: {str(e)}[/error]\n")
                continue


def main():
    """Main entry point."""
    try:
        cli = ChatAgentCLI()
        cli.run()
    except KeyboardInterrupt:
        console.print("\n\n[success]Goodbye![/success]\n")
        sys.exit(0)
    except Exception as e:
        console.print(f"\n[error]Fatal error: {str(e)}[/error]\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
