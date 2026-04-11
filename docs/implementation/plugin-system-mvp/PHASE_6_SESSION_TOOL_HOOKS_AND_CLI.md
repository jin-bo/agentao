# Phase 6: Session Tool Hooks And CLI Diagnostics

## Goal

完成剩余受支持 hook 事件的执行路径，并补齐最小 CLI / diagnostics 面，使插件系统达到可用 MVP。

## Why This Phase Exists

在 `UserPromptSubmit` 之外，剩余 hooks 主要是 side-effect 型 lifecycle hooks。它们依赖前面已经稳定的 schema、payload 和 dispatcher seam，因此适合作为最终阶段收尾。

## Scope

本阶段包含：

- `SessionStart`
- `SessionEnd`
- `PreToolUse`
- `PostToolUse`
- `PostToolUseFailure`
- command hook execution
- tool alias matching
- repeatable `--plugin-dir`
- `agentao plugin list`
- startup diagnostics surface

本阶段不包含：

- `http` hooks
- `agent` hooks
- 非 `UserPromptSubmit` 的 `prompt` hooks
- marketplace / install / remove

## Dependencies

本阶段依赖：

- Phase 1 plugin loading
- Phase 5 hook parser / payload adapter / dispatcher foundation
- 现有 tool runner

## Supported Event Contract

受支持事件：

- `SessionStart`
- `SessionEnd`
- `PreToolUse`
- `PostToolUse`
- `PostToolUseFailure`

支持的 hook type：

- `command`: supported
- `prompt`: warning + skip
- `http`: warning + skip
- `agent`: warning + skip

## Payload Contract

`SessionStart`：

```json
{
  "hook_event_name": "SessionStart",
  "session_id": "uuid-or-null",
  "cwd": "/abs/project",
  "transcript_path": null
}
```

`PreToolUse`：

```json
{
  "hook_event_name": "PreToolUse",
  "tool_name": "Read",
  "tool_input": {"path": "README.md"},
  "cwd": "/abs/project",
  "session_id": "uuid-or-null"
}
```

`PostToolUseFailure`：

```json
{
  "hook_event_name": "PostToolUseFailure",
  "tool_name": "Bash",
  "tool_input": {"command": "bad-cmd"},
  "tool_error": "command failed",
  "cwd": "/abs/project",
  "session_id": "uuid-or-null"
}
```

## Runtime Rules

- 所有这些 hooks 都是 side-effect only
- 不改变 tool input/output
- hook 失败只记 warning
- matcher 和 `if` 基于 Claude-compatible alias

## Proposed APIs

```python
class PluginHookDispatcher:
    def dispatch_session_start(...) -> list[HookAttachmentRecord]: ...
    def dispatch_session_end(...) -> list[HookAttachmentRecord]: ...
    def dispatch_pre_tool_use(...) -> list[HookAttachmentRecord]: ...
    def dispatch_post_tool_use(...) -> list[HookAttachmentRecord]: ...
    def dispatch_post_tool_use_failure(...) -> list[HookAttachmentRecord]: ...
```

CLI：

```text
agentao --plugin-dir /path/to/plugin
agentao plugin list
```

## Lifecycle Sequence

```text
session start
  -> dispatch_session_start()

before tool run
  -> dispatch_pre_tool_use()
  -> execute tool
  -> on success dispatch_post_tool_use()
  -> on failure dispatch_post_tool_use_failure()

session shutdown
  -> dispatch_session_end()
```

## Fixture Coverage

建议主要使用：

- `session-hooks-plugin`
- `tool-hooks-plugin`
- `unsupported-hook-type-plugin`
- `unsupported-hook-event-plugin`
- `duplicate-name-global`
- `duplicate-name-project`
- `unsupported-fields-plugin`

## Issue Backlog

1. 实现 `dispatch_session_start()` / `dispatch_session_end()`
2. 在 tool runner 接入 `dispatch_pre_tool_use()`
3. 在 tool success/failure path 接入 post hooks
4. 实现 repeatable `--plugin-dir`
5. 实现 `agentao plugin list`
6. 编写 lifecycle hooks 和 CLI 测试

## Tests

- `SessionStart` command hook 执行
- `SessionEnd` command hook 执行
- `PreToolUse` payload 使用 Claude-compatible alias
- `PostToolUse` payload 包含稳定字段
- `PostToolUseFailure` payload 包含错误字段
- unsupported hook type warning
- unsupported hook event warning
- failing hook only warns
- repeatable `--plugin-dir`
- `plugin list` 输出 warnings

## Acceptance Criteria

1. 剩余受支持 hook 事件都能执行
2. tool lifecycle hooks 不改变主工具行为
3. CLI 能发现当前加载的 plugins
4. diagnostics 能解释不兼容点和失败原因

## Out Of Scope

- `http` hooks
- `agent` hooks
- non-`UserPromptSubmit` prompt hooks
- marketplace / install / remove

## Related Issues

- [15 Session And Tool Lifecycle Hooks](issues/15-session-and-tool-lifecycle-hooks.md)
- [16 CLI Plugin Discovery And Diagnostics](issues/16-cli-plugin-discovery-and-diagnostics.md)
- [18 Diagnostics Renderer And Compatibility Notes](issues/18-diagnostics-renderer-and-compatibility-notes.md)
