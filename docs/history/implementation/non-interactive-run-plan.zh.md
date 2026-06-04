# 非交互式 `agentao run` —— 设计方案

**日期：** 2026-05-08
**状态：** 草案（M0 范围）

---

## TL;DR

新增 `agentao run` 子命令：

1. 从 stdin 或 `--spec <file>` 读取结构化 run spec；
2. 与显式 CLI flags 合并；
3. 执行一次 Agentao turn；
4. 输出机器可读的结果。

```bash
agentao run --format json < task.yaml
agentao run --spec task.yaml --format text
agentao run --prompt "Summarize this directory" --format json
```

`agentao run` 是自动化入口。交互式 REPL 与 `agentao -p` 保留。本次工作完成后，`-p` 重写为同一个 pipeline 的轻量 shim，两者共用一张 exit-code 表。

---

## 为什么要做

`agentao -p` 故意做得简单。在自动化场景下不够用：

- 运行时设置散落在 flags、env vars、cwd、CLI 默认值之间；
- stdin 被当作提示词文本，不是结构化任务对象；
- 调用方需要解析自然语言输出才能取到 status / replay 路径 / token 用量 / 失败原因；
- CI 无法在一个任务模板上做小范围 per-run override。

`agentao run` 为这个用例提供稳定契约：

```text
run spec + CLI overrides → 一次 Agentao turn → 结构化结果
```

---

## 范围

**M0（本文档）：** 子命令、spec 加载器、合并规则、`text` / `json` 输出、exit codes、非交互中止路径、`-p` shim、基础测试。

**Post-MVP（单独跟踪，不在 M0）：**

- `--format jsonl` 实时事件流 + 新增 `RunLifecycleEvent`
- `attachments:` 字段
- `provider:` selector（多 provider 的 env-var 前缀）
- `plugins:` per-run 额外目录（并发隔离）
- SIGINT 精确 JSONL 终止
- JSON Schema 快照
- 会话 resume

把 M0 切到这个尺寸，能用 1–2 个 PR 落地，不破坏任何现有 transport / engine 签名。

---

## Non-Goals

- 不让所有 Agentao 子命令都接 YAML/JSON stdin。
- 不引入 GJSON/JQ 风格的 transform 语言。
- 不引入 OpenAPI 风格的 flag metadata。
- 不把内部 replay/debug payload 暴露成 run-result schema。
- `agentao run` 不做交互式审批。失败显式上报。
- 不提供 `approve_all` 快捷选项。完全放行用 `permission_mode: full-access`——诚实且可审计。
- 不引入并行的权限匹配器。spec 级规则复用 `PermissionEngine`。
- 不新增 `Transport` 异常类型，不改 `confirm_tool` 签名。非交互中止路径基于现有原语实现：`CancellationToken` 加同步 `EventStream.add_observer(...)`（通过新增的两个 thin pass-through `Agent.add_event_observer` / `remove_event_observer` 访问），详见 Design Decisions。
- 不替代 ACP。ACP 是长期协议面；`agentao run` 是一次性本地进程面。

---

## 用户使用形态

### 通过 stdin 传入 spec

```bash
agentao run --format json < task.yaml
```

```yaml
prompt: "Review this repository for obvious test failures."
permission_mode: read-only
model: gpt-5.4
max_iterations: 8
skills:
  - code-review
replay: true
```

### 用 flags 覆盖 spec

```bash
agentao run \
  --model gpt-5.5 \
  --permission-mode workspace-write \
  --format json \
  < task.yaml
```

规则：

```text
effective spec = 默认值 + stdin/file spec + 显式 CLI flags
```

只有用户**显式提供**的 flag 才会覆盖 spec。argparse 的默认值不能算作用户意图。

### 从文件读 spec

```bash
agentao run --spec .agentao/tasks/review.yaml --format json
```

`--spec` 与 piped stdin 同时出现 → 清晰报错（exit `2`）。一次运行只接受一个结构化 spec 来源。

### 内联 prompt 便利写法

```bash
agentao run --prompt "Summarize the current directory" --format json
```

适合需要结构化输出但不想写 YAML 文件的调用方。它不替代 `agentao -p`，只是共享同一个结果契约。

---

## M0 Run Spec

```yaml
prompt: string
cwd: string
model: string
base_url: string
permission_mode: read-only | workspace-write | full-access | plan
interaction_policy: reject       # M0 仅接受 "reject"
permissions:
  allow:
    - tool: string               # glob —— 与 user-scope 规则同语法
      args: { ... }              # 可选 arg-pattern map
      domain:                    # 可选 URL/domain 匹配
        url_arg: string
        allowlist: [string]
        blocklist: [string]
  deny:
    - tool: string
      args: { ... }
      domain: { ... }
max_iterations: int
skills:
  - string
replay: boolean
output:
  format: text | json
```

### 字段说明

- `prompt`：未提供 `--prompt` 时必填。
- `cwd`：本次运行的工作目录，相对路径相对进程 cwd 解析，映射到 `Agent.working_directory`。`RunResult.cwd` 返回解析后的绝对路径。
- `model`、`base_url`：仅本次运行覆盖 env-derived LLM 设置，通过 `**overrides` 走 `build_from_environment(...)`。**spec 不接受 `api_key`** ——secrets 留在环境变量或 host 注入的客户端里。
- `permission_mode`：现有 Agentao 模式（`agentao/permissions.py:71-76`）。默认与交互式 CLI 一致（`workspace-write`）。CI 示例应显式设 `read-only`。
- `interaction_policy`：M0 仅接受 `reject`。ASK 且无 `permissions.allow` 命中 → `permission_required`（exit `3`）。`ask_user(...)` → `interaction_required`（exit `3`）。字段省略时按 `reject` 处理。其他取值预留。
- `permissions.allow` / `permissions.deny`：注入到现有 `PermissionEngine`，仅本次运行有效。匹配器与规则形状与 `~/.agentao/permissions.json` 完全一致（见 `agentao/permissions.py::_matches`）。优先级与 provenance 见 Design Decisions 的 **Permissions** 节。
- `max_iterations`：现有 chat-loop 上限。spec / flag 都不设时默认 `100`（`Agent.chat(max_iterations=100)`）。
- `skills`：turn 开始前要激活的技能名。append 到已发现的 active skills，不替换。任一缺失 → 在 turn 开始前失败。
- `replay`：本次运行启用 `ReplayManager`。
- `output.format`：spec 级别的输出默认值，被 `--format` 覆盖。

未知 spec 字段默认报错（`extra="forbid"`）。如确有跨版本兼容需求，可后续加 `--ignore-unknown`。

---

## 合并规则

优先级：

1. 内置默认值。
2. 来自 `--spec` 或 stdin 的 spec。
3. 显式 CLI flags。

规则：

- 标量：后者覆盖前者。
- 列表：显式 CLI 列表替换 spec 列表（如重复 `--skill` 替换 `skills:`）。
- YAML/JSON 中的 `null` 清空一个可选 spec 字段。
- 未知 spec 字段 → exit `2`。

实现必须跟踪哪些 flag 是用户显式传入的；argparse 默认值不算用户意图。

---

## 输出契约

### `--format text`

stdout 只输出最终助手文本。诊断信息走 stderr。这是与 `agentao -p` 最接近的形态，但走的是新 pipeline。

### `--format json`

运行结束后输出一个 JSON 对象。

成功：

```json
{
  "status": "ok",
  "session_id": "session-id",
  "turn_id": "turn-id",
  "cwd": "/abs/path/to/project",
  "model": "gpt-5.5",
  "final_text": "The tests fail because ...",
  "replay_path": ".agentao/replays/session-id.jsonl",
  "usage": {
    "prompt_tokens": 12000,
    "completion_tokens": 900,
    "total_tokens": 12900
  },
  "tool_calls": 7,
  "warnings": []
}
```

失败：

```json
{
  "status": "error",
  "session_id": "session-id",
  "turn_id": "turn-id",
  "cwd": "/abs/path/to/project",
  "model": "gpt-5.5",
  "error": {
    "type": "permission_required",
    "message": "run_shell_command requires approval in this mode",
    "tool_name": "run_shell_command",
    "tool_call_id": "call_01HX..."
  },
  "replay_path": ".agentao/replays/session-id.jsonl",
  "warnings": []
}
```

error envelope 字段规则：

- `type` 与 `message` 始终存在。
- `tool_name` 在 `permission_required` / `permission_denied` / `interaction_required` 时存在。`ask_user(...)` 触发的 `interaction_required` 中 `tool_name` 为 `"ask_user"`。
- `tool_call_id` 在运行进入 permission decision 阶段时存在。从 run pipeline 已通过 `Agent.add_event_observer(...)` 注册的同步 observer 接收的 `permission_decision` event 上读出（D1）。
- **结构化输出永不写入原始 `args`。** 它们可能携带用户数据、路径、部分 secret。需要原始 `args` 的调用方读 `replay_path`。

`cwd` 与 `model` 是本次运行实际生效的解析后值，便于审计。

`--format json` 模式下 stdout 仅含该 JSON 对象，诊断信息走 stderr。

M0 故意不带 `schema_version`，让 result envelope 与 host events 保持同形（host events 也没有该字段）。后续若引入 `run-result-1.0.json` schema 快照，再做版本化。

---

## Exit Codes

| Code | 含义 |
| ---- | ---- |
| `0`  | 运行成功 |
| `1`  | 运行时错误 / provider 错误 / 未预期失败 |
| `2`  | CLI 用法或 run spec 无效（未知字段 / YAML/JSON 解析失败 / 同时给了 `--spec` 与 stdin） |
| `3`  | 在非交互模式下需要 permission 或 interaction |
| `4`  | 达到 max iterations 仍未给出最终回答 |
| `130`| 收到 SIGINT/SIGTERM（M0 故意把两者都映射到 `130`，不走惯例的 `SIGINT=130` / `SIGTERM=143` 拆分 —— 非交互运行不需要区分，单一取消处理路径让 pipeline 更简单） |

`exit 3` 时，`error.type` 区分：

- `permission_required` —— runtime 产生了 ASK/prompt 决策（引擎 ASK **或** `runtime/tool_planning.py:317-321` 的 `tool.requires_confirmation` fallback）且无 `permissions.allow` 命中。
- `permission_denied` —— 引擎返回 DENY（rule / preset / hardline / read-only short-circuit 任一）。`error.matched_rule` 在有规则命中时携带投影后的命中规则。**只要底层值是 `None`，序列化就整个省略 `matched_rule` key** —— 既覆盖 hardline 拒绝（`permissions.py:432`），也覆盖 `runtime/tool_planning.py:298-302` 合成的无规则 detail（read-only short-circuit）。Transport 内部状态可暂存 `None`，但序列化必须丢掉这个 key，而不是输出 `"matched_rule": null`。`error.message` 始终通过标准 reason 前缀指明来源（`hardline:` / `mode-preset:` / `user-rule:` / `injected:run-spec:`）。read-only short-circuit 复用现有 `mode-preset:` 家族，字面量为 `mode-preset:read-only` —— 不引入新的前缀类别。
- `interaction_required` —— `ask_user(...)` 被调用。

可控的运行时失败一律输出结构化 JSON。CLI 用法/spec 无效类失败可只走 stderr。

`agentao -p` 迁移到同一张表。当前 `-p` 在 max-iterations 时返回 `2`（`cli/entrypoints.py:60`）；本次改动后返回 `4`，与 `agentao run` 对齐。**这是 breaking change**，需要在 release notes 显式说明。

---

## Design Decisions

本节是容易写错的部分的权威说明。下方 Phase 1 / 2 引用本节，**不重复阐述**。

### D1. 非交互中止路径（不引入新的 transport API）

run pipeline 必须在以下情况中止：

- 引擎对 agent 试图调用的 tool 返回 DENY，或
- runtime 产生 ASK/prompt 决策（引擎 ASK **或** `tool.requires_confirmation` fallback）且无 spec 级 `allow` 命中，或
- agent 调用 `ask_user(...)`。

现有 transports（`NullTransport`、`SdkTransport`）在非交互场景下会自动 approve。`confirm_tool` 返 `False` 只把 plan 标记为 `CANCELLED`，chat loop 还会继续——对自动化是错的。

**M0 机制（不新增异常类型，不改 `Transport` 签名）：**

1. 新增 `NonInteractiveTransport(SdkTransport)`，位于 `agentao/transport/non_interactive.py`。它把 rejection 与 max-iterations 标志都记在实例属性上，并**取消由 run pipeline 显式构造、再通过 `agent.chat(cancellation_token=...)` 传入的 `CancellationToken`**（pipeline 必须显式构造该 token——`runtime/turn.py:60` 在缺省时会自己内部 new 一个，那个 token transport 拿不到）。

   ```python
   class NonInteractiveTransport(SdkTransport):
       def __init__(self):
           super().__init__()
           self.rejection: dict | None = None
           self.max_iterations_hit: bool = False
           self._cancel: Callable[[str], None] | None = None
           # 由 pipeline 的事件订阅器从 outcome=="prompt" 的
           # permission_decision 事件 push 进来；下面的 confirm_tool
           # 用它恢复 tool_call_id。
           self._ask_queue: list[tuple[str, str | None]] = []

       def bind_cancel(self, cancel_fn):
           self._cancel = cancel_fn

       def queue_ask(self, tool_name, tool_call_id):
           self._ask_queue.append((tool_name, tool_call_id))

       def confirm_tool(self, name, description, args):
           # 只有当 PermissionEngine 没有短路到 ALLOW（即没有 spec 级 allow
           # 命中）时，ASK 才会到这里。按 tool_name 从 FIFO 中取最早一条
           # 已 queue 的 ASK 事件——FIFO 匹配在此处足够，因为 Phase 1 是按
           # plan 顺序 emit permission_decision，Phase 2 也按同一顺序走
           # plan（tool_runner.py:193-200）。
           tool_call_id = None
           for i, (n, tcid) in enumerate(self._ask_queue):
               if n == name:
                   tool_call_id = tcid
                   del self._ask_queue[i]
                   break
           self.rejection = {
               "type": "permission_required",
               "tool_name": name,
               "tool_call_id": tool_call_id,
               "message": f"{name} requires approval in this mode",
           }
           if self._cancel:
               self._cancel(f"permission_required: {name}")
           return False  # plan → CANCELLED；tool 不会执行

       def ask_user(self, question):
           self.rejection = {
               "type": "interaction_required",
               "tool_name": "ask_user",
               "question": question,
               "message": "ask_user requires interaction in non-interactive mode",
           }
           if self._cancel:
               self._cancel("interaction_required: ask_user")
           return "[interaction_required]"

       def on_max_iterations(self, max_iterations, pending_tools):
           # 复用 agentao -p 现在已经在用的 Transport hook
           # （cli/entrypoints.py:25-37）。把 flag 记下来，pipeline 即可
           # 映射到 exit 4，不需要新造一个 MaxIterationsError。
           self.max_iterations_hit = True
           return {"action": "stop"}
   ```

2. run pipeline 在 agent 的 host event stream 上注册一个**同步 observer**，按 `event.outcome` 路由 `permission_decision` 事件。`Agent.events()` 是 async（返回 async iterator），同步 CLI 在没有后台 event loop 的情况下驱动不了它；`EventStream.add_observer(callback)` 会在生产线程内联触发回调，是 M0 的正确原语。

   本次工作给 `Agent` 加两个 thin pass-through，避免 pipeline 直接访问私有属性：

   ```python
   # agentao/agent.py
   def add_event_observer(self, callback):
       return self._host_events.add_observer(callback)

   def remove_event_observer(self, callback):
       return self._host_events.remove_observer(callback)
   ```

   pipeline 的 observer：

   - `"deny"` —— 记录 `transport.rejection` 并取消 token：

     ```python
     transport.rejection = {
         "type": "permission_denied",
         "tool_name": event.tool_name,
         "tool_call_id": event.tool_call_id,
         "matched_rule": event.matched_rule,  # hardline 时可能为 None
         "message": event.reason,             # 已带 source 前缀
     }
     cancel_fn(f"permission_denied: {event.tool_name}")
     ```

     `event.reason` 是 runtime 已经格式化好的、带 source tag 的字符串（`hardline:<desc>` / `mode-preset:<tool>` / `user-rule:<tool>` / `injected:run-spec:<tool>` —— 见 `permissions.py:419-421`；read-only short-circuit 在 `runtime/tool_planning.py:298-302` 也合成一条同属 `mode-preset:` 家族的 reason）。在这里直接保存它，JSON 序列化时按字面量输出为 `error.message` 即可，无需再从 `matched_rule` 反推（hardline 与 read-only short-circuit 场景里 `matched_rule` 本来就不存在）。

   - `"prompt"` —— push 到 transport 的 ASK FIFO，让 `confirm_tool` 触发时能取回 id：

     ```python
     transport.queue_ask(event.tool_name, event.tool_call_id)
     ```

   - `"allow"` —— 不动作。

   runner 在 Phase 2 之前会为每个 plan emit 一条 `permission_decision`（`tool_runner.py:193-195`），而 `add_observer` 回调在 `publish()` 内同步触发（`host/events.py:205-217`），所以同一批里的所有 `confirm_tool` 调用之前 FIFO 就已就绪。pipeline 不需要在 `ToolCallPlan` 上加新字段。observer 必须便宜、非阻塞 —— 它跑在生产线程上；抛异常会被 `EventStream` 记日志后丢弃，损坏的 sink 不会拖垮运行时。

   **Emit-gate 修正（observer-only 消费者必需）。** runner 当前在没有 async 订阅者时会跳过 `permission_decision` emission：`tool_runner.py:238-256` 走 `EventStream._has_subscribers()`（`host/events.py:396`），它只统计 `_subscribers`（async 迭代器），不看 `_observers`。M0 pipeline 只注册 sync observer 时，gate 求值为假，**事件根本不会 emit** —— DENY 永远不会到达 pipeline，ASK FIFO 也会一直为空。

   最小修法（Phase 1，不引入新公开 surface）：

   - `EventStream` 上加同辈方法 `_has_listeners()`，当**任一** async subscriber 或 sync observer 在场时返回 `True`（约 3 LOC）。
   - 把 `ToolRunner._should_emit_permission_events()` 改为调 `_has_listeners()`，不再调 `_has_subscribers()`（约 1 LOC）。原有 fallback（"无内省能力 → 仍然 emit"）保留。
   - `_has_subscribers()` 与它现有的测试**保持不动** —— `host/events.py:396` 文档化为 async subscriber 状态的 test hook，其它调用方可能依赖这个更窄的语义。

3. `agent.chat(...)` 返回后，pipeline 通过读三个状态来分类。**它不 catch `AgentCancelledError` 或 `KeyboardInterrupt`** —— `runtime/turn.py:97-109` 已经把这两个异常都吞掉、改返回 sentinel 文本，所以外层 `try/except` 永远进不去。

   分类（按顺序）：
   - `transport.rejection` 已设 → exit `3`，`error.type` 取相应值（`permission_required` / `permission_denied` / `interaction_required`）。
   - `transport.max_iterations_hit` 已设 → exit `4`。
   - `token.is_cancelled` 且 `transport.rejection is None` → exit `130`（SIGINT 路径；pipeline 安装的 SIGINT handler 会调 `token.cancel("sigint")`，chat loop 现有的 `KeyboardInterrupt` catcher 会再调 `token.cancel("user-cancel")`——任一 reason 都算证据）。
   - 其他 → exit `0`，使用返回的 `final_text`。

   `chat()` 抛出的通用异常（provider 错误等）由 `runtime/turn.py:110-113` 重新抛出，在入口处被 catch 为 exit `1`。

**相比早期设计，避免了：**

- 新增 sentinel 异常类型（`PermissionRequired` / `PermissionDenied` / `InteractionRequired`）。
- `Transport.confirm_tool` 签名变更 → 不需要修改 6 处调用点（`base.py`、`sdk.py`、`null.py`、`replay/adapter.py`、`acp/transport.py`、`cli/app.py`）。
- 在 `Transport` 抽象基类上新增 `on_permission_decision` hook。
- 新造 `MaxIterationsError` —— 直接复用 `agentao -p` 已经在用的 `on_max_iterations` Transport hook。
- 复用 chat loop 已有的 `CancellationToken` —— 与 SIGINT 走同一套机制。

**取舍：** DENY 发生时，现有运行时仍会先产生一条 "cancelled by user" 的 tool result，下一轮 chat loop 才发现 cancellation。这与现在 SIGINT 行为完全一致；最终的 `RunResult` 不受影响。

### D2. 权限规则注入（仅 deny 进入 pre-check tier）

**M0 信任模型：** 任务文件（run spec）始终能**收紧**本地策略（spec `deny` 无条件生效）。spec `allow` 是**叠加性**的 —— 它加入标准 user-rule list，沿用现有 `PermissionEngine` 的 mode 语义。M0 **不**为 spec allow 引入新的优先级 tier，**不**改动引擎现有的各模式 source 排序。

这样改动面最小（一个新 pre-check + 一处 user list 扩展），同时保留各模式的既有契约 —— 最重要的是 `permission_mode: full-access` 仍然意味着完全放行（引擎在 `full-access` / `plan` 下是 preset 优先于 user rules，见 `permissions.py:442-451`）。再激进就是对权限模型的重设计，超出 M0 范围。

`PermissionEngine` 增加一个新 list 与一个方法：

```python
def add_run_rules(
    self,
    *,
    allow: list[dict],   # 已由 RunPermissionRule.to_engine_dict("allow") 转换
    deny: list[dict],    # 已由 RunPermissionRule.to_engine_dict("deny") 转换
    source: str = "run-spec",
) -> None:
    # spec deny：唯一新增的 pre-check tier，hardline 之后、其他来源之前，
    # 在每种模式下都最先求值。必须如此，因为引擎按 source list 是
    # first-match-wins（permissions.py:452-462），spec deny 若被附加到
    # user 规则列表里，会被先存在的 allow:*（read-only/workspace-write
    # 下的 user 规则）或 preset allow:*（full-access/plan）盖住。
    self._run_scope_rules.extend(deny)
    # spec allow：附加到标准 user-rule list 末尾。沿用现有排序：
    # read-only / workspace-write 下 user 规则先于 preset；full-access /
    # plan 下 preset 先于 user 规则。不引入新 tier，不改变现有 mode
    # 语义。
    self.rules.extend(allow)
    self.add_loaded_source(f"injected:{source}")  # provenance + cache 失效
```

`decide_detail` 求值顺序：

```text
hardline → spec deny（新 pre-check）→ 现有 PermissionEngine 排序
```

"现有排序"完全沿用 `permissions.py:442-451` 已有的：`full-access` / `plan` 下 preset-then-user，`read-only` / `workspace-write` 下 user-then-preset。

实际后果（写测试时记住）：

- `read-only` 与 `workspace-write` 下，user-rule list 中靠前的 user/project `deny` 仍然能盖住同 tool 的 spec `allow`。spec 不能放松 standing user/project 限制。
- `full-access` 与 `plan` 下，preset 仍然优先于任何 user 规则（包括 spec allow）。spec deny 仍然生效，因为它在任何 preset 之前求值 —— 这正是新 pre-check tier 存在的意义。

`_run_scope_rules` 命中时，reason 字符串为 `"injected:run-spec:<tool>"`；spec allow 在 user tier 内命中时，按标准 `user-rule:<tool>` 前缀。两种情况的 provenance 都走 `loaded_sources`（每次 run 共用一个 `"injected:run-spec"` 标签）。

`active_permissions().rules` 投影在每种模式下都把 `_run_scope_rules` prepend 到最前，确保快照顺序与 `decide_detail` 求值顺序一致（`permissions.py:525-527` 的不变量）。`matched_rule` 投影（`host/projection.py:project_matched_rule`）无需改动 —— `action` 本来就是引擎规则的常规字段。

**action 注入只在一处发生：** `RunPermissionRule.to_engine_dict(action="allow"|"deny")` 产出已经规范化的引擎 dict。`add_run_rules` 只做 extend —— 它**不**合成 `action` 字段。`action` 因此永远不由 spec 作者填写（`RunPermissionRule` 上的 `extra="forbid"` 会拦截 YAML 里的 `action:`）。

### D3. Transport 必须构造期注入

`Agentao.__init__` 把 `self.transport` 传给 `ToolRunner`，再由 `ToolRunner` 存到 `ToolExecutor` 与 `ToolResultFormatter`（`agent.py:404`）。构造完成后再 `agent.transport = NonInteractiveTransport(...)` 不会到达上述持有引用的地方。

run pipeline 因此先构造 `NonInteractiveTransport`，通过 `build_from_environment(transport=...)` 注入（factory 已经通过 `**overrides` 接受 `transport=`，见 `embedding/factory.py:215-222`）。构造完成后再绑定 cancel：`transport.bind_cancel(token.cancel)`。

### D4. `agentao -p` 是轻量 shim

`agentao -p <text>` ≡ `agentao run --format text --prompt <text>`。当前 `run_print_mode` 函数体改成调用同一个 `cli.run.execute(...)` 入口的 stub。Exit-code 统一自动随之达成 —— 不再需要为 max-iterations 单开一个分支。

### D5. Plugins / skills / replay / sessions

- Plugins：按交互式 CLI 现有方式发现；M0 不做 per-run `extra_dirs` 注入（Post-MVP）。
- Skills：spec `skills:` 在 turn 开始前激活；缺失则在 chat 开始前失败。
- Replay：**`spec.replay` 对本次 run 是权威。** pipeline 必须给 `build_from_environment(...)` 显式传入 `replay_config=ReplayConfig(enabled=spec.replay)`，以绕过 factory 的盘上自动加载（`factory.py:194-199`：只要 `**overrides` 里没有 `replay_config`，factory 就会自动 `load_replay_config(wd)`）。否则 `replay: false` 的运行可能悄悄继承到项目/环境的 replay 配置 —— 与 spec 契约相悖。
- Sessions：每次运行都是新的。Resume 是 Post-MVP。

---

## 实施计划

两阶段交付。每阶段独立可发、可评审。

### Phase 1 —— 模型、parser、transport、引擎扩展

- 新增模块 `agentao/cli/run_models.py`：
  - `RunSpec`、`RunOutputOptions`、`RunPermissionRule`、`RunPermissionDomainRule`、`RunResult`。
  - `RunSpec` 与 rule 模型：`extra="forbid"`。`RunResult`：`extra="ignore"`（forward-compat）。
  - `RunPermissionRule.to_engine_dict(action)` 转引擎 dict 形态；`action` 在此处注入，永不由用户填写。
- 新增模块 `agentao/transport/non_interactive.py`：
  - `NonInteractiveTransport(SdkTransport)`，按 D1（约 30 LOC）。
  - 不改 `Transport` 基类，不改任何现有 transport，不引入新异常类型。
- `PermissionEngine.add_run_rules(...)`，按 D2（约 10 LOC + 把 first-match-wins 主循环扩展为在每种模式下先走 `_run_scope_rules`）。
- `Agent.add_event_observer` / `remove_event_observer` 作为 `EventStream.add_observer/remove_observer` 的 thin pass-through，按 D1 step 2（约 6 LOC）。
- `EventStream._has_listeners()` + 一行 `ToolRunner._should_emit_permission_events()` 调用更新，按 D1 step 2 的 emit-gate 修正（约 4 LOC；observer-only 消费者必需，否则 `permission_decision` 永远不 emit）。
- `runtime/tool_planning.py:298-302` 中 read-only short-circuit 合成 reason 字符串改一行：从 `"readonly mode blocks non-read-only tools"` 改为 `"mode-preset:read-only"`，归入 Output Contract 要求 `error.message` 必须有的 `mode-preset:` 前缀家族。无新类别，无新模型。

### Phase 2 —— CLI 子命令与 pipeline

- 新增模块 `agentao/cli/run.py`：
  - argparse 子 parser：`--spec`、`--prompt`、`--format`、`--model`、`--base-url`、`--permission-mode`、`--interaction-policy`、`--max-iterations`、`--skill`、`--replay`。
  - YAML/JSON 加载器，严格未知字段校验。
  - 显式 flag 跟踪以满足合并规则。
  - Pipeline：
    1. 解析 spec、合并 CLI overrides、校验 `RunSpec`。
    2. 构造 `NonInteractiveTransport()`。
    3. 解析 `cwd`（绝对路径）。
    4. `agent = build_from_environment(working_directory=cwd, transport=transport, model=spec.model, base_url=spec.base_url, replay_config=ReplayConfig(enabled=spec.replay), ...)`。显式 `replay_config=` 用于抑制 factory 的盘上自动加载 —— 详见 D5。
    5. 同步交互式 CLI 已经在做的两处运行时状态（`cli/app.py:125-133`）：
       ```python
       engine.set_mode(spec.permission_mode)
       agent.tool_runner.set_readonly_mode(
           spec.permission_mode == PermissionMode.READ_ONLY
       )
       ```
       仅调 `engine.set_mode("read-only")` **不够** —— `_PRESET_RULES["read-only"]` 按设计就是空的（`permissions.py:153`）；read-only 的实际拦截在 `ToolRunner` 的 `readonly_mode` flag 上、`runtime/tool_planning.py:298` 处生效。漏掉第二行调用会让 `permission_mode: read-only` 的运行静默可写。
    6. 通过 `RunPermissionRule.to_engine_dict("allow"|"deny")` 转换 spec 规则，再 `engine.add_run_rules(allow=..., deny=..., source="run-spec")`（已规范化的 dict；D2）。
    7. （replay 挂接已经由上面的显式 `replay_config=` 在 `build_from_environment` 内完成。）
    8. 激活 `spec.skills`；任一缺失则失败。
    9. **显式构造 cancellation token：** `token = CancellationToken()`。`runtime/turn.py:60` 在 `cancellation_token=` 缺省时会自己 new 一个，那个内部 token transport 拿不到 —— 所以必须由 pipeline 拥有。
    10. `transport.bind_cancel(token.cancel)`。
    11. 安装 SIGINT/SIGTERM handler，调用 `token.cancel("sigint")`，让用户中断走与权限/交互拒绝同一套取消通道。
    12. 通过 `agent.add_event_observer(callback)` 注册同步 observer —— 按 `outcome` 路由 `permission_decision`（D1 step 2）：`deny` 设 rejection 并取消；`prompt` push 到 `transport.queue_ask(...)` 以便 ASK 取回 `tool_call_id`；`allow` 不动作。`chat()` 返回后用 `remove_event_observer` 解绑。
    13. 取 LLM token 总量快照（`llm/client.py:498`）以便算 per-run delta。
    14. `agent.chat(spec.prompt, max_iterations=spec.max_iterations, cancellation_token=token)`。**不要**用 `try/except` 去接 `AgentCancelledError` 或 `KeyboardInterrupt` —— `runtime/turn.py:97-109` 已经把这两个吞掉、改返回 sentinel 文本。
    15. 按 D1 step 3 读 `transport.rejection`、`transport.max_iterations_hit`、`token.is_cancelled` 进行分类；构造 `RunResult`，算 `usage` delta，按 `--format` 序列化，映射 exit code（`0` / `3` / `4` / `130`）。`chat()` 抛出的通用异常在入口处被 catch 为 exit `1`。
- 把 `agentao -p` 重写为 D4 的 shim。在 release notes 说明 max-iterations exit code `2 → 4` 的变化。

---

## 测试矩阵

最小覆盖：

- YAML spec 能加载。
- JSON spec 能加载。
- 非法 YAML/JSON exit `2`。
- 未知字段 exit `2`。
- `--spec` 与 piped stdin 同时提供 exit `2`。
- CLI 标量 flag 覆盖 spec 标量。
- 重复 `--skill` 覆盖 spec `skills`。
- `--format json` 仅向 stdout 输出合法 JSON（断言 stderr 干净）。
- `--format text` 仅输出最终文本。
- ASK 且无匹配的 `permissions.allow` exit `3`，`error.type="permission_required"`，`error.tool_name` 已设，`error.message` 已设（匹配 `"<tool> requires approval in this mode"`），`error.tool_call_id` 与 agent 公共事件流上对应的 `permission_decision` event id 匹配。断言 error envelope 中**不含**原始 `args`。
- DENY（spec `permissions.deny` 命中 agent 试图调用的 tool）exit `3`，`error.type="permission_denied"`，`error.matched_rule` 已设，且该 `tool_call_id` **没有** `tool_lifecycle phase="started"` 事件。
- `ask_user(...)` 调用 exit `3`，`error.type="interaction_required"`，`error.message` 已设（匹配 `"ask_user requires interaction in non-interactive mode"`）。
- 未知 `interaction_policy` 值（如 `approve_all`）exit `2`。
- `workspace-write` 模式下，`~/.agentao/permissions.json` 已有 `allow:*` 时，spec `permissions.deny` 仍能拦下 tool（`_run_scope_rules` 排序的回归保护——没有它 user `allow:*` 会盖住 spec deny）。
- `full-access` 模式下，spec `permissions.deny` 仍能拦下 tool（防 preset `allow:*` 盖住 spec deny 的回归保护）。
- `read-only` 与 `workspace-write` 下，spec `permissions.allow` **不能**覆盖 user-rule list 中靠前的同 tool user `deny`（叠加语义的回归保护 —— spec allow 走标准 first-match-wins 加入 user 列表）。
- `read-only` 与 `workspace-write` 下，当 standing user policy 没有覆盖某个 tool 时，spec `permissions.allow` 能为它授权（叠加语义的正向覆盖；通过 `user-rule:<tool>` reason 前缀断言）。
- `full-access` 下，`permission_mode: full-access` 仍然意味着完全放行 —— spec `permissions.allow` 在 active rule 快照中可见，但按现有引擎排序，preset `allow:*` 先匹配（防止意外重排 `full-access` / `plan` 下 preset 与 user rules 的回归保护）。
- `add_run_rules(...)` 的 deny 路径：emit 的 `permission_decision` 事件 `reason="injected:run-spec:<tool>"`，`loaded_sources` 含 `"injected:run-spec"`，`engine.active_permissions().rules[0]` 即被注入的 deny 规则。
- hardline 拒绝的 tool：`permission_denied` envelope **整个省略** `error.matched_rule`（断言序列化后的 JSON 中**不含**该 key，而非 present-with-`null`），`error.message` 以 `hardline:` 开头（matched_rule-omit-on-hardline 契约的回归保护，同时也是 deny observer 把 `event.reason` 正确带到 envelope 的回归保护）。
- spec-deny 前缀透传：spec `permissions.deny` 拦下的 tool 产生的 `error.message` 以 `injected:run-spec:` 开头（同一通路在非 hardline 来源下的回归保护）。
- `permission_mode: read-only` 在没有任何 user / preset rule 命中的情况下也能拦下非 read-only tool（Phase 2 step 5 中 `set_readonly_mode(True)` 同步调用的回归保护 —— 漏掉第二行 read-only 在 `runtime/tool_planning.py:298` 处不会生效，运行可能静默改动 workspace）。同时断言 `error.message == "mode-preset:read-only"`，以及序列化 envelope 中 `error.matched_rule` key **不存在**（合成 detail 的 reason 修正 + 序列化层 `matched_rule is None → 省略 key` 规则的回归保护）。
- Observer-only emit-gate：run pipeline **只**注册 sync observer（不挂任何 async `Agent.events()` 订阅者）时，会触发 DENY 的 tool 调用仍然能让 observer 收到 `permission_decision` 事件（`_has_listeners()` 修正的回归保护 —— 没有它 `_has_subscribers()` 返回 false，事件不发，pipeline 会静默错过 DENY）。
- spec 中 `replay: false` 时，即便项目存在 `.agentao/replay.toml` 启用了 replay，运行也不应产生任何 replay 输出（Phase 2 中显式注入 `replay_config=` 的回归保护 —— 没有它 factory 的盘上自动加载仍会挂上 manager）。
- ASK `tool_call_id` 关联：同一批含两个相同 tool name 的 ASK plan 时，第一次 `confirm_tool` 取到第一个 plan 的 `tool_call_id`，不是第二个（验证 D1 中 FIFO 在重复条目场景下也能正常工作）。
- 构造期注入的 `NonInteractiveTransport` 真的到达 tool 执行路径：触发一次 ASK 后断言被调用的是 `NonInteractiveTransport.confirm_tool`（不是 `SdkTransport.confirm_tool`）。
- `permissions.allow` 的 `args` pattern 与实际调用参数不匹配时，**不**自动 approve。
- max-iterations 失败 exit `4`（断言走 `NonInteractiveTransport` 上新的 `on_max_iterations` flag，**不要**断言抛出异常）。
- chat 期间收到 SIGINT exit `130`（断言 `agent.chat()` 返回后 `token.is_cancelled` 为真且 `transport.rejection is None`；**不要**断言 `KeyboardInterrupt` 向外传播 —— `runtime/turn.py:97-103` 会吞掉）。
- 缺失的 skill 在 chat 开始前失败。
- replay 启用时 result 含 `replay_path`。
- `agentao -p` 在 max iterations 命中时返回 `4`（不是 `2`）—— exit-code 统一的回归保护。

---

## Open Questions

- M0 不发 `RunLifecycleEvent`；如果 Post-MVP 加入 JSONL 流，host event 基类是否应该加一个可选 `schema_version`，让 `RunLifecycleEvent` 与现有 host event 家族保持一致？还是让 `RunLifecycleEvent` 成为家族里的特例？

---

## 推荐 M0 决策

- **权限默认值：** `workspace-write`（与交互式 CLI 一致）。CI 示例仍应显式设 `read-only`。
- **interaction policy 默认值：** `reject`。无 `approve_all`。
- **权限规则（M0 信任模型）：** spec 级 `allow`/`deny` 复用现有引擎匹配器。spec `deny` 进入专用的 `_run_scope_rules` pre-check tier，hardline 之后、其他来源之前，在每种模式下都最先求值 —— 安全闸门无条件生效。spec `allow` 是**叠加性**的：附加到标准 user-rule list，沿用引擎现有的各模式排序（`permissions.py:442-451`）。M0 **不**为 spec allow 引入新 tier，**不**改变现有各模式 source 排序 —— `permission_mode: full-access` 仍然意味着完全放行（D2）。
- **`cwd` 策略：** 允许指向任意目录（包括原始进程 cwd 之外）。用户显式选定的 `cwd` **就是**他们为本次运行声明的 workspace 边界；`permission_mode` 规则在该 cwd 内适用。这与交互式 CLI 一致 —— 用户启动 `agentao` 之前本来就可以 `cd` 到任意位置。解析后的绝对路径写入 `RunResult.cwd` 以便审计。
- **Transport：** `NonInteractiveTransport` 构造期注入（D3）。**不**新增 sentinel 异常，**不**改 `Transport.confirm_tool` 签名，**不**在基类加 `on_permission_decision` hook。中止路径复用现有 `CancellationToken` 与同步 `EventStream.add_observer(...)`（通过新增 thin pass-through `Agent.add_event_observer` 访问；D1）。
- **输出：** M0 仅 `text` 与 `json`。JSONL 流式 Post-MVP。
- **Exit codes：** 与 `-p` 共用，`-p` 退化成 shim（D4）。
- **Skills：** append-activate，不替换。
- **Replay：** `spec.replay` 对本次 run 是权威。pipeline 必须给 `build_from_environment(...)` 显式传入 `replay_config=ReplayConfig(enabled=spec.replay)`，绕过 factory 的盘上自动加载（D5）。`RunResult.replay_path` 返回 summary 路径。
- **Secrets：** spec 永不接受。
- **Sessions：** 仅 fresh，不支持 resume。
