"""What 0.5.0 removed is gone, and says so at the call site.

One file for the whole batch, so "is X really removed?" has one place to be
answered. Each test pins a *loud* failure: the risk in a removal release is
not the ``ImportError`` a host sees on upgrade, it is the call that keeps
working and now means something else — a callback bound to
``max_context_tokens``, a ``project_root=None`` that still resolves to the
process cwd.

Migration guide: ``docs/migration/0.4.x-to-0.5.0.md``.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from agentao.embedding import sessions

LEGACY_CALLBACKS = (
    "confirmation_callback",
    "step_callback",
    "thinking_callback",
    "ask_user_callback",
    "output_callback",
    "tool_complete_callback",
    "llm_text_callback",
    "on_max_iterations_callback",
)


def _agentao(*args, **kwargs):
    with patch("agentao.agent.LLMClient") as mock_llm_client:
        mock_llm_client.return_value.logger = Mock()
        mock_llm_client.return_value.model = "gpt-4"
        from agentao.agent import Agentao

        return Agentao(*args, **kwargs)


# ---------------------------------------------------------------------------
# The two shim modules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module, name",
    [
        ("agentao.harness", "HarnessEvent"),
        ("agentao.harness", "HostEvent"),
        ("agentao.harness.models", "HarnessEvent"),
        ("agentao.harness.schema", "export_harness_acp_json_schema"),
        ("agentao.session", "save_session"),
    ],
)
def test_the_deprecated_import_paths_are_gone(module, name):
    """``ImportError``, not only ``ModuleNotFoundError``.

    A checkout that had the package before this change keeps an
    ``agentao/harness/__pycache__`` directory, which Python imports as an
    empty namespace package. Nothing can be imported *from* it, which is the
    assertion that holds in both states.
    """
    with pytest.raises(ImportError):
        getattr(importlib.import_module(module), name)
        raise ImportError(name)  # reached only through an empty namespace


def test_the_host_package_kept_every_name_the_alias_re_exported():
    from agentao import host

    assert "HostEvent" in host.__all__
    assert not [n for n in host.__all__ if "arness" in n]


# ---------------------------------------------------------------------------
# Agentao.__init__
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", LEGACY_CALLBACKS)
def test_each_legacy_callback_kwarg_is_a_type_error(tmp_path, name):
    with pytest.raises(TypeError, match=name):
        _agentao(working_directory=tmp_path, **{name: lambda *a, **k: None})


def test_a_sixth_positional_argument_is_a_type_error(tmp_path):
    """The silent mis-bind the keyword-only ``*`` exists to prevent.

    The sixth positional was ``confirmation_callback``. Had the four
    parameters behind it stayed positional, this call would have put the
    callback into ``max_context_tokens``.
    """
    import inspect

    from agentao.agent import Agentao

    # conftest wraps ``__init__`` in a ``(*args, **kwargs)`` shim that also
    # backfills credentials by keyword; bind against the real signature.
    init = Agentao.__init__
    for cell in getattr(init, "__closure__", None) or ():
        inner = cell.cell_contents
        if callable(inner) and getattr(inner, "__name__", "") == "__init__":
            init = inner
    signature = inspect.signature(init)

    signature.bind(
        None, "k", "https://x.test/v1", "m", 0.1, 100, working_directory=tmp_path,
    )
    with pytest.raises(TypeError, match="positional"):
        signature.bind(
            None, "k", "https://x.test/v1", "m", 0.1, 100, lambda *a: True,
            working_directory=tmp_path,
        )


def test_build_compat_transport_still_takes_all_eight(tmp_path):
    """The migration surface outlives the kwargs it migrates from."""
    from agentao.embedding.compat import build_compat_transport

    seen = []
    transport = build_compat_transport(
        **{name: (lambda *a, **k: seen.append(a)) for name in LEGACY_CALLBACKS},
    )
    agent = _agentao(working_directory=tmp_path, transport=transport)

    assert agent.transport is transport


def test_the_agent_carries_none_of_the_removed_attributes(tmp_path):
    agent = _agentao(working_directory=tmp_path)
    removed = LEGACY_CALLBACKS + (
        "_has_thinking_handler",
        "_replay_recorder",
        "_replay_adapter",
        "_host_replay_sink",
        "_replay_config",
        "_latest_session_summary_id",
        "_emit_context_compressed",
        "_emit_session_summary_if_new",
    )
    try:
        assert [n for n in removed if hasattr(agent, n)] == []
        # The public replay methods are live API, and were never on the list.
        for name in ("start_replay", "end_replay", "reload_replay_config"):
            assert callable(getattr(agent, name))
    finally:
        agent.close()


def test_tool_runner_no_longer_swallows_the_four_ignored_kwargs():
    from agentao.runtime.tool_runner import ToolRunner
    from agentao.tools.base import ToolRegistry
    from agentao.transport import NullTransport

    for name in (
        "confirmation_callback", "step_callback",
        "output_callback", "tool_complete_callback",
    ):
        with pytest.raises(TypeError, match=name):
            ToolRunner(ToolRegistry(), None, NullTransport(), Mock(), **{name: None})


# ---------------------------------------------------------------------------
# embedding.sessions — ``project_root`` is required, and ``None`` is not cwd
# ---------------------------------------------------------------------------

_MESSAGES = [{"role": "user", "content": "hello"}]

#: Exactly the three things ``persist_agent_session`` reads. Not a ``Mock``:
#: one answers any attribute, and its ``get_active_skills().keys()`` is not
#: iterable — a ``TypeError`` of its own, raised before the one under test.
_FAKE_AGENT = SimpleNamespace(
    messages=_MESSAGES,
    get_current_model=lambda: "m",
    skill_manager=SimpleNamespace(get_active_skills=lambda: {}),
)

_ENTRY_POINTS = {
    "save_session": lambda **kw: sessions.save_session(_MESSAGES, "m", **kw),
    "persist_agent_session": lambda **kw: sessions.persist_agent_session(
        _FAKE_AGENT, **kw,
    ),
    "load_session_record": lambda **kw: sessions.load_session_record(**kw),
    "load_session": lambda **kw: sessions.load_session(**kw),
    "list_sessions": lambda **kw: sessions.list_sessions(**kw),
    "delete_session": lambda **kw: sessions.delete_session("some-id", **kw),
    "delete_all_sessions": lambda **kw: sessions.delete_all_sessions(**kw),
}


@pytest.fixture
def cwd_with_a_session(tmp_path, monkeypatch):
    """A process cwd that holds a session the old fallback would have found."""
    cwd = tmp_path / "somewhere-else"
    cwd.mkdir()
    path, _sid = sessions.save_session(_MESSAGES, "m", project_root=cwd)
    monkeypatch.chdir(cwd)
    return path


@pytest.mark.parametrize("name", sorted(_ENTRY_POINTS))
@pytest.mark.parametrize("how", ["omitted", "explicit-none"])
def test_every_session_entry_point_refuses_a_missing_project_root(
    cwd_with_a_session, name, how,
):
    """Omitted **and** ``None`` — the second is the one signatures cannot say.

    ``project_root=None`` used to mean the process cwd. With a session
    sitting in the cwd, a fallback that survived anywhere would make ``load``
    succeed and ``delete_all`` delete; the file is checked afterwards.
    """
    kwargs = {} if how == "omitted" else {"project_root": None}

    with pytest.raises(TypeError, match="project_root"):
        _ENTRY_POINTS[name](**kwargs)

    assert cwd_with_a_session.exists()
    assert len(list(cwd_with_a_session.parent.glob("*.json"))) == 1


def test_the_session_entry_points_still_work_with_a_root(tmp_path):
    _path, sid = sessions.save_session(_MESSAGES, "m", project_root=tmp_path)

    assert (tmp_path / ".agentao" / "sessions").is_dir()
    assert sessions.load_session(sid, project_root=tmp_path)[0] == _MESSAGES
    assert [s["session_id"] for s in sessions.list_sessions(tmp_path)] == [sid]
    assert sessions.delete_session(sid, tmp_path) is True
    assert sessions.list_sessions(project_root=Path(tmp_path)) == []
