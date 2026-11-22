"""``/mcp verbose [server]`` (``agentao/cli/commands/mcp_verbose.py``).

The end-to-end tests connect real stdio MCP subprocesses through a real
``McpClientManager`` and a real ``Agentao``, so tool and resource lines come
from real ``mcp.types`` models. The redaction rules are unit-tested on their
own, since a leak there is the failure that matters most.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest

from agentao.cli.commands.mcp_verbose import (
    _auth_line,
    _listing,
    command_display,
    mask_args,
    redact_url,
)
from agentao.mcp.client import McpClient, McpClientManager
from agentao.mcp.oauth_store import OAuthRecord, OAuthRuntime

from .support.mcp_skills import PDF, modern
from .support.resource_mcp_server import resource_server
from .support.skills_mcp_server import SkillsServer, pdf_skill, skill, skill_md
from .support.stdio_mcp_server import stdio_server

pytestmark = pytest.mark.usefixtures("isolated_home", "isolated_skill_dirs")


# ── redaction ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://mcp.example.com", "https://mcp.example.com"),
        ("https://mcp.example.com/", "https://mcp.example.com"),
        ("https://mcp.example.com:8443/v1", "https://mcp.example.com:8443/… (path hidden)"),
        # A key in the path (Zapier style) must not show.
        (
            "https://actions.zapier.com/mcp/sk-ak-XXXX/sse",
            "https://actions.zapier.com/… (path hidden)",
        ),
        (
            "https://user:pw@mcp.example.com/mcp?token=abc#z9q",
            "https://mcp.example.com/… (path, query, fragment, credentials hidden)",
        ),
        ("http://[::1]:9000?key=1", "http://[::1]:9000 (query hidden)"),
    ],
)
def test_redact_url_keeps_only_scheme_host_and_port(url, expected):
    shown = redact_url(url)
    assert shown == expected
    for secret in ("abc", "pw", "sk-ak", "z9q"):
        assert secret not in shown


@pytest.mark.parametrize(
    "args, expected",
    [
        (["run", "-e", "GITHUB_TOKEN=ghp_abc", "img"], ["run", "-e", "GITHUB_TOKEN=*****", "img"]),
        (["--api-key", "sk-1"], ["--api-key", "*****"]),
        (["--api-key=sk-1"], ["--api-key=*****"]),
        (["--token", "${TOKEN}"], ["--token", "${TOKEN}"]),  # a name, not a value
        (["KEY=$VAR"], ["KEY=$VAR"]),
        (["-H", "Authorization: Bearer abc"], ["-H", "*****"]),
        (["Authorization: Bearer abc"], ["Authorization: Bearer *****"]),
        (["Authorization: Basic ${CRED}"], ["Authorization: Basic ${CRED}"]),
        (["--url", "https://u:p@h/x?k=1"], ["--url", "https://h/… (path, query, credentials hidden)"]),
        (["--port", "8080", "plain"], ["--port", "8080", "plain"]),
        # base64 padding must not turn the credential into a ``KEY=`` key.
        (["Authorization: Basic dXNlcjpwYXNz=="], ["Authorization: Basic *****"]),
        # A DSN's password is in the userinfo, not behind ``http(s)://``.
        (["postgresql://u:pw@db/x"], ["postgresql://db/… (path, credentials hidden)"]),
        (
            ["postgresql://u:pw@db/x?sslmode=require"],
            ["postgresql://db/… (path, query, credentials hidden)"],
        ),
        (["-h", "plain"], ["-h", "plain"]),  # only ``-H`` takes a header
    ],
)
def test_mask_args(args, expected):
    assert mask_args(args) == expected



def test_command_shows_the_files_placeholder_never_the_value(monkeypatch):
    monkeypatch.setenv("PROBE_TOKEN", "s3cret")
    live = {"command": "npx", "args": ["server", "--key", "s3cret"]}
    raw = {"command": "npx", "args": ["server", "--key", "${PROBE_TOKEN}"]}
    shown = command_display(live, raw)
    assert shown == "npx server --key '${PROBE_TOKEN}'"
    assert "s3cret" not in shown


def test_command_masks_a_literal_secret_in_the_file(monkeypatch):
    # A literal expands to itself, so the file check passes it: masking must
    # still hide it.
    args = ["run", "-i", "-e", "GITHUB_TOKEN=ghp_abc123", "ghcr.io/github/github-mcp-server"]
    shown = command_display({"command": "docker", "args": args}, {"command": "docker", "args": args})
    assert "ghp_abc123" not in shown
    assert "GITHUB_TOKEN=*****" in shown


def test_command_quotes_an_arg_with_spaces():
    args = ["--name", "a  b"]
    assert command_display({"command": "srv", "args": args}, {"command": "srv", "args": args}) == (
        "srv --name 'a  b'"
    )


def test_tools_and_skills_are_listed_whole_resources_capped():
    names = [f"t{i:02d}" for i in range(24)]
    assert _listing("Tools", names) == f"    • Tools: {', '.join(names)}"
    assert _listing("Resources", names, cap=10).endswith(", … 14 more")


@pytest.mark.parametrize(
    "raw",
    [
        None,  # a server a host or ACP passed in
        {"command": "npx", "args": ["server", "--key", "${OTHER}"]},  # edited since
        {"command": "uvx", "args": ["server", "--key", "${PROBE_TOKEN}"]},
        {"command": "npx", "args": "server --key x"},  # not a list
    ],
    ids=["no-file", "args-differ", "command-differs", "malformed"],
)
def test_command_hides_args_it_cannot_trace_to_the_file(monkeypatch, raw):
    monkeypatch.setenv("PROBE_TOKEN", "s3cret")
    live = {"command": "npx", "args": ["server", "--key", "s3cret"]}
    shown = command_display(live, raw)
    assert shown == "npx (args hidden: not read from mcp.json)"


def test_command_without_args_is_the_program():
    assert command_display({"command": "my-server"}, None) == "my-server"


# ── auth ────────────────────────────────────────────────────────────────────


def _record(url):
    return OAuthRecord(
        server_url=url, issuer="https://auth.example.com",
        token_endpoint="https://auth.example.com/token", access_token="tok",
    )


def test_auth_line_for_each_kind_of_server(tmp_path):
    runtime = OAuthRuntime(root=tmp_path / "oauth")
    url = "https://mcp.example.com/mcp"

    def client(config):
        return McpClient("web", config, oauth=runtime)

    assert _auth_line(client({"command": "x"})) == "n/a (stdio)"
    assert _auth_line(client({"url": url, "oauth": False})) == 'off ("oauth": false)'
    header = client({"url": url, "headers": {"authorization": "Bearer t"}})
    assert _auth_line(header) == "Authorization header"
    plain = client({"url": url})
    assert _auth_line(plain).startswith("OAuth: no credentials stored")
    runtime.store.ensure_root()
    runtime.store.save(_record(url))
    assert _auth_line(plain) == "OAuth: credentials stored"


# ── end to end ──────────────────────────────────────────────────────────────


def _agent(tmp_path, manager):
    from agentao.agent import Agentao

    wd = tmp_path / "wd"
    wd.mkdir(exist_ok=True)
    return Agentao(
        working_directory=wd, api_key="k", base_url="https://test.local/v1", model="m",
        logger=logging.getLogger("test.mcp_verbose"), mcp_manager=manager,
    )


def _run(agent, args, capsys):
    from agentao.cli.commands.mcp import handle_mcp_command

    handle_mcp_command(SimpleNamespace(agent=agent), args)
    return " ".join(capsys.readouterr().out.split())


def test_verbose_shows_config_tools_resources_and_hides_secrets(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("PROBE_DELAY", "0.0")
    tools_cfg, _ = stdio_server(tmp_path, delay=0.0)
    tools_cfg["env"] = {"API_KEY": "s3cret-value"}
    script, marks, _delay = tools_cfg["args"]
    docs_cfg, _ = resource_server(tmp_path / "docs")
    manager = McpClientManager({"probe": tools_cfg, "docs": docs_cfg})
    manager.connect_all()
    agent = _agent(tmp_path, manager)
    try:
        # The file names the delay by variable; the live config holds "0.0".
        (agent.working_directory / ".agentao").mkdir(exist_ok=True)
        (agent.working_directory / ".agentao" / "mcp.json").write_text(json.dumps({
            "mcpServers": {"probe": {**tools_cfg, "args": [script, marks, "${PROBE_DELAY}"]}},
        }))
        # A tool the server offers but the registry does not hold.
        hidden = next(
            t.name for t in agent.tools.list_tools() if getattr(t, "_mcp_tool", None)
            and t._mcp_tool.name == "slow_b"
        )
        agent.tools.unregister(hidden)

        out = _run(agent, "verbose", capsys)
        assert "s3cret-value" not in out
        assert "Env: API_KEY=*****" in out
        assert "${PROBE_DELAY}" in out
        assert "Auth: n/a (stdio)" in out
        assert "Tools: slow_a, slow_c" in out
        assert "Not offered to the model (tool filter or name clash): slow_b" in out
        assert "note://a" in out  # a resource of ``docs``
        # ``docs`` has no file entry, so its args are not shown.
        assert "(args hidden: not read from mcp.json)" in out

        one = _run(agent, "verbose docs", capsys)
        assert "MCP Servers (1)" in one and "slow_a" not in one

        missing = _run(agent, "verbose nope", capsys)
        assert "MCP server 'nope' not found." in missing

        from agentao.cli.commands.mcp import handle_mcp_command

        handle_mcp_command(SimpleNamespace(agent=agent), "list")
        raw_summary = capsys.readouterr().out
        assert "Use /mcp verbose [server]" in raw_summary
        assert "Tools:" not in raw_summary
        assert raw_summary.endswith("skills.\n\n")  # one blank line, not two
    finally:
        agent.close()
        manager.disconnect_all(timeout=0)


def test_verbose_escapes_server_text_and_survives_a_broken_server(tmp_path, capsys):
    from agentao.cli.commands import mcp_verbose

    tools_cfg, _ = stdio_server(tmp_path, delay=0.0)
    manager = McpClientManager({"probe": tools_cfg})
    manager.connect_all()
    agent = _agent(tmp_path, manager)
    try:
        def boom(*_a, **_k):
            raise RuntimeError("[/red] bad \x1b[2J")

        original = mcp_verbose._auth_line
        mcp_verbose._auth_line = boom
        try:
            out = _run(agent, "verbose", capsys)
        finally:
            mcp_verbose._auth_line = original
        assert "details unavailable: [/red] bad" in out
        assert "\x1b" not in out
    finally:
        agent.close()
        manager.disconnect_all(timeout=0)


def test_verbose_without_servers(capsys):
    from agentao.cli.commands.mcp import handle_mcp_command

    handle_mcp_command(SimpleNamespace(agent=SimpleNamespace(mcp_manager=None)), "verbose")
    assert "No MCP servers configured." in capsys.readouterr().out


def test_a_server_that_is_not_connected_gets_no_resource_request(tmp_path):
    from agentao.cli.commands.mcp_verbose import _resource_lines

    calls = []
    manager = SimpleNamespace(
        resources_allowed=lambda name: True,
        list_resources=lambda *a: calls.append(a),
    )
    client = McpClient("down", {"command": "x"})
    assert _resource_lines(manager, client) == ["    • Resources: (not connected)"]
    assert calls == []


def test_a_broken_mcp_json_is_read_quietly(tmp_path, capsys, caplog):
    tools_cfg, _ = stdio_server(tmp_path, delay=0.0)
    manager = McpClientManager({"probe": tools_cfg})
    manager.connect_all()
    agent = _agent(tmp_path, manager)
    try:
        (agent.working_directory / ".agentao").mkdir(exist_ok=True)
        (agent.working_directory / ".agentao" / "mcp.json").write_text("{not json")
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            out = _run(agent, "verbose", capsys)
        assert "(args hidden: not read from mcp.json)" in out
        assert not [r for r in caplog.records if "mcp.json" in r.getMessage()]
    finally:
        agent.close()
        manager.disconnect_all(timeout=0)


@modern
def test_skills_line_tags_active_and_disabled_skills(tmp_path, capsys):
    entry, files = pdf_skill()
    other, other_files = skill("other", {"SKILL.md": skill_md("other", "Another skill")})
    server = SkillsServer(tmp_path / "docs", pages=[[entry, other]], files={**files, **other_files})
    manager = McpClientManager({"docs": server.config()})
    manager.connect_all()
    agent = _agent(tmp_path, manager)
    try:
        sm = agent.skill_manager
        sm.active_skills[PDF] = {"task": "t"}  # what an approved activation leaves
        sm.disabled_skills.add("mcp:docs:skill://other/SKILL.md")
        out = _run(agent, "verbose docs", capsys)
        assert "Skills: pdf-processing (active), other (disabled)" in out
    finally:
        agent.close()
        manager.disconnect_all(timeout=0)
