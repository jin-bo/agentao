# Phase 1: Manifest And Loader

## Goal

建立插件系统的基础读取层，让 Agentao 能稳定发现插件目录、解析 `plugin.json`、决议 precedence，并生成可诊断的 `LoadedPlugin` 列表。

这一阶段不接入 skills、agents、MCP、hooks 的运行时，只负责“找得到、读得对、决议稳定、错误可诊断”。

## Why This Phase Exists

如果 manifest、discovery、precedence 没先稳定，后续任何 skills / hooks / MCP 集成都会建立在不可靠输入上，最终很难调试。

这一阶段是整个 plugin 系统的地基。

## Scope

本阶段包含：

- `plugin.json` Python schema
- manifest 结构校验
- manifest path safety 校验
- global/project/inline 三层 plugin discovery
- `plugins_config.json` disable 规则
- 同名 plugin precedence 决议
- `LoadedPlugin` 的基础构建
- warnings / errors / diagnostics snapshot

本阶段不包含：

- skill registration
- command mapping
- agent registration
- MCP registration
- hook execution
- CLI 交互命令

## MVP Contract For This Phase

- 插件目录来源：
  - `<home>/.agentao/plugins`
  - `<project-root>/.agentao/plugins`
  - `--plugin-dir`
- 插件 identity 使用 `plugin.json.name`
- precedence 规则：
  - global < project < inline
- 同名插件只做原子覆盖，不做组件级 merge
- 已知不支持字段保留 warning，不导致整个插件失败
- 不安全路径直接 fatal

## Proposed Files

新文件：

- `agentao/plugins/__init__.py`
- `agentao/plugins/models.py`
- `agentao/plugins/manifest.py`
- `agentao/plugins/loader.py`
- `agentao/plugins/manager.py`
- `agentao/plugins/diagnostics.py`

测试文件：

- `tests/test_plugin_manifest.py`
- `tests/test_plugin_loader.py`

可能改动：

- `agentao/cli/subcommands.py`
  - 仅为后续 `--plugin-dir` 参数预留接线点时才需要

## Data Model

建议最小类型：

```python
@dataclass
class PluginAuthor:
    name: str
    email: str | None = None
    url: str | None = None


@dataclass
class PluginDependencyRef:
    plugin_name: str
    version: str | None = None
    marketplace: str | None = None


@dataclass
class PluginCommandMetadata:
    source: str | None = None
    content: str | None = None
    description: str | None = None
    argument_hint: str | None = None
    model: str | None = None
    allowed_tools: list[str] = field(default_factory=list)


@dataclass
class PluginManifest:
    name: str
    version: str | None = None
    description: str | None = None
    author: PluginAuthor | None = None
    homepage: str | None = None
    repository: str | None = None
    license: str | None = None
    keywords: list[str] = field(default_factory=list)
    dependencies: list[PluginDependencyRef] = field(default_factory=list)
    commands: str | list[str] | dict[str, PluginCommandMetadata] | None = None
    skills: str | list[str] | None = None
    agents: str | list[str] | None = None
    hooks: str | dict[str, Any] | list[str | dict[str, Any]] | None = None
    mcp_servers: str | dict[str, Any] | None = None
    unsupported_fields: dict[str, Any] = field(default_factory=dict)


@dataclass
class PluginCandidate:
    name: str
    root_path: Path
    source: Literal["global", "project", "inline"]
    source_rank: int
    manifest: PluginManifest
    warnings: list["PluginWarning"]


@dataclass
class LoadedPlugin:
    name: str
    version: str | None
    root_path: Path
    source: Literal["global", "project", "inline"]
    manifest: PluginManifest
    skill_roots: list[Path]
    command_paths: list[Path]
    agent_paths: list[Path]
    hook_specs: list[Any]
    mcp_servers: dict[str, dict[str, Any]]
    warnings: list["PluginWarning"]
```

## Parser And Loader APIs

```python
class PluginManifestParser:
    def parse_file(self, plugin_root: Path) -> PluginManifest: ...
    def parse_dict(self, raw: dict[str, Any], *, plugin_root: Path) -> PluginManifest: ...
    def validate_paths(self, manifest: PluginManifest, *, plugin_root: Path) -> list[PluginWarning]: ...


class PluginManager:
    def discover_candidates(self) -> list[PluginCandidate]: ...
    def filter_disabled(self, candidates: list[PluginCandidate]) -> list[PluginCandidate]: ...
    def resolve_precedence(self, candidates: list[PluginCandidate]) -> list[PluginCandidate]: ...
    def load_plugin(self, candidate: PluginCandidate) -> LoadedPlugin: ...
    def load_plugins(self) -> list[LoadedPlugin]: ...
    def list_plugins(self) -> list[LoadedPlugin]: ...
    def get_warnings(self) -> list[PluginWarning]: ...
    def get_errors(self) -> list[PluginLoadError]: ...
```

## Validation Rules

结构规则：

- `name` 必填
- `name` 不能为空
- `name` 不允许空格
- `commands` 支持 path、path list、metadata mapping
- `skills` / `agents` 支持 path 或 path list
- `hooks` 支持 path、inline object、混合数组
- `mcpServers` 支持 path 或 inline object

安全规则：

- 所有相对路径必须以 `./` 开头
- resolve 后仍位于 plugin root 内
- 禁止 `../`
- 禁止绝对路径
- 禁止 symlink 越界

兼容性规则：

- 这些字段 warning + preserve：
  - `outputStyles`
  - `lspServers`
  - `settings`
  - `channels`
  - `userConfig`

## Discovery And Precedence

目录层级：

1. global: `<home>/.agentao/plugins`
2. project: `<project-root>/.agentao/plugins`
3. inline: `--plugin-dir`

precedence：

1. 先按 `plugin.json.name` 分组
2. 再按 `source_rank` 选择最终生效候选
3. 同名 plugin 不做跨层 merge

disable 规则：

- global config: `<home>/.agentao/plugins_config.json`
- project config: `<project-root>/.agentao/plugins_config.json`
- project config 对当前项目最终态有更高优先级

## Example Inputs

最小 `plugin.json`：

```json
{
  "name": "demo-plugin",
  "version": "0.1.0",
  "description": "Minimal Agentao / Claude-compatible demo plugin"
}
```

完整 `plugin.json`：

```json
{
  "name": "code-review-assistant",
  "version": "0.3.0",
  "description": "Project plugin for code review workflows",
  "skills": ["./skills"],
  "commands": {
    "review-summary": {
      "source": "./commands/review-summary.md",
      "description": "Summarize the current patch for review",
      "argumentHint": "[scope]"
    }
  },
  "agents": ["./agents/reviewer.md"],
  "hooks": ["./hooks/hooks.json"],
  "mcpServers": "./.mcp.json"
}
```

## Lifecycle Sequence

```text
Agentao startup
  -> PluginManager.discover_candidates()
  -> apply plugins_config.json
  -> resolve precedence
  -> load resolved plugins
  -> collect warnings/errors
  -> expose LoadedPlugin list
```

## Fixture Coverage

建议主要使用这些 fixtures：

- `minimal-plugin`
- `full-plugin`
- `inline-config-plugin`
- `unsupported-fields-plugin`
- `invalid-json-plugin`
- `path-traversal-plugin`
- `duplicate-name-global`
- `duplicate-name-project`

## Issue Backlog

1. 定义 plugin models 和 warnings/errors 类型
2. 实现 `PluginManifestParser.parse_dict()`
3. 实现 path validation
4. 实现 discovery 和 precedence
5. 实现 `plugins_config.json` disable rules
6. 实现 diagnostics snapshot 与单测

## Tests

- valid minimal `plugin.json`
- malformed JSON
- unsupported fields warning
- path traversal rejection
- same-name plugin precedence
- project override global
- disabled plugin filtering
- broken plugin does not block others

## Acceptance Criteria

满足以下条件即可完成本阶段：

1. Agentao 能稳定发现三层 plugin 目录
2. 能基于 `plugin.json.name` 做 precedence 决议
3. 能输出稳定的 `LoadedPlugin` 列表
4. 已知 unsupported field 记录 warning
5. 不安全路径直接 fail
6. 单个坏插件不会阻断其它插件加载

## Out Of Scope

- skills runtime
- commands runtime
- agents runtime
- MCP runtime
- hook dispatch
- message injection

## Related Issues

- [01 Plugin Models And Diagnostics Types](issues/01-plugin-models-and-diagnostics-types.md)
- [02 Plugin Manifest Parser](issues/02-plugin-manifest-parser.md)
- [03 Manifest Path Safety Validation](issues/03-manifest-path-safety-validation.md)
- [04 Plugin Discovery Disable Rules And Precedence](issues/04-plugin-discovery-disable-rules-and-precedence.md)
- [05 LoadedPlugin Assembly And Diagnostics Snapshot](issues/05-loadedplugin-assembly-and-diagnostics-snapshot.md)
