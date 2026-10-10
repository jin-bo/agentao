"""A skill installed from a repository subdirectory records its own version.

The archive root's ``skill.json`` belongs to the repository. The version that
lands in the registry has to come from the package the installer selected,
whether the ref names it (``owner/repo:skills/pdf``) or the installer finds it
as the single subdirectory holding ``SKILL.md`` (``owner/repo``). Goes through
the real ``GitHubSkillSource`` with only the socket replaced, so the archive is
downloaded and extracted exactly as in production.
"""

import io
import json
import zipfile

import httpx
import pytest

from agentao.skills import sources
from agentao.skills.installer import SkillInstaller
from agentao.skills.registry import SkillRegistry


@pytest.fixture(autouse=True)
def _project_root_at_tmp_path(tmp_path):
    # Pin the project root to tmp_path (see test_skill_installer.py, #463).
    (tmp_path / ".git").mkdir()


def _archive(package_version, root_version="9.9.9", subdir="pdf"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        top = "owner-repo-sha/"
        if root_version is not None:
            zf.writestr(top + "skill.json", json.dumps({"version": root_version}))
        zf.writestr(
            f"{top}{subdir}/SKILL.md",
            "---\nname: pdf\ndescription: PDF tools.\n---\n# PDF\n",
        )
        if package_version is not None:
            zf.writestr(
                f"{top}{subdir}/skill.json",
                json.dumps({"schema_version": 1, "name": "pdf", "version": package_version}),
            )
    return buf.getvalue()


class _Remote:
    """Serves one archive; ``HEAD`` always reports a changed ETag."""

    def __init__(self, archive):
        self.archive = archive
        self.etag = 0

    def handle(self, request):
        self.etag += 1
        headers = {"ETag": f'W/"{self.etag}"'}
        if request.method == "HEAD":
            return httpx.Response(200, headers=headers)
        return httpx.Response(200, content=self.archive, headers=headers)


@pytest.fixture
def remote(monkeypatch):
    remote = _Remote(b"")
    client_class = httpx.Client
    monkeypatch.setattr(
        sources.httpx,
        "Client",
        lambda **kw: client_class(transport=httpx.MockTransport(remote.handle), **kw),
    )
    return remote


def _installer(tmp_path):
    reg = SkillRegistry(tmp_path / "reg.json")
    return SkillInstaller(reg, sources.GitHubSkillSource(), "project", tmp_path)


@pytest.mark.parametrize(
    "ref,subdir",
    [("owner/repo:skills/pdf", "skills/pdf"), ("owner/repo", "pdf")],
    ids=["explicit-path", "auto-discovered"],
)
def test_install_records_the_selected_package_version(tmp_path, remote, ref, subdir):
    remote.archive = _archive("2.3.4", subdir=subdir)
    record = _installer(tmp_path).install(ref)
    assert record.name == "pdf"
    assert record.version == "2.3.4"


@pytest.mark.parametrize("ref", ["owner/repo:pdf", "owner/repo"])
def test_a_package_without_a_manifest_does_not_inherit_the_root_version(
    tmp_path, remote, ref
):
    remote.archive = _archive(None)
    assert _installer(tmp_path).install(ref).version == ""


def test_a_package_at_the_archive_root_keeps_its_version(tmp_path, remote):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("owner-repo-sha/SKILL.md", "---\nname: pdf\ndescription: d\n---\n")
        zf.writestr("owner-repo-sha/skill.json", json.dumps({"version": "1.0.0"}))
    remote.archive = buf.getvalue()
    assert _installer(tmp_path).install("owner/repo").version == "1.0.0"


@pytest.mark.parametrize("ref", ["owner/repo:pdf", "owner/repo"])
def test_update_records_the_selected_package_version(tmp_path, remote, ref):
    installer = _installer(tmp_path)
    remote.archive = _archive("2.3.4")
    installer.install(ref)

    remote.archive = _archive("2.4.0", root_version="10.0.0")
    updated = installer.update("pdf")
    assert updated is not None
    assert updated.version == "2.4.0"
    assert SkillRegistry(tmp_path / "reg.json").get("pdf").version == "2.4.0"
