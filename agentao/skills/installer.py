"""Skill package installer with validation and atomic replacement."""

import json
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from .registry import (
    InstalledSkillRecord,
    SkillRegistry,
    install_dir_for_scope,
)
from .sources import SkillSource


# ------------------------------------------------------------------
# Exceptions
# ------------------------------------------------------------------

class SkillInstallError(Exception):
    """Base exception for skill installation failures."""


class SkillValidationError(SkillInstallError):
    """Package does not meet skill format requirements."""


class SkillConflictError(SkillInstallError):
    """Target skill already exists and --force was not used."""


class SkillFetchError(SkillInstallError):
    """Failed to download or extract the skill package."""


# ------------------------------------------------------------------
# YAML frontmatter parser (shared with SkillManager)
# ------------------------------------------------------------------

def _parse_yaml_frontmatter(content: str) -> tuple:
    """Parse YAML frontmatter from markdown. Returns (frontmatter_dict, body)."""
    if not content.startswith("---"):
        return {}, content

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content

    try:
        frontmatter = yaml.safe_load(parts[1]) or {}
        frontmatter = {
            k: str(v).strip() if v is not None else ""
            for k, v in frontmatter.items()
        }
    except yaml.YAMLError:
        return {}, content

    return frontmatter, parts[2].strip()


# ------------------------------------------------------------------
# Name normalization
# ------------------------------------------------------------------

_VALID_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$")


def normalize_skill_name(raw: str) -> str:
    """Lowercase, replace spaces/underscores with hyphens, strip edges."""
    name = raw.strip().lower()
    name = re.sub(r"[\s_]+", "-", name)
    name = re.sub(r"-{2,}", "-", name)
    name = name.strip("-")
    return name


# ------------------------------------------------------------------
# Installer
# ------------------------------------------------------------------

class SkillInstaller:
    """Fetch, validate, and install skill packages."""

    # Warn (don't reject) if total package exceeds this
    _SIZE_WARN_BYTES = 10 * 1024 * 1024  # 10 MB

    # Files that trigger a security warning
    _SUSPICIOUS_PATTERNS = (".env", "credentials", ".key", ".pem", ".p12")

    def __init__(
        self,
        registry: SkillRegistry,
        source: SkillSource,
        scope: str,
        cwd: Optional[Path] = None,
    ) -> None:
        self._registry = registry
        self._source = source
        self._scope = scope
        self._cwd = cwd

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def install(self, ref: str, force: bool = False) -> InstalledSkillRecord:
        """Full install flow: resolve → fetch → validate → atomic copy → register."""
        try:
            spec = self._source.resolve(ref)
        except ValueError as exc:
            raise SkillInstallError(str(exc)) from exc

        tmp_dir = Path(tempfile.mkdtemp(prefix="agentao-skill-"))
        try:
            try:
                result = self._source.fetch(spec, tmp_dir)
            except Exception as exc:
                raise SkillFetchError(f"Failed to fetch {ref}: {exc}") from exc

            package_root = self._find_package_root(
                result.extracted_dir, spec.package_path
            )
            skill_name, warnings = self._validate_package(package_root)

            # Print warnings (non-fatal)
            for w in warnings:
                print(f"  Warning: {w}")

            # Conflict check
            target_dir = install_dir_for_scope(self._scope, skill_name, self._cwd)
            if target_dir.exists() and not force:
                existing = self._registry.get(skill_name)
                if existing:
                    raise SkillConflictError(
                        f"Skill '{skill_name}' already installed in {self._scope} "
                        f"scope. Use --force to overwrite."
                    )
                else:
                    raise SkillConflictError(
                        f"Directory '{target_dir}' already exists (unmanaged). "
                        f"Use --force to overwrite."
                    )

            # Atomic install
            self._atomic_replace(package_root, target_dir)

            # Rewrite SKILL.md name to the normalized form so SkillManager
            # exposes it under the same key the registry tracks.
            self._normalize_skill_md_name(target_dir / "SKILL.md", skill_name)

            # Build record — preserve the full ref including @tag if present
            full_ref = f"{spec.owner}/{spec.repo}"
            if spec.package_path:
                full_ref += f":{spec.package_path}"
            if spec.ref:
                full_ref += f"@{spec.ref}"
            record = InstalledSkillRecord(
                name=skill_name,
                source_type=spec.source_type,
                source_ref=full_ref,
                installed_at=datetime.now(timezone.utc).isoformat(),
                install_scope=self._scope,
                install_dir=str(target_dir),
                version=result.version,
                revision=result.revision,
                etag=result.etag,
            )
            self._registry.add(record)
            self._registry.save()
            return record

        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def update(self, name: str) -> Optional[InstalledSkillRecord]:
        """Check for update and install if available. Returns None if up-to-date."""
        record = self._registry.get(name)
        if record is None:
            raise SkillInstallError(f"Skill '{name}' not found in registry.")

        try:
            info = self._source.check_update(record.source_ref, record.etag)
        except Exception as exc:
            raise SkillFetchError(
                f"Failed to check updates for '{name}': {exc}"
            ) from exc

        if not info.has_update:
            return None

        # Fetch fresh copy
        tmp_dir = Path(tempfile.mkdtemp(prefix="agentao-skill-"))
        try:
            spec = self._source.resolve(record.source_ref)
            try:
                result = self._source.fetch(spec, tmp_dir)
            except Exception as exc:
                raise SkillFetchError(
                    f"Failed to fetch update for '{name}': {exc}"
                ) from exc

            package_root = self._find_package_root(
                result.extracted_dir, spec.package_path
            )
            skill_name, warnings = self._validate_package(package_root)
            for w in warnings:
                print(f"  Warning: {w}")

            if skill_name != record.name:
                raise SkillInstallError(
                    f"Upstream skill was renamed from '{record.name}' to "
                    f"'{skill_name}'. Remove and re-install to adopt the new name."
                )

            target_dir = Path(record.install_dir)
            self._atomic_replace(package_root, target_dir)

            # Re-normalize SKILL.md name so SkillManager keeps exposing
            # the skill under the registry-tracked name after update.
            self._normalize_skill_md_name(target_dir / "SKILL.md", record.name)

            # Update record
            record.revision = result.revision
            record.etag = result.etag or info.latest_etag
            record.version = result.version or record.version
            record.installed_at = datetime.now(timezone.utc).isoformat()
            self._registry.add(record)
            self._registry.save()
            return record

        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Package discovery
    # ------------------------------------------------------------------

    def _find_package_root(self, extracted_dir: Path, package_path: str = "") -> Path:
        """Locate the directory containing SKILL.md."""
        if package_path:
            package_root = extracted_dir / package_path
            if not package_root.exists():
                raise SkillValidationError(
                    f"Package path '{package_path}' not found in repository archive."
                )
            if not package_root.is_dir():
                raise SkillValidationError(
                    f"Package path '{package_path}' is not a directory."
                )
            if not (package_root / "SKILL.md").exists():
                raise SkillValidationError(
                    f"No SKILL.md found in package path '{package_path}'."
                )
            return package_root

        if (extracted_dir / "SKILL.md").exists():
            return extracted_dir

        # Check for a single subdirectory with SKILL.md
        subdirs = [d for d in extracted_dir.iterdir() if d.is_dir()]
        candidates = [d for d in subdirs if (d / "SKILL.md").exists()]

        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise SkillValidationError(
                "Ambiguous package: multiple subdirectories contain SKILL.md."
            )

        raise SkillValidationError("No SKILL.md found in package.")

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_package(self, package_dir: Path) -> tuple:
        """Validate a skill package. Returns (skill_name, warnings_list)."""
        warnings = []
        skill_md = package_dir / "SKILL.md"

        # 1. SKILL.md exists
        if not skill_md.exists():
            raise SkillValidationError("No SKILL.md found in package.")

        # 2. Readable UTF-8
        try:
            content = skill_md.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise SkillValidationError(
                f"SKILL.md is not valid UTF-8: {exc}"
            ) from exc

        # 3. YAML frontmatter present
        if not content.startswith("---"):
            raise SkillValidationError("SKILL.md missing YAML frontmatter.")

        frontmatter, _body = _parse_yaml_frontmatter(content)
        if not frontmatter:
            raise SkillValidationError(
                "SKILL.md frontmatter is empty or malformed."
            )

        # 4. name field required
        raw_name = frontmatter.get("name", "")
        if not raw_name:
            raise SkillValidationError(
                "SKILL.md frontmatter missing required 'name' field."
            )

        # 5. description field required
        if not frontmatter.get("description", ""):
            raise SkillValidationError(
                "SKILL.md frontmatter missing required 'description' field."
            )

        # 6. Name normalization and character validation
        skill_name = normalize_skill_name(raw_name)
        if not skill_name or not _VALID_NAME_RE.match(skill_name):
            raise SkillValidationError(
                f"Skill name '{raw_name}' (normalized: '{skill_name}') "
                f"contains invalid characters. "
                f"Only lowercase letters, digits, and hyphens are allowed."
            )

        # 7. Name consistency with skill.json
        skill_json_path = package_dir / "skill.json"
        manifest_version = ""
        if skill_json_path.exists():
            try:
                manifest = json.loads(
                    skill_json_path.read_text(encoding="utf-8")
                )
            except (json.JSONDecodeError, OSError) as exc:
                raise SkillValidationError(
                    f"skill.json is malformed: {exc}"
                ) from exc

            # 8. skill.json schema validation
            schema_ver = manifest.get("schema_version", 1)
            if schema_ver != 1:
                raise SkillValidationError(
                    f"Unsupported skill.json schema_version: {schema_ver}"
                )

            manifest_name = manifest.get("name", "")
            if manifest_name:
                norm_manifest = normalize_skill_name(manifest_name)
                if norm_manifest != skill_name:
                    raise SkillValidationError(
                        f"Name mismatch: SKILL.md says '{skill_name}' "
                        f"but skill.json says '{norm_manifest}'."
                    )

            manifest_version = str(manifest.get("version", ""))

        # 9. Suspicious files
        suspicious = []
        for p in package_dir.rglob("*"):
            if p.is_file():
                lower = p.name.lower()
                if any(lower.startswith(pat) or lower.endswith(pat)
                       for pat in self._SUSPICIOUS_PATTERNS):
                    suspicious.append(str(p.relative_to(package_dir)))
        if suspicious:
            warnings.append(
                f"Package contains potentially sensitive files: "
                f"{', '.join(suspicious[:5])}"
            )

        # 10. Size sanity
        total_size = sum(
            f.stat().st_size for f in package_dir.rglob("*") if f.is_file()
        )
        if total_size > self._SIZE_WARN_BYTES:
            mb = total_size / (1024 * 1024)
            warnings.append(
                f"Package is {mb:.1f} MB — skills are typically documentation, "
                f"not large binaries."
            )

        return skill_name, warnings

    # ------------------------------------------------------------------
    # Atomic filesystem operations
    # ------------------------------------------------------------------

    @staticmethod
    def _atomic_replace(source: Path, target: Path) -> None:
        """Copy *source* to *target* with backup-based rollback on failure."""
        backup = target.with_name(target.name + ".bak")

        try:
            if target.exists():
                target.rename(backup)
            shutil.copytree(source, target)
            # Success — clean up backup
            if backup.exists():
                shutil.rmtree(backup)
        except Exception:
            # Clean up partially copied target on failure.
            if target.exists():
                shutil.rmtree(target)
            # Rollback from backup if a previous version existed.
            if backup.exists():
                backup.rename(target)
            raise

    @staticmethod
    def _normalize_skill_md_name(skill_md: Path, normalized_name: str) -> None:
        """Rewrite the ``name:`` field in SKILL.md frontmatter to *normalized_name*.

        This ensures SkillManager exposes the skill under the same key the
        registry uses, even if the original SKILL.md had a non-normalized name.
        """
        try:
            content = skill_md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return

        if not content.startswith("---"):
            return

        parts = content.split("---", 2)
        if len(parts) < 3:
            return

        # Replace the name line in the frontmatter section.
        fm_lines = parts[1].splitlines(keepends=True)
        rewritten = []
        for line in fm_lines:
            stripped = line.lstrip()
            if stripped.startswith("name:"):
                # Preserve original indentation
                indent = line[: len(line) - len(stripped)]
                rewritten.append(f"{indent}name: {normalized_name}\n")
            else:
                rewritten.append(line)

        new_content = "---" + "".join(rewritten) + "---" + parts[2]
        try:
            skill_md.write_text(new_content, encoding="utf-8")
        except OSError:
            pass  # Best-effort
