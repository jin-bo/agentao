from types import SimpleNamespace

from agentao.cli import AgentaoCLI
from agentao.cli.commands_ext import _show_agents_dashboard


def test_dashboard_keeps_live_view_for_pending_tasks(monkeypatch):
    calls = {"live_started": False}

    task_states = [
        [{"id": "t1", "agent_name": "worker", "status": "pending", "task": "queued", "created_at": 1}],
        [{"id": "t1", "agent_name": "worker", "status": "pending", "task": "queued", "created_at": 1}],
        [{"id": "t1", "agent_name": "worker", "status": "pending", "task": "queued", "created_at": 1}],
        [{"id": "t1", "agent_name": "worker", "status": "completed", "task": "queued", "created_at": 1}],
        [{"id": "t1", "agent_name": "worker", "status": "completed", "task": "queued", "created_at": 1}],
    ]

    def fake_list():
        if task_states:
            return task_states.pop(0)
        return [{"id": "t1", "agent_name": "worker", "status": "completed", "task": "queued", "created_at": 1}]

    class FakeLive:
        def __init__(self, *args, **kwargs):
            calls["live_started"] = True

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def update(self, renderable):
            calls["last_renderable"] = renderable

    prints = []

    monkeypatch.setattr("rich.live.Live", FakeLive)
    monkeypatch.setattr("agentao.cli._globals.console.print", lambda *args, **kwargs: prints.append((args, kwargs)))
    monkeypatch.setattr("time.sleep", lambda *args, **kwargs: None)

    cli = AgentaoCLI.__new__(AgentaoCLI)
    cli.agent = SimpleNamespace(bg_store=SimpleNamespace(list=fake_list))
    _show_agents_dashboard(cli)

    assert calls["live_started"] is True
    assert prints
