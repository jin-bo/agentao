"""Smoke test: the app imports, tools instantiate, and FastAPI routes exist.

Run with:  uv run python tests/smoke.py
"""
from __future__ import annotations

from app.main import app
from app.tools import CreateTaskTool, ListProjectsTool


def main() -> None:
    # 1. Tools can be instantiated and expose the expected surface.
    lp = ListProjectsTool("acme")
    assert lp.name == "list_projects"
    assert "active" in lp.execute("active")
    assert lp.is_read_only is True

    ct = CreateTaskTool("acme")
    assert ct.name == "create_task"
    assert ct.requires_confirmation is True

    # 2. FastAPI app has the expected routes.
    paths = {r.path for r in app.routes}
    for expected in {"/chat/{session_id}", "/chat/{session_id}/cancel",
                     "/session/{session_id}", "/healthz"}:
        assert expected in paths, f"missing route {expected!r}"

    print("smoke ok")


if __name__ == "__main__":
    main()
