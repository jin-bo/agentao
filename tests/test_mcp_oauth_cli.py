"""The CLI side of MCP OAuth (docs/design/mcp-oauth.md §7, §8.1, §12 "CLI UI", §13.4).

The listener is real — real sockets on 127.0.0.1 — and so is the paste
reader, over a pseudo-terminal. The end-to-end test drives the PR 1 fake
authorization server through ``CliLoginUI`` with a scripted browser that
follows ``/authorize`` and then requests the redirect from the real listener.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import List, Optional
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from agentao.cli import mcp_auth
from agentao.cli.mcp_auth import LoginOutcome, handle_mcp_subcommand, needs_login_lines
from agentao.cli.mcp_login_ui import MAX_PASTE_CHARS, CliLoginUI, read_hidden_line
from agentao.cli.mcp_login_ui import browser_available as real_browser_available
from agentao.mcp.client import McpClientManager, ServerStatus
from agentao.mcp.oauth import OAuthLoginError
from agentao.mcp.oauth_store import OAuthRuntime
from tests.support.oauth_server import MCP_URL, FakeOAuthOrigin, patched

CONFIG = {"url": MCP_URL, "timeout": 10}
AUTH_URL = "https://as.example/authorize?client_id=c&state=the-state&code_challenge=x"

_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(url: str) -> int:
    try:
        with _NO_PROXY.open(url, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def callback_url(ui: CliLoginUI, **params: str) -> str:
    return f"http://127.0.0.1:{ui.port}{ui.path}?{urlencode(params)}"


def browser_ok(url: str) -> bool:
    return True


def never_read(prompt: str, stop: threading.Event) -> Optional[str]:
    stop.wait(30)
    return None


async def in_thread(fn, *args):
    return await asyncio.get_running_loop().run_in_executor(None, fn, *args)


def make_ui(**kwargs) -> CliLoginUI:
    lines: List[str] = []
    kwargs.setdefault("write", lines.append)
    kwargs.setdefault("launch_browser", browser_ok)
    kwargs.setdefault("read_line", never_read)
    ui = CliLoginUI("svr", **kwargs)
    ui.lines = lines  # type: ignore[attr-defined]
    return ui


@pytest.fixture(autouse=True)
def a_display(monkeypatch):
    """Headless Linux (CI) has no display, which turns the browser off; the
    scripted browser stands in for one. ``TestHeadless`` turns it back off."""
    from agentao.cli import mcp_login_ui

    monkeypatch.setattr(mcp_login_ui, "browser_available", lambda: True)


# ---------------------------------------------------------------------------
# The listener
# ---------------------------------------------------------------------------


class TestListener:
    def test_binds_loopback_on_an_os_assigned_port(self):
        async def go():
            ui = make_ui()
            uri = await ui.prepare(None)
            try:
                host, port = ui._server.sockets[0].getsockname()[:2]
                return uri, host, port
            finally:
                await ui.close()

        uri, host, port = asyncio.run(go())
        assert host == "127.0.0.1" and port > 0
        assert uri == f"http://localhost:{port}/callback/svr"

    def test_redirect_host_and_server_name_are_in_the_uri(self):
        async def go():
            ui = CliLoginUI("my server/x", redirect_host="127.0.0.1", write=lambda s: None)
            uri = await ui.prepare(None)
            await ui.close()
            return uri, ui.port

        uri, port = asyncio.run(go())
        assert uri == f"http://127.0.0.1:{port}/callback/my%20server%2Fx"

    def test_a_named_port_in_use_fails_with_its_number(self):
        busy = socket.socket()
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]

        async def go():
            ui = make_ui(callback_port=port)
            try:
                await ui.prepare(port)
            finally:
                await ui.close()

        try:
            with pytest.raises(OAuthLoginError, match=f"port {port} .*in use"):
                asyncio.run(go())
        finally:
            busy.close()

    def test_a_remembered_port_in_use_falls_back_to_a_new_one(self):
        busy = socket.socket()
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]

        async def go():
            ui = make_ui()  # no oauth.callback_port: the port came from a stored registration
            uri = await ui.prepare(port)
            await ui.close()
            return uri, ui.port

        try:
            uri, bound = asyncio.run(go())
        finally:
            busy.close()
        assert bound != port and uri == f"http://localhost:{bound}/callback/svr"

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX SO_REUSEADDR; Windows does not set it")
    def test_a_port_the_last_login_left_in_time_wait_is_reused(self):
        """The callback's server side closes first, so its port sits in TIME_WAIT."""

        async def go():
            first = make_ui()
            await first.prepare(None)
            await first.open(AUTH_URL)
            # Read to EOF before closing, so the listener's side closes first
            # and its port is the one left in TIME_WAIT.
            reader, writer = await asyncio.open_connection("127.0.0.1", first.port)
            writer.write(f"GET {first.path}?code=c&state=the-state HTTP/1.1\r\n\r\n".encode())
            await writer.drain()
            assert (await reader.read()).startswith(b"HTTP/1.1 200")
            writer.close()
            await first.wait()
            await first.close()
            second = make_ui(callback_port=first.port)
            await second.prepare(first.port)
            await second.close()
            return first.port, second.port

        first, second = asyncio.run(go())
        assert first == second

    def test_the_callback_settles_the_login(self):
        async def go():
            ui = make_ui()
            await ui.prepare(None)
            await ui.open(AUTH_URL)
            status = await in_thread(get, callback_url(ui, code="the-code", state="the-state", iss="https://as.example"))
            result = await ui.wait()
            await ui.close()
            return status, result

        status, result = asyncio.run(go())
        assert status == 200
        assert result == ("the-code", "the-state", "https://as.example")

    def test_a_foreign_state_path_or_method_does_not_settle_it(self):
        async def go():
            ui = make_ui()
            await ui.prepare(None)
            await ui.open(AUTH_URL)
            forged = await in_thread(get, callback_url(ui, code="evil", state="other"))
            stateless = await in_thread(get, callback_url(ui, code="evil"))
            wrong_path = await in_thread(get, f"http://127.0.0.1:{ui.port}/callback/other?code=c&state=the-state")
            assert not ui._result.done()
            real = await in_thread(get, callback_url(ui, code="real", state="the-state"))
            again = await in_thread(get, callback_url(ui, code="late", state="the-state"))
            result = await ui.wait()
            await ui.close()
            return forged, stateless, wrong_path, real, again, result

        forged, stateless, wrong_path, real, again, result = asyncio.run(go())
        assert (forged, stateless, wrong_path, real, again) == (400, 400, 404, 200, 400)
        assert result[0] == "real"

    def test_an_error_redirect_fails_the_login_with_its_reason(self):
        async def go():
            ui = make_ui()
            await ui.prepare(None)
            await ui.open(AUTH_URL)
            await in_thread(get, callback_url(ui, error="access_denied", error_description="user said no", state="the-state"))
            try:
                await ui.wait()
            finally:
                await ui.close()

        with pytest.raises(OAuthLoginError, match="access_denied.*user said no"):
            asyncio.run(go())

    def test_timeout_fails_and_close_shuts_the_listener(self):
        async def go():
            ui = make_ui(timeout=0.2)
            await ui.prepare(None)
            await ui.open(AUTH_URL)
            started = time.monotonic()
            with pytest.raises(OAuthLoginError, match="no authorization arrived"):
                await asyncio.wait_for(ui.wait(), 5)  # a TimeoutError here is the bug
            elapsed = time.monotonic() - started
            await ui.close()
            return ui.port, elapsed

        port, elapsed = asyncio.run(go())
        assert elapsed < 5
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=1).close()

    def test_an_oversized_request_is_dropped_without_settling(self):
        async def go():
            ui = make_ui()
            await ui.prepare(None)
            await ui.open(AUTH_URL)
            reader, writer = await asyncio.open_connection("127.0.0.1", ui.port)
            writer.write(b"GET " + ui.path.encode() + b"?code=" + b"a" * 40000 + b" HTTP/1.1\r\n\r\n")
            await writer.drain()
            await reader.read()
            writer.close()
            done = ui._result.done()
            await ui.close()
            return done

        assert asyncio.run(go()) is False


# ---------------------------------------------------------------------------
# Browser and paste
# ---------------------------------------------------------------------------


class ScriptedReader:
    """Answers the paste prompt from a list, then waits to be stopped."""

    def __init__(self, answers: List[str]):
        self.answers = list(answers)
        self.prompts = 0

    def __call__(self, prompt: str, stop: threading.Event) -> Optional[str]:
        self.prompts += 1
        if self.answers:
            return self.answers.pop(0)
        stop.wait(30)
        return None


class TestBrowserAndPaste:
    def test_the_url_is_printed_and_the_browser_opened(self):
        opened: List[str] = []

        def browser(url: str) -> bool:
            opened.append(url)
            return True

        reader = ScriptedReader([])

        async def go():
            ui = make_ui(launch_browser=browser, read_line=reader)
            await ui.prepare(None)
            await ui.open(AUTH_URL)
            await ui.close()
            return ui.lines

        lines = asyncio.run(go())
        assert any(AUTH_URL in line for line in lines)
        assert opened == [AUTH_URL]
        assert reader.prompts == 0  # no paste prompt when a browser opened

    def test_no_browser_pastes_the_redirect(self):
        reader = ScriptedReader([])

        async def go():
            ui = make_ui(open_browser=False, read_line=reader)
            await ui.prepare(None)
            reader.answers[:] = [
                "",
                "not a url at all",
                f"http://localhost:{ui.port}/callback/other?code=c&state=the-state",
                "x" * (MAX_PASTE_CHARS + 1),
                f"http://localhost:{ui.port}{ui.path}?code=pasted&state=the-state",
            ]
            await ui.open(AUTH_URL)
            result = await ui.wait()
            await ui.close()
            return result, ui.lines

        result, lines = asyncio.run(go())
        assert result == ("pasted", "the-state", None)
        assert any("not this login's redirect" in line for line in lines)
        assert any(f"longer than {MAX_PASTE_CHARS}" in line for line in lines)

    def test_a_failed_browser_falls_back_to_paste(self):
        reader = ScriptedReader([])

        async def go():
            ui = make_ui(launch_browser=lambda url: False, read_line=reader)
            await ui.prepare(None)
            reader.answers[:] = [f"http://localhost:{ui.port}{ui.path}?code=p&state=the-state"]
            await ui.open(AUTH_URL)
            result = await ui.wait()
            await ui.close()
            return result

        assert asyncio.run(go())[0] == "p"

    def test_the_listener_keeps_waiting_beside_the_paste_prompt(self):
        reader = ScriptedReader([])

        async def go():
            ui = make_ui(open_browser=False, read_line=reader)
            await ui.prepare(None)
            await ui.open(AUTH_URL)
            await in_thread(get, callback_url(ui, code="via-listener", state="the-state"))
            result = await ui.wait()
            started = time.monotonic()
            await ui.close()
            return result, time.monotonic() - started

        (code, _, _), close_s = asyncio.run(go())
        assert code == "via-listener"
        assert close_s < 0.8  # the blocked paste prompt was told to stop (close waits up to 1 s)

    def test_terminal_text_is_sanitized(self):
        async def go():
            ui = make_ui()
            await ui.prepare(None)
            await ui.open(AUTH_URL + "&x=\x1b[31mred")
            await ui.close()
            return ui.lines

        assert not any("\x1b" in line for line in asyncio.run(go()))


def settings(attrs):
    """Terminal attributes minus ``PENDIN``, which the kernel sets on a mode switch."""
    import termios

    attrs = list(attrs)
    attrs[3] &= ~getattr(termios, "PENDIN", 0)
    return attrs


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX pseudo-terminal")
class TestHiddenReader:
    def _pty(self):
        import pty

        master, slave = pty.openpty()
        return master, slave

    def test_reads_a_line_without_echo_and_restores_the_terminal(self):
        import termios

        master, slave = self._pty()
        result: List[Optional[str]] = []
        try:
            before = termios.tcgetattr(slave)
            reader = threading.Thread(
                target=lambda: result.append(read_hidden_line("URL: ", threading.Event(), fd=slave))
            )
            reader.start()
            echoed = b""
            while b"URL: " not in echoed:  # echo is off once the prompt is out
                echoed += os.read(master, 4096)
            os.write(master, b"abc\x7fd\n")
            reader.join(5)
            line = result[0]
            after = termios.tcgetattr(slave)
            time.sleep(0.05)
            echoed += os.read(master, 4096)
        finally:
            os.close(master)
            os.close(slave)
        assert line == "abd"
        assert b"abd" not in echoed and b"abc" not in echoed
        assert settings(after) == settings(before)

    def test_a_line_longer_than_the_canonical_limit_survives(self):
        master, slave = self._pty()
        url = "http://localhost:1/callback/svr?code=" + "c" * 3000
        try:
            def feed():
                for i in range(0, len(url), 500):
                    os.write(master, url[i:i + 500].encode())
                    time.sleep(0.01)
                os.write(master, b"\r")

            def drain():  # what a terminal does with output (echo, bells)
                try:
                    while os.read(master, 4096):
                        pass
                except OSError:
                    pass

            threading.Thread(target=feed, daemon=True).start()
            threading.Thread(target=drain, daemon=True).start()
            stop = threading.Event()
            threading.Timer(5, stop.set).start()  # fail, not hang, if the line never ends
            line = read_hidden_line("", stop, fd=slave)
        finally:
            os.close(master)
            os.close(slave)
        assert line == url

    def test_stop_returns_none_promptly(self):
        import termios

        master, slave = self._pty()
        stop = threading.Event()
        try:
            before = termios.tcgetattr(slave)
            threading.Timer(0.2, stop.set).start()
            started = time.monotonic()
            line = read_hidden_line("", stop, fd=slave)
            elapsed = time.monotonic() - started
            after = termios.tcgetattr(slave)
        finally:
            os.close(master)
            os.close(slave)
        assert line is None and elapsed < 2
        assert settings(after) == settings(before)


# ---------------------------------------------------------------------------
# End to end: login → tool call → logout through the real UI
# ---------------------------------------------------------------------------


@pytest.fixture
def origin():
    return FakeOAuthOrigin()


@pytest.fixture
def runtime(tmp_path):
    return OAuthRuntime(tmp_path / "mcp-oauth", token_timeout=5.0)


def scripted_browser(origin: FakeOAuthOrigin, visits: List[str]):
    """Follows ``/authorize`` on the fake, then requests the redirect for real."""

    def browser(authorization_url: str) -> bool:
        code, state = origin.authorize(authorization_url)
        redirect = parse_qs(urlsplit(authorization_url).query)["redirect_uri"][0]
        target = f"{redirect}?{urlencode({'code': code, 'state': state})}"
        visits.append(target)

        def visit() -> None:
            time.sleep(0.05)
            get(target)

        threading.Thread(target=visit, daemon=True).start()
        return True

    return browser


def test_login_tool_call_logout_end_to_end(runtime, origin):
    visits: List[str] = []
    lines: List[str] = []

    def factory(name, **kwargs):
        return CliLoginUI(
            name, **kwargs, launch_browser=scripted_browser(origin, visits), read_line=never_read
        )

    manager = McpClientManager({"svr": CONFIG}, oauth_runtime=runtime)
    with patched(origin):
        manager.connect_all()
        assert manager.get_client("svr").status == ServerStatus.NEEDS_AUTH
        assert needs_login_lines(manager.get_server_status()) == [
            "MCP server 'svr' needs login — run /mcp login svr"
        ]
        outcome = mcp_auth.login(manager, "svr", write=lines.append, ui_factory=factory)
        assert outcome is LoginOutcome.CONNECTED
        assert "ok" in manager.call_tool("svr", "echo", {})
        assert mcp_auth.logout(manager, "svr", write=lines.append) is True
        assert runtime.store.load(MCP_URL) is None
        manager.disconnect_all()
    assert len(visits) == 1 and urlsplit(visits[0]).path == "/callback/svr"
    assert urlsplit(visits[0]).hostname == "localhost"
    assert any("Logged in to 'svr' — connected, 1 tool(s)." == line for line in lines)
    assert any("Logged out of 'svr'" in line for line in lines)


def test_a_cancelled_login_closes_the_listener(runtime, origin):
    """Ctrl+C in the REPL cancels the call; ``login()``'s finally closes the UI."""
    uis: List[CliLoginUI] = []

    def factory(name, **kwargs):
        ui = CliLoginUI(name, **kwargs, launch_browser=browser_ok, read_line=never_read)
        uis.append(ui)
        return ui

    manager = McpClientManager({"svr": CONFIG}, oauth_runtime=runtime)
    real_login = manager.login

    def interrupted_login(name, ui):
        def interrupt():
            while ui.port is None:
                time.sleep(0.01)
            time.sleep(0.1)
            import _thread

            _thread.interrupt_main()

        threading.Thread(target=interrupt, daemon=True).start()
        return real_login(name, ui)

    manager.login = interrupted_login  # type: ignore[method-assign]
    lines: List[str] = []
    with patched(origin):
        outcome = mcp_auth.login(manager, "svr", write=lines.append, ui_factory=factory)
        port = uis[0].port
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            except OSError:
                break
            time.sleep(0.05)
        else:
            pytest.fail("the callback listener stayed open after the cancel")
        manager.disconnect_all()
    assert outcome is LoginOutcome.CANCELLED


# ---------------------------------------------------------------------------
# Surfaces
# ---------------------------------------------------------------------------


class TestSurfaces:
    def test_non_oauth_and_unknown_servers_are_refused_before_any_listener(self, runtime):
        lines: List[str] = []
        manager = McpClientManager(
            {"stdio": {"command": "true"}, "hdr": {"url": MCP_URL, "headers": {"Authorization": "x"}}},
            oauth_runtime=runtime,
        )
        try:
            for name in ("stdio", "hdr", "nope"):
                assert mcp_auth.login(manager, name, write=lines.append, ui_factory=pytest.fail) is LoginOutcome.FAILED
        finally:
            manager.disconnect_all()
        assert "does not use OAuth" in lines[0] and "does not use OAuth" in lines[1]
        assert "'nope' is not configured" in lines[2]

    def test_subcommand_exit_codes(self, runtime, origin):
        visits: List[str] = []
        lines: List[str] = []

        def manager_factory(plugin_dirs):
            return McpClientManager({"svr": CONFIG}, oauth_runtime=runtime)

        real_login = mcp_auth.login

        def login(manager, name, **kwargs):
            def factory(n, **kw):
                return CliLoginUI(n, **kw, launch_browser=scripted_browser(origin, visits), read_line=never_read)

            return real_login(manager, name, **kwargs, ui_factory=factory)

        ns = argparse.Namespace
        with patched(origin):
            mp = pytest.MonkeyPatch()
            mp.setattr(mcp_auth, "login", login)
            try:
                assert handle_mcp_subcommand(ns(mcp_action=None), write=lines.append, manager_factory=manager_factory) == 2
                assert handle_mcp_subcommand(ns(mcp_action="login", name="svr", no_browser=False), write=lines.append, manager_factory=manager_factory) == 0
                assert handle_mcp_subcommand(ns(mcp_action="logout", name="svr"), write=lines.append, manager_factory=manager_factory) == 0
                assert handle_mcp_subcommand(ns(mcp_action="login", name="nope", no_browser=True), write=lines.append, manager_factory=manager_factory) == 1
            finally:
                mp.undo()
        assert any("restarted to load them" in line for line in lines)

    def test_parser_and_light_routing(self, monkeypatch):
        from agentao.cli import _light

        args, extras = _light._build_parser().parse_known_args(["mcp", "login", "linear", "--no-browser"])
        assert (args.subcommand, args.mcp_action, args.name, args.no_browser, extras) == (
            "mcp", "login", "linear", True, []
        )
        seen = []
        monkeypatch.setattr(mcp_auth, "handle_mcp_subcommand", lambda a: seen.append(a) or 0)
        with pytest.raises(SystemExit) as exit_info:
            _light.run_light(["mcp", "logout", "linear"])
        assert exit_info.value.code == 0 and seen[0].mcp_action == "logout"
        assert _light.run_light(["plugin", "list"]) is False

    def test_light_entry_needs_no_cli_extras(self):
        """``agentao mcp`` runs from a bare install: no rich / prompt_toolkit import."""
        import subprocess

        script = (
            "import sys\n"
            "class Block:\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            "        if name.split('.')[0] in ('rich', 'prompt_toolkit', 'readchar', 'pygments'):\n"
            "            raise ImportError(name)\n"
            "sys.meta_path.insert(0, Block())\n"
            "sys.argv = ['agentao', 'mcp']\n"
            "import agentao.cli\n"
            "agentao.cli.entrypoint()\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=60
        )
        assert proc.returncode == 2, proc.stderr
        assert "usage: agentao mcp" in proc.stderr


# ---------------------------------------------------------------------------
# The REPL: /mcp list, /mcp login|logout, the startup line
# ---------------------------------------------------------------------------


class TestRepl:
    def _cli(self, manager):
        from types import SimpleNamespace

        from agentao.tools.base import ToolRegistry

        return SimpleNamespace(agent=SimpleNamespace(mcp_manager=manager, tools=ToolRegistry()))

    def _scripted_ui(self, monkeypatch, origin):
        from agentao.cli import mcp_login_ui

        class Scripted(CliLoginUI):
            def __init__(self, name, **kwargs):
                super().__init__(
                    name, **kwargs, launch_browser=scripted_browser(origin, []), read_line=never_read
                )

        monkeypatch.setattr(mcp_login_ui, "CliLoginUI", Scripted)

    def _run(self, cli, args):
        from agentao.cli._globals import console
        from agentao.cli.commands.mcp import handle_mcp_command

        with console.capture() as captured:
            handle_mcp_command(cli, args)
        return captured.get()

    def test_list_and_startup_line_say_needs_login(self, runtime, origin):
        from agentao.cli.ui import _print_mcp_needs_login
        from agentao.cli._globals import console

        manager = McpClientManager({"svr": CONFIG}, oauth_runtime=runtime)
        cli = self._cli(manager)
        with patched(origin):
            manager.connect_all()
            listing = self._run(cli, "list")
            with console.capture() as captured:
                _print_mcp_needs_login(cli)
            manager.disconnect_all()
        assert "needs login" in listing and "needs_auth" not in listing
        assert "MCP server 'svr' needs login — run /mcp login svr" in captured.get()

    def test_login_from_startup_needs_auth_asks_for_a_restart(self, runtime, origin, monkeypatch):
        self._scripted_ui(monkeypatch, origin)
        manager = McpClientManager({"svr": CONFIG}, oauth_runtime=runtime)
        cli = self._cli(manager)
        with patched(origin):
            manager.connect_all()
            out = self._run(cli, "login svr")
            logout = self._run(cli, "logout svr")
            manager.disconnect_all()
        assert "Logged in to 'svr' — connected, 1 tool(s)." in out
        assert "Restart agentao to load the tools of 'svr'." in out
        assert "Logged out of 'svr'" in logout

    def test_login_with_tools_registered_needs_no_restart(self, runtime, origin, monkeypatch):
        from agentao.tooling.mcp_tools import register_mcp_tools

        self._scripted_ui(monkeypatch, origin)
        manager = McpClientManager({"svr": CONFIG}, oauth_runtime=runtime)
        cli = self._cli(manager)
        cli.agent.llm = __import__("types").SimpleNamespace(logger=__import__("logging").getLogger("t"))
        with patched(origin):
            manager.connect_all()
            manager.login("svr", CliLoginUI(
                "svr", launch_browser=scripted_browser(origin, []), read_line=never_read, write=lambda s: None
            ))
            register_mcp_tools(cli.agent, manager)  # as a startup with a stored credential would
            out = self._run(cli, "login svr")
            manager.disconnect_all()
        assert "Logged in to 'svr'" in out
        assert "Restart agentao" not in out

    def test_usage_errors(self, runtime):
        manager = McpClientManager({"svr": CONFIG}, oauth_runtime=runtime)
        cli = self._cli(manager)
        try:
            assert "Usage: /mcp login" in self._run(cli, "login")
            assert "Usage: /mcp logout" in self._run(cli, "logout svr --no-browser")
            assert "Usage: /mcp login" in self._run(cli, "login a b")
        finally:
            manager.disconnect_all()
        assert "No MCP servers configured" in self._run(self._cli(None), "login svr")


# ---------------------------------------------------------------------------
# /code-review fixes
# ---------------------------------------------------------------------------


class TestCodeReviewFixes:
    def test_a_mcp_parse_error_prints_once_and_exits_2(self, capsys):
        from agentao.cli import _light

        with pytest.raises(SystemExit) as exit_info:
            _light.run_light(["mcp", "login"])  # no name
        assert exit_info.value.code == 2
        assert capsys.readouterr().err.count("usage:") == 1

    def test_help_before_mcp_prints_help_and_never_logs_in(self, monkeypatch, capsys):
        from agentao.cli import _light

        monkeypatch.setattr(mcp_auth, "handle_mcp_subcommand", lambda a: pytest.fail("ran the login"))
        with pytest.raises(SystemExit) as exit_info:
            _light.run_light(["-h", "mcp", "login", "linear"])
        assert exit_info.value.code == 0
        assert "usage:" in capsys.readouterr().out

    def test_the_no_browser_message_brackets_an_ipv6_redirect_host(self):
        async def go():
            ui = make_ui(redirect_host="::1", open_browser=False)
            uri = await ui.prepare(None)
            await ui.open(AUTH_URL)
            await ui.close()
            return uri, ui.lines

        uri, lines = asyncio.run(go())
        assert uri.startswith("http://[::1]:")
        assert any(f"{uri}?code=" in line for line in lines)
        assert not any("http://::1:" in line for line in lines)

    def test_ctrl_c_at_the_windows_paste_prompt_interrupts_the_caller_not_the_loop(self, monkeypatch):
        """A KeyboardInterrupt raised on the reader thread would reach a task on
        the MCP loop and escape ``run_forever``; the reader hands it to the main
        thread instead and returns ``None``."""
        import _thread
        import types

        from agentao.cli import mcp_login_ui

        keys = iter("ab\x03")
        fake = types.SimpleNamespace(kbhit=lambda: True, getwch=lambda: next(keys))
        monkeypatch.setitem(sys.modules, "msvcrt", fake)
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
        interrupted = []
        monkeypatch.setattr(_thread, "interrupt_main", lambda *a: interrupted.append(True))
        try:
            line = mcp_login_ui._read_hidden_line_windows("", threading.Event())
        except KeyboardInterrupt:
            pytest.fail("Ctrl+C raised on the reader thread")
        assert line is None and interrupted == [True]


# ---------------------------------------------------------------------------
# Codex review, round 1
# ---------------------------------------------------------------------------


class TestCodexRound1:
    def test_headless_linux_skips_the_browser_and_offers_the_paste(self, monkeypatch):
        from agentao.cli import mcp_login_ui

        monkeypatch.setattr(mcp_login_ui.sys, "platform", "linux")
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.setattr(mcp_login_ui, "browser_available", real_browser_available)
        assert real_browser_available() is False
        reader = ScriptedReader([])

        async def go():
            ui = make_ui(launch_browser=lambda url: pytest.fail("opened a browser"), read_line=reader)
            await ui.prepare(None)
            reader.answers[:] = [f"http://localhost:{ui.port}{ui.path}?code=p&state=the-state"]
            await ui.open(AUTH_URL)
            result = await asyncio.wait_for(ui.wait(), 5)
            await ui.close()
            return result

        assert asyncio.run(go())[0] == "p"
        monkeypatch.setenv("DISPLAY", ":0")
        assert real_browser_available() is True

    def test_too_many_query_fields_keep_the_paste_prompt_alive(self):
        reader = ScriptedReader([])

        async def go():
            ui = make_ui(open_browser=False, read_line=reader)
            await ui.prepare(None)
            flood = "&".join(f"f{i}=x" for i in range(40))
            reader.answers[:] = [
                f"http://localhost:{ui.port}{ui.path}?{flood}",
                f"http://localhost:{ui.port}{ui.path}?code=after&state=the-state",
            ]
            await ui.open(AUTH_URL)
            flood_status = await in_thread(get, f"http://127.0.0.1:{ui.port}{ui.path}?{flood}")
            result = await asyncio.wait_for(ui.wait(), 5)
            await ui.close()
            return flood_status, result, ui.lines

        flood_status, result, lines = asyncio.run(go())
        assert flood_status == 400
        assert result[0] == "after"
        assert any("too many query parameters" in line for line in lines)

    def test_repl_login_output_strips_terminal_escapes(self, runtime, origin, monkeypatch):
        from types import SimpleNamespace

        from agentao.cli._globals import console
        from agentao.cli.commands.mcp import handle_mcp_command
        from agentao.tools.base import ToolRegistry

        def hostile(manager, name, **kwargs):
            kwargs["write"]("Login to 'svr' failed: refused (\x1b]0;pwned\x07\x1b[2Jboom)")
            return LoginOutcome.FAILED

        monkeypatch.setattr(mcp_auth, "login", hostile)
        cli = SimpleNamespace(agent=SimpleNamespace(mcp_manager=object(), tools=ToolRegistry()))
        with console.capture() as captured:
            handle_mcp_command(cli, "login svr")
        out = captured.get()
        assert "boom" in out and "\x1b" not in out and "\x07" not in out


# ---------------------------------------------------------------------------
# Codex review, round 2
# ---------------------------------------------------------------------------


class TestCodexRound2:
    def test_a_cancelled_login_returns_only_after_the_paste_prompt_let_go(self, runtime, origin):
        """The REPL's next prompt must not start while the reader owns the terminal."""
        reader_returned: List[float] = []

        def slow_to_let_go(prompt: str, stop: threading.Event) -> Optional[str]:
            stop.wait(30)
            time.sleep(0.5)  # restoring the terminal, say
            reader_returned.append(time.monotonic())
            return None

        uis: List[CliLoginUI] = []

        def factory(name, **kwargs):
            kwargs["open_browser"] = False
            ui = CliLoginUI(name, **kwargs, launch_browser=browser_ok, read_line=slow_to_let_go)
            uis.append(ui)
            return ui

        manager = McpClientManager({"svr": CONFIG}, oauth_runtime=runtime)
        real_login = manager.login

        def interrupted_login(name, ui):
            def interrupt():
                while ui._paste_task is None:
                    time.sleep(0.01)
                time.sleep(0.1)
                import _thread

                _thread.interrupt_main()

            threading.Thread(target=interrupt, daemon=True).start()
            return real_login(name, ui)

        manager.login = interrupted_login  # type: ignore[method-assign]
        with patched(origin):
            outcome = mcp_auth.login(manager, "svr", write=lambda s: None, ui_factory=factory)
            returned = time.monotonic()
            manager.disconnect_all()
        assert outcome is LoginOutcome.CANCELLED
        assert reader_returned and reader_returned[0] <= returned
        assert uis[0].closed.is_set()

    def test_the_shell_command_finds_a_plugins_mcp_server(self, tmp_path, monkeypatch):
        import json

        plugin = tmp_path / "plug"
        plugin.mkdir()
        (plugin / "plugin.json").write_text(json.dumps({"name": "plug"}), encoding="utf-8")
        (plugin / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"linear": {"url": "https://mcp.linear.example/mcp"}}}),
            encoding="utf-8",
        )
        project = tmp_path / "project"
        project.mkdir()
        monkeypatch.chdir(project)
        monkeypatch.setattr("agentao.paths.user_root", lambda: tmp_path / "home")
        # Loading .env would climb out of tmp_path and fill this worker's os.environ.
        monkeypatch.setattr("agentao._env.safe_load_dotenv", lambda *a, **k: None)
        assert mcp_auth.plugin_mcp_servers({}, [plugin]) == {
            "linear": {"url": "https://mcp.linear.example/mcp"}
        }
        args, _ = __import__("agentao.cli._light", fromlist=["_build_parser"])._build_parser().parse_known_args(
            ["mcp", "--plugin-dir", str(plugin), "logout", "linear"]
        )
        seen = {}

        def factory(plugin_dirs):
            seen["dirs"] = plugin_dirs
            manager = mcp_auth._manager_from_config(plugin_dirs)
            seen["servers"] = set(manager.server_configs)
            return manager

        lines: List[str] = []
        assert handle_mcp_subcommand(args, write=lines.append, manager_factory=factory) == 0
        assert [str(d) for d in seen["dirs"]] == [str(plugin)]
        assert "linear" in seen["servers"]
        assert "'linear' had no stored credential." in lines

    @pytest.mark.skipif(not socket.has_ipv6, reason="no IPv6")
    def test_an_ipv6_redirect_host_gets_an_ipv6_listener(self):
        async def go():
            ui = make_ui(redirect_host="::1")
            try:
                uri = await ui.prepare(None)
            except OAuthLoginError as e:
                if "could not open" in str(e):
                    pytest.skip(f"no IPv6 loopback here: {e}")
                raise
            await ui.open(AUTH_URL)
            status = await in_thread(get, f"{uri}?code=v6&state=the-state")
            result = await asyncio.wait_for(ui.wait(), 5)
            await ui.close()
            return uri, status, result

        uri, status, result = asyncio.run(go())
        assert uri.startswith("http://[::1]:")
        assert status == 200 and result[0] == "v6"

    @pytest.mark.parametrize("host", ["example.com", "10.0.0.1", "0.0.0.0", "::"])
    def test_a_non_loopback_redirect_host_is_refused_before_the_login_starts(self, host):
        async def go():
            ui = make_ui(redirect_host=host)
            try:
                await ui.prepare(None)
            finally:
                await ui.close()

        with pytest.raises(OAuthLoginError, match="loopback"):
            asyncio.run(go())


# ---------------------------------------------------------------------------
# Codex review, round 3
# ---------------------------------------------------------------------------


class TestCodexRound3:
    def test_a_callback_before_the_authorization_starts_is_refused(self):
        """Between ``prepare`` and ``open`` (OAuth discovery), no redirect can be ours."""

        async def go():
            ui = make_ui()
            await ui.prepare(None)
            stale = await in_thread(get, callback_url(ui, code="stale", state="anything"))
            stateless = await in_thread(get, callback_url(ui, code="stale"))
            settled_early = ui._result.done()
            await ui.open(AUTH_URL)
            real = await in_thread(get, callback_url(ui, code="real", state="the-state"))
            result = await asyncio.wait_for(ui.wait(), 5)
            await ui.close()
            return stale, stateless, settled_early, real, result

        stale, stateless, settled_early, real, result = asyncio.run(go())
        assert (stale, stateless, real) == (400, 400, 200)
        assert settled_early is False and result[0] == "real"


# ---------------------------------------------------------------------------
# Codex review, round 4
# ---------------------------------------------------------------------------


class TestCodexRound4:
    @pytest.mark.parametrize("config", [{"type": "htp", "url": MCP_URL}, {"type": "http"}])
    def test_an_invalid_transport_is_reported_not_raised(self, runtime, config):
        lines: List[str] = []
        manager = McpClientManager({"bad": config}, oauth_runtime=runtime)
        try:
            assert mcp_auth.login(manager, "bad", write=lines.append, ui_factory=pytest.fail) is LoginOutcome.FAILED
            assert mcp_auth.logout(manager, "bad", write=lines.append) is False
        finally:
            manager.disconnect_all()
        assert len(lines) == 2 and all(line.startswith("MCP server 'bad': ") for line in lines)
