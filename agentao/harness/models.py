"""Deprecated alias — see :mod:`agentao.host.models`.

``HarnessEvent`` is kept as an alias for the new :data:`HostEvent` to
unblock the existing ``from agentao.harness.models import HarnessEvent``
import path until 0.5.0.
"""

from agentao.host.models import *  # noqa: F401,F403
from agentao.host.models import HostEvent as _HostEvent, __all__ as _host_all

HarnessEvent = _HostEvent

__all__ = list(_host_all) + ["HarnessEvent"]
