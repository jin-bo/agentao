"""Agent tools for specialized tasks."""

from typing import Any, Dict

from .base import Tool


class CLIHelpAgentTool(Tool):
    """Tool for getting help with CLI usage."""

    @property
    def name(self) -> str:
        return "cli_help"

    @property
    def description(self) -> str:
        return "Get help and guidance on using the ChatAgent CLI. Provides information about commands, features, and best practices."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "Question or topic to get help with",
                },
            },
            "required": ["question"],
        }

    def execute(self, question: str) -> str:
        """Provide CLI help."""
        help_text = f"""
ChatAgent CLI Help
==================

Question: {question}

Available Commands:
- Type your message to chat with the AI
- Type 'help' for this help message
- Type 'clear' to clear conversation history
- Type 'exit' or 'quit' to exit the program
- Type '/skill <skill_name>' to activate a skill

Available Tools:
- read_file: Read file contents
- write_file: Write content to a file
- replace: Edit a file by replacing text
- list_directory: List directory contents
- glob: Find files matching a pattern
- search_file_content: Search for text in files
- run_shell_command: Execute shell commands
- web_fetch: Fetch content from URLs
- google_web_search: Search the web
- save_memory: Save important information
- activate_skill: Activate a Claude skill

Features:
- Multi-turn conversations with context
- Function calling for tool usage
- Skills support for specialized tasks
- Memory system for saving information

For more detailed information, refer to the documentation or ask specific questions.
"""
        return help_text.strip()


class CodebaseInvestigatorTool(Tool):
    """Tool for investigating codebase structure and content."""

    @property
    def name(self) -> str:
        return "codebase_investigator"

    @property
    def description(self) -> str:
        return "Analyze and investigate codebase structure, find files, search code, and understand project organization. Use this for complex codebase exploration tasks."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Investigation task description (e.g., 'find all Python files', 'search for function definitions', 'analyze project structure')",
                },
                "directory": {
                    "type": "string",
                    "description": "Base directory to investigate (defaults to current directory)",
                    "default": ".",
                },
                "file_patterns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "File patterns to focus on (e.g., ['*.py', '*.js'])",
                },
            },
            "required": ["task"],
        }

    def execute(
        self, task: str, directory: str = ".", file_patterns: list = None
    ) -> str:
        """Investigate codebase."""
        from pathlib import Path

        try:
            path = Path(directory).expanduser()
            if not path.exists():
                return f"Error: Directory {directory} does not exist"

            results = []
            results.append(f"Codebase Investigation Task: {task}")
            results.append(f"Directory: {directory}")
            results.append("")

            # Analyze directory structure
            results.append("=== Directory Structure ===")
            dirs = set()
            files_by_ext = {}

            patterns = file_patterns or ["*"]
            for pattern in patterns:
                for item in path.rglob(pattern):
                    if item.is_file():
                        ext = item.suffix or "no_extension"
                        files_by_ext[ext] = files_by_ext.get(ext, 0) + 1

                        # Track directories
                        parent = item.parent
                        if parent != path:
                            dirs.add(parent.relative_to(path))

            # Report findings
            results.append(f"Total directories: {len(dirs)}")
            results.append(f"File types found:")
            for ext, count in sorted(files_by_ext.items(), key=lambda x: x[1], reverse=True):
                results.append(f"  {ext}: {count} files")

            results.append("")
            results.append("=== Key Directories ===")
            for d in sorted(dirs)[:20]:  # Limit to 20 directories
                results.append(f"  {d}/")

            # Provide suggestions based on task
            results.append("")
            results.append("=== Suggestions ===")
            if "python" in task.lower() or any(p.endswith(".py") for p in (patterns or [])):
                results.append("- Use 'glob' with pattern '**/*.py' to find all Python files")
                results.append("- Use 'search_file_content' to search for specific code patterns")
                results.append("- Check for 'requirements.txt', 'setup.py', or 'pyproject.toml'")
            elif "javascript" in task.lower() or "js" in task.lower():
                results.append("- Use 'glob' with pattern '**/*.js' to find JavaScript files")
                results.append("- Look for 'package.json' for project dependencies")
            else:
                results.append("- Use 'glob' to find files by pattern")
                results.append("- Use 'search_file_content' to search within files")
                results.append("- Use 'read_file' to examine specific files")

            return "\n".join(results)

        except Exception as e:
            return f"Error investigating codebase: {str(e)}"
