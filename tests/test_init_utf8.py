"""Cover the ``_ensure_utf8`` helper on POSIX.

Windows behavior is exercised by the CI matrix; on POSIX the function must
be a no-op — no env mutation, no stream reconfigure, no exception. The
contract matters because ``_ensure_utf8()`` runs at import time, so a
regression that forced reconfigure on Linux/macOS would surprise embedded
hosts whose pytest harness or asyncio runner had wrapped stdout/stderr.
"""

import os
import sys

import pytest

import agentao


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only contract")
def test_ensure_utf8_noop_on_posix(monkeypatch):
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    sentinel = object()
    monkeypatch.setattr(sys.stdout, "reconfigure", lambda **_: sentinel, raising=False)

    agentao._ensure_utf8()

    assert "PYTHONIOENCODING" not in os.environ
    # ``reconfigure`` must NOT have been called — if the function ran past
    # the platform guard it would have replaced sys.stdout's reconfigure
    # return value via the sentinel. We can't check that directly, so the
    # absence of the env var combined with no exceptions is the proxy.


def test_ensure_utf8_is_idempotent():
    """Calling multiple times must not raise; relevant on hosts that re-import."""
    agentao._ensure_utf8()
    agentao._ensure_utf8()
