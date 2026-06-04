# Phase 3: Agents

## Goal

把插件中的 `agents` 接入 Agentao 的 agent discovery 和 registration 流程，使 Claude-compatible agent markdown 可以作为插件能力被发现和调用。

## Why This Phase Exists

plugin `agents` 与 `skills/commands` 类似，都是相对静态的资源注册问题，但它们直接连接到 Agentao agent runtime，因此需要单独阶段隔离风险。

## Scope

本阶段包含：

- 默认 `agents/` 目录加载
- manifest `agents` 路径加载
- plugin agent namespacing
- source metadata 标记
- malformed agent 文件隔离与 diagnostics

本阶段不包含：

- MCP
- hooks
- `UserPromptSubmit`
- tool lifecycle hooks

## Dependencies

本阶段依赖：

- Phase 1 的 plugin discovery / loading
- 已存在的 AgentManager 基础设施

## Runtime Contract

- plugin agents 应被视为额外 source，而不是替换现有 agent source
- plugin agents 默认 namespaced
- built-in/project agent 与 plugin agent 重名时为 fatal
- 单个 agent 文件错误不应拖垮整个插件集

## Naming Rules

默认 runtime name：

- `plugin_name:agent_name`

示例：

- `code-review-assistant:reviewer`
- `code-review-assistant:security`

## Proposed Runtime API

```python
class AgentManager:
    def register_plugin_agents(
        self,
        plugin: LoadedPlugin,
        agent_defs: list["PluginAgentDefinition"],
    ) -> None: ...
```

建议类型：

```python
@dataclass
class PluginAgentDefinition:
    runtime_name: str
    plugin_name: str
    source_path: Path
    raw_markdown: str
    description: str | None = None
```

## Discovery Rules

- 默认扫描 `agents/*.md`
- manifest `agents` 可追加额外 markdown 文件
- 所有路径都必须在 plugin root 内

## Error Handling

- 单个 markdown 文件 malformed：
  - 记录 diagnostics
  - 跳过该 agent
- plugin 内 agent runtime name collision：
  - fatal for that plugin
- plugin 与已有 agent collision：
  - fatal

## Fixture Coverage

建议主要使用：

- `agents-only-plugin`
- `malformed-agent-plugin`
- `full-plugin`

## Lifecycle Sequence

```text
PluginManager.load_plugins()
  -> for each LoadedPlugin
  -> discover agent markdown files
  -> parse into PluginAgentDefinition list
  -> validate names and collisions
  -> AgentManager.register_plugin_agents(...)
```

## Issue Backlog

1. 为 AgentManager 增加 plugin registration seam
2. 实现默认 `agents/` 扫描
3. 实现 manifest `agents` 额外路径支持
4. 实现 namespacing 和 source metadata
5. 实现 malformed agent isolation diagnostics
6. 编写 agent 集成测试

## Tests

- plugin agent 正确发现
- namespaced name 正确
- source metadata 正确
- malformed agent isolated failure
- plugin agent list 可见

## Acceptance Criteria

1. plugin agents 能被发现
2. plugin agents 能进入 agent list
3. malformed agent 不会拖垮其它 plugins
4. 重名冲突有 clear diagnostics

## Out Of Scope

- MCP
- hooks
- message injection

## Related Issues

- [08 Plugin Agents Registration](issues/08-plugin-agents-registration.md)
