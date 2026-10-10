"""The official ACP Registry: query it and turn an entry into ``acp.json`` config.

The Registry (https://github.com/agentclientprotocol/registry) publishes one
JSON index of ACP agents and how to launch them. This module reads that index
and converts a selected entry into the ``command`` / ``args`` / ``env`` /
``cwd`` server shape that ``.agentao/acp.json`` already uses. Nothing here
launches an agent, writes a file, or registers a server — the caller decides
(the CLI's ``/acp registry add`` does all three, with confirmation);
:func:`agentao.acp_client.config.add_server_entry` writes the entry and
:meth:`ACPManager.add_server` registers it.

Scope (first version):

- ``npx`` and ``uvx`` distributions only. When an entry offers both, ``npx``
  is chosen unless the caller asks for ``uvx``. Binary-only entries are
  rejected: downloading and unpacking archives is out of scope.
- The package spec must pin the entry's own ``version`` in one of the
  supported spellings — ``name@X``, ``@scope/name@X`` (npm), ``name==X`` /
  ``name@X``, with optional ``[extras]`` (uv). Anything else is rejected
  rather than guessed at.
- Environment values containing ``$`` are rejected: ``acp.json`` expands
  ``$VAR`` / ``${VAR}`` in ``env`` with :func:`os.path.expandvars`, which has
  no escape, so such a value could not be stored as written.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from .. import json_parse

#: The official stable index.
REGISTRY_URL = "https://cdn.agentclientprotocol.com/registry/v1/latest/registry.json"

#: Supported runners, in the order a two-runner entry is resolved.
SUPPORTED_RUNNERS = ("npx", "uvx")

#: ``startupTimeoutMs`` written for Registry entries. The first launch
#: downloads the package (npm / PyPI), which the 10 s default for
#: hand-written entries does not allow for.
REGISTRY_STARTUP_TIMEOUT_MS = 120_000

#: Upper bound on the index size read from the network.
_MAX_INDEX_BYTES = 8 * 1024 * 1024

_NPM_SPEC = re.compile(r"^(?P<name>(?:@[a-z0-9][\w.-]*/)?[a-z0-9][\w.-]*)@(?P<version>[^@\s]+)$", re.I)
_UV_SPEC = re.compile(
    r"^(?P<name>[a-z0-9][\w.-]*)(?P<extras>\[[\w.,\s-]+\])?(?:==|@)(?P<version>[^=@\s]+)$", re.I,
)


class RegistryError(Exception):
    """The Registry could not be read, or an entry cannot be used as asked."""


@dataclass(frozen=True)
class RegistryAgent:
    """One agent entry from the Registry index."""

    id: str
    name: str
    version: str
    description: str = ""
    distribution: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @property
    def runners(self) -> List[str]:
        """Supported runners this entry offers, in resolution order."""
        return [r for r in SUPPORTED_RUNNERS if isinstance(self.distribution.get(r), dict)]

    @property
    def distribution_types(self) -> List[str]:
        """Every distribution type the entry declares (``npx``, ``binary``, …)."""
        return sorted(self.distribution)


@dataclass(frozen=True)
class ServerEntry:
    """A Registry entry converted to an ``acp.json`` server object."""

    agent: RegistryAgent
    runner: str
    package: str
    config: Dict[str, Any]

    @property
    def command_line(self) -> List[str]:
        """The launch command, for display before the user confirms."""
        return [self.config["command"], *self.config["args"]]


# ---------------------------------------------------------------------------
# Reading the index
# ---------------------------------------------------------------------------


def parse_registry(data: Any) -> List[RegistryAgent]:
    """The well-formed agent entries of a parsed index.

    An entry missing a string ``id`` / ``name`` / ``version`` or an object
    ``distribution`` is skipped, so one malformed entry does not hide the
    rest of the index.

    Raises:
        RegistryError: *data* is not an index object with an ``agents`` list.
    """
    if not isinstance(data, dict) or not isinstance(data.get("agents"), list):
        raise RegistryError("the Registry index has no 'agents' list")
    agents: List[RegistryAgent] = []
    for raw in data["agents"]:
        if not isinstance(raw, dict):
            continue
        ident, name, version = raw.get("id"), raw.get("name"), raw.get("version")
        dist = raw.get("distribution")
        if not all(isinstance(v, str) and v for v in (ident, name, version)):
            continue
        if not isinstance(dist, dict):
            continue
        description = raw.get("description")
        agents.append(RegistryAgent(
            id=ident, name=name, version=version,
            description=description if isinstance(description, str) else "",
            distribution=dict(dist), raw=dict(raw),
        ))
    return agents


def fetch_registry(
    url: str = REGISTRY_URL,
    *,
    timeout: float = 15.0,
    client: Any = None,
) -> List[RegistryAgent]:
    """Download and parse the Registry index.

    Args:
        url: Index URL; the official stable index by default.
        timeout: Seconds for the whole request.
        client: An ``httpx.Client`` to use instead of a fresh one (tests,
            hosts with their own proxy / transport settings).

    Raises:
        RegistryError: Network failure, non-200 status, oversized or
            non-JSON body, or a body that is not an index.
    """
    import httpx

    owned = client is None
    http = client if client is not None else httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        try:
            with http.stream("GET", url, timeout=timeout) as response:
                if response.status_code != 200:
                    raise RegistryError(
                        f"could not read the ACP Registry: HTTP {response.status_code} from {url}"
                    )
                chunks: List[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > _MAX_INDEX_BYTES:
                        raise RegistryError(
                            f"the ACP Registry index is larger than {_MAX_INDEX_BYTES} bytes"
                        )
                    chunks.append(chunk)
        except httpx.HTTPError as exc:
            raise RegistryError(f"could not read the ACP Registry: {exc}") from exc
    finally:
        if owned:
            http.close()
    try:
        data = json_parse.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RegistryError(f"the ACP Registry index is not valid JSON: {exc}") from exc
    return parse_registry(data)


def search_registry(agents: Iterable[RegistryAgent], query: str) -> List[RegistryAgent]:
    """Entries whose id, name or description contains *query* (any case).

    An exact id match comes first, then id / name matches, then description
    matches; an empty query returns every entry.
    """
    needle = query.strip().lower()
    items = list(agents)
    if not needle:
        return items

    def rank(agent: RegistryAgent) -> Optional[int]:
        if agent.id.lower() == needle:
            return 0
        if needle in agent.id.lower() or needle in agent.name.lower():
            return 1
        if needle in agent.description.lower():
            return 2
        return None

    ranked = [(r, i, a) for i, a in enumerate(items) if (r := rank(a)) is not None]
    return [a for _, _, a in sorted(ranked, key=lambda t: (t[0], t[1]))]


def find_agent(agents: Iterable[RegistryAgent], agent_id: str) -> RegistryAgent:
    """The entry with id *agent_id*.

    Raises:
        RegistryError: No entry has that id.
    """
    for agent in agents:
        if agent.id == agent_id:
            return agent
    raise RegistryError(f"no ACP Registry agent with id {agent_id!r}")


# ---------------------------------------------------------------------------
# Entry → acp.json server object
# ---------------------------------------------------------------------------


def package_version(runner: str, package: str) -> Optional[str]:
    """The version pinned by *package* for *runner*; ``None`` if unsupported."""
    pattern = _NPM_SPEC if runner == "npx" else _UV_SPEC
    match = pattern.match(package)
    return match.group("version") if match else None


def entry_to_server_config(
    agent: RegistryAgent,
    *,
    runner: Optional[str] = None,
    cwd: str = ".",
    startup_timeout_ms: int = REGISTRY_STARTUP_TIMEOUT_MS,
) -> ServerEntry:
    """Convert *agent* to an ``acp.json`` server object.

    The result keeps the entry's package spec, arguments and environment;
    runs from *cwd* (``"."`` — the project root ``acp.json`` resolves it
    against); and sets ``autoStart: false`` so adding an entry never starts
    it, and a ``startupTimeoutMs`` long enough for a first-run download.
    ``npx`` gets ``--yes`` before the package so its install prompt never
    waits on a terminal that is not there.

    Args:
        agent: The Registry entry.
        runner: ``"npx"`` or ``"uvx"``; by default the first of those the
            entry offers.

    Raises:
        RegistryError: The entry offers no supported runner (or not the one
            asked for), its package spec is malformed or does not pin the
            entry's version, or an argument / environment value cannot be
            stored as written.
    """
    offered = agent.runners
    if runner is None:
        if not offered:
            kinds = ", ".join(agent.distribution_types) or "none"
            raise RegistryError(
                f"{agent.id} is distributed as {kinds}; only npx and uvx are supported"
            )
        runner = offered[0]
    elif runner not in SUPPORTED_RUNNERS:
        raise RegistryError(f"unsupported runner {runner!r}; use npx or uvx")
    elif runner not in offered:
        raise RegistryError(f"{agent.id} has no {runner} distribution")

    dist = agent.distribution[runner]
    package = dist.get("package")
    if not isinstance(package, str) or not package:
        raise RegistryError(f"{agent.id}: the {runner} distribution has no package")
    pinned = package_version(runner, package)
    if pinned is None:
        raise RegistryError(
            f"{agent.id}: unsupported {runner} package spec {package!r}; "
            f"expected a pinned version such as "
            + ("'name@1.2.3'" if runner == "npx" else "'name==1.2.3'")
        )
    if pinned != agent.version:
        raise RegistryError(
            f"{agent.id}: package {package!r} pins {pinned}, "
            f"but the Registry entry is version {agent.version}"
        )

    args = dist.get("args") or []
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise RegistryError(f"{agent.id}: 'args' must be a list of strings")
    env = dist.get("env") or {}
    if not isinstance(env, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in env.items()
    ):
        raise RegistryError(f"{agent.id}: 'env' must map strings to strings")
    expanded = sorted(k for k, v in env.items() if "$" in v)
    if expanded:
        raise RegistryError(
            f"{agent.id}: env value(s) for {', '.join(expanded)} contain '$', which "
            f"acp.json would expand as a variable reference; add this agent by hand"
        )

    runner_args = ["--yes", package] if runner == "npx" else [package]
    config: Dict[str, Any] = {
        "command": runner,
        "args": [*runner_args, *args],
        "env": dict(env),
        "cwd": cwd,
        "autoStart": False,
        "startupTimeoutMs": int(startup_timeout_ms),
        "description": f"{agent.name} {agent.version} (ACP Registry: {agent.id})",
    }
    return ServerEntry(agent=agent, runner=runner, package=package, config=config)
