"""The CLI's OAuth login UI for MCP servers (docs/design/mcp-oauth.md §7).

Implements :class:`agentao.mcp.oauth.OAuthLoginUI`: the harness runs the
authorization-code flow, and this module is the part that reaches a person —
a callback listener for the redirect, a browser to open, and a paste prompt for
when there is no browser.

* The listener binds loopback only — ``127.0.0.1``, or the loopback address
  ``oauth.redirect_host`` names — on an OS-assigned port unless the server's ``oauth.callback_port`` (or its stored registration) names one. A
  named port that is taken fails the login with a message naming it; the port
  is never assumed to belong to another agentao.
* The callback path is ``/callback/<server>``, so two logins running at once
  cannot receive each other's code.
* With no browser (``--no-browser``, ``webbrowser.open`` failing, a Linux
  session with no display), the URL is printed and the redirect URL can be
  pasted back — read without echo, size-bounded — while the listener keeps
  waiting, so an SSH port forward still works.
* After 300 s without a redirect the login fails; :meth:`close` shuts the
  listener and the paste prompt on every exit.

Standard library only, like ``agentao --login``: ``agentao mcp login`` runs
from a bare ``pip install agentao``.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote, urlsplit

from ..mcp.oauth import OAuthLoginError
from ..security.terminal_text import sanitize_terminal_text

#: How long to wait for the redirect once the browser has been sent.
CALLBACK_TIMEOUT_S = 300.0

#: Upper bound on a pasted redirect URL, and on a callback request's head.
MAX_PASTE_CHARS = 8192
_MAX_REQUEST_BYTES = 16384

#: How long one callback connection may take to send its request.
_REQUEST_READ_S = 10.0

#: How often the paste reader checks whether it has been told to stop.
_POLL_S = 0.1

_DONE_PAGE = (
    "<!doctype html><meta charset=utf-8><title>agentao</title>"
    "<p>{message}</p><p>You can close this window and return to agentao.</p>"
)

Writer = Callable[[str], None]
LineReader = Callable[[str, threading.Event], Optional[str]]


def _stderr(text: str) -> None:
    sys.stderr.write(text + "\n")
    sys.stderr.flush()


def callback_path(server_name: str) -> str:
    return "/callback/" + quote(server_name, safe="")


def browser_available() -> bool:
    """False where ``webbrowser`` would pick a console browser or nothing.

    On Linux with no display, ``webbrowser`` falls back to text browsers
    (``www-browser``, ``lynx``) that take over this very terminal.
    """
    if sys.platform.startswith("linux"):
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return True


def bind_address(redirect_host: str) -> str:
    """The loopback address to listen on for ``redirect_host``.

    ``localhost`` listens on ``127.0.0.1`` (§7: never every interface). A
    loopback literal — ``127.0.0.1``, ``::1`` — listens on itself, so the
    browser's redirect to it arrives. Anything else is refused before the
    login starts: a listener the redirect cannot reach would only time out.
    """
    import ipaddress

    host = redirect_host.strip().strip("[]")
    if host.lower() == "localhost":
        return "127.0.0.1"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is None or not address.is_loopback:
        raise OAuthLoginError(
            f"'oauth.redirect_host' must be localhost or a loopback address "
            f"(127.0.0.1, ::1), got {redirect_host!r}"
        )
    return str(address)


def _open_browser(url: str) -> bool:
    import webbrowser

    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False


def read_hidden_line(
    prompt: str, stop: threading.Event, *, fd: Optional[int] = None
) -> Optional[str]:
    """Read one line from the terminal without echo; ``None`` if stopped or no terminal.

    Polls, so :meth:`CliLoginUI.close` can stop it when the redirect arrives
    through the listener instead. Reads byte by byte in non-canonical mode: a
    canonical-mode line is capped by the terminal driver (1024 bytes on
    macOS), shorter than a real redirect URL. At most :data:`MAX_PASTE_CHARS`
    + 1 characters are kept; a longer answer comes back that long, and the
    caller refuses it.
    """
    if sys.platform == "win32":  # pragma: no cover - exercised on Windows only
        return _read_hidden_line_windows(prompt, stop)
    import select
    import termios

    own = fd is None
    if fd is None:
        try:
            fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
        except OSError:
            return None
    try:
        try:
            saved = termios.tcgetattr(fd)
        except termios.error:
            return None
        mode = termios.tcgetattr(fd)
        mode[3] &= ~(termios.ECHO | termios.ICANON)
        mode[6][termios.VMIN] = 1
        mode[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, mode)
        try:
            os.write(fd, prompt.encode("utf-8", "replace"))
            buf = bytearray()
            while not stop.is_set():
                ready, _, _ = select.select([fd], [], [], _POLL_S)
                if not ready:
                    continue
                chunk = os.read(fd, 4096)
                if not chunk:
                    return None
                for byte in chunk:
                    if byte in (0x0A, 0x0D):
                        return buf.decode("utf-8", "replace")
                    if byte in (0x7F, 0x08):
                        if buf:
                            buf.pop()
                    elif byte == 0x15:  # Ctrl+U
                        buf.clear()
                    elif byte == 0x04 and not buf:  # Ctrl+D on an empty line
                        return None
                    elif len(buf) <= MAX_PASTE_CHARS:
                        buf.append(byte)
            return None
        finally:
            termios.tcsetattr(fd, termios.TCSANOW, saved)
            try:
                os.write(fd, b"\n")
            except OSError:
                pass
    finally:
        if own:
            os.close(fd)


def _read_hidden_line_windows(
    prompt: str, stop: threading.Event
) -> Optional[str]:  # pragma: no cover - Windows only
    import msvcrt
    import time

    if not sys.stdin.isatty():
        return None
    sys.stderr.write(prompt)
    sys.stderr.flush()
    chars: List[str] = []
    try:
        while not stop.is_set():
            if not msvcrt.kbhit():
                time.sleep(_POLL_S / 2)
                continue
            ch = msvcrt.getwch()
            if ch in ("\r", "\n"):
                return "".join(chars)
            if ch == "\x08":
                if chars:
                    chars.pop()
            elif ch == "\x03":
                # Not raised here: this runs on a reader thread whose result
                # an asyncio task on the MCP loop awaits, and a
                # KeyboardInterrupt re-raised there escapes ``run_forever``
                # and kills the loop thread. Interrupt the waiting caller.
                import _thread

                _thread.interrupt_main()
                return None
            elif len(chars) <= MAX_PASTE_CHARS:
                chars.append(ch)
        return None
    finally:
        sys.stderr.write("\n")
        sys.stderr.flush()


class CliLoginUI:
    """One login's listener, browser and paste prompt.

    Built per login, for one server: the callback path carries its name.
    ``callback_port`` is the server's configured ``oauth.callback_port``: that
    port, when taken, fails the login; a port only remembered from a stored
    registration falls back to an OS-assigned one. ``open_browser=False`` is
    ``--no-browser``. ``write`` receives every line
    meant for the user; ``read_line`` and ``launch_browser`` are seams for
    tests and default to the terminal and :mod:`webbrowser`.
    """

    def __init__(
        self,
        server_name: str,
        *,
        redirect_host: Optional[str] = None,
        callback_port: Optional[int] = None,
        open_browser: bool = True,
        timeout: float = CALLBACK_TIMEOUT_S,
        write: Writer = _stderr,
        read_line: LineReader = read_hidden_line,
        launch_browser: Callable[[str], bool] = _open_browser,
    ):
        self.server_name = server_name
        self.redirect_host = (redirect_host or "localhost").strip().strip("[]")
        self.callback_port = callback_port
        self.open_browser = open_browser
        self.timeout = timeout
        self._write = write
        self._read_line = read_line
        self._launch_browser = launch_browser
        self.path = callback_path(server_name)
        self.port: Optional[int] = None
        self.redirect_uri: Optional[str] = None
        self._server: Optional[asyncio.AbstractServer] = None
        self._result: Optional[asyncio.Future] = None
        self._stop = threading.Event()
        self._paste_task: Optional[asyncio.Task] = None
        self._reader_done: List[asyncio.Future] = []
        self._expected_state: Optional[str] = None
        self._authorizing = False
        #: Set once :meth:`close` has finished — listener shut, paste prompt
        #: returned and the terminal restored. Waited on from the thread that
        #: cancelled the login, which the loop does not tell when that is.
        self.closed = threading.Event()
        self._bind = "127.0.0.1"

    def _say(self, text: str) -> None:
        self._write(sanitize_terminal_text(text))

    # -- OAuthLoginUI ---------------------------------------------------

    async def prepare(self, preferred_port: Optional[int]) -> str:
        self._bind = bind_address(self.redirect_host)
        loop = asyncio.get_running_loop()
        self._result = loop.create_future()
        try:
            self._server = await self._listen(preferred_port or 0)
        except OSError as e:
            if preferred_port and preferred_port != self.callback_port:
                # Only remembered from the stored registration: a new port
                # just means a new dynamic registration (or, for a configured
                # client_id, the message telling the user to pin the port).
                try:
                    self._server = await self._listen(0)
                except OSError as e2:
                    raise OAuthLoginError(
                        f"could not open the OAuth callback listener: {e2}"
                    ) from None
            elif preferred_port:
                raise OAuthLoginError(
                    f"the OAuth callback port {preferred_port} on {self._bind} is in use "
                    f"({e.strerror or e}); free it or set a different "
                    "'oauth.callback_port' for this server"
                ) from None
            else:
                raise OAuthLoginError(f"could not open the OAuth callback listener: {e}") from None
        self.port = self._server.sockets[0].getsockname()[1]
        host = f"[{self.redirect_host}]" if ":" in self.redirect_host else self.redirect_host
        self.redirect_uri = f"http://{host}:{self.port}{self.path}"
        return self.redirect_uri

    async def _listen(self, port: int) -> asyncio.AbstractServer:
        return await asyncio.start_server(
            self._handle,
            host=self._bind,
            port=port,
            limit=_MAX_REQUEST_BYTES,
            # POSIX ``SO_REUSEADDR`` rebinds a port the last login's callback
            # left in TIME_WAIT, and still refuses one another program is
            # listening on. On Windows it would allow exactly that, so not there.
            reuse_address=sys.platform != "win32",
        )

    async def open(self, authorization_url: str) -> None:
        # Any local process can reach the listener; only a redirect carrying
        # this login's ``state`` may settle it, so a stray or forged request
        # cannot end the login before the real one arrives.
        self._expected_state = (
            parse_qs(urlsplit(authorization_url).query).get("state") or [None]
        )[0]
        self._authorizing = True
        self._say(f"Open this URL to authorize MCP server '{self.server_name}':")
        self._say(f"  {authorization_url}")
        opened = False
        if self.open_browser and browser_available():
            opened = await self._in_thread(self._launch_browser, authorization_url)
        if opened:
            self._say(
                f"Waiting for the browser (up to {int(self.timeout)} s; Ctrl+C to cancel)..."
            )
            return
        self._say(
            "No browser was opened. After authorizing, your browser is sent to "
            f"{self.redirect_uri}?code=... — that page may "
            "fail to load. Paste its full URL below (input is hidden), or leave this "
            "waiting if the redirect can still reach this machine."
        )
        self._paste_task = asyncio.get_running_loop().create_task(self._paste_loop())

    async def wait(self) -> Tuple[str, Optional[str], Optional[str]]:
        assert self._result is not None, "prepare() was not called"
        try:
            return await asyncio.wait_for(asyncio.shield(self._result), self.timeout)
        except asyncio.TimeoutError:
            raise OAuthLoginError(
                f"no authorization arrived within {int(self.timeout)} s"
            ) from None

    @property
    def in_use(self) -> bool:
        """Whether :meth:`prepare` was reached, i.e. whether a :meth:`close` is owed."""
        return self._result is not None

    async def close(self) -> None:
        try:
            await self._close()
        finally:
            self.closed.set()

    async def _close(self) -> None:
        self._stop.set()
        if self._paste_task is not None:
            self._paste_task.cancel()
        if self._server is not None:
            self._server.close()
            try:
                await asyncio.wait_for(self._server.wait_closed(), 2.0)
            except (asyncio.TimeoutError, Exception):
                pass
            self._server = None
        # Give the paste reader its poll interval to restore the terminal:
        # the REPL prompt comes back right after this returns.
        for done in self._reader_done:
            try:
                await asyncio.wait_for(asyncio.shield(done), 1.0)
            except (asyncio.TimeoutError, Exception):
                pass
        if self._result is not None and not self._result.done():
            self._result.cancel()

    # -- the redirect, from either source -------------------------------

    def _deliver(self, query: str) -> Optional[str]:
        """Settle the login from a redirect's query; return why not, if not."""
        assert self._result is not None
        if self._result.done():
            return "This login has already finished."
        try:
            params: Dict[str, List[str]] = parse_qs(query, max_num_fields=32)
        except ValueError:  # more fields than any redirect carries
            return "The redirect carries too many query parameters."

        def one(key: str) -> Optional[str]:
            values = params.get(key)
            return values[0] if values else None

        if not self._authorizing:
            # Before ``open`` there is no authorization to answer: anything
            # arriving now is a stale redirect to a reused port or another
            # local process, and taking it would end the login unstarted.
            return "No authorization has been started for this login yet."
        if self._expected_state is not None and one("state") != self._expected_state:
            return "The redirect does not belong to this login (its state does not match)."
        error = one("error")
        if error:
            detail = one("error_description")
            self._result.set_exception(
                OAuthLoginError(
                    f"the authorization server refused the login: {error}"
                    + (f" ({detail})" if detail else "")
                )
            )
            return None
        code = one("code")
        if not code:
            return "The redirect carried no authorization code."
        self._result.set_result((code, one("state"), one("iss")))
        return None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            try:
                head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), _REQUEST_READ_S)
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError,
                    asyncio.TimeoutError, ConnectionError):
                return
            request_line = head.split(b"\r\n", 1)[0].decode("latin-1")
            parts = request_line.split(" ")
            if len(parts) != 3 or parts[0] != "GET":
                await self._respond(writer, 405, "Only GET is accepted here.")
                return
            target = urlsplit(parts[1])
            if target.path != self.path:
                await self._respond(writer, 404, "Not an agentao login callback.")
                return
            problem = self._deliver(target.query)
            if problem is None:
                if self._result is not None and self._result.exception() is not None:
                    await self._respond(writer, 200, "The authorization server refused the login.")
                else:
                    await self._respond(writer, 200, "Authorization received.")
            else:
                await self._respond(writer, 400, problem)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    @staticmethod
    async def _respond(writer: asyncio.StreamWriter, status: int, message: str) -> None:
        import html

        reason = {200: "OK", 400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed"}[status]
        body = _DONE_PAGE.format(message=html.escape(message)).encode("utf-8")
        writer.write(
            f"HTTP/1.1 {status} {reason}\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Cache-Control: no-store\r\n"
            "Connection: close\r\n\r\n".encode("latin-1")
            + body
        )
        try:
            await writer.drain()
        except ConnectionError:
            pass

    def _accept_pasted(self, text: str) -> Optional[str]:
        if len(text) > MAX_PASTE_CHARS:
            return f"That is longer than {MAX_PASTE_CHARS} characters; paste only the redirect URL."
        try:
            parts = urlsplit(text)
        except ValueError:
            return "That is not a URL."
        if parts.path != self.path:
            return f"That is not this login's redirect: its path should be {self.path}."
        return self._deliver(parts.query)

    async def _paste_loop(self) -> None:
        assert self._result is not None
        while not self._result.done() and not self._stop.is_set():
            line = await self._in_thread(self._read_line, "Redirect URL: ", self._stop, track=True)
            if line is None:
                return
            line = line.strip()
            if not line or self._result.done():
                continue
            problem = self._accept_pasted(line)
            if problem is not None:
                self._say(problem)

    def _in_thread(self, fn: Callable[..., Any], *args: Any, track: bool = False) -> "asyncio.Future[Any]":
        """Run a blocking call on its own daemon thread.

        Not the loop's default executor: the paste prompt can block for the
        whole login, and that executor is what httpx resolves hostnames on.
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()

        def settle(setter: Callable[[Any], None], value: Any) -> None:
            if not future.done():
                setter(value)

        # Separate from ``future``, which a cancelled awaiter cancels with
        # it: ``close`` needs to know when the thread itself has returned.
        finished: asyncio.Future = loop.create_future()

        def run() -> None:
            try:
                value = fn(*args)
            except BaseException as e:  # noqa: BLE001 - handed to the awaiting task
                outcome: Tuple[Callable[[Any], None], Any] = (future.set_exception, e)
            else:
                outcome = (future.set_result, value)
            try:
                loop.call_soon_threadsafe(settle, *outcome)
                loop.call_soon_threadsafe(
                    lambda: finished.done() or finished.set_result(None)
                )
            except RuntimeError:
                pass  # the loop is gone; nobody is waiting

        if track:
            self._reader_done.append(finished)
        threading.Thread(target=run, name="agentao-mcp-login", daemon=True).start()
        return future
