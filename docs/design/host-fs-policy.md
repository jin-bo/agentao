# Host FS policy: path-domain write boundary over the single chokepoint (incl. shell)

> Status: **design proposal**, sourced from **two** independent embedding hosts' requirements (a
> knowledge-base host and a multi-room chat host, both embedding `Agentao(...)`). Tracks a boundary
> both hosts are currently forced to re-implement, and proposes it as a host-contract primitive.
> Split into **(a) path-domain generalization /
> (b) hot-swappable lifecycle / (c) shell enforcement** — each landable independently;
> **(a)+(b) do not depend on (c)**.
> Roadmap context: `docs/design/path-a-roadmap.md` — a **demand-gated P1 *candidate***, **not** a
> tracked §4 P1 item (the §4 table holds only P1.1–P1.3). Two internal embedding hosts validate the
> demand, but §4's P1 trigger is *external*-adopter evidence, so this stays **P1-adjacent** until a
> checkpoint (§16.1) promotes it. Note: part (c) maps to a roadmap **non-goal / P2** item — see (c).
>
> **Update — host-side interim path found (covers *one* facet), priority *lowered*:** the
> **within-cwd immutable carve-out** (KB host's facet) is satisfiable **today**, zero agentao change,
> by injecting a policy-checking `FileSystem` capability (`Agentao(filesystem=...)`) that tests a
> **leaf-dereferenced effective target** — see *Interim adoption path*. **But the external-root facet
> (chahua) is NOT** wrapper-satisfiable for native `write_file`: the built-in tools run a single-root
> `PathPolicy` pre-check *before* the capability (`file_ops.py:197,368`), so a write to a root outside
> cwd is rejected upstream of the wrapper — it needs either a host `extra_tool` or the option-2
> gate-pushdown (an agentao change). That makes building
> `fs_policy=` / `set_fs_policy` as new first-class API **further demand-gated, not now**: promote
> only on (i) a **third** host copy-pasting the wrapper, (ii) the wrapper **breaking** because
> `FileSystem` / `_bind_and_register` internals shift, or (iii) a genuine **(c)** (shell) need — the
> one thing a filesystem wrapper provably cannot do. Until one fires, the honest recommendation is
> **ship the wrapper recipe, do not build the `fs_policy=` API** — with one caveat: the recipe must
> test a **leaf-dereferenced effective target** (reusing single-root `contain_file` per root is
> *fail-open* for the immutable carve — see *Interim adoption path*), and the small
> `PathPolicy.contain_any` helper that centralizes that resolve is worth landing early because it is
> the line between a correct wrapper and a fail-open one.

## TL;DR

An embedding host needs to declare, **once at construction**, that some subpaths of an
otherwise-writable `working_directory` are **deterministically read-only** (e.g. `raw/`, config
files), **and/or** that additional roots *outside* cwd are writable (a chat host's `share/`,
`tasks/`) — and have that boundary enforced over the **structured write tools**, hot-swappable for a
`/mode`-style posture flip. **Near-term scope is the structured file tools only**: shell enforcement
is part (c), which maps to a roadmap **P2** item and may never ship, so until then shell stays a host
concern (snapshot sentinel / OS sandbox the host already runs).

agentao already has the two right primitives, but neither is generalized or wired for this:

- `security/path_policy.py` — single-root containment only.
- `sandbox/` — single writable subtree (`_RW1`), no immutable subset, disabled by default,
  darwin-only.

So every embedding host re-implements the boundary in its own code (per-path regex deny-rules +
per-turn snapshot diffs + a `set_mode` workaround for non-removable run-rules). This proposal asks
agentao to provide the boundary as an embedding-face primitive.

## Motivating hosts — two facets of the same gap

Two real embedding hosts hit the single-root limit from opposite directions. They are not two
requirements — they are two facets of one generalized predicate (see (a)).

| Host | Shape | Re-implementation forced today |
|---|---|---|
| Knowledge-base host | **Within-root read-only carve-out** — cwd writable, with `raw/`+config carved **deterministically read-only** (a read-only *subset*, not a cwd allow-list — see Augment below) | per-path regex deny-rules on `file_path` (fragile; wrong arg name fails open) |
| Multi-room chat host (chahua) | **External multi-root** — per-guest cwd (`<room>/guests/<name>/`) plus physically separate room-level trees (`<room>/share/`, `<room>/tasks/<active>/artifacts/`) reached via `symlink`-into-cwd | a bespoke `TaskWriteArtifactTool` (`task_tools.py:326`) that **bypasses `PathPolicy`** to write straight to disk, because `contain_file` resolves the `./task` symlink and rejects the target as outside `working_directory` |

The chahua case is the sharper proof of "single-root forces a bypass": its symlink (`./task` →
`<room>/tasks/<id>/artifacts/`) resolves *outside* the guest cwd, so native `write_file('./task/x')`
is rejected while `read_file('./task/x')` succeeds — a read/write asymmetry the host has to paper
over with a bypass tool **and teach the model about in the tool description**. The same host also
hand-rolls `allowed_root = (room_dir/"share").resolve()` containment at its transport layer
(`server_inbound_io.py`) — a second, independent re-implementation of exactly this boundary.

## Current state (grep-verified against `main`)

| Gap | Evidence |
|---|---|
| Write boundary is single-root only, no path domains | `security/path_policy.py` — `@dataclass(frozen=True) class PathPolicy` holds only `project_root: Path`; `contain_file` asserts containment in that one root. No writable-set / immutable-subset vocabulary. |
| Shell writes bypass the path gate entirely | `path_policy.py` docstring: *"Shell command **arguments** are not inspected — only the cwd is contained."* `echo>` / `mv` / `python -c` can write anywhere the OS allows. |
| Per-run deny-rules are add-only and regex-by-arg | `permissions.py` — `add_run_rules(deny=…)` appends to `_run_scope_rules`; **no** `remove_run_rules` / `clear_run_rules`. `_matches` uses `re.search(arg_pattern, str(tool_args.get(arg_key,"")))` (`permissions.py:527`) — caller must know the exact arg name (`file_path`), and a wrong/renamed arg silently fails open. |
| Sandbox exists but isn't a writable-domain surface | `sandbox/` has `SandboxPolicy` + `workspace_root` + macOS profiles. `embedding/factory.py:187-188` *does* construct `SandboxPolicy(project_root=wd)` — it is wired into the factory. What's missing: `enabled` defaults to `False` (`sandbox/policy.py:56`), it's `platform: "darwin"` only (no landlock/bubblewrap impl), profiles express a *single* writable subtree `_RW1` (`workspace-write.sb:18-19`) with **no immutable-subpath** concept, and there's no kwarg to derive `_RW1` from a declared writable domain. |

## (a) Generalize `PathPolicy`: single root → declared **root set** (writable ∪ immutable)

**Scope:** structured write tools only (`write_file`, `replace`, any future file-write tool).
Pure path semantics, zero platform dependency. **Independent — ship first.**

- Extend `PathPolicy` (or add a sibling consulted at the same chokepoint) from
  `project_root: Path` to `writable: list[Path]` ∪ `immutable: list[Path]` — **sets of roots, each
  of which may live *outside* `working_directory`** (the multi-room host needs `<room>/share` and
  `<room>/tasks`, which are siblings of the guest cwd, not children of it).
- Reuse the existing resolve + symlink-follow + `..`-safe logic (`_resolve_for_write` /
  `_assert_inside`). The predicate generalizes from `is_relative_to(project_root)` to:
  **`resolved ∈ (working_directory ∪ ⋃ writable)` AND `resolved ∉ ⋃ immutable`**, evaluated on the
  symlink-resolved target (cwd is implicitly in the writable set — see Augment below).
  **Containment semantics, not regex** — this is the whole point vs. the current `re.search`-by-arg
  deny-rule path.
- `immutable` takes priority over `writable` (a path inside a writable root but under an immutable
  subpath is read-only). This is the **knowledge-base host's** facet.
- Roots outside `working_directory` cover the **multi-room host's** facet: a `symlink` whose target
  resolves into a declared external root is allowed. This is what retires its bypass tool.

### Where the chokepoint actually is (the "single chokepoint" is a design requirement, not today's reality)

Today there is **no** single chokepoint. `PathPolicy.for_tool(...)` is invoked *inside each built-in
write tool's* `execute()` — grep-verified at `tools/file_ops.py:197,368` and `tools/shell.py:230`,
and **nowhere else**. The shared capability that every tool is bound to is `agent.filesystem`
(`tooling/registry.py:78` — `_bind_and_register` sets `tool.filesystem = agent.filesystem` for
**built-in *and* `extra_tools` alike**). That capability does **not** consult `PathPolicy`. So:

- A host-injected `extra_tool` that calls `self.filesystem.write_text(...)` writes **straight to
  disk, past the gate** — it inherits the binding but not the per-tool `for_tool` call the built-ins
  make by hand.
- MCP tools (`mcp_*`) are entirely outside this path.

This changes (a)'s contract, so state it explicitly. Two options:

1. **Scope FsPolicy to built-in file tools** — honest and small, but then "all structured write
   tools enforce the boundary" is **false** for injected/MCP tools, and the doc must say so.
2. **Push enforcement down into the `FileSystem` capability** (`agent.filesystem`) — make
   `write_text` (the only write method on the `FileSystem` protocol today, `filesystem.py:73`, plus
   any future write methods) consult the policy, **and replace the built-in tools' own single-root
   `PathPolicy.for_tool(...).contain_file(...)` pre-check** (`file_ops.py:197,368`) so the capability
   is the *sole* gate.
   - **Don't just *delete* the pre-check — it does double duty.** `contain_file` today both (1)
     resolves the (possibly relative) `file_path` into the absolute `Path` handed to `write_text`
     *and* (2) runs the security check; and `FileSystem` requires **absolute** paths
     (`filesystem.py:46`). So the tools must first call `Tool._resolve_path(file_path)` (cwd-binding,
     `base.py:61`) for the resolution half, then hand the result to the **policy-enforced**
     `write_text` for the final leaf-deref allow/deny. Dropping the pre-check without restoring the
     resolution step would let relative paths fall back to the process cwd (or reach the FS raw),
     re-opening exactly the containment hole `PathPolicy` exists to close.
   Because `_bind_and_register` hands that *same* capability to `extra_tools`,
   this is what makes "single chokepoint" *true*: injected tools that write through the bound
   filesystem are then covered for free, and the built-ins' hand-rolled `for_tool` calls collapse
   into it. **Recommended** — it's the only option that matches the
   "single chokepoint" language the rest of this doc uses. (MCP tools still bypass — they don't use
   `agent.filesystem` — so the closed-allow-set claim must always be scoped to "writes through the
   agentao filesystem capability," never "any tool.")

### Augment, not replace — and what `fs_policy` never gates (read before copying the examples)

- **`working_directory` is always implicitly writable (augment semantics).** `writable=[...]`
  declares *additional* roots — typically *outside* the cwd; it does **not** replace the cwd. A host
  never has to re-list its own cwd to let the agent write its working area, and forgetting to would
  otherwise be a silent footgun.
- **agentao's own persistence is outside `fs_policy`'s scope entirely.** `PathPolicy` is applied
  only in the write *tools*: `PathPolicy.for_tool(...)` appears solely at `tools/file_ops.py:197,368`
  and `tools/shell.py:230` (grep-verified — those are its only call sites). `MemoryManager`
  (`memory/manager.py`) and `session.py` import no `PathPolicy` and write `.agentao/memory.db` /
  `sessions/` through direct sqlite/file I/O that never reaches the chokepoint — so `fs_policy` can
  neither protect nor break those under any semantics. (This corrects a tempting misread: declaring
  `writable=[share, tasks]` does **not** endanger the guest's `.agentao/` persistence. The thing a
  *replace* allow-list would have broken is the guest writing scratch files into its cwd via
  `write_file` — and augment semantics keeps that working.)

**Deliberate: augment is unconditional — there is no cwd-internal allow-list mode.** cwd is
writable-by-default and `immutable` carves read-only subpaths out of it (deny-list posture). Why
default to deny-list rather than a cwd-internal allow-list? **Not** because the two are "equally
fragile" — they fail in *opposite* directions, and for a security boundary that asymmetry matters:

- A cwd allow-list (`writable=[wiki,workspace,graph]`) that forgets `graph` makes `graph` silently
  read-only → the write **fails loudly**. Fail-*safe* (an availability failure).
- A deny-list (`immutable=[raw,config]`) that forgets a sensitive dir leaves it **writable** → the
  agent can overwrite it silently. Fail-*open* (a security failure) — the worse mode for something
  this doc calls a "deterministic security boundary."

The honest justification is **status-quo preservation, not footgun symmetry.** Deny-list default =
today's behavior (single-root containment ⇒ cwd fully writable) **plus** a new read-only carve — it
*relaxes nothing*. A cwd-internal allow-list is a **stricter, new** posture (most of cwd becomes
read-only). So defaulting to deny-list only *adds* capability; wanting the stricter closed allow-list
is a new requirement — demand-gate it. The fail-open risk of a forgotten `immutable` entry is
**host-owned and identical to today's cwd-fully-writable baseline** — this proposal does not newly
introduce it. Neither motivating host wants the allow-list — the KB host accepted the deny-list
carve-out, the multi-room host wants cwd fully writable — so both ride the *same* unconditional
predicate. A *conditional* augment ("declaring an in-cwd writable root flips cwd to allow-list") was
considered and rejected: one declaration silently changing cwd's whole default posture is a
surprising non-local effect. If a future host genuinely needs a closed allow-list *within* cwd, add
it then as an explicit opt-in (e.g. `FsPolicy(closed_cwd=True)`) — demand-gated, not built
speculatively.

### Security invariant (do not relax PathPolicy's reason for existing)

`PathPolicy` exists specifically to stop symlink/absolute-path escapes (`write_file('/etc/passwd')`,
`write_file('../outside')` — see its docstring). Generalizing to external roots must **not** reopen
that hole:

- **Default with no `fs_policy` stays today's single-root containment.** External roots are an
  opt-in escape hatch, never the new default.
- The allow-set is **closed**: `resolved ∈ (working_directory ∪ ⋃ declared roots)`. We authorize
  **destination roots**, not "whatever a symlink points to." chahua's `./task` symlink is allowed
  *because its target resolves into the declared `<room>/tasks` root* — the policy trusts the root,
  the symlink is just the ergonomic way to reach it. Framing it as "trust symlink targets" would
  reintroduce the `/etc/passwd`-via-symlink escape. There is no "write anywhere" mode — that is
  `PermissionMode`'s orthogonal axis (so the old `deny_outside_root` flag is dropped: a write
  boundary is *always* a closed allow-set; the flag was both redundant and, once roots can be
  external, self-contradictory in name).
- `immutable` still wins across the whole resolved space, so a host can declare external writable
  roots **and** carve read-only subpaths within them.
- **No new TOCTOU surface.** External roots still go through `resolve()`-then-check, identical to
  today's single-root `PathPolicy`; the classic check-vs-write symlink-swap window is neither
  widened nor narrowed by allowing declared external roots.
- **Relative root entries resolve against `working_directory`, canonicalized at construction.** The
  KB example uses `immutable=["raw", "AGENTAO.md"]` (relative) while `writable` may name external
  *absolute* roots — so relative-vs-absolute semantics must be pinned, not left to `Path` defaults.
  Rule: a relative `writable`/`immutable` entry joins to `working_directory` (matching
  `_resolve_for_write`'s existing relative-join-to-`project_root`, `path_policy.py:119-120`), **not**
  to process cwd. All roots are `expanduser().resolve()`-canonicalized and validated **at
  `FsPolicy` construction** (a security boundary should fail loudly on a malformed root, not silently
  at first write). **A two-rule split removes the self-reference** (an earlier draft said "rejected
  unless it *also* lands in a declared external root," which needs the external set to already be
  known): **relative** entries are cwd-relative and must **not** escape cwd — a `..`-escaping relative
  entry is rejected at construction, full stop; an **external** root must be declared as an
  **absolute** (or host-pre-resolved) `Path`. "Is this an external root?" is then answered
  *syntactically* by the entry's own form, never by re-checking against the set being built.

Proposed construction kwarg on `embedding/factory.build_from_environment`:

```python
# Knowledge-base host — read-only carve-out within cwd (immutable facet):
agent = build_from_environment(
    working_directory=kb,                              # implicitly writable
    fs_policy=FsPolicy(
        immutable=["raw", "AGENTAO.md", "SCHEMA.md"],  # everything else under cwd stays writable
    ),
)

# Multi-room chat host (chahua) — additional external roots (writable facet):
agent = build_from_environment(
    working_directory=room / "guests" / name,           # implicitly writable: guest's own area,
                                                         # .agentao/ persistence, scratch via write_file
    fs_policy=FsPolicy(
        writable=[room / "share", room / "tasks"],       # ADDED roots, outside the guest cwd
    ),
)
# Note (isolation): the cwd line assumes chahua's default room-isolation
# (`<room>/guests/<name>/`). Under isolation="global" the cwd is
# `<user_data_root>/guests/<name>/` (config.py:220) — a tree disjoint from
# `<room>/share` and `<room>/tasks`, not even a sibling. That strengthens the
# external-root case: the declared roots are genuinely cross-tree.
```

**Trade-off to decide (multi-room host):** declaring the `<room>/tasks` **parent** as writable
keeps the policy static — the `./task` symlink retargets across `open/set_active/close` and the
policy never changes (so (b) hot-swap is *not* needed). The cost is that **all** tasks' artifacts
become writable, not just the active one. If "only the active task is writable" is a security
requirement, declare the active-task artifacts dir specifically and use (b) to swap the policy on
retarget. The parent-root option trades active-only precision for policy stability; the host picks.

**Deliverable:** every write through the agentao `FileSystem` capability enforces `FsPolicy` at one
chokepoint (per "Where the chokepoint actually is": option 2 — enforce in `agent.filesystem` — is
what makes this hold for injected `extra_tools`, not just built-ins; MCP tools stay out of scope) —
the host never has to know per-tool arg names or write regexes. Retires **both** the knowledge-base
host's regex deny-rule layer **and** the multi-room host's `PathPolicy`-bypass tool (with its
read/write asymmetry and the model-facing caveat in the tool description).

## (b) Make the policy hot-swappable / run-rules removable

**Scope:** lifecycle of the per-run boundary. Independent of (a) and (c).

- Add `agent.set_fs_policy(FsPolicy(...))` + an `agent.fs_policy` getter so a host `/mode` flip
  changes posture by *replacing* the policy, leaving no residual rules.
- **Invalidate the `for_tool` cache on swap — the existing cache keys on cwd only.**
  `PathPolicy.for_tool` memoizes on `tool._path_policy_cache = (wd, policy)` and returns the cached
  policy whenever `cached[0] == wd` (`path_policy.py:48-53`). Since the key is **`working_directory`
  alone**, a `set_fs_policy(...)` that keeps cwd fixed (the common case — only `immutable`/`writable`
  changes) would leave every already-bound tool serving the **stale** policy. So `set_fs_policy` must
  either bump a policy identity/version into the cache key, or actively clear the per-tool caches on
  swap. This is the concrete coupling between (a)'s cache and (b)'s lifecycle — name it in the impl,
  it is an easy silent bug.
- Independently useful even without `FsPolicy`: give `_run_scope_rules` a public
  `remove_run_rules` / `clear_run_rules` so the existing add-only API stops forcing the
  "inject permanently at construction, never unload on `set_mode`" workaround.

**Orthogonality (keep clean):** `FsPolicy` answers *where a write may land*; `PermissionMode` /
`readonly_mode` answers *whether writes/shell run at all*. The two compose; a host declares the FS
domains once. We are intentionally **not** asking `FsPolicy` to re-derive read-only — that stays
Mode's job.

**Anti-pattern (a real host nearly shipped this):** do **not** write
`set_fs_policy(FsPolicy(writable=[]))` to mean "read-only." An empty writable set is not how you
express a read-only posture — `FsPolicy` never answers *whether* a write may happen. A host's
`/mode` read-only flip is a `PermissionMode` / `readonly_mode` change (a two-point posture on the
permission engine + tool runner), *not* an `FsPolicy` swap. Use `set_fs_policy` only to change which
domains are writable/immutable; leave "can the agent write at all" to Mode.

**Not always needed:** (b) is only load-bearing when a host needs the writable set to *change* mid-
session. A host that declares a stable parent root and retargets a symlink underneath it (the multi-
room host's `./task` pattern, see (a)'s trade-off) keeps a static policy and does not need
`set_fs_policy` at all.

## Interim adoption path — host-side wrapper (covers the immutable facet only)

The enforcement point this proposal wants — option 2 in *Where the chokepoint actually is*, a
policy-checking `FileSystem` capability — is **already an injectable host-contract surface today**:
`Agentao(filesystem=...)` (`agent.py:87`) is bound onto every tool by `_bind_and_register`
(`registry.py:78`). A host can inject a wrapper with no agentao change:

> **Scope, stated up front — the wrapper can only *restrict*, never *expand*.** The built-in
> `write_file` / `replace` run their own single-root `PathPolicy.for_tool(...).contain_file(...)`
> **before** calling `self.filesystem.write_text` (`file_ops.py:197,368`). The wrapper sits
> *downstream* of that pre-check, so:
> - **Within-cwd immutable carve-out (KB host): fully works.** `cwd/raw/secret` is inside cwd → it
>   *passes* the built-in pre-check → reaches the wrapper → the wrapper denies it. Carving read-only
>   subpaths out of an already-writable cwd is exactly "restrict within what's allowed." ✅ zero
>   agentao change, native `write_file`.
> - **External roots outside cwd (chahua): does NOT work for native `write_file`.** `./task/x →
>   <room>/tasks/...` resolves outside cwd → the built-in pre-check **rejects it upstream**, before
>   the wrapper ever runs. The wrapper cannot *allow* what the pre-check already denied. The host's
>   only options are (a) route external writes through an `extra_tool` (chahua already has
>   `TaskWriteArtifactTool`) whose `self.filesystem` is the wrapper — which makes that bypass
>   *policy-gated* but does **not** retire it and does **not** make native `write_file` reach external
>   roots; or (b) the option-2 gate-pushdown, which is an agentao code change. So the external-root
>   facet is **not** "zero-change host-satisfiable" — only the immutable facet is.

```python
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class _Rule:                          # immutable: swap the whole reference, never mutate fields piecemeal
    writable: tuple[Path, ...]        # roots ADDED outside cwd; cwd is implicit (see ctor)
    immutable: tuple[Path, ...]

def _effective_target(raw: str) -> Path:
    """Where open() would actually write: parent chain resolved (..-safe), leaf symlink followed.
    This — not the literal path — is the only correct basis for a membership test (see note below)."""
    p = Path(raw).expanduser()
    if not p.is_absolute():
        # FileSystem accepts ABSOLUTE paths only (filesystem.py:46); relative resolution is the
        # tool's job (Tool._resolve_path). Reject here so a relative path can't be silently
        # resolved against the *process* cwd instead of the agent's working_directory.
        raise PermissionError(f"FsPolicy: non-absolute path reached the filesystem: {raw}")
    t = p.parent.resolve(strict=False) / p.name      # follow parent-chain symlinks
    if t.is_symlink():                               # open() will follow a leaf symlink too
        t = t.resolve(strict=False)
    return t

def _under(root: Path, target: Path) -> bool:
    root = root.resolve()
    return target == root or root in target.parents

class PolicyFileSystem:
    def __init__(self, inner, working_directory: Path, rule: _Rule):
        self._fs = inner
        self._wd = working_directory.resolve()       # cwd is ALWAYS implicitly writable (augment)
        self._rule = rule
    def set_policy(self, rule: _Rule):                # host-side equivalent of the proposed set_fs_policy
        self._rule = rule                             # atomic ref-swap (GIL) — no torn read
    def _check(self, raw: str):
        rule = self._rule                             # one local snapshot per write
        t = _effective_target(raw)                    # resolve ONCE, then test membership
        if not any(_under(w, t) for w in (self._wd, *rule.writable)):
            raise PermissionError(f"FsPolicy: outside writable roots: {t}")
        if any(_under(m, t) for m in rule.immutable): # immutable wins — leaf-symlink-safe
            raise PermissionError(f"FsPolicy: immutable: {t}")
    def write_text(self, path, data, *, append=False):
        self._check(str(path)); return self._fs.write_text(path, data, append=append)
    def __getattr__(self, name): return getattr(self._fs, name)

# KB host: cwd implicitly writable, raw/ + config carved read-only:
agent = build_from_environment(
    working_directory=kb,
    filesystem=PolicyFileSystem(LocalFileSystem(), kb,
        _Rule(writable=(), immutable=(kb / "raw", kb / "AGENTAO.md"))),
)
```

**Callers must hand the wrapper absolute paths.** The wrapper *is* a `FileSystem` implementation, and
the protocol accepts absolute paths only (`filesystem.py:46`) — relative resolution is the *tool's*
responsibility (`Tool._resolve_path`, `base.py:61`). Built-in `write_file` / `replace` already pass
`write_text` an absolute path (their upstream `contain_file` resolved it), so the wrapper sees
absolute paths from them. But the **wrapper-gated `extra_tool`** that serves chahua's external-root
route must call `self._resolve_path(file_path)` itself *before* `self.filesystem.write_text(...)`;
otherwise a relative `./task/x` would be resolved against the **process** cwd, not the agent's
`working_directory`, and the check would diverge from the actual write. The `not p.is_absolute()`
guard above turns that into a loud refusal instead of a silent process-cwd write. (A host *may*
instead bind relatives to `_wd` inside the wrapper, but that duplicates the resolution policy the
protocol deliberately assigns to tools — prefer resolving in the tool.)

**Why not just reuse `PathPolicy.contain_file` per root (an earlier draft did — it was fail-open).**
`contain_file` is **not** a membership predicate. It asserts the *parent-resolved* path is inside
`project_root` **before** it dereferences a leaf symlink (`path_policy.py:76-85`). So
`cwd/scratch/link → cwd/raw/secret` passes the writable-cwd check, but the immutable-`raw` check
**fails open**: the parent (`cwd/scratch`) isn't under `raw`, so `contain_file(project_root=raw)`
raises *before* following the link into `raw` — the wrapper would wrongly allow the write and
`open()` then clobbers `raw/secret` through the symlink, violating "immutable wins." The correct
basis is the **leaf-dereferenced effective target** tested with `is_relative_to` against every root
(`_effective_target` above). This *does* re-implement ~6 lines of `PathPolicy`'s resolve logic
host-side — which is exactly the security-critical part, and exactly why a correct
multi-root + immutable predicate **cannot** be composed from the single-root primitive today (see
*The agentao-side helper…* below). The host still does not re-derive the algorithm from scratch, but
"reuse `contain_file` and you're safe" was wrong — say so.

**Hot-swap works — but only by mutating the shared instance, never by replacing it.**
`_bind_and_register` copies `agent.filesystem` into each tool's *own* `tool.filesystem` at
registration (`registry.py:78`; tools then read `self.filesystem`, `tools/base.py:43-48`). So
`agent.filesystem = NewWrapper()` is **invisible** to already-registered tools. The working
mechanism: every tool points at the *same* wrapper instance, so `wrapper.set_policy(...)` is seen by
all of them on the next write. Store the policy as one frozen `_Rule` and swap the reference
atomically to avoid a torn read (new `writable` + old `immutable`). Bonus: because the wrapper
re-checks live on every call, it **sidesteps the `for_tool` cache-staleness hazard** that (b) must
fix in the built-in path.

**What the wrapper still cannot do** (unchanged from the body): MCP tools (`mcp_*`) don't pass
through `agent.filesystem`; shell (`echo > f` via `run_shell_command`) goes through the `shell`
capability, not `filesystem` — that is (c), and no filesystem wrapper can reach it.

### Consequence for priority — the facets split

The two facets are **not** equally host-satisfiable, so they get different recommendations:

- **Immutable carve-out (KB host):** fully wrapper-satisfiable today (leaf-deref membership, native
  `write_file`). This *is* demand evidence that the within-cwd boundary is host-satisfiable, and it
  **lowers** the case for building new agent API for that facet.
- **External roots (chahua):** **not** zero-change host-satisfiable for native tools — the built-in
  single-root pre-check (`file_ops.py:197,368`) sits upstream of the wrapper. The host can only get
  it by (a) keeping an `extra_tool` (now wrapper-gated, but not retired) or (b) the option-2
  gate-pushdown — an agentao code change. So this facet **retains a genuine agentao-shaped kernel**:
  making the built-in write tools honor a declared multi-root policy (i.e. slice (a)'s gate-pushdown)
  is the *only* way to retire the bypass and let native `write_file` reach external roots.

So promote beyond the wrapper on a concrete trigger:

- a **third** embedding host needs the boundary (the wrapper is being copy-pasted), or
- the wrapper **breaks** because `FileSystem` / `_bind_and_register` internals shift, or
- a host needs native external-root writes / to retire a bypass tool (the gate-pushdown), or
- a host genuinely needs **(c)** (shell), which no filesystem wrapper can do.

Honest recommendation: **do not build the `fs_policy=` lifecycle API now.** Ship the wrapper recipe
for the immutable facet; treat the external-root gate-pushdown as the first slice worth building when
chahua (or a third host) actually needs native external writes rather than a gated `extra_tool`.

### The agentao-side helper that makes the wrapper *correct*, not just shorter

Because the single-root `contain_file` cannot serve as a multi-root membership predicate (above),
every host wrapper must hand-write `_effective_target` — security-critical resolve logic that a
fraction of hosts *will* get subtly wrong (the exact footgun this whole doc exists to remove). So a
leaf-deref membership helper on the existing primitive is **not** ergonomic sugar; it is the
**correct-reuse surface**:

```python
# resolves the leaf-dereferenced target and tests it against every root; raises on escape/immutable
PathPolicy.contain_any(raw, writable=[...], immutable=[...])
```

Still small — one classmethod over the existing resolve internals, **no** agent-lifecycle API, **no**
new host-contract object. But this is the **one part of the proposal worth landing even while the
`fs_policy=` API stays demand-gated**: it is the difference between a correct host wrapper and a
fail-open one. The interim wrapper *is* usable without it (the `_effective_target` recipe above is
correct), so this is a strong recommendation, not a blocker — land it early to stop each host
re-deriving the security-critical resolve.

## (c) Enforce `FsPolicy` over shell — the hard, platform-gated part

**Scope:** the only part a host genuinely cannot do itself. Hosts should **not** block on this —
they can keep a best-effort snapshot sentinel until it lands.

**Roadmap reconciliation (read before citing the back-link):** (c) *is* the roadmap's §2.3 explicit
non-goal — "✗ Cross-platform strong sandbox — embedding hosts already isolate at process level" —
and its §5 **P2** item "Sandbox backend interface (… linux-bubblewrap …)". So (c) is the
**most-deferred slice of this proposal — P2, not P1 — and may never ship**. This is consistent, not
contradictory: the proposal's near-term value is (a)+(b), which **do not depend on (c)** (stated in
the header). Until (c) exists, enforcing the boundary *over shell* stays a host concern; and **once
(a)'s gate-pushdown lands**, the proposed (a)+(b) design covers every *structured* write tool (the
interim wrapper alone covers only the immutable facet — see *Interim adoption path*).

- **Preferred — OS sandbox:** wire `FsPolicy` into `sandbox/`: derive the writable params from
  `FsPolicy.writable`, and add an **immutable-subpath** capability.
  - Concrete gap 1 — *single param vs. a writable **set***: the current profile takes **one**
    `(param "_RW1")` (`workspace-write.sb:18-19`). `FsPolicy.writable: list[Path]` does **not** map
    onto a single `_RW1` — it needs either multiple params (`_RW1`, `_RW2`, …) or a
    **generated** profile that emits one `(subpath …)` per declared writable root. "Derive `_RW1`
    from `writable`" undersells this; it's profile *generation*, not a one-line param substitution.
  - Concrete gap 2 — *immutable carve*: enforcing `immutable=["raw"]` while cwd is writable needs a
    new profile shape with deny-after-allow ordering, e.g.
    `(allow file-write* (subpath _RW1)) (deny file-write* (subpath _RAW))`. SBPL supports this;
    it's new profile work.
  - Concrete gap 3 — *the closed allow-set isn't actually closed at the OS layer*: the profile also
    unconditionally allows writes to `/tmp`, `/var/tmp`, `/private/tmp`, `/private/var/tmp`, and
    `/private/var/folders/…` (`workspace-write.sb:18-26`, for `npm`/`pip`/build temp). So the shell
    layer's writable set = `FsPolicy.writable ∪ {temp dirs}`, which is **wider** than what (a)
    enforces for structured tools. Either document temp dirs as explicit, intentional exceptions to
    the closed-allow-set invariant, or scope the invariant's "closed" claim to the structured-tool
    layer and call the shell layer "closed modulo build-temp." Do not let the doc imply the sandbox
    enforces the *same* closed set (a) does — it doesn't.
  - Linux has **no** implementation today (`grep landlock` → no match) — landlock/bubblewrap would
    be net-new.
- **Cross-platform fallback (no OS sandbox):** provide a deterministic-where-possible fallback —
  reject shell writes to declared `immutable` domains via pre/post path validation or a diff, or at
  minimum **emit a host-subscribable "write out-of-bounds" signal** (fits the existing
  `host` / `EventStream` contract).
- **Honest framing:** "deterministic" holds only at the OS-sandbox layer. Without it, pre/post diff
  is best-effort against a concurrent or adversarial shell — document it as
  *deterministic-in-sandbox, best-effort-sentinel + out-of-bounds signal otherwise*, not as a
  blanket guarantee.

**Detection ≠ enforcement:** (c) is a write *boundary*. A host whose per-turn scan exists only to
*detect which files changed* (the multi-room host's artifact `diff-scan`, not a guard against
out-of-bounds writes) gains a **new** boundary from (c), not a simplification — its scan stays for
its own purpose. Do not assume every host with a post-turn diff wants (c).

## Why this belongs in agentao (boundary rationale)

"Where a write may land" is a deterministic security boundary, and `PathPolicy` is already
agentao's primitive for exactly that family. This is the natural generalization of an existing
host-contract primitive (`project_root: Path` → a declared `writable`/`immutable` root set) plus
wiring to an existing one (`sandbox/`) — **not** a new product concern, and it asks agentao to learn
**zero** host-domain semantics. Two independent hosts already re-implement it from opposite
directions (within-root subdivision and external multi-root), which is the demand evidence the
roadmap's P1 gating asks for. Shell enforcement specifically *cannot* live host-side without
re-creating a sandbox agentao already owns. Every embedding host inherits the benefit.

## What stays host-side (out of scope for agentao)

- **Which** paths are writable/immutable — host policy, declared via `FsPolicy`, not built in.
- Domain gates (e.g. post-write frontmatter / dead-link checks, ingest snapshot gates) — business
  validation.
- **Transport-layer path authorization** (e.g. the multi-room host validating a remote guest's
  `./share/x` reference over the wire) — a different boundary from in-process tool writes; stays
  host-side even after `FsPolicy` lands.
- **Artifact detection** (post-turn diff-scan to learn *which* files changed) — host-side; orthogonal
  to the write boundary (see (c)).
- Single-writer concurrency (write lock) — host process model.
- The optional invariant-sentinel snapshot — fine to keep as defense-in-depth once (c) lands.

## Suggested sequencing

0. **Now — host-side, zero agentao change (immutable facet only):** inject the `PolicyFileSystem`
   wrapper (see *Interim adoption path*) for the **within-cwd read-only carve-out** (KB host). The
   wrapper must test a **leaf-dereferenced effective target** (not single-root `contain_file` per
   root — that is fail-open for the immutable carve). This deletes the KB host's regex deny-rule
   layer and `set_mode` workaround without waiting for any agentao change. **The external-root facet
   (chahua) does *not* unblock here** — the built-in single-root pre-check rejects out-of-cwd writes
   upstream of the wrapper; chahua keeps a (now wrapper-gated) `extra_tool` until step 1.
0.5 **Land `PathPolicy.contain_any(raw, writable, immutable)`** — small, no new API surface, but it
   centralizes the security-critical leaf-deref resolve so each host's wrapper can't get it wrong.
   This is the *one* slice worth landing early even while the rest stays demand-gated.
1. **Gate-pushdown for the external-root facet** (option 2: built-in write tools honor a declared
   multi-root policy via the `FileSystem` capability; remove their single-root pre-check) — the first
   slice worth *building* when a host needs **native** external-root writes / to retire a bypass tool.
   The full `fs_policy=` / `set_fs_policy` lifecycle API stays **demand-gated** beyond that (third
   host, wrapper-breaking refactor, or (c) — see *Consequence for priority*).
2. **(c)** follows on its own track; until it ships, the snapshot sentinel remains the *only* shell
   write-defense on Linux — document it as load-bearing there, not "optional".
