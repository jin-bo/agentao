"""GitHub subdirectory installs must report the selected skill's version."""

import io
import json
import zipfile

import httpx
import pytest

from agentao.skills import sources


@pytest.mark.parametrize("package_path,package_version", [("skills/pdf", "2.3.4"), ("skills/pdf", None), ("", "9.9.9")])
def test_fetch_uses_selected_package_manifest(tmp_path, monkeypatch, package_path, package_version):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("owner-repo-sha/skill.json", json.dumps({"version": "9.9.9"}))
        zf.writestr("owner-repo-sha/skills/pdf/SKILL.md", "# PDF")
        if package_version is not None:
            zf.writestr("owner-repo-sha/skills/pdf/skill.json", json.dumps({"version": package_version}))
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=archive.getvalue()))
    client_class = httpx.Client
    monkeypatch.setattr(sources.httpx, "Client", lambda **kwargs: client_class(transport=transport, **kwargs))
    source = sources.GitHubSkillSource()
    ref = "owner/repo" + (f":{package_path}" if package_path else "")
    result = source.fetch(source.resolve(ref), tmp_path)
    assert result.version == (package_version or "")
    assert (result.extracted_dir / "skills/pdf/SKILL.md").is_file()
