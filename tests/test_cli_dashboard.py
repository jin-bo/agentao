from agentao.cli import AgentaoCLI


def test_dashboard_keeps_live_view_for_pending_tasks(monkeypatch):
    calls = {"live_started": False}

    task_states = [
        [{"id": "t1", "agent_name": "worker", "status": "pending", "task": "queued", "created_at": 1}],
        [{"id": "t1", "agent_name": "worker", "status": "pending", "task": "queued", "created_at": 1}],
        [{"id": "t1", "agent_name": "worker", "status": "pending", "task": "queued", "created_at": 1}],
        [{"id": "t1", "agent_name": "worker", "status": "completed", "task": "queued", "created_at": 1}],
        [{"id": "t1", "agent_name": "worker", "status": "completed", "task": "queued", "created_at": 1}],
    ]

    def fake_list_bg_tasks():
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

    monkeypatch.setattr("agentao.agents.tools.list_bg_tasks", fake_list_bg_tasks)
    monkeypatch.setattr("rich.live.Live", FakeLive)
    monkeypatch.setattr("agentao.cli.console.print", lambda *args, **kwargs: prints.append((args, kwargs)))
    monkeypatch.setattr("time.sleep", lambda *args, **kwargs: None)

    cli = AgentaoCLI.__new__(AgentaoCLI)
    AgentaoCLI._show_agents_dashboard(cli)

    assert calls["live_started"] is True
    assert prints
