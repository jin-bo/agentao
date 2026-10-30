"""The parent ``Agentao`` the sub-agent suites spawn from."""

from __future__ import annotations

from typing import Any

from agentao.agent import Agentao
from agentao.permissions import PermissionEngine


def make_parent(tmp_path, **kwargs: Any) -> Agentao:
    """An ``Agentao`` with the built-in agents enabled, rooted at ``tmp_path``.

    The permission engine's user root is ``tmp_path / "user"``, so no rule is read from
    the real home. The caller closes the agent.
    """
    (tmp_path / "user").mkdir(exist_ok=True)
    return Agentao(
        working_directory=tmp_path, api_key="k",
        base_url="https://test.local/v1", model="m",
        enable_builtin_agents=True,
        permission_engine=PermissionEngine(
            project_root=tmp_path, user_root=tmp_path / "user",
        ),
        **kwargs,
    )
