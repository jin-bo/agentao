"""Source adapters for fetching skill packages from remote origins."""

import abc
import dataclasses
import hashlib
import io
import os
import zipfile
from pathlib import Path
from typing import Optional

import httpx


@dataclasses.dataclass
class SourceSpec:
    """Resolved source location ready for fetching."""

    source_type: str       # "github"
    owner: str
    repo: str
    ref: Optional[str]     # branch/tag, None = default branch
    package_path: str      # optional subdirectory containing SKILL.md
    archive_url: str


@dataclasses.dataclass
class FetchResult:
    """Result of downloading and extracting a skill package."""

    extracted_dir: Path    # temp dir with extracted content
    revision: str          # content hash or commit sha
    etag: str              # HTTP ETag header
    version: str           # from skill.json if present, else ""


@dataclasses.dataclass
class UpdateInfo:
    """Result of checking whether a remote skill has a newer version."""

    has_update: bool
    current_revision: str
    latest_revision: str
    latest_etag: str


class SkillSource(abc.ABC):
    """Abstract interface for skill package sources."""

    @abc.abstractmethod
    def resolve(self, ref: str) -> SourceSpec:
        """Parse a user-provided reference into a fetchable spec."""

    @abc.abstractmethod
    def fetch(self, spec: SourceSpec, dest_dir: Path) -> FetchResult:
        """Download and extract the package into *dest_dir*."""

    @abc.abstractmethod
    def check_update(self, source_ref: str, current_etag: str) -> UpdateInfo:
        """Check whether the remote has a newer version than *current_etag*."""


class GitHubSkillSource(SkillSource):
    """Fetch skill packages from GitHub repositories."""

    _API_BASE = "https://api.github.com"

    def __init__(self) -> None:
        self._token = os.environ.get("GITHUB_TOKEN", "")

    def _headers(self) -> dict:
        headers = {
            "User-Agent": "agentao-skill-installer",
            "Accept": "application/vnd.github+json",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def resolve(self, ref: str) -> SourceSpec:
        """Parse ``owner/repo[:path][@ref]``."""
        at_ref = None
        if "@" in ref:
            ref, at_ref = ref.rsplit("@", 1)

        package_path = ""
        if ":" in ref:
            ref, package_path = ref.split(":", 1)
            package_path = package_path.strip("/")
            if not package_path:
                raise ValueError(
                    f"Invalid GitHub ref '{ref}:'. Package path cannot be empty."
                )
            if package_path.startswith("/") or any(
                part in ("", ".", "..") for part in package_path.split("/")
            ):
                raise ValueError(
                    f"Invalid GitHub ref package path '{package_path}'. "
                    "Use a relative path like skills/pdf."
                )

        parts = ref.strip("/").split("/")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(
                f"Invalid GitHub ref '{ref}'. Expected format: owner/repo[:path][@ref]"
            )

        owner, repo = parts
        archive_url = f"{self._API_BASE}/repos/{owner}/{repo}/zipball"
        if at_ref:
            archive_url += f"/{at_ref}"

        return SourceSpec(
            source_type="github",
            owner=owner,
            repo=repo,
            ref=at_ref,
            package_path=package_path,
            archive_url=archive_url,
        )

    def fetch(self, spec: SourceSpec, dest_dir: Path) -> FetchResult:
        """Download the zipball and extract into *dest_dir*."""
        with httpx.Client(
            follow_redirects=True, timeout=60, headers=self._headers()
        ) as client:
            resp = client.get(spec.archive_url)

            if resp.status_code == 403:
                remaining = resp.headers.get("X-RateLimit-Remaining", "?")
                raise RuntimeError(
                    f"GitHub API rate limit reached (remaining: {remaining}). "
                    "Set GITHUB_TOKEN env var for higher limits."
                )
            if resp.status_code == 404:
                raise RuntimeError(
                    f"Repository {spec.owner}/{spec.repo} not found on GitHub."
                )
            resp.raise_for_status()

            etag = resp.headers.get("ETag", "")
            content_bytes = resp.content

        # Compute content hash as revision
        revision = hashlib.sha256(content_bytes).hexdigest()[:16]

        # Extract zipball
        dest_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(content_bytes)) as zf:
            zf.extractall(dest_dir)

        # GitHub zipballs have a single top-level directory like owner-repo-sha/
        top_entries = list(dest_dir.iterdir())
        if len(top_entries) == 1 and top_entries[0].is_dir():
            extracted = top_entries[0]
        else:
            extracted = dest_dir

        # Read version from skill.json if present
        version = ""
        skill_json = extracted / "skill.json"
        if skill_json.exists():
            import json
            try:
                data = json.loads(skill_json.read_text(encoding="utf-8"))
                version = str(data.get("version", ""))
            except (json.JSONDecodeError, OSError):
                pass

        return FetchResult(
            extracted_dir=extracted,
            revision=revision,
            etag=etag,
            version=version,
        )

    def check_update(self, source_ref: str, current_etag: str) -> UpdateInfo:
        """Send a conditional request to check for updates."""
        # Parse the source_ref to build the URL
        spec = self.resolve(source_ref)

        headers = self._headers()
        if current_etag:
            headers["If-None-Match"] = current_etag

        with httpx.Client(
            follow_redirects=True, timeout=30, headers=headers
        ) as client:
            resp = client.head(spec.archive_url)

        if resp.status_code == 304:
            return UpdateInfo(
                has_update=False,
                current_revision="",
                latest_revision="",
                latest_etag=current_etag,
            )

        if resp.status_code == 403:
            remaining = resp.headers.get("X-RateLimit-Remaining", "?")
            raise RuntimeError(
                f"GitHub API rate limit reached (remaining: {remaining}). "
                "Set GITHUB_TOKEN env var for higher limits."
            )

        resp.raise_for_status()

        latest_etag = resp.headers.get("ETag", "")
        has_update = latest_etag != current_etag if current_etag else True

        return UpdateInfo(
            has_update=has_update,
            current_revision="",
            latest_revision="",
            latest_etag=latest_etag,
        )
