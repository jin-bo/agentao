"""``tools/list`` pagination and its bounds.

Design: ``docs/design/mcp-tool-list-pagination.md``.

Before this, ``McpClient`` issued exactly one ``list_tools()`` and took
``.tools``, so every tool a paginating server exposed past page 1 was invisible
to the model with no error anywhere. These tests pin the loop and the four
bounds that make driving a peer-controlled loop safe.

Every page here is a **real** ``mcp.types.ListToolsResult`` (via
``tests.support.mcp.tools_result``). A ``MagicMock`` would be actively wrong:
the cursor is read through ``_compat.field``, whose tail case is a ``hasattr``
probe, and a mock answers that for any name — yielding a truthy mock cursor and
a loop no real server could produce. See ``tests/support/mcp.py``.
"""

import pytest

from agentao.mcp.client import (
    _MAX_CURSOR_BYTES,
    _MAX_TOOL_PAGES,
    _MAX_TOOLS,
    McpCatalogError,
    McpClient,
    ServerStatus,
)
from tests.support.mcp import (
    FakeMcpServer,
    client_against,
    handshake_result,
    paging_session,
    run_async,
    tools_result,
)


def _client(session) -> McpClient:
    client = McpClient("svr", {"command": "echo"})
    client._session = session
    return client


def _collect(session):
    return run_async(_client(session)._list_all_tools())


# ---------------------------------------------------------------------------
# The common path must not change
# ---------------------------------------------------------------------------


def test_single_page_issues_exactly_one_call_with_params_none():
    """The no-regression pin: a single-page server sees byte-identical traffic.

    ``params=None`` is the SDK default, so this is the same request the
    pre-pagination ``list_tools()`` sent — not merely an equivalent one. That
    property is what makes the change safe to land without a staged rollout.
    """
    calls = []
    tools = _collect(paging_session([tools_result(["a", "b"])], calls=calls))

    assert [t.name for t in tools] == ["a", "b"]
    assert calls == [None]


def test_multi_page_collects_every_page_in_order():
    calls = []
    session = paging_session(
        [
            tools_result(["a"], next_cursor="c1"),
            tools_result(["b"], next_cursor="c2"),
            tools_result(["c"]),
        ],
        calls=calls,
    )

    tools = _collect(session)

    assert [t.name for t in tools] == ["a", "b", "c"]
    # First page unparameterized, each later page carrying the prior cursor.
    assert calls[0] is None
    assert [p.cursor for p in calls[1:]] == ["c1", "c2"]


# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------


def test_empty_string_cursor_is_a_cursor_not_an_absence():
    """``""`` is a legal MCP cursor; ``if cursor`` would silently re-fetch page 1.

    Without this test the truthiness bug is invisible — a suite that only ever
    uses non-empty cursors passes either way. The observable symptom of the bug
    is not a hang (``""`` is in ``seen_cursors`` by then, so the repeated-cursor
    guard trips on the next pass) but a *wrong verdict*: a compliant server
    reported as having repeated a cursor, and under the fail-closed policy
    dropped entirely.
    """
    calls = []
    session = paging_session(
        [tools_result(["a"], next_cursor=""), tools_result(["b"])], calls=calls
    )

    tools = _collect(session)

    assert [t.name for t in tools] == ["a", "b"]
    # The second request must carry the empty cursor, not fall back to None.
    assert calls[0] is None
    assert calls[1] is not None and calls[1].cursor == ""


def test_repeated_cursor_is_rejected():
    """A server stuck on one cursor — modelled directly, not via a fixture.

    ``paging_session`` keys pages by the cursor that fetches them and rejects
    anything else, so it deliberately *cannot* express a server that hands back
    the same cursor forever. That is a misbehaving peer, not a well-formed
    fixture, so it gets its own fake.
    """

    class _Stuck:
        async def list_tools(self, *, params=None):
            return tools_result(["a"], next_cursor="same")

    with pytest.raises(McpCatalogError, match="repeated tools/list pagination cursor"):
        _collect(_Stuck())


def test_oversized_cursor_is_rejected_by_utf8_bytes_not_characters():
    """The cap is a byte budget — a multibyte cursor must not get 4x the room.

    This cursor is deliberately sized to **pass** a ``len()`` check and fail the
    byte check, so an implementation counting characters stays green on every
    other test and fails only here.
    """
    # 3 bytes per char in UTF-8: under the cap by character count, over it by
    # bytes.
    cursor = "中" * (_MAX_CURSOR_BYTES // 3 + 1)
    assert len(cursor) < _MAX_CURSOR_BYTES < len(cursor.encode("utf-8"))

    session = paging_session([tools_result(["a"], next_cursor=cursor)])

    with pytest.raises(McpCatalogError, match="cursor larger than"):
        _collect(session)


def test_single_page_over_the_item_cap_is_rejected():
    """One page may itself exceed the catalog cap.

    Asserts the raise and nothing else. This test **cannot** distinguish
    check-before-``extend`` from check-after: ``tools`` is a local accumulator
    and ``self._tools`` is assigned only on success, so "nothing was registered"
    holds for either ordering. The ordering is a code-review invariant, not a
    testable one — do not add machinery chasing it.
    """
    session = paging_session([tools_result([f"t{i}" for i in range(_MAX_TOOLS + 1)])])

    with pytest.raises(McpCatalogError, match=f"exceeded the {_MAX_TOOLS}-tool"):
        _collect(session)


def test_item_cap_counts_across_pages():
    half = _MAX_TOOLS // 2 + 1
    session = paging_session(
        [
            tools_result([f"a{i}" for i in range(half)], next_cursor="c1"),
            tools_result([f"b{i}" for i in range(half)], next_cursor="c2"),
        ]
    )

    with pytest.raises(McpCatalogError, match=f"exceeded the {_MAX_TOOLS}-tool"):
        _collect(session)


def test_page_cap_is_enforced():
    """A server that always returns a fresh cursor is bounded by page count."""

    class _Endless:
        def __init__(self):
            self._i = 0

        async def list_tools(self, *, params=None):
            self._i += 1
            return tools_result([f"t{self._i}"], next_cursor=f"c{self._i}")

    with pytest.raises(McpCatalogError, match=f"exceeded {_MAX_TOOL_PAGES} pages"):
        _collect(_Endless())


# ---------------------------------------------------------------------------
# The cross-SDK-major contract, over a real wire
# ---------------------------------------------------------------------------


def test_pagination_round_trips_a_real_client_session_on_either_major():
    """The `params=` contract, pinned by the SDK itself rather than by a fake.

    Every other test here injects a fake session whose ``list_tools(*, params)``
    signature *this file* wrote — so if a supported SDK major did not accept a
    keyword ``params``, they would all still pass while every real connect
    raised ``TypeError``. And the only pre-existing real-wire ``tools/list``
    test is ``@modern_only``, i.e. skipped on both mcp 1.x CI cells, which is
    exactly where that risk lives.

    So this drives a genuine ``ClientSession`` over the SDK's in-memory streams
    and answers on the wire, asserting the cursor agentao asked for arrived as
    the server sees it. Deliberately **not** ``@modern_only``: the handshake era
    is the common path and the one the floor cell exercises.
    """
    pages = {
        None: {"tools": [], "nextCursor": "page2"},
        "page2": {"tools": [], "nextCursor": "page3"},
        "page3": {"tools": []},
    }

    def tools_list(params):
        cursor = (params or {}).get("cursor")
        return pages[cursor]

    server = FakeMcpServer(
        {"initialize": handshake_result(), "tools/list": tools_list}
    )

    async def run():
        async with client_against(server) as client:
            return await client._handshake()

    assert run_async(run()) == []

    # Three tools/list round-trips, and the cursors arrived over the wire under
    # whatever key the installed major serializes them as.
    assert server.methods == ["initialize", "tools/list", "tools/list", "tools/list"]
    sent = [p.get("cursor") if p else None for p in server.params[1:]]
    assert sent == [None, "page2", "page3"]


# ---------------------------------------------------------------------------
# Failure isolation
# ---------------------------------------------------------------------------


def test_a_bounds_violation_fails_only_that_server():
    """One offending server goes ERROR; its siblings still register their tools.

    **What this does and does not pin.** It pins that a catalog bound is scoped
    to the server that tripped it — the siblings reach ``CONNECTED`` with their
    tools intact, and the failure is legible as ``status``/``error_message``
    rather than as a raised exception. It does *not* pin the ``gather``-level
    isolation in ``_connect_all_async``: ``connect()`` swallows every exception
    into ``status=ERROR`` without re-raising, so these assertions would hold
    just as well for a sequential loop, or with ``return_exceptions=True``
    removed. Do not read this test as protecting that structure — if
    ``_connect_all_async`` ever moves to a TaskGroup that cancels siblings on
    first error, the guard would have to be the *absence* of a raise reaching
    ``connect()``, which is a different test.
    """
    from unittest.mock import patch

    from agentao.mcp.client import McpClientManager
    from tests.support.mcp import initialize_result

    async def fake_connect_stdio(self):
        server_name = self.name

        class _Session:
            async def initialize(self):
                return initialize_result()

            async def list_tools(self, *, params=None):
                if server_name == "bad":
                    return tools_result(["x"], next_cursor="loop")
                return tools_result([f"ok-{server_name}"])

        self._session = _Session()

    manager = McpClientManager(
        {name: {"command": "echo"} for name in ("good1", "bad", "good2")}
    )
    try:
        with patch.object(McpClient, "_connect_stdio", fake_connect_stdio):
            manager.connect_all()

        bad = manager.get_client("bad")
        assert bad.status is ServerStatus.ERROR
        assert "repeated tools/list pagination cursor" in (bad.error_message or "")
        assert bad.tools == []

        for name in ("good1", "good2"):
            good = manager.get_client(name)
            assert good.status is ServerStatus.CONNECTED
            assert [t.name for t in good.tools] == [f"ok-{name}"]
    finally:
        # ``connect_all`` spins up a dedicated event loop via ``_get_loop`` and
        # only ``disconnect_all`` closes it. This is the repo's only test on the
        # real ``connect_all`` path, so without this the loop and its selector
        # self-pipe descriptors leak for the rest of the session.
        manager.disconnect_all()
