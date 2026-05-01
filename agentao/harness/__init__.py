"""Deprecated alias for :mod:`agentao.host`.

The host-facing contract package was renamed from ``agentao.harness`` to
``agentao.host`` in 0.4.2, together with its public type and function
names. The new names reflect what the package actually is — the surface
a host application talks to — rather than the runtime it surrounds
(which the design doc still refers to as the "harness" embedded inside
the host).

Renamed in 0.4.2:

==========================================  ============================
old (deprecated, still importable here)     new (canonical, ``agentao.host``)
==========================================  ============================
``HarnessEvent``                            ``HostEvent``
``export_harness_event_json_schema``        ``export_host_event_json_schema``
``export_harness_acp_json_schema``          ``export_host_acp_json_schema``
``docs/schema/harness.events.v1.json``      ``docs/schema/host.events.v1.json``
``docs/schema/harness.acp.v1.json``         ``docs/schema/host.acp.v1.json``
==========================================  ============================

This module re-exports the new names from :mod:`agentao.host` and
binds the old names to the same objects, so existing imports keep
working with a one-time DeprecationWarning. The whole alias surface is
scheduled for removal in 0.5.0.

Migrate with a literal find/replace::

    from agentao.harness import HarnessEvent           # old
    from agentao.host    import HostEvent              # new
"""

import warnings as _warnings

_warnings.warn(
    "agentao.harness was renamed to agentao.host in 0.4.2 "
    "(HarnessEvent → HostEvent, export_harness_* → export_host_*, "
    "schema files renamed). The alias will be removed in 0.5.0. "
    "Replace `from agentao.harness ...` with `from agentao.host ...` "
    "and update the symbol names accordingly.",
    DeprecationWarning,
    stacklevel=2,
)

from agentao.host import *  # noqa: E402,F401,F403
from agentao.host import (  # noqa: E402
    HostEvent as _HostEvent,
    __all__ as _host_all,
)
from agentao.host.schema import (  # noqa: E402
    export_host_acp_json_schema as _export_host_acp_json_schema,
    export_host_event_json_schema as _export_host_event_json_schema,
)

# Old symbol names → new objects. Keep these aliases for the lifetime
# of the ``agentao.harness`` shim (i.e. until 0.5.0); they ship and die
# together.
HarnessEvent = _HostEvent
export_harness_event_json_schema = _export_host_event_json_schema
export_harness_acp_json_schema = _export_host_acp_json_schema

__all__ = list(_host_all) + [
    "HarnessEvent",
    "export_harness_event_json_schema",
    "export_harness_acp_json_schema",
]
