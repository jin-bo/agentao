# Skill Install / Update Design

## Goal

Add non-interactive skill package management commands to Agentao:

```bash
agentao skill install jin-bo/agentao-git-skill
agentao skill remove web-scraper
agentao skill list
agentao skill update --all
```

The design must fit the current codebase:

- interactive `/skills` stays focused on activation / enable / disable
- `SkillManager` stays responsible for discovery / activation
- new install/update logic manages remote source, local copy, and version metadata

## Current Constraints

From the current code:

- [`agentao/cli.py`](../../agentao/cli.py#L2520) only has a thin top-level `argparse` with a single optional `subcommand`, so `agentao skill install ...` is not yet representable cleanly.
- [`agentao/skills/manager.py`](../../agentao/skills/manager.py#L24) only scans local folders and a disabled-skills config. It does not track install source, version, or update state.
- Skills are already layered by priority:
  1. `~/.agentao/skills`
  2. `<project>/.agentao/skills`
  3. `<project>/skills`

That layering is a good base. Installed remote skills should live in one managed layer, not in repo-root `skills/`.

## Proposed UX

### Commands

```bash
agentao skill install <ref> [--scope global|project] [--force]
agentao skill remove <name> [--scope global|project]
agentao skill list [--installed] [--json]
agentao skill update <name> [--scope global|project]
agentao skill update --all [--scope global|project]
```

### Default Scope

Default install scope is `project`.

Rules:

- if the current directory is a project root, install into project scope by default
- if the current directory is not a project root, fall back to `global`
- `--scope` always overrides the default

Recommended project-root detection:

- treat the current directory as project-scoped when it contains one of:
  - `.git/`
  - `pyproject.toml`
  - `package.json`
  - `.agentao/`

This keeps installs local to the current repo in the common case, while still making the command usable from arbitrary directories.

### Reference Syntax

Support GitHub shorthand only:

```bash
agentao skill install jin-bo/agentao-git-skill
```

Interpret as GitHub repo default branch, skill package at repo root.

### Remove Semantics

```bash
agentao skill remove web-scraper
```

Behavior:

- remove the installed managed package directory
- remove registry entry
- if active in current session, deactivate it
- keep unrelated local/manual skills untouched
- if `--scope` is omitted, remove from the default resolved scope first; if not found there, optionally fall back to the other managed scope with a clear message

### List Semantics

`agentao skill list` should show managed installed skills, not every discovered skill.

Output fields:

- name
- version / revision
- source type (`github` / `manual`)
- source ref
- installed scope
- update status (`up-to-date`, `update-available`, `unknown`)

Existing interactive `/skills` remains the command for "what skills are currently discoverable/active".

Default behavior:

- `agentao skill list` shows both project and global managed installs
- output must clearly label each row with its scope
- future optional flags can narrow the view, for example `--scope project` or `--scope global`

## Storage Design

### Install Location

Do not introduce a separate `skills-installed/` directory.

Managed installs should use the existing writable skill roots:

- global: `~/.agentao/skills/<skill-name>/`
- project: `<project>/.agentao/skills/<skill-name>/`

`SkillManager` scan order stays simple:

1. `~/.agentao/skills/`
2. `<project>/.agentao/skills/`
3. `<project>/skills/`

Rationale:

- keeps the filesystem model simple
- avoids adding another skills layer just for package management
- stays aligned with how skills are already discovered today
- still preserves repo-root `skills/` as highest priority overrides

### Registry File

Add a registry JSON file per scope:

- global: `~/.agentao/skills_registry.json`
- project: `<project>/.agentao/skills_registry.json`

Example:

```json
{
  "skills": {
    "web-scraper": {
      "name": "web-scraper",
      "source_type": "github",
      "source_ref": "jin-bo/agentao-git-skill",
      "installed_at": "2026-04-10T12:00:00Z",
      "install_scope": "global",
      "install_dir": "/Users/me/.agentao/skills/web-scraper",
      "version": "1.2.0",
      "revision": "sha256:abcd",
      "etag": "W/\"1234\"",
      "manifest_path": "/Users/me/.agentao/skills/web-scraper/skill.json"
    }
  }
}
```

This is separate from [`skills_config.json`](../../agentao/skills/manager.py#L20), which should continue to store disabled-skill state only.

## Package Format

### Minimum Valid Package

An installable skill package must contain:

- `SKILL.md`

Optional:

- `references/`
- `assets/`
- `scripts/`
- `skill.json`

### New Optional Manifest

Define `skill.json` for managed packages:

```json
{
  "schema_version": 1,
  "name": "web-scraper",
  "version": "1.2.0",
  "description": "Use when the user needs guided web scraping workflows.",
  "source": {
    "type": "github",
    "repo": "jin-bo/agentao-git-skill"
  }
}
```

Rules:

- `SKILL.md` remains the runtime source of truth for skill content
- `skill.json` is optional for backward compatibility
- if absent, installer derives metadata from source and local files

## Architecture

### 1. `SkillManager` remains runtime loader

Keep [`SkillManager`](../../agentao/skills/manager.py) focused on:

- scanning directories
- parsing `SKILL.md`
- activation / deactivation
- disabled state

Small extension only:

- optionally expose each loaded skill's origin layer

Do not put network/download/update logic into `SkillManager`.

### 2. Add `SkillRegistry`

New module:

- `agentao/skills/registry.py`

Responsibilities:

- load/save `skills_registry.json`
- CRUD installed-skill metadata by scope
- resolve installed skill by name
- mark revision/version after install/update/remove

### 3. Add `SkillInstaller`

New module:

- `agentao/skills/installer.py`

Responsibilities:

- parse install refs
- fetch package from source
- validate extracted directory
- install atomically into managed skill root
- update registry
- compare remote vs local revision for update

### 4. Add source adapters

New module:

- `agentao/skills/sources.py`

Interface:

- `resolve(ref) -> SourceSpec`
- `fetch(spec, dest_dir) -> FetchResult`
- `check_update(installed_record) -> UpdateInfo`

Implementations:

- `GitHubSkillSource`

This keeps `install` and `update` extensible without coupling CLI to source-specific rules.

## Source Resolution

### GitHub

Input:

```bash
agentao skill install jin-bo/agentao-git-skill
```

Recommended fetch strategy:

1. Use GitHub archive download of default branch or requested ref.
2. Extract to temp dir.
3. Detect package root:
   - repo root contains `SKILL.md`, or
   - repo root contains exactly one subdir with `SKILL.md`
4. Validate package.
5. Copy into managed install root `<scope>/skills/<skill-name>`.

Stored metadata:

- `source_type = github`
- `source_ref = jin-bo/agentao-git-skill`
- `revision = commit sha or archive digest`

Future extension:

```bash
agentao skill install jin-bo/agentao-git-skill@v1.4.0
agentao skill install github.com/jin-bo/agentao-git-skill
```

## Update Semantics

### Single Skill

```bash
agentao skill update web-scraper
```

Flow:

1. load installed record from registry
2. ask source adapter for latest revision/version
3. if unchanged, report up-to-date
4. if changed:
   - fetch to temp dir
   - validate
   - replace install dir atomically
   - update registry
   - reload skills

### Batch

```bash
agentao skill update --all
```

Flow:

1. iterate registry entries
2. skip unmanaged/manual entries
3. update each independently
4. print summary:
   - updated
   - already up-to-date
   - failed

Default behavior:

- if `--scope` is omitted, update only the default resolved scope
- if we later want cross-scope updates, add explicit `--scope all` rather than making it implicit

### Atomic Replacement

Use this install/update pattern:

1. fetch into temp dir
2. validate temp dir
3. rename current dir to backup
4. rename temp dir to final dir
5. on success, delete backup
6. on failure, roll back backup

This avoids half-installed skills.

## CLI Refactor

### Problem

[`entrypoint()`](../../agentao/cli.py#L2520) currently parses only:

- `agentao init`
- `agentao -p`
- interactive mode

It should be refactored to explicit subparsers.

### Proposed Layout

Top-level subcommands:

- `agentao init`
- `agentao skill ...`
- `agentao` interactive
- `agentao -p ...`
- `agentao --acp --stdio`

Skill subcommands:

- `install`
- `remove`
- `list`
- `update`

Suggested implementation split:

- keep `entrypoint()` small
- add `build_parser()`
- add `handle_skill_subcommand(args)`

New CLI helpers can stay in `agentao/cli.py` first; if it grows further, move to `agentao/skills/cli.py`.

## Validation Rules

Installer should reject a package when:

- no `SKILL.md`
- malformed package root discovery
- resolved skill name does not match explicit install target after normalization
- destination conflicts with a repo-root/manual skill unless `--force`

Recommended normalization:

- canonical skill id = lowercase, hyphen-preserving
- package dir name should match manifest/frontmatter name when available

## Conflict Rules

### Name conflicts across layers

Current behavior already allows higher-priority layers to override lower ones.

For managed installs:

- `install` should fail if the same skill name already exists in the same managed scope unless `--force`
- `list` should show if a managed skill is currently shadowed by a higher-priority repo skill
- `update` should still update the managed copy even if shadowed

### Remove safety

`remove` only deletes directories recorded in the registry and only when the registry marks them as managed installs.

It must never delete:

- `<project>/skills/<name>`

and it must never delete manually created skills in:

- `~/.agentao/skills/<name>`
- `<project>/.agentao/skills/<name>`

unless the registry says that exact directory was installed and is managed by Agentao.

That means registry ownership is the safety boundary, not the parent directory name.

## Recommended Implementation Phases

### Phase 1: Local foundations

- add registry module
- add CLI parser for `agentao skill list/remove`
- support remove/list for registry-managed skills in existing install roots

### Phase 2: GitHub install

- add GitHub source adapter
- add `agentao skill install <owner/repo>`
- add `agentao skill update <name>` for GitHub-installed skills

### Phase 3: Polish

- `--json` output
- `update --all`
- shadow/conflict hints
- richer version display

## Tests

Add tests near the current skills suite:

- `tests/test_skill_registry.py`
- `tests/test_skill_installer.py`
- `tests/test_skill_cli.py`

Key cases:

- install GitHub skill into global scope
- install project-scoped skill
- reject invalid package without `SKILL.md`
- update when revision changed
- `update --all` partial success summary
- remove only deletes managed installed skill
- list shows source/version/scope
- managed install root participates in `SkillManager` scan order
- repo-root `skills/` still overrides managed install

## Recommendation

The cleanest path is:

1. keep `SkillManager` as runtime discovery only
2. introduce `SkillRegistry` + `SkillInstaller`
3. store managed packages directly in existing writable skill roots
4. refactor CLI to proper `argparse` subparsers

That gives you GitHub install and `update --all` without adding another directory layer, while still protecting user-authored local skills through explicit registry ownership.
