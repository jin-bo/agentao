"""Blueprint A's custom tools — hit the host's own product APIs.

In production these would call `httpx` against an internal service; for the
demo we keep an in-memory store so the example runs end-to-end with zero
external dependencies.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Dict

from agentao.tools.base import Tool


# In-memory "database" keyed by tenant.
_PROJECTS: Dict[str, list[Dict[str, Any]]] = {
    "acme": [
        {"id": "p-1", "name": "Q4 Launch",       "status": "active"},
        {"id": "p-2", "name": "Docs Refresh",    "status": "active"},
        {"id": "p-3", "name": "Legacy Migration","status": "archived"},
    ],
    "bigco": [
        {"id": "p-4", "name": "Security Audit",  "status": "active"},
    ],
}
_TASKS: Dict[str, list[Dict[str, Any]]] = {"acme": [], "bigco": []}


class ListProjectsTool(Tool):
    def __init__(self, tenant_id: str):
        self._tenant_id = tenant_id

    @property
    def name(self) -> str: return "list_projects"
    @property
    def description(self) -> str: return "List projects visible to the current user."
    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["active", "archived", "all"]}
            },
        }
    @property
    def is_read_only(self) -> bool: return True

    def execute(self, status: str = "active") -> str:
        rows = _PROJECTS.get(self._tenant_id, [])
        if status != "all":
            rows = [r for r in rows if r["status"] == status]
        return json.dumps(rows)


class CreateTaskTool(Tool):
    def __init__(self, tenant_id: str):
        self._tenant_id = tenant_id

    @property
    def name(self) -> str: return "create_task"
    @property
    def description(self) -> str: return "Create a task inside a project."
    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "required": ["project_id", "title"],
            "properties": {
                "project_id":    {"type": "string"},
                "title":         {"type": "string"},
                "assignee_email":{"type": "string"},
                "due_date":      {"type": "string", "format": "date"},
            },
        }
    @property
    def requires_confirmation(self) -> bool: return True

    def execute(self, project_id: str, title: str,
                assignee_email: str = "", due_date: str = "") -> str:
        row = {
            "id": f"t-{uuid.uuid4().hex[:6]}",
            "project_id": project_id,
            "title": title,
            "assignee_email": assignee_email,
            "due_date": due_date,
        }
        _TASKS.setdefault(self._tenant_id, []).append(row)
        return json.dumps(row)
