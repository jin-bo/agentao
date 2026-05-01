"""Deprecated alias — see :mod:`agentao.host.projection`.

``HarnessToolEmitter`` / ``HarnessPermissionEmitter`` /
``HarnessSubagentEmitter`` are kept as aliases for the renamed
``Host*Emitter`` classes until 0.5.0.
"""

from agentao.host.projection import *  # noqa: F401,F403
from agentao.host.projection import (
    HostPermissionEmitter as _HostPermissionEmitter,
    HostSubagentEmitter as _HostSubagentEmitter,
    HostToolEmitter as _HostToolEmitter,
    __all__ as _host_all,
)

HarnessToolEmitter = _HostToolEmitter
HarnessPermissionEmitter = _HostPermissionEmitter
HarnessSubagentEmitter = _HostSubagentEmitter

__all__ = list(_host_all) + [
    "HarnessToolEmitter",
    "HarnessPermissionEmitter",
    "HarnessSubagentEmitter",
]
