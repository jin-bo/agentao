# 12. 非交互入口

`agentao` 不只是一套 REPL。顶层命令还支持初始化、一次性 prompt、会话恢复、ACP server 模式，以及 skill / plugin 管理。

## `agentao init` — 写 `.env`

第一次在项目里使用时，可以让向导生成 `.env`：

```bash
agentao init
```

向导会询问 provider、API key、base URL、model，然后写入：

```bash
LLM_PROVIDER=OPENAI
OPENAI_API_KEY=...
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-5.4
```

已有 `.env` 时会先询问是否覆盖。

## `agentao -p` / `--print` — 一次性运行

打印模式发送一个 prompt，输出回答，然后退出。

```bash
agentao -p "总结 README"
cat issue.md | agentao --print "根据下面内容生成修复计划"
```

从 0.4.x 起，`-p` 是 `agentao run --format text --prompt …` 的薄壳，两者共用下文 [`agentao run`](#agentao-run-自动化结构化入口) 一节里的统一退出码表。

> **升级提示（0.3.x → 0.4.x）：** 旧版 `-p` 把"达到最大工具迭代数"映射为退出码 `2`。0.4.x 起这种情况是 `4`；`2` 现在表示"用法或 spec 校验失败"。

## `agentao run` — 自动化结构化入口

`agentao run` 是面向自动化的稳定面：把结构化 spec（来自 stdin 或 `--spec`）与显式 CLI 覆盖合并，跑一个 Agentao turn，输出机器可读的结果。

```bash
# spec 从 stdin 进入
agentao run --format json < task.yaml

# spec 从文件进入，并用 flag 覆盖
agentao run --spec .agentao/tasks/review.yaml --model gpt-5.5 --format json

# 不写 spec 文件，直接给 prompt
agentao run --prompt "总结当前目录" --format json
```

`--spec` 与管道 stdin 互斥，同时给会以退出码 `2` 失败。

### M0 spec 结构

```yaml
prompt: string                 # 必填（或用 --prompt 传入）。可模板化。
instructions: string           # 追加到 system prompt。可模板化。`.strip()` 后非空时
                               # 经 Agentao(project_instructions=…) 注入，并跳过
                               # AGENTAO.md 的磁盘读取。
parameters:                    # spec 级 --param 替换所用的类型化参数槽
  - name: string               # ASCII 标识符；不能是 Jinja 保留名
    required: boolean          # 默认 false；与 `default` 互斥
    default: string            # v1 仅支持字符串；若有 `choices` 必须在其中
    choices: [string]          # 可选枚举
cwd: string                    # 这次 run 的工作目录
model: string                  # 覆盖环境里的 LLM model
base_url: string               # 覆盖环境里的 base URL
permission_mode: read-only | workspace-write | full-access | plan
interaction_policy: reject     # M0 仅接受 "reject"
permissions:
  allow:
    - tool: string             # glob — 与 ~/.agentao/permissions.json 同语法
      args: { ... }            # 可选参数模式
      domain:                  # 可选 URL/domain 匹配
        url_arg: string
        allowlist: [string]
        blocklist: [string]
  deny:
    - tool: string
      args: { ... }
      domain: { ... }
max_iterations: int            # 默认 100
skills: [string]               # 在自动发现的激活 skills 之上追加
replay: boolean                # 这次 run 启用 ReplayManager
output:
  format: text | json
```

`extra="forbid"` —— 未知 spec 字段会以退出码 `2` 失败。secrets（`api_key`）**绝不**在 spec 里接收，必须留在环境变量或宿主注入的 client 里。

CLI flag 只在用户**显式**提供时才覆盖 spec 值，argparse 默认值不会抹掉 spec 字段。

### 参数与模板渲染（`--param`）

`prompt` 与 `instructions` 是 Jinja2 模板，对类型化参数值做替换。其他 spec 字段**不**做模板化（给 `permissions` 或 `skills` 加模板容易引发陷阱）。

```yaml
# .agentao/runs/review-pr.yaml
parameters:
  - name: pr_number
    required: true
  - name: depth
    default: shallow
    choices: [shallow, deep]
instructions: |
  你正在审查 PR #{{ pr_number }}。
  使用 {{ depth }} 模式：shallow = 只看表层；deep = 跨文件追踪数据流。
prompt: 审查 PR #{{ pr_number }}，专注于正确性 bug。
permission_mode: workspace-write
max_iterations: 30
```

```bash
agentao run --spec .agentao/runs/review-pr.yaml \
            --param pr_number=142 --param depth=deep
```

`--param KEY=VALUE` 可重复。只在**第一个** `=` 处切分 —— value 可以包含更多 `=`（如 `--param expr=a=b` → `expr` → `a=b`）。

**触发规则。** 当 `spec.parameters` 与 `--param` 两侧均为空时，渲染器完全不调用 —— 没声明 parameters 的 spec 里的字面 `{{ }}` 会**原样**透传给 LLM。当 spec 没有 `parameters` 但 CLI 传了 `--param` 时，运行退出码 `2`，确保拼写错误不会被吃掉。

**错误（一律退出码 `2` / `invalid_spec`）：**

- 缺 required 参数、未知参数、不在 choices 中。
- `--param` 形态错误（`expected KEY=VALUE`、重复 key、非标识符 key）。
- 模板里用到未声明变量（StrictUndefined）：`template uses undefined variable 'X' (declare it in spec.parameters)`。
- Jinja 渲染期抛出的其他异常（`{{ 1/0 }}` → `ZeroDivisionError`、缺 loader 的 `{% include %}` 等）会被捕获并报为 `template error in spec.<field>`。
- 沙箱拒绝：渲染器使用 `jinja2.sandbox.SandboxedEnvironment`，所以走属性链的逃逸（如 `{{ ''.__class__.__mro__ }}`）会被拒绝 —— 共享 / 不可信来源的 recipe 无法在 `permission_mode` 与工具权限生效之前访问 Python 内部。

**保留参数名。** 看起来是 ASCII 标识符、但被 Jinja 保留的名字会在 spec 校验阶段被拒：常量（`true`/`True`/`false`/`False`/`none`/`None`）、关键字（`for`/`if`/`in`/`set`/`is`/`not`/`or`/…）以及 Jinja runtime 注入的 `self` / `parent`。完整清单见 `agentao/cli/run_models.py::_JINJA_RESERVED_NAMES`。

**Instructions 优先级。** 渲染后的 `spec.instructions` 非空、且至少包含一个非空白字符时，会经 `Agentao(project_instructions=…)` 注入并跳过 `AGENTAO.md` 磁盘读取。**全空白**的输出（例如 YAML block scalar 渲染成 `"\n"`）会回落到 `AGENTAO.md`，从而保证渲染成空的模板不会静默把项目说明给抹掉。

完整设计动机 —— 包括 v1 范围、延迟落地的 `number` / `boolean` 类型，以及 goose-recipes 的对比 —— 见 [docs/design/run-spec-parameters.zh.md](https://github.com/jin-bo/agentao/blob/main/docs/design/run-spec-parameters.zh.md)。

### 输出契约

`--format text`：stdout 上只写最终 assistant 文本；诊断信息走 stderr。最接近老 `agentao -p` 的形态。

`--format json`：run 结束后输出一个 envelope：

```json
{
  "status": "ok",
  "session_id": "...",
  "turn_id": "...",
  "cwd": "/abs/path/to/project",
  "model": "gpt-5.5",
  "final_text": "...",
  "replay_path": ".agentao/replays/<id>.jsonl",
  "usage": {
    "prompt_tokens": 12000,
    "completion_tokens": 900,
    "total_tokens": 12900
  },
  "tool_calls": 7,
  "warnings": []
}
```

失败时 `final_text` 为 `null`，`error` 字段携带 `{ type, message, tool_name?, tool_call_id?, question?, matched_rule? }`。`type` 取值：`permission_required`、`permission_denied`、`interaction_required`、`max_iterations`、`runtime_error`、`invalid_spec`、`interrupted`。消费方应把 envelope 视为前向兼容（多余字段直接忽略）。

### 统一退出码（`agentao run` 与 `agentao -p` 共用）

| 退出码 | 含义 |
|--------|------|
| `0`    | 正常完成 |
| `1`    | 运行时错误 |
| `2`    | 用法错误 / spec 校验失败 / 未知 spec 字段 |
| `3`    | 需要权限或交互（非交互环境无人审批） |
| `4`    | 达到最大工具迭代数，回答可能不完整 |
| `130`  | 被中断（SIGINT / SIGTERM） |

完整 M0 设计 —— 合并规则、Non-goals、Post-MVP 范围（`jsonl` 事件流、`attachments`、`provider`、每次 run 的 `plugins`、session resume）—— 见 [docs/implementation/NON_INTERACTIVE_RUN_PLAN.md](https://github.com/jin-bo/agentao/blob/main/docs/implementation/NON_INTERACTIVE_RUN_PLAN.md)。

## `--resume` — 启动即恢复会话

```bash
agentao --resume
agentao --resume a1b2c3
```

不带 id 恢复最近会话；带 id 时按前缀匹配。REPL 内同等命令是 `/sessions resume <id>`。

## `--acp --stdio` — 作为 ACP Server

```bash
agentao --acp --stdio
```

这会把 Agentao 作为 ACP stdio JSON-RPC server 启动，供 IDE、宿主进程或其他 agent 调用。`--stdio` 当前只在 `--acp` 下有效。

## `--plugin-dir` — 临时加载插件

```bash
agentao --plugin-dir ./my-plugin
agentao plugin --plugin-dir ./my-plugin list
```

`--plugin-dir` 可重复。适合本地开发插件时不安装包，直接指向目录。

## `agentao skill ...`

顶层 `skill` 子命令用于管理受管安装的 skills：

```bash
agentao skill install owner/repo[:path][@ref]
agentao skill list
agentao skill list --installed
agentao skill remove <name>
agentao skill update <name>
agentao skill update --all
```

REPL 内的 `/skills` 管“当前会话看见什么、激活什么”；顶层 `agentao skill ...` 管“磁盘上安装了什么”。

## `agentao plugin list`

```bash
agentao plugin list
agentao plugin list --json
```

这会加载插件并输出诊断，适合 CI 或发布前检查。REPL 内的 `/plugins` 是同一类诊断的交互版。

## `agentao doctor` —— 健康快照

```bash
agentao doctor
agentao doctor --json
```

把 harness 已经能给出的所有健康信号汇总到一份报告里：`.env` 的 provider 检查（只看 API key **是否存在**，绝不输出 key 值）、`settings.json`、permissions、MCP、replay 配置、ACP schema 导出状态、项目 + 用户 memory store、插件诊断、可选依赖探测。`--json` 是给 CI / 宿主用的契约面，人类可读输出是终端默认。

输出契约：

- `{"ok": bool, "sections": {...}, "findings": [...]}`
- `ok` 为 `false` 当且仅当至少一条 finding 的 `"level": "error"`，此时退出码为 **1**。Warning（例如刚 clone 时的 "API key not set"）保持 `ok=true`、退出 **0**，不会误绊 CI。
- 每条 finding 含 `level`（`info` / `warning` / `error`）、`area`（所属 section）、可读 `message`、`source`（路径或环境变量标签）。
- Doctor 是**只读**的：不会建 memory DB、不会改文件。探测一个不存在的 `memory.db` 会报 `"absent"`，绝不顺手把空文件创出来。
- 未知 flag 退出码 **2**（与 `agentao run` 一致）—— 一旦 CI 写错（如 `--jsno`）会立即报错，不会用错误参数静默 exit 0。

## `agentao config validate` —— 显式配置校验

```bash
agentao config validate
agentao config validate --json
```

`doctor` 的窄版伙伴，**只**校验用户可编辑配置：`settings.json`、`.env` provider 变量、`permissions.json`、`mcp.json`、`settings.json` 的 `replay` 段、memory-store 探测。插件发现刻意排除（用 `doctor` 或 `plugin list`）。

会显式报出运行时通常会**默默吞掉**的问题：

- 上述文件的 JSON 损坏 / 顶层非 object 形态；
- `LLM_TEMPERATURE` / `LLM_MAX_TOKENS` 解析失败；
- MCP 单条 server 条目非 object，或其 `env` / `headers` / `args` 含非 string（运行时 `expand_env_vars` 会抛 `TypeError`）；
- 用户级与项目级 `mcp.json` 同名碰撞（运行时会丢掉项目级条目，validate 会 warn 让你知道）；
- `ReplayConfig.from_mapping` 会静默吞掉的 replay 设置（`max_instances: 0`、非 bool 的 capture flag、未知 flag 键等）。

运行时行为**不变**——`agentao config validate` 是显式校验面。factory 仍是 best-effort，这样宿主带着可选配置坏掉时也不会被卡住启动。

两个命令都支持 `--plugin-dir DIR`（doctor 用它来发现插件），且都依赖 `[cli]` extra；缺依赖时会走标准 missing-dep 守卫并打印安装提示。

---

::: tip 真相源头
顶层参数 parser 在 [`agentao/cli/entrypoints.py:_build_parser`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/entrypoints.py)。非交互 print 模式在同文件的 `run_print_mode`（薄壳，转发到 [`agentao/cli/run.py:execute`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/run.py)）。Spec 模型在 [`agentao/cli/run_models.py`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/run_models.py)；Jinja2 沙箱化渲染器（负责 `prompt` 与 `instructions`）在 [`agentao/cli/run_template.py`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/run_template.py)。Skill / plugin 子命令在 [`agentao/cli/subcommands.py`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/subcommands.py)。
:::
