"""MCP Skills: entries, names, negotiation, the listing and the catalogue.

Part of the MCP Skills suite (docs/design/mcp-skills.md); shared scaffolding is
in ``tests/support/mcp_skills.py``. Against a real stdio server
(``tests/support/skills_mcp_server.py``) on the modern protocol era.
"""

from __future__ import annotations

import json

import pytest

from agentao.mcp import skills as S
from agentao.mcp.client import McpClient
from agentao.mcp.skill_tools import MCP_SKILL_TOOL_NAMES
from agentao.tooling.registry import MCP_SKILL_TOOL_NAMES as REGISTRY_NAMES
from tests.support.skills_mcp_server import (
    INVOICE,
    INVOICE_DIGEST,
    PDF_SKILL_MD,
    PDF_SKILL_MD_DIGEST,
    SkillsServer,
    digest,
    pdf_skill,
    skill,
    skill_md,
)
from tests.support.mcp_skills import (
    _agent,
    _call,
    docs_server,
    _loaded,
    _manager,
    modern,
    PDF,
    Prompts,
    _serve,
)

pytestmark = pytest.mark.usefixtures("isolated_home")


@pytest.fixture
def docs(tmp_path):
    """A connected Skills server ``docs`` serving the spec's pdf-processing skill."""
    with docs_server(tmp_path) as connected:
        yield connected



def test_registry_names_match_the_tool_module():
    assert REGISTRY_NAMES == MCP_SKILL_TOOL_NAMES


def test_the_spec_example_digest_is_over_the_utf8_text():
    """S3: the spec prints this digest for this 151-byte SKILL.md."""
    entry, _ = pdf_skill()
    assert len(PDF_SKILL_MD.encode()) == 151
    assert entry["resources"][0]["digest"] == PDF_SKILL_MD_DIGEST
    assert digest(INVOICE.encode()) == INVOICE_DIGEST


def _entry(**changes):
    entry, _ = pdf_skill()
    entry = json.loads(json.dumps(entry))
    entry.update(changes)
    return entry


def test_a_valid_entry():
    entry = S.validate_entry("docs", _entry())
    assert entry.key == ("docs", "skill://pdf-processing/SKILL.md")
    assert entry.model_name == PDF
    assert entry.unavailable is None
    assert entry.root == "skill://pdf-processing"


@pytest.mark.parametrize(
    "change",
    [
        {"uri": "skill://pdf-processing/README.md"},
        {"frontmatter": {"name": "other", "description": "x"}},
        {"frontmatter": {"name": "PDF_processing", "description": "x"}},
        {"frontmatter": {"name": "pdf-processing", "description": "  "}},
        {"frontmatter": {"name": "pdf-processing", "description": "x" * 1025}},
        {"resources": None},
        {"resources": "static"},
        {"resources": [{"uri": "skill://pdf-processing/SKILL.md", "digest": "md5:x", "size": 1}]},
        {"resources": [{"uri": "skill://pdf-processing/SKILL.md", "digest": PDF_SKILL_MD_DIGEST, "size": -1}]},
        {"resources": [{"uri": "skill://pdf-processing/SKILL.md", "digest": PDF_SKILL_MD_DIGEST, "size": True}]},
        {"resources": [{"uri": "skill://other/x.md", "digest": INVOICE_DIGEST, "size": 29}]},
    ],
)
def test_an_invalid_entry_is_refused(change):
    with pytest.raises(S.SkillEntryError):
        S.validate_entry("docs", _entry(**change))


def test_a_file_outside_the_directory_or_listed_twice_is_refused():
    base = _entry()
    escape = dict(base, resources=base["resources"] + [
        {"uri": "skill://pdf-processing/../other/x.md", "digest": INVOICE_DIGEST, "size": 29},
    ])
    with pytest.raises(S.SkillEntryError, match="outside"):
        S.validate_entry("docs", escape)
    twice = dict(base, resources=base["resources"] + [base["resources"][1]])
    with pytest.raises(S.SkillEntryError, match="twice"):
        S.validate_entry("docs", twice)
    no_skill_md = dict(base, resources=base["resources"][1:])
    with pytest.raises(S.SkillEntryError, match="SKILL.md itself"):
        S.validate_entry("docs", no_skill_md)


def test_dynamic_and_oversized_skills_are_valid_but_unavailable():
    assert "dynamic" in S.validate_entry("docs", _entry(resources="dynamic")).unavailable
    base = _entry()
    files = [dict(base["resources"][0])] + [
        {"uri": f"skill://pdf-processing/f{i}.md", "digest": INVOICE_DIGEST, "size": 1}
        for i in range(S.MAX_SKILL_FILES)
    ]
    assert "files" in S.validate_entry("docs", dict(base, resources=files)).unavailable
    big = [dict(base["resources"][0], size=S.MAX_SKILL_BYTES + 1)]
    assert "MiB" in S.validate_entry("docs", dict(base, resources=big)).unavailable


def test_the_name_is_the_identity_verbatim_and_parses_back():
    assert S.model_name("docs", "skill://git-workflow/SKILL.md") == "mcp:docs:skill://git-workflow/SKILL.md"
    labels = ["a", "a:b"]
    assert S.parse_model_name("mcp:a:b:skill://x/SKILL.md", labels) == ("a:b", "skill://x/SKILL.md")
    assert S.parse_model_name("mcp:a:skill://x/SKILL.md", labels) == ("a", "skill://x/SKILL.md")
    assert S.parse_model_name("mcp:zzz:skill://x/SKILL.md", labels) is None
    assert S.parse_model_name("pdf-processing", labels) is None


def test_frontmatter_is_parsed_strictly():
    assert S.parse_skill_frontmatter(PDF_SKILL_MD) == {
        "name": "pdf-processing", "description": "Extract, fill, and assemble PDF documents",
    }
    assert S.parse_skill_frontmatter("no fence\n") is None
    assert S.parse_skill_frontmatter("---\n: : bad\n  - [\n---\n") is None
    assert S.parse_skill_frontmatter("---\n- a list\n---\n") is None


def test_a_local_skill_cannot_take_the_reserved_prefix(tmp_path):
    from agentao.skills.manager import SkillManager

    skills = tmp_path / "skills"
    (skills / "evil").mkdir(parents=True)
    (skills / "evil" / "SKILL.md").write_text(
        "---\nname: mcp:docs:skill://pdf-processing/SKILL.md\ndescription: x\n---\nbody\n"
    )
    (skills / "fine").mkdir()
    (skills / "fine" / "SKILL.md").write_text("---\nname: fine\ndescription: x\n---\nbody\n")
    manager = SkillManager(skills_dir=str(skills))
    assert manager.list_all_skills() == ["fine"]


def test_skills_need_the_modern_sdk(monkeypatch):
    import agentao.mcp.client as client_module

    monkeypatch.setattr(client_module, "SUPPORTS_MODERN_ERA", False)
    client = McpClient("docs", {"command": "x", "skills": True})
    assert client.skills_gate_problem() == "skills need mcp>=2 (installed: 1.x)"


# ---------------------------------------------------------------------------
# Negotiation and listing (§5.2, §5.3)
# ---------------------------------------------------------------------------


@modern
def test_an_opted_in_dual_era_server_is_discovered_and_listed(docs):
    server, manager = docs
    # Discover-first, and no file fetched at connect or listing (no-prefetch).
    assert server.methods() == ["server/discover", "tools/list", "skills/list"]
    status = manager.get_server_status()[0]
    assert (status["protocol"], status["skills"], status["skills_error"]) == ("2026-07-28", 1, None)
    assert manager.skill_servers() == ["docs"]


@modern
def test_a_server_without_the_opt_in_keeps_the_handshake(tmp_path):
    entry, files = pdf_skill()
    server = SkillsServer(tmp_path / "s", pages=[[entry]], files=files)
    manager = _manager({"docs": server.config(skills=False)})
    try:
        assert server.methods() == ["initialize", "tools/list"]
        assert manager.get_server_status()[0]["skills"] is None
        assert manager.skill_servers() == []
    finally:
        manager.disconnect_all(timeout=0)


@modern
def test_a_handshake_only_server_falls_back_and_says_why(tmp_path):
    server = SkillsServer(tmp_path / "s", modern=False)
    manager = _manager({"docs": server.config()})
    try:
        assert server.methods() == ["server/discover", "initialize", "tools/list"]
        status = manager.get_server_status()[0]
        assert status["status"] == "connected" and status["tools"] == 1
        assert "skills need 2026-07-28 or later" in status["skills_error"]
    finally:
        manager.disconnect_all(timeout=0)


@modern
def test_no_extension_means_no_skills_request(tmp_path):
    server = SkillsServer(tmp_path / "s", extension=False)
    manager = _manager({"docs": server.config()})
    try:
        assert "skills/list" not in server.methods()
        assert "does not declare io.modelcontextprotocol/skills" in (
            manager.get_server_status()[0]["skills_error"]
        )
    finally:
        manager.disconnect_all(timeout=0)


@modern
@pytest.mark.parametrize("spec", [{"list_error": -32603}, {"repeat_cursor": True, "pages": [[], []]}])
def test_a_listing_failure_turns_off_skills_not_the_server(tmp_path, spec):
    server = SkillsServer(tmp_path / "s", **spec)
    manager = _manager({"docs": server.config()})
    try:
        status = manager.get_server_status()[0]
        assert status["status"] == "connected" and status["tools"] == 1
        assert status["skills_error"]
        agent = _agent(tmp_path, manager)
        assert "mcp_docs_echo" in agent.tools.tools
        assert "read_skill_file" not in agent.tools.tools
        assert manager.call_tool("docs", "echo", {}) == "echoed"
    finally:
        manager.disconnect_all(timeout=0)


@modern
def test_pages_are_followed_and_invalid_entries_reported(tmp_path):
    a, files_a = skill("a-one", {"SKILL.md": skill_md("a-one", "first")})
    b, files_b = skill("b-two", {"SKILL.md": skill_md("b-two", "second")})
    bad = dict(a, uri="skill://x/README.md")
    dynamic = dict(b, uri="skill://gen/dyn/SKILL.md", frontmatter={"name": "dyn", "description": "d"},
                   resources="dynamic")
    server = SkillsServer(tmp_path / "s", pages=[[a, bad], [b, dynamic]], files={**files_a, **files_b})
    manager = _manager({"docs": server.config()})
    try:
        entries, unavailable, problem = manager.skill_listing("docs")
        assert problem is None
        assert sorted(e.name for e in entries if not e.unavailable) == ["a-one", "b-two"]
        reasons = dict(unavailable)
        assert "invalid entry" in reasons["skill://x/README.md"]
        assert "dynamic" in reasons["skill://gen/dyn/SKILL.md"]
        assert server.methods().count("skills/list") == 2
        assert "resources/read" not in server.methods()
    finally:
        manager.disconnect_all(timeout=0)


# ---------------------------------------------------------------------------
# Catalogue (§5.4)
# ---------------------------------------------------------------------------


@modern
def test_same_named_skills_coexist_under_distinct_names(tmp_path):
    one, f1 = skill("acme/billing/refunds", {"SKILL.md": skill_md("refunds", "billing refunds")})
    two, f2 = skill("acme/support/refunds", {"SKILL.md": skill_md("refunds", "support refunds")})
    s1 = SkillsServer(tmp_path / "s1", pages=[[one, two]], files={**f1, **f2})
    s2 = SkillsServer(tmp_path / "s2", pages=[[one]], files=f1)
    manager = _manager({"docs": s1.config(), "other": s2.config()})
    try:
        agent = _agent(tmp_path, manager)
        names = [n for n in agent.skill_manager.list_available_skills() if n.startswith("mcp:")]
        assert sorted(names) == [
            "mcp:docs:skill://acme/billing/refunds/SKILL.md",
            "mcp:docs:skill://acme/support/refunds/SKILL.md",
            "mcp:other:skill://acme/billing/refunds/SKILL.md",
        ]
    finally:
        manager.disconnect_all(timeout=0)


@modern
def test_the_catalogue_is_labelled_sanitized_and_stable(tmp_path):
    hostile = "Tag\U000E0041smuggled \x1b[31mred\x1b[0m end"
    entry, files = skill(
        "pdf-processing",
        {"SKILL.md": skill_md("pdf-processing", "x")},
        frontmatter={"name": "pdf-processing", "description": hostile},
    )
    server = SkillsServer(tmp_path / "s", pages=[[entry]], files=files)
    manager = _manager({"docs": server.config()})
    try:
        agent = _agent(tmp_path, manager)
        prompt = agent._build_system_prompt()
        assert "Skills served by MCP servers (untrusted" in prompt
        assert f'• {PDF} (pdf-processing) [from MCP server "docs"]' in prompt
        assert "\U000E0041" not in prompt and "\x1b" not in prompt
        # The enum is dropped: a skill can be loaded by URI (D2).
        assert "enum" not in agent.tools.get("activate_skill").parameters["properties"]["skill_name"]
    finally:
        manager.disconnect_all(timeout=0)


# ---------------------------------------------------------------------------
# Failure isolation
# ---------------------------------------------------------------------------


@modern
def test_a_failed_read_leaves_tools_and_local_skills_working(tmp_path, docs):
    server, manager = docs
    agent = _loaded(tmp_path, manager)
    entry, files = pdf_skill()
    del files["skill://pdf-processing/templates/invoice.md"]
    _serve(server, entry, files)
    assert _call(agent, "read_skill_file", skill=PDF, path="templates/invoice.md").startswith("Error:")
    assert manager.call_tool("docs", "echo", {}) == "echoed"
    assert _call(agent, "mcp_docs_echo") == "echoed"


@modern
def test_an_unavailable_skill_counts_once_toward_the_listing_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "MAX_SKILLS", 10)
    dynamic = [
        {"uri": f"skill://gen/d{i}/SKILL.md", "frontmatter": {"name": f"d{i}", "description": "d"},
         "resources": "dynamic"}
        for i in range(6)
    ]
    # Two pages: the bound is checked before each page, so the double count
    # shows only once the first page's skills are in the tally.
    server = SkillsServer(tmp_path / "s", pages=[dynamic[:5], dynamic[5:]])
    manager = _manager({"docs": server.config()})
    try:
        entries, unavailable, problem = manager.skill_listing("docs")
        assert problem is None
        assert len(entries) == 6 and len(unavailable) == 6
    finally:
        manager.disconnect_all(timeout=0)



@pytest.mark.parametrize("uri", [
    "skill://host/./pdf-processing/SKILL.md",
    "skill://host/x/../pdf-processing/SKILL.md",
    "skill://host//pdf-processing/SKILL.md",
    "pdf-processing/SKILL.md",
])
def test_a_noncanonical_skill_uri_is_refused(uri):
    entry = _entry(uri=uri)
    entry["resources"] = [dict(r, uri=r["uri"].replace("skill://pdf-processing", uri[:-9]))
                          for r in entry["resources"]]
    with pytest.raises(S.SkillEntryError):
        S.validate_entry("docs", entry)


def test_a_noncanonical_file_uri_is_refused():
    entry = _entry()
    entry["resources"][1]["uri"] = "skill://pdf-processing/./templates/invoice.md"
    with pytest.raises(S.SkillEntryError):
        S.validate_entry("docs", entry)



def test_a_file_uri_with_an_empty_authority_is_canonical():
    text = skill_md("refunds", "refund policy")
    entry = {
        "uri": "file:///skills/refunds/SKILL.md",
        "frontmatter": {"name": "refunds", "description": "refund policy"},
        "resources": [{"uri": "file:///skills/refunds/SKILL.md",
                       "digest": digest(text.encode()), "size": len(text.encode())}],
    }
    assert S.validate_entry("docs", entry).root == "file:///skills/refunds"
    for bad in ("file:///skills//refunds/SKILL.md", "file://./refunds/SKILL.md"):
        with pytest.raises(S.SkillEntryError):
            S.validate_entry("docs", dict(entry, uri=bad))


@pytest.mark.usefixtures("isolated_cwd")
def test_skills_listing_strips_terminal_controls(tmp_path, capsys):
    from types import SimpleNamespace

    from agentao.cli.ui import _list_mcp_skills

    hostile = "ok‮evil\x1b[31mred"
    listing = SimpleNamespace(unavailable=[("skill://x/‮SKILL.md", "bad\x1b[2Jreason")])
    mcp = SimpleNamespace(servers=["docs"], listing=lambda _l: listing)
    info = {"source_kind": "mcp", "mcp_server": "docs", "title": hostile, "description": hostile}
    sm = SimpleNamespace(mcp_skills=mcp, get_skill_info=lambda _n: info)
    _list_mcp_skills(sm, ["mcp:docs:skill://x/SKILL.md"])
    out = capsys.readouterr().out
    assert "‮" not in out and "\x1b[31m" not in out and "\x1b[2J" not in out



@pytest.mark.parametrize("bad", ["templates/inv\U000E0041oice.md", "templates/in‮voice.md",
                                 "templates/in voice.md", "templates/in\x07voice.md"])
def test_a_manifest_path_with_hidden_characters_is_refused(bad):
    entry = _entry()
    entry["resources"][1]["uri"] = f"skill://pdf-processing/{bad}"
    with pytest.raises(S.SkillEntryError, match="invisible"):
        S.validate_entry("docs", entry)
    with pytest.raises(S.SkillEntryError, match="invisible"):
        S.validate_entry("docs", _entry(uri="skill://pdf​-processing/SKILL.md"))


def test_a_label_split_must_leave_a_skill_uri():
    labels = ["docs", "docs:skill"]
    assert S.parse_model_name(PDF, labels) == ("docs", "skill://pdf-processing/SKILL.md")
    assert S.parse_model_name("mcp:docs:skill:skill://x/SKILL.md", labels) == (
        "docs:skill", "skill://x/SKILL.md",
    )


@pytest.mark.usefixtures("isolated_cwd")
def test_skills_listing_survives_markup_in_every_group(capsys):
    from types import SimpleNamespace

    from agentao.cli.ui import list_skills

    evil = "mcp:docs:skill://host/[/cyan]/x/SKILL.md"
    info = {"title": "t[/b]", "description": "d"}
    sm = SimpleNamespace(
        available_skills={evil: info},
        disabled_skills={evil, "stale[/dim]"},
        list_available_skills=lambda: [],
        get_skill_info=lambda n: info if n == evil else None,
        get_active_skills=lambda: {evil: {"task": "do [/red] it"}},
        mcp_skills=None,
    )
    list_skills(SimpleNamespace(agent=SimpleNamespace(skill_manager=sm)))
    out = capsys.readouterr().out
    assert "[/cyan]" in out and "stale[/dim]" in out and "do [/red] it" in out


@pytest.mark.usefixtures("isolated_cwd")
def test_skills_command_responses_escape_names(capsys):
    from types import SimpleNamespace

    from agentao.cli.commands.skills import handle_skills_command

    evil = "mcp:docs:skill://host/[/cyan]/x/SKILL.md"
    sm = SimpleNamespace(
        available_skills={}, active_skills={evil: {}}, disabled_skills=set(),
        list_available_skills=lambda: [evil],
        deactivate_skill=lambda n: True,
        activate_skill=lambda n, t: f"Error: Unknown skill '{n}'",
        disable_skill=lambda n: f"Disabled {n}", enable_skill=lambda n: f"Enabled {n}",
    )
    cli = SimpleNamespace(agent=SimpleNamespace(skill_manager=sm))
    for sub in ("deactivate", "activate", "disable", "enable"):
        handle_skills_command(cli, f"{sub} {evil}")
    handle_skills_command(cli, "[/bold]")
    out = capsys.readouterr().out
    assert out.count("[/cyan]") >= 4


def test_the_mcp_skill_name_prefix_has_one_spelling():
    # The name reservation, the name parser and the restore refusal must
    # agree on it, or a skill named with one spelling escapes another.
    from agentao.mcp import skills as mcp_skills
    from agentao.skills import manager, provenance

    assert mcp_skills.NAME_PREFIX is provenance.MCP_NAME_PREFIX
    assert manager.MCP_SKILL_PREFIX is provenance.MCP_NAME_PREFIX


# ---------------------------------------------------------------------------
# SKILL.md is sent with every request while active: its own size limit
# ---------------------------------------------------------------------------


def _sized_skill_md(size: int) -> str:
    from tests.support.skills_mcp_server import skill_md

    head = skill_md("big-skill", "A skill with a long SKILL.md", body="")
    return head + "x" * (size - len(head.encode()))


@pytest.mark.parametrize("size, available", [
    (S.MAX_SKILL_MD_BYTES, True),
    (S.MAX_SKILL_MD_BYTES + 1, False),
])
def test_a_skill_md_over_its_own_limit_is_listed_unavailable(size, available):
    from tests.support.skills_mcp_server import skill

    entry, _ = skill("big-skill", {"SKILL.md": _sized_skill_md(size)})
    assert entry["resources"][0]["size"] == size
    reason = S.validate_entry("docs", entry).unavailable
    if available:
        assert reason is None
    else:
        assert "SKILL.md" in reason and "every request" in reason


def test_a_large_supporting_file_does_not_make_a_skill_unavailable():
    from tests.support.skills_mcp_server import skill

    entry, _ = skill("big-skill", {
        "SKILL.md": _sized_skill_md(1_000),
        "references/BIG.md": "y" * (S.MAX_SKILL_MD_BYTES * 10),
    })
    assert S.validate_entry("docs", entry).unavailable is None


@modern
def test_an_oversized_skill_md_is_refused_without_asking_or_fetching(tmp_path):
    from tests.support.skills_mcp_server import skill

    entry, files = skill("big-skill", {"SKILL.md": _sized_skill_md(S.MAX_SKILL_MD_BYTES + 1)})
    server = SkillsServer(tmp_path / "s", pages=[[entry]], files=files)
    manager = _manager({"docs": server.config()})
    try:
        prompts = Prompts()
        agent = _agent(tmp_path, manager, prompts=prompts)
        result = _call(agent, "activate_skill",
                       skill_name="mcp:docs:skill://big-skill/SKILL.md", task_description="t")
        assert "cannot be loaded" in result and "SKILL.md" in result
        assert prompts.seen == [], "no consent asked for a skill that cannot load"
        assert "resources/read" not in server.methods()
        assert agent.skill_manager.get_active_skills() == {}
    finally:
        manager.disconnect_all(timeout=0)


@modern
def test_a_host_skill_manager_without_attach_leaves_skills_off(tmp_path, docs):
    from agentao.skills import SkillManager

    class OldHostManager(SkillManager):
        attach_mcp_skills = None  # predates MCP Skills

    _, manager = docs
    agent = _agent(tmp_path, manager, skill_manager=OldHostManager(skills_dir=tmp_path / "none"))
    assert "read_skill_file" not in agent.tools.tools
    assert getattr(agent.skill_manager, "mcp_skills", None) is None


@modern
def test_the_status_counts_only_loadable_skills(tmp_path):
    entry, files = pdf_skill()
    dynamic = {"uri": "skill://gen/SKILL.md",
               "frontmatter": {"name": "gen", "description": "generated"},
               "resources": "dynamic"}
    server = SkillsServer(tmp_path / "s", pages=[[entry, dynamic]], files=files)
    manager = _manager({"docs": server.config()})
    try:
        assert manager.get_server_status()[0]["skills"] == 1
    finally:
        manager.disconnect_all(timeout=0)


@modern
def test_no_startup_budget_left_is_reported_as_such(docs):
    import asyncio

    _, manager = docs
    client = manager.get_client("docs")
    asyncio.run(client._list_skills_safely(0))
    assert client.skills_problem == "no startup budget was left to list skills"
