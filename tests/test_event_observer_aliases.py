"""``add_event_observer`` / ``remove_event_observer`` are deprecated aliases.

They still register on the same stream as the ``host_event`` names, and
warn at the caller's line so a host can find the call to move.
"""

from __future__ import annotations

import logging

import pytest

from agentao import Agentao


@pytest.fixture
def agent(tmp_path):
    agent = Agentao(
        working_directory=tmp_path,
        api_key="k",
        base_url="https://test.local/v1",
        model="m",
        logger=logging.getLogger("test_event_observer_aliases"),
    )
    yield agent
    agent.close()


def _observer(event) -> None:
    pass


def test_add_event_observer_warns_and_registers(agent):
    with pytest.warns(DeprecationWarning, match="add_host_event_observer") as rec:
        handle = agent.add_event_observer(_observer)
    assert rec[0].filename == __file__
    assert handle is _observer
    assert agent.remove_host_event_observer(_observer) is True


def test_remove_event_observer_warns_and_detaches(agent):
    agent.add_host_event_observer(_observer)
    with pytest.warns(DeprecationWarning, match="remove_host_event_observer") as rec:
        assert agent.remove_event_observer(_observer) is True
    assert rec[0].filename == __file__
    assert agent.remove_host_event_observer(_observer) is False


def test_the_host_event_names_do_not_warn(agent, recwarn):
    agent.add_host_event_observer(_observer)
    agent.remove_host_event_observer(_observer)
    assert not [w for w in recwarn if w.category is DeprecationWarning]
