"""Memory tool for saving important information."""

import json
from pathlib import Path
from datetime import datetime
from typing import Any, Dict

from .base import Tool


class SaveMemoryTool(Tool):
    """Tool for saving important information to memory."""

    def __init__(self, memory_file: str = ".chatagent/memory.json"):
        """Initialize memory tool.

        Args:
            memory_file: Path to the memory file
        """
        self.memory_file = Path(memory_file).expanduser()
        self._ensure_memory_file()

    def _ensure_memory_file(self):
        """Ensure memory file and its parent directory exist."""
        self.memory_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.memory_file.exists():
            self.memory_file.write_text(json.dumps({"memories": []}, indent=2))

    @property
    def name(self) -> str:
        return "save_memory"

    @property
    def description(self) -> str:
        return (
            "Save important information to long-term memory for future conversations. "
            "Call this proactively when you learn durable facts about the user or project: "
            "preferences, names, key decisions, recurring workflows. "
            "Do NOT save ephemeral or session-specific details. "
            "Use descriptive snake_case keys like 'user_preferred_language'."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "A short identifier for this memory (e.g., 'user_preference', 'project_context')",
                },
                "value": {
                    "type": "string",
                    "description": "The information to remember",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags for categorizing this memory",
                },
            },
            "required": ["key", "value"],
        }

    def execute(self, key: str, value: str, tags: list = None) -> str:
        """Save information to memory."""
        try:
            # Load existing memories
            with open(self.memory_file, "r") as f:
                data = json.load(f)

            memories = data.get("memories", [])

            # Create new memory entry
            memory = {
                "key": key,
                "value": value,
                "timestamp": datetime.now().isoformat(),
                "tags": tags or [],
            }

            # Update or append
            updated = False
            for i, m in enumerate(memories):
                if m.get("key") == key:
                    memories[i] = memory
                    updated = True
                    break

            if not updated:
                memories.append(memory)

            # Save back to file
            data["memories"] = memories
            with open(self.memory_file, "w") as f:
                json.dump(data, f, indent=2)

            action = "Updated" if updated else "Saved"
            return f"{action} memory: {key}"

        except Exception as e:
            return f"Error saving memory: {str(e)}"

    def get_all_memories(self) -> list:
        """Get all saved memories.

        Returns:
            List of memory entries
        """
        try:
            with open(self.memory_file, "r") as f:
                data = json.load(f)
            return data.get("memories", [])
        except Exception:
            return []

    def search_memories(self, query: str) -> list:
        """Search memories by key or tags.

        Args:
            query: Search query

        Returns:
            List of matching memories
        """
        memories = self.get_all_memories()
        query_lower = query.lower()

        results = []
        for memory in memories:
            if query_lower in memory.get("key", "").lower():
                results.append(memory)
            elif query_lower in memory.get("value", "").lower():
                results.append(memory)
            elif any(query_lower in tag.lower() for tag in memory.get("tags", [])):
                results.append(memory)

        return results

    def filter_by_tag(self, tag: str) -> list:
        """Filter memories by specific tag.

        Args:
            tag: Tag to filter by

        Returns:
            List of memories with the specified tag
        """
        memories = self.get_all_memories()
        tag_lower = tag.lower()

        return [m for m in memories if any(tag_lower == t.lower() for t in m.get("tags", []))]

    def delete_memory(self, key: str) -> bool:
        """Delete a memory by key.

        Args:
            key: Memory key to delete

        Returns:
            True if deleted, False if not found
        """
        try:
            with open(self.memory_file, "r") as f:
                data = json.load(f)

            memories = data.get("memories", [])
            original_count = len(memories)

            # Filter out the memory with the specified key
            memories = [m for m in memories if m.get("key") != key]

            if len(memories) == original_count:
                return False  # Memory not found

            # Save back to file
            data["memories"] = memories
            with open(self.memory_file, "w") as f:
                json.dump(data, f, indent=2)

            return True

        except Exception:
            return False

    def clear_all_memories(self) -> int:
        """Clear all memories.

        Returns:
            Number of memories deleted
        """
        try:
            with open(self.memory_file, "r") as f:
                data = json.load(f)

            count = len(data.get("memories", []))

            # Clear memories
            data["memories"] = []
            with open(self.memory_file, "w") as f:
                json.dump(data, f, indent=2)

            return count

        except Exception:
            return 0


class SearchMemoryTool(Tool):
    """Tool for searching memories."""

    def __init__(self, memory_tool: SaveMemoryTool):
        """Initialize search memory tool.

        Args:
            memory_tool: SaveMemoryTool instance to use
        """
        self.memory_tool = memory_tool

    @property
    def name(self) -> str:
        return "search_memory"

    @property
    def description(self) -> str:
        return "Search saved memories by keyword or tag. Useful for finding specific information."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query to match against memory keys and tags",
                },
            },
            "required": ["query"],
        }

    def execute(self, query: str) -> str:
        """Search memories."""
        try:
            results = self.memory_tool.search_memories(query)

            if not results:
                return f"No memories found matching '{query}'"

            # Format results
            output = f"Found {len(results)} memory(ies) matching '{query}':\n\n"
            for memory in results:
                output += f"• {memory['key']}: {memory['value']}\n"
                if memory.get('tags'):
                    output += f"  Tags: {', '.join(memory['tags'])}\n"
                output += f"  Saved: {memory['timestamp']}\n\n"

            return output.strip()

        except Exception as e:
            return f"Error searching memories: {str(e)}"


class DeleteMemoryTool(Tool):
    """Tool for deleting a specific memory."""

    def __init__(self, memory_tool: SaveMemoryTool):
        """Initialize delete memory tool.

        Args:
            memory_tool: SaveMemoryTool instance to use
        """
        self.memory_tool = memory_tool

    @property
    def name(self) -> str:
        return "delete_memory"

    @property
    def description(self) -> str:
        return "Delete a specific memory by its key. Use with caution as this is permanent."

    @property
    def requires_confirmation(self) -> bool:
        return True

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "The key of the memory to delete",
                },
            },
            "required": ["key"],
        }

    def execute(self, key: str) -> str:
        """Delete a memory."""
        try:
            if self.memory_tool.delete_memory(key):
                return f"Successfully deleted memory: {key}"
            else:
                return f"Memory not found: {key}"

        except Exception as e:
            return f"Error deleting memory: {str(e)}"


class ClearMemoryTool(Tool):
    """Tool for clearing all memories."""

    def __init__(self, memory_tool: SaveMemoryTool):
        """Initialize clear memory tool.

        Args:
            memory_tool: SaveMemoryTool instance to use
        """
        self.memory_tool = memory_tool

    @property
    def name(self) -> str:
        return "clear_all_memories"

    @property
    def description(self) -> str:
        return "Clear ALL saved memories. Use with extreme caution as this is permanent and cannot be undone."

    @property
    def requires_confirmation(self) -> bool:
        return True

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
        }

    def execute(self) -> str:
        """Clear all memories."""
        try:
            count = self.memory_tool.clear_all_memories()
            return f"Successfully cleared {count} memory(ies)"

        except Exception as e:
            return f"Error clearing memories: {str(e)}"


class FilterMemoryByTagTool(Tool):
    """Tool for filtering memories by tag."""

    def __init__(self, memory_tool: SaveMemoryTool):
        """Initialize filter memory tool.

        Args:
            memory_tool: SaveMemoryTool instance to use
        """
        self.memory_tool = memory_tool

    @property
    def name(self) -> str:
        return "filter_memory_by_tag"

    @property
    def description(self) -> str:
        return "Filter and list memories that have a specific tag."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tag": {
                    "type": "string",
                    "description": "The tag to filter memories by",
                },
            },
            "required": ["tag"],
        }

    def execute(self, tag: str) -> str:
        """Filter memories by tag."""
        try:
            results = self.memory_tool.filter_by_tag(tag)

            if not results:
                return f"No memories found with tag '{tag}'"

            # Format results
            output = f"Found {len(results)} memory(ies) with tag '{tag}':\n\n"
            for memory in results:
                output += f"• {memory['key']}: {memory['value']}\n"
                if memory.get('tags'):
                    output += f"  Tags: {', '.join(memory['tags'])}\n"
                output += f"  Saved: {memory['timestamp']}\n\n"

            return output.strip()

        except Exception as e:
            return f"Error filtering memories: {str(e)}"


class ListMemoryTool(Tool):
    """Tool for listing all saved memories."""

    def __init__(self, memory_tool: SaveMemoryTool):
        self.memory_tool = memory_tool

    @property
    def name(self) -> str:
        return "list_memories"

    @property
    def description(self) -> str:
        return (
            "List all saved memories. Use this to see what information has been "
            "saved for reference. Returns all memory keys, values, tags, and timestamps."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
        }

    def execute(self) -> str:
        """List all memories."""
        try:
            memories = self.memory_tool.get_all_memories()

            if not memories:
                return "No memories saved yet."

            # Collect all tags for summary
            all_tags: Dict[str, int] = {}
            for memory in memories:
                for tag in memory.get("tags", []):
                    all_tags[tag] = all_tags.get(tag, 0) + 1

            output = f"All saved memories ({len(memories)} total):\n\n"
            for memory in memories:
                output += f"• {memory['key']}: {memory['value']}\n"
                if memory.get("tags"):
                    output += f"  Tags: {', '.join(memory['tags'])}\n"
                output += f"  Saved: {memory['timestamp']}\n\n"

            if all_tags:
                output += "Tag summary:\n"
                for tag, count in sorted(all_tags.items(), key=lambda x: -x[1]):
                    output += f"  #{tag}: {count}\n"

            return output.strip()

        except Exception as e:
            return f"Error listing memories: {str(e)}"
