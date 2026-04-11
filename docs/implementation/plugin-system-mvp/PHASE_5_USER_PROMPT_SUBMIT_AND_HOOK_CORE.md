# Phase 5: UserPromptSubmit And Hook Core

## Goal

实现 Claude-compatible 的 hook schema、payload adapter，以及最关键的 `UserPromptSubmit` 执行路径和消息注入模型。

## Why This Phase Exists

hooks 是整个插件系统里最复杂的能力，而 `UserPromptSubmit` 又是唯一直接影响消息流的事件。必须把它单独作为阶段处理，避免影响其它 runtime path。

## Scope

本阶段包含：

- `hooks/hooks.json` Claude-compatible subset schema
- `ToolAliasResolver`
- `ClaudeHookPayloadAdapter`
- `PluginHookDispatcher.dispatch_user_prompt_submit()`
- `command` and `prompt` support for `UserPromptSubmit`
- `HookAttachmentRecord`
- `PreparedTurnMessage`
- `PreparedUserTurn`
- `prepare_user_turn()`
- Claude-compatible attachment normalization

本阶段不包含：

- `SessionStart`
- `SessionEnd`
- `PreToolUse`
- `PostToolUse`
- `PostToolUseFailure`
- CLI diagnostics polish

## Dependencies

本阶段依赖：

- Phase 1 plugin loading
- 已有 message history 模型
- 未来 hook execution runner 的基础 shell 执行能力

## Supported Hook Contract

`UserPromptSubmit`：

- `command`: supported
- `prompt`: supported
- `http`: warning + skip
- `agent`: warning + skip

其它事件：

- 本阶段只做 schema parse，不做执行

## Message Injection Contract

必须与 Claude-compatible 模型保持一致：

1. 先生成 attachment-like records
2. 再 normalize 为 meta user messages
3. 不直接把自由文本拼接到用户原始输入

支持的 attachment 语义：

- `hook_additional_context`
- `hook_success`
- `hook_stopped_continuation`
- `hook_blocking_error`

## Proposed Types

```python
@dataclass
class HookAttachmentRecord:
    attachment_type: str
    payload: dict[str, Any]
    hook_name: str
    hook_event: str
    tool_use_id: str
    uuid: str
    timestamp: str


@dataclass
class PreparedTurnMessage:
    role: Literal["user", "assistant", "system", "tool"]
    content: str
    is_meta: bool = False
    source: str | None = None


@dataclass
class PreparedUserTurn:
    original_user_message: str
    hook_attachments: list[HookAttachmentRecord]
    normalized_messages: list[PreparedTurnMessage]
    should_query: bool
    stop_reason: str | None = None


@dataclass
class UserPromptSubmitResult:
    blocking_error: str | None = None
    prevent_continuation: bool = False
    stop_reason: str | None = None
    additional_contexts: list[str] = field(default_factory=list)
    messages: list[HookAttachmentRecord] = field(default_factory=list)
```

## Proposed APIs

```python
class ClaudeHooksParser:
    def parse_file(self, path: Path) -> dict[str, Any]: ...
    def parse_dict(self, raw: dict[str, Any]) -> dict[str, Any]: ...


class ToolAliasResolver:
    def to_claude_name(self, agentao_tool_name: str) -> str: ...


class ClaudeHookPayloadAdapter:
    def build_user_prompt_submit(
        self,
        *,
        user_message: str,
        session_id: str | None,
        cwd: Path,
        plugin: LoadedPlugin | None,
    ) -> dict[str, Any]: ...


class PluginHookDispatcher:
    def dispatch_user_prompt_submit(
        self,
        *,
        payload: dict[str, Any],
        loaded_plugins: list[LoadedPlugin],
    ) -> UserPromptSubmitResult: ...
```

## `prepare_user_turn()` Sequence

```text
user submits prompt
  -> build UserPromptSubmit payload
  -> dispatch matching hooks serially
  -> collect SingleHookResult values
  -> aggregate to UserPromptSubmitResult
  -> convert attachment records to PreparedTurnMessage(meta)
  -> if blockingError/preventContinuation:
       -> do not append original user message
     else:
       -> append original user message
  -> return PreparedUserTurn
```

## Prompt Hook Rules

- 只允许出现在 `UserPromptSubmit`
- 必须返回结构化 JSON 子集
- invalid free-form output 只记 warning
- prompt hook 自己不能再次触发 `UserPromptSubmit`

## Fixture Coverage

建议主要使用：

- `user-prompt-submit-command-plugin`
- `user-prompt-submit-prompt-plugin`
- `user-prompt-submit-blocking-plugin`
- `unsupported-hook-type-plugin`
- `unsupported-hook-event-plugin`

## Issue Backlog

1. 实现 `ClaudeHooksParser`
2. 实现 `ToolAliasResolver`
3. 实现 `ClaudeHookPayloadAdapter.build_user_prompt_submit()`
4. 实现 `PluginHookDispatcher.dispatch_user_prompt_submit()`
5. 实现 `prepare_user_turn()` 和 normalization
6. 编写 `UserPromptSubmit` hooks 测试

## Tests

- `UserPromptSubmit` command hook 执行
- `UserPromptSubmit` prompt hook 执行
- payload 包含原始用户输入
- payload 不包含 reminder 注入文本
- attachment shape Claude-compatible
- normalization 模板稳定
- `additionalContext` 生效
- `blockingError` 阻止 query
- `preventContinuation` 阻止 query
- prompt hook invalid free-form output warning
- prompt hook non-recursive

## Acceptance Criteria

1. `UserPromptSubmit` 能在 query 前运行
2. `command` / `prompt` hook 行为符合设计
3. attachment 到 meta message 的归一化与 Claude-compatible 约定一致
4. 错误只产生 warning，不中断主流程

## Out Of Scope

- session hooks
- tool lifecycle hooks
- CLI polish

## Related Issues

- [10 Claude Hooks Parser And Runtime Validation](issues/10-claude-hooks-parser-and-runtime-validation.md)
- [11 Tool Alias Resolver And Hook Payload Adapters](issues/11-tool-alias-resolver-and-hook-payload-adapters.md)
- [12 UserPromptSubmit Command Hooks](issues/12-userpromptsubmit-command-hooks.md)
- [13 UserPromptSubmit Prompt Hooks](issues/13-userpromptsubmit-prompt-hooks.md)
- [14 Prepare User Turn And Attachment Normalization](issues/14-prepare-user-turn-and-attachment-normalization.md)
