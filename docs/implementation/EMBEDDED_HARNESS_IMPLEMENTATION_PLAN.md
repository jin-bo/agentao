# Embedded Harness — Implementation Plan

**Date:** 2026-04-27
**Status:** Strategy locked in;待执行
**Source PLAN:** `workspace/reports/agentao-embedded-harness-feature-review-2026-04-27.md`
**Scope:** 评审 Codex 给出的"如何落地"建议,并基于用户决策定稿为可执行的 PR 序列。

---

## TL;DR

- **目标:** 让 `Agentao` 成为长期唯一的 embedding API,in-place 清理 `__init__` 的隐式副作用(env / home / cwd / 读盘)。
- **核心手段:** 把所有隐式行为上提到 `agentao/embedding/factory.py::build_from_environment()`。`Agentao(...)` 退化为纯组装,CLI / ACP 走 factory 保持行为零差异。
- **不做:** 新建并行 `AgentSession`、ToolRunner / ChatLoopRunner 全量 async 化、P0 引入 MemoryStore / Logger capability。
- **PR 序列:** PR 1(schema_version + logger 注入)→ PR 2(FS / Shell capability)→ PR 3a(factory + CLI/ACP 切换,0.2.16)→ PR 3b(删除 core fallback,0.2.16)→ PR 4(`arun` async surface)→ PR 5a(`Agentao()` soft deprecation + 内部 README/docs/examples/tests 迁移,0.2.16)→ PR 5b(`working_directory` 必传 hard break,0.3.0)→ PR 6+(MemoryStore / MCPRegistry protocol、Replay / Sandbox / BgStore 默认禁用)。

---

## Decisions locked in

1. **`Agentao` 是 long-term embedding API**,不新建并行 `AgentSession`。所有改动 in-place 落在 `Agentao` 上。
2. **P0 = in-place 清理副作用**,不是堆 capability 层。env / home / cwd / 读盘全部上提到 `agentao.embedding.factory`。
3. **Async-first 仅在 public surface 落地**,runtime 内部仍 sync。`Agentao.arun()` 用 thread executor 桥接;不引入 `AsyncTransport`。
4. **PR 3 拆成 3a + 3b**,降低 review 风险。3a 新增 factory + CLI/ACP 切换(fallback 仍在);3b 删除 core fallback、参数必传。3a 与 3b 不留长期中间态。
5. **Logger 修复用最小 `logger` / `log_file` 参数**,不引入 logger capability。
6. **MemoryStore / MCPRegistry protocol 推到 PR 6+**,P0 只做 memory roots / mcp config 显式注入。
7. **Replay / Sandbox / BgStore 纳入构造副作用清理范围**,默认参数 None 禁用,factory 负责按 CLI 行为启用。
8. **`Agentao()` 不走立即 hard-break,先在 0.2.16 patch 加 soft deprecation,下一 minor(0.3.0)再 hard break**(PR 5a → PR 5b)。理由:`Agentao` 是已发布的 public API,自带 `examples/` + README 把 `Agentao()` 写成正确用法,下游(PyPI 安装方)不可知。soft deprecation 增量 ~5 行 `warnings.warn`,代价接近零。**不允许跨过 0.3.0 之后还保留 deprecation** —— 拖更长会让"in-place 清理"形同虚设。

---

## Context

原始 PLAN 把方向定下来了:agentao = embedded harness,核心工作是"拆耦合"。Codex 给出了具体落地策略(新增 `core/` 包、保留旧 `Agentao` 作 adapter、capability 注入到现有 tool、P0 分级)。

我验证完代码,Codex 在事实层面准确(下面有逐条核对)。但**用户选定了 in-place 清理路线**,因此 Codex 的 side-by-side 方案(新建 `AgentSession`)被否决,其余核心建议(capability 注入、versioned events、logger 修复、配置注入、不读盘)依然适用 —— 只是落点全部从"新 core 类"改成"`Agentao` 自己 + 一个 thin factory"。Codex 没覆盖到的子系统(replay / plugins / sandbox / bg_store)也要纳入。

---

## 一、Codex 的事实判断 — 全部已核对

| Codex claim | 现状代码 | 结论 |
|---|---|---|
| `Agentao.__init__` 是大构造器,12+ 子系统副作用 | `agentao/agent.py:46-289`(245 行 init body) | ✅ 准确,实际比 Codex 描述更严重 |
| LLM 在 init 时读 env(provider/api/model) | `agentao/llm/client.py:108-127`,5 个 `os.getenv` | ✅ 准确 |
| Logger 全局 handler 复写 | `agentao/llm/client.py:140-156` 改写 `getLogger("agentao")` package root | ✅ 准确,且**这是 ACP set_model 路径的 live bug** —— 切换 model 重建 LLMClient 会反复触发 |
| `chat()` 同步 facade | `agentao/agent.py:459-477` | ✅ |
| Transport 同步协议 | `agentao/transport/base.py:28-55`(`emit` / `confirm_tool` / `ask_user` / `on_max_iterations` 全 sync) | ✅ |
| Shell 直接 `subprocess.Popen` | `agentao/tools/shell.py:268-310` | ✅ |
| File ops 直接 `open()` | `agentao/tools/file_ops.py:68-75`(读)+ 写/编辑同样 | ✅ |
| MCP/Permissions 读盘 | `agentao/mcp/config.py:84`(`Path.home()`)+ `agentao/permissions.py:190-201`(默认 `Path.cwd()`) | ✅ 已支持显式 `project_root` 注入,但 fallback 仍在 |
| AgentEvent 缺 `schema_version` | `agentao/transport/events.py:42-76` 只有 `type` + `data` | ✅ 准确 |
| `working_directory` 默认 `Path.cwd()` | `agentao/agent.py:291-306` 已是 lazy fallback | ✅ —— Codex 的描述略宽,实际已经分了"frozen vs lazy"两条路径 |
| `runtime/` 已拆出 ToolRunner / ChatLoopRunner / ToolExecutor | `agentao/runtime/` 共 11 个文件 | ✅ Codex 正确指出这是好接入点 |

**一处需要补充:** Codex 漏列了 `replay`、`plugins`、`sandbox_policy`、`bg_store`、`plan_session` —— 这五个也是 `Agentao.__init__` 里直接创建的子系统(`agent.py:216-280`),embedding host 同样无法控制。后续 capability 设计要把它们一起考虑。

---

## 二、Codex 推荐的方向 — 适配 in-place 路线后,我同意的部分

1. **Capability 后端注入到现有 tool,不重写 tool** —— 这点尤其对。`ReadFileTool` / `WriteFileTool` / `EditTool` / `ShellTool` 各有 100-300 行的 schema/格式化/截断/二进制检测逻辑,这些都不应该重写,只换底层 `read_bytes` / `write_text` / `run` 调用即可。in-place 路线下,capability 直接注入到 `Agentao.__init__` 的 keyword 参数即可。

2. **P0-B(versioned AgentEvent)优先级靠前** —— 这是最便宜的高收益项。给 dataclass 加一个 `schema_version: int = 1` 字段 + `to_dict()` 方法即可,零兼容风险。

3. **P0-A(logger 注入)** —— 这是 **当下 live bug**,不只是架构债。修复的最小形态是 `LLMClient(logger=...)` 接收注入,不再去写 package root。

4. **不删除 ACP/CLI** —— 同意。它们继续作为 adapter 存在,只是在 PR 3 之后改成走 `factory.build_from_environment()` 而不是直接调 `Agentao(...)`。

### 否决:Codex 的 side-by-side 新建 `AgentSession`

用户已确认长期只保留一个 embedding API(就是 `Agentao`)。所以 Codex 的"新建 `agentao/core/AgentSession`"被否决。代价是 in-place 清理 `Agentao.__init__` 的兼容压力会更大;对策见第四节的 PR 切分。

---

## 三、Codex 推荐的方向 — 我会 push back 的部分

### 3.1 "Async-first" 的边界要更小

Codex 建议:`AsyncTransport` + 旧 `Transport` 共存,`ToolRunner` 内部走 async。这会扩大改动面,而 ToolRunner / ChatLoopRunner 本质是单线程同步循环,async 化拿不到对应收益。

**更小的方案:** 只让 **Session API 异步**(host 可以 `await session.run(...)`);**runtime 内部仍然同步**;async host 通过 `loop.run_in_executor()` 在边界处一次性桥接。`Transport` 不需要新增 async 版本,直接保留现有 sync 协议;真正需要 async 的只是 host 调用 session 的入口。

为什么这样够用:host 关心的是"我从 async 代码里能不能 await",不关心"chat loop 内部怎么 yield"。chat loop 是 LLM 请求 → 工具执行 → 再 LLM 请求,本质是 sequential I/O,thread executor 完全够用。等真正出现 host 需要 cooperative cancellation 透到 LLM HTTP 请求那一层的需求,再做 async transport 不迟。

### 3.2 MemoryStore 不应该在 P0

Codex 把 `MemoryStore` 列为 capability。但 memory 不是 fs/shell 那种"换个 backend"的事:它有 SQLite schema、soft delete、tag 索引、session_summary 关联表、retrieval scoring。要抽 protocol 就要先把 `MemoryManager` 的 public API 钉死,这是 P1 工程量。

**P0 应该只做:** `MemoryManager` 不再在 init 里默认 `Path.home() / ".agentao"`,改成由调用方传入两个 root 参数。`Agentao.__init__` 把这两个 path 算好后再传(保留 CLI 默认行为)。protocol 化推到 P1。

### 3.3 Logger 修复比 Codex 描述的更小

Codex 写的"实例级 logger"暗示要一个 `Logger` capability。但当下 P0 修复的最小形态只是:

```python
class LLMClient:
    def __init__(self, ..., logger: logging.Logger | None = None, log_file: str | None = None):
        self.logger = logger or logging.getLogger("agentao.llm")
        if log_file:
            ...  # 只在 log_file 显式传入时才装 file handler
```

CLI adapter 显式传 `log_file=str(working_dir / "agentao.log")`;embedded host 传 `logger=their_logger, log_file=None`。**不需要 capability protocol,一个 PR 就能落。**

### 3.4 Workspace 显式化(in-place 路线下的真正切分)

in-place 路线下,Codex 的"embedding API 强制显式 workspace"必须落在 `Agentao` 自己上 —— 但这是一个**会破坏现有 CLI 行为**的改动:今天 CLI 不传 `working_directory`,依赖 `Path.cwd()` lazy fallback。

切分:
- `Agentao.__init__(*, working_directory: Path)` —— 改成强制非 None(major version bump)
- 新增 `agentao/embedding/factory.py::build_from_environment()` —— 内部 `Path.cwd()` 一次,显式传给 `Agentao(working_directory=...)`
- CLI/ACP 全部迁移到 factory(在同一个 PR 里完成),零行为差异
- 嵌入 host 直接 `Agentao(working_directory=Path(...))`,行为可预测

**不能** 让 `Agentao.working_directory` property 继续 fallback,否则 in-place 清理就只是装饰性改动,host 仍然踩到 cwd 副作用。这是 PR 5,放在最后是因为它必须等所有 fallback(env / home / cwd)都集中到 factory 之后才能落。

### 3.5 Codex 漏掉的子系统清单

`Agentao.__init__` 里还有这五个子系统,in-place 清理时要一起考虑:

| 子系统 | 现状 | 处理建议(in-place 路线) |
|---|---|---|
| `ReplayRecorder` / `ReplayConfig`(`agent.py:275-280`) | 默认从 `working_directory` 加载 config | `Agentao` 接受可选 `replay_config: ReplayConfig \| None = None`,None 时禁用;factory 负责从盘加载 |
| `SandboxPolicy`(`agent.py:262-269`) | 读 `.agentao/sandbox.json` | 同上;`sandbox_policy: SandboxPolicy \| None = None` |
| `BackgroundTaskStore`(`agent.py:217-224`) | 写 `.agentao/background_tasks.json` | `bg_store: BackgroundTaskStore \| None = None`,None 时不持久化 |
| 插件 hook(`_plugin_hook_rules` / `_loaded_plugins`) | CLI 在 `Agentao` 实例化后注入 | 保持现状(已经是注入式) |
| `PlanSession`(`agent.py:252`) | 已支持外部传入 | 保持,无需改 |

**关键约束:** in-place 路线下,`Agentao` 默认行为仍然要等价于今天(否则 CLI 会退化)。因此 `replay_config=None` 等"默认禁用"语义只对**显式调用 `Agentao()` 的嵌入 host** 生效;CLI 走 factory,factory 显式启用所有功能保持现状。这等于把"今天 init 里隐式做的事"全部上提到 factory,`Agentao` 自己变成纯组装。

---

## 四、推荐的 PR 序列(in-place 清理路线)

核心策略:把 `Agentao.__init__` 里隐式做的所有事(读 env / home / cwd / dotenv / `.agentao/*`)上提到一个 thin factory `build_from_environment()`,`Agentao` 本身退化成纯组装。CLI/ACP 走 factory(零行为差异);嵌入 host 直接 `Agentao(...)`(完全可控)。

每个 PR 自身可独立 ship,前者不阻塞后者(除了显式标记的依赖)。

### PR 1 — 两个无新抽象的修复(1-2 天,independently good)

**1A. `AgentEvent` 加 `schema_version`**
- `agentao/transport/events.py`:`@dataclass` 加 `schema_version: int = 1` + `to_dict()`
- ACP `transport.py`、SSE adapter 的事件序列化路径输出 `schema_version`
- 现有所有事件构造点零修改(default value)

**1B. `LLMClient` 接受注入 logger**
- 添加 `logger=None, log_file=None` 参数;`log_file=None` 时不装 file handler,不触动 package root
- `Agentao.__init__` 继续传 `log_file=str(self.working_directory / "agentao.log")`,CLI 行为不变
- 修掉 ACP set_model 反复 evict 的 live bug

无依赖,可并行做。

### PR 2 — Capability 接口 + 默认实现注入到 `Agentao.__init__`(3-5 天)

新增 `agentao/capabilities/`:

```python
class FileSystem(Protocol):
    def read_bytes(self, path: Path) -> bytes: ...
    def write_text(self, path: Path, content: str, append: bool = False) -> None: ...
    def list_dir(self, path: Path, recursive: bool = False) -> list[FileEntry]: ...
    def stat(self, path: Path) -> FileStat: ...

class ShellExecutor(Protocol):
    def run(self, request: ShellRequest) -> ShellResult: ...
    def run_background(self, request: ShellRequest) -> BackgroundHandle: ...
```

加默认 `LocalFileSystem` / `LocalShellExecutor`,**对外行为完全等价于今天直接调用 `open()` / `subprocess.Popen` 的逻辑**。

`Agentao.__init__` 新增 `filesystem=None`、`shell=None` 参数,None 时装 local 默认。`ReadFileTool` / `WriteFileTool` / `EditTool` / `ShellTool` 改走 capability —— CLI / ACP / 测试零差异。

依赖:无。

### PR 3 — Factory 出现 + 各子系统去 fallback(拆成 3a / 3b 两个 PR)

把"factory 出现"和"删除 fallback"拆成两步,降低 review 风险。语义最终形态不变 —— 中间态不长期保留,3b 紧跟 3a 合并即可。

#### PR 3a — Factory 出现 + CLI/ACP 切到 factory(1 周,**新增** + 各子系统接受可选必传参数,fallback 暂留)

新增 `agentao/embedding/factory.py::build_from_environment(working_directory: Path | None = None) -> Agentao`,内部按今天 CLI 的语义负责:

- `load_dotenv()` + 读 `LLM_PROVIDER` / `*_API_KEY` / `*_BASE_URL` / `*_MODEL` → 构造 LLM 配置
- `working_directory or Path.cwd()`(在 factory 里 fallback 一次,resolve 为绝对路径)
- 加载 `<wd>/.agentao/permissions.json` + `~/.agentao/permissions.json` → 构造 `PermissionEngine`
- 加载 `<wd>/.agentao/mcp.json` + `~/.agentao/mcp.json` → 构造 MCP server config dict
- 计算 memory roots(`<wd>/.agentao` + `~/.agentao`)
- 加载 replay config / sandbox config / bg store path
- 用所有这些已构造对象显式调用 `Agentao(...)`

**3a 不删除子系统的 fallback** —— `LLMClient` 仍然能读 env,`MemoryManager` 仍然能 fallback `Path.home()`,`PermissionEngine` / `load_mcp_config` 仍然能 fallback `Path.cwd()`。但 factory 永远显式传入,不再触发 fallback 路径。

**同 PR 改动:** CLI 入口 `cli.py` 和 ACP 的 `session_new.py` / `session_load.py` 切到 factory;现有测试零变化(因为 CLI 的运行轨迹仍然等价)。

回归定位:行为差异都集中在"factory 内 vs CLI 内"哪边读盘,fallback 仍在,任何线上事故都能回滚到旧 CLI 路径。

依赖:可基于 PR 2,但不强依赖。

#### PR 3b — 收紧:删除 core fallback,参数必传(几天,**纯收紧** + 删代码)

- `LLMClient.__init__` 删掉 `os.getenv` 那一段(`agentao/llm/client.py:108-127`),`api_key` / `base_url` / `model` / `temperature` 必传
- `MemoryManager.__init__` 删掉 `Path.home()` fallback,两个 root 必传
- `PermissionEngine.__init__` 删掉 `Path.cwd()` fallback,`project_root` 必传
- `load_mcp_config()` 删掉 `Path.cwd()` fallback,`project_root` 必传
- 同步收紧测试:任何直接 `LLMClient()` / `PermissionEngine()` / `MemoryManager()` 的测试改成显式传参

**因为 3a 已经让 factory 显式传入所有这些值,3b 删除 fallback 不会触发任何运行路径变化** —— 这就是拆 PR 的全部价值:3b 是"删代码 + 紧测试",回归窗口很小。

依赖:**强依赖 PR 3a**。两个 PR 之间不要留长期中间态 —— 3a merge 后尽快 ship 3b。

### PR 4 — Async chat surface(1 周)

- `Agentao` 新增 `async def arun(user_message, ...) -> str`(把 `_chat_inner` / `ChatLoopRunner.run` 用 thread executor 桥接)
- 旧 `chat()` 保留,内部成为 `asyncio.run(self.arun(...))` 的 sync wrapper(或继续走 sync 路径,看测试覆盖)
- 不引入 `AsyncTransport`;runtime 内部仍然 sync,这是 Codex 推荐的"小一号"async 边界

依赖:无,但建议在 PR 3 之后(否则 async API 还是会摸到 env)。

### PR 5 — `working_directory` 必传(拆成 5a soft deprecation + 5b hard break)

PR 5 不能纯硬破。证据:`Agentao` 在 `agentao/__init__.py:19` 通过 `__all__` 暴露为 public API;README、`docs/MODEL_SWITCHING.md`、`docs/LOGGING.md`、`docs/features/CHATAGENT_MD_FEATURE.md` 多处把 `Agentao()` 写成推荐用法;repo 内 `examples/data-workbench/`、`examples/batch-scheduler/` 都是 `from agentao import Agentao` + `Agentao(...)` 的真实下游;PyPI 已发布(`pyproject.toml` 有完整发布元数据 + GitHub repo)。直接 hard break 等于让 README 抄作业的用户拿到 `TypeError`。

成本极低的折中:soft deprecation 在 0.2.16(patch),然后 hard break 在 0.3.0(minor,标准 SemVer breaking)。

#### PR 5a — Soft deprecation + 内部下游迁移(~3-4 天,与 PR 3b 同 release 或紧随)

**Agentao 改动(~5 行):**
```python
# agentao/agent.py
def __init__(self, ..., working_directory: Optional[Path] = None, ...):
    if working_directory is None:
        warnings.warn(
            "Agentao() without working_directory= is deprecated and will be "
            "required in 0.3.0. Pass an explicit Path, or use "
            "agentao.embedding.build_from_environment() for CLI-style "
            "auto-detection of cwd/.env/.agentao/.",
            DeprecationWarning,
            stacklevel=2,
        )
    # 现状不变:lazy fallback 仍生效
```

**同 PR 内部下游迁移:**
- `examples/data-workbench/src/workbench.py`、`examples/batch-scheduler/src/daily_digest.py` 改成显式 `Agentao(working_directory=Path(__file__).parent)` 或 `build_from_environment()`
- README.md / README.zh.md / docs/MODEL_SWITCHING.md / docs/LOGGING.md / docs/features/CHATAGENT_MD_FEATURE.md 的 `Agentao()` 例子全部改成显式形式 + 加迁移 note
- 测试代码 `test_multi_turn.py` / `test_skills_prompt.py` / `test_skill_integration.py` 改成显式 `working_directory=tmp_path`(消除内部 DeprecationWarning 噪音)
- CHANGELOG 写明 0.3.0 将做 hard break

依赖:可在 PR 3a 之后任何时候做(不需要 PR 3b 落地);建议与 PR 3b 同 release 让 0.2.16 同时具备 factory + 警告。

#### PR 5b — Hard break(minor bump,0.3.0)

- 删除 PR 5a 那 5 行 `warnings.warn`
- `Agentao.__init__(*, working_directory: Path)` 改成强制 keyword-only 必传
- 删除 `working_directory` property 的 `Path.cwd()` 分支(`agent.py:291-306`)
- factory 在内部处理 `None → Path.cwd()` 的兜底,然后**显式**传给 `Agentao`
- 跑 `pytest tests/` 全套确认所有测试已显式传参(应该已经在 5a 完成)
- CHANGELOG 标记 BREAKING

依赖:**强依赖 PR 3b 和 PR 5a**(其他子系统已无 cwd fallback 才改 Agentao 才有意义;deprecation 已经在 0.2.16 patch release 走过一轮)。

**不允许跨过 0.3.0 之后还保留 deprecation** —— 拖更长会让"in-place 清理"形同虚设,deprecation 永远成为"事实兼容承诺"。

### PR 6+ — 推到后面的项

- **MemoryStore protocol** —— 等 PR 5 落地后再做。protocol 化要先把 public API 钉死,工程量大于 fs/shell。
- **MCPRegistry / PermissionRegistry protocol** —— 同上。
- **Replay / Sandbox / BgStore 接受 None 默认禁用** —— 嵌入 host 友好;PR 3 已经分离了构造路径,这步只是把"None 时跳过"的语义 ship 出去。

---

## 五、关键文件清单(后续实施会动到的)

| 文件 | 类型 | PR |
|---|---|---|
| `agentao/transport/events.py` | 改 | PR 1A |
| `agentao/transport/sdk.py`、`agentao/acp/transport.py` | 改(序列化输出 schema_version) | PR 1A |
| `agentao/llm/client.py:89-194` | 改(logger 注入) | PR 1B |
| `agentao/agent.py:131-137` | 改(透传 logger 参数) | PR 1B |
| `agentao/capabilities/{filesystem,shell}.py` | 新 | PR 2 |
| `agentao/tools/file_ops.py`、`agentao/tools/shell.py` | 改(走 capability) | PR 2 |
| `agentao/tools/base.py` | 可能改(让 tool 拿到 capability handle) | PR 2 |
| `agentao/agent.py:216-289` | 改(默认装 local capability) | PR 2 |
| `agentao/embedding/factory.py` | 新(env / dotenv / home / cwd 全部上提到这里) | PR 3a |
| `agentao/cli.py`、`agentao/acp/session_new.py`、`agentao/acp/session_load.py` | 改(切到 factory,旧 fallback 暂留) | PR 3a |
| `agentao/llm/client.py:108-130` | 改(去 `os.getenv`,参数必传) | PR 3b |
| `agentao/memory/manager.py` | 改(去 `Path.home()` fallback,roots 必传) | PR 3b |
| `agentao/permissions.py:190-201` | 改(去 `Path.cwd()` fallback) | PR 3b |
| `agentao/mcp/config.py:84-86` | 改(去 `Path.cwd()` fallback) | PR 3b |
| `agentao/agent.py:459-477` | 改(加 `arun`,`chat` 退化为 sync wrapper) | PR 4 |
| `agentao/agent.py:46-112`(`__init__` 入口) | 改(`working_directory=None` 时 `warnings.warn`) | PR 5a |
| `examples/data-workbench/src/workbench.py:21`、`examples/batch-scheduler/src/daily_digest.py:21` | 改(显式 `working_directory=` 或 `build_from_environment()`) | PR 5a |
| `README.md:747`、`README.zh.md:739`、`docs/MODEL_SWITCHING.md`、`docs/LOGGING.md`、`docs/features/CHATAGENT_MD_FEATURE.md` | 改(`Agentao()` 例子全部显式) | PR 5a |
| `tests/test_multi_turn.py`、`tests/test_skills_prompt.py`、`tests/test_skill_integration.py` | 改(显式 `working_directory=tmp_path`) | PR 5a |
| `agentao/agent.py:46-112` + `:291-306` | 改(删 deprecation warn + 必传 + 删 `Path.cwd()` 分支) | PR 5b |

---

## 六、验证计划(每个 PR 独立)

**PR 1A**:`pytest tests/` 全绿(已有的 transport / event 测试覆盖 schema 字段);新增一个 `test_event_schema_version.py` 断言 `AgentEvent().schema_version == 1`;ACP wire trace 中能看到 `schema_version` 字段。

**PR 1B**:`pytest tests/` 全绿;新增测试断言 `LLMClient(logger=mock_logger)` 不会触动 `getLogger("agentao")` 的 handlers;ACP 反复 `set_model` 后 file handler 不会重复创建/丢失(可以查 log 文件是否仍在写入)。

**PR 2**:`pytest tests/test_*.py` 全绿(因为默认 LocalFS / LocalShell 行为等价);手动跑 `./run.sh` 确认 read/write/edit/shell 工作正常;新增 `test_filesystem_capability_swap.py` 用 in-memory fake 验证 tool 能被换底层。

**PR 3a**:`pytest tests/` 全绿;CLI / ACP 启动行为应当完全等价于今天(因为 factory 用同一套 env / cwd / home 语义);新增 `test_factory_build_from_environment.py` 在 tmpdir 模拟 `.env` + `.agentao/` + `~/.agentao/` 走完整 factory 流。

**PR 3b**:`pytest tests/` 全绿(关键 —— 任何直接 `LLMClient()` / `PermissionEngine()` / `MemoryManager()` 的测试要补齐参数);新增 `test_agent_pure_injection.py` 不走 factory,纯注入构造 `Agentao` 跑一次 chat 验证嵌入路径;CLI/ACP 行为不应有任何变化(因为 3a 已经让 factory 显式传入)。

**PR 4**:`pytest tests/` 全绿;新增 `test_async_chat.py` 用 `asyncio.run(agent.arun(...))` 调一次 chat;旧 `chat()` 行为(包括 cancellation、replay、max_iterations)保持不变。

**PR 5a**:`pytest -W error::DeprecationWarning tests/` —— 让 warning 升级成 error 验证内部代码已经全部迁移完毕(任何遗漏的 `Agentao()` 调用会被精确定位);手动跑 `./run.sh` 不应触发 warning;`python examples/data-workbench/src/workbench.py` 不应触发 warning;CHANGELOG 写明 0.3.0 将 hard break。

**PR 5b**:`pytest tests/` 全绿(应当自然通过,因为 5a 已经清理过);CHANGELOG 标记 BREAKING + minor bump 到 0.3.0;手动跑 `./run.sh` 确认 CLI 启动、`/clear`、`cd` 后 cwd 行为符合预期(CLI 在 factory 里做 `Path.cwd()`,因此每次启动 capture 一次)。

---

## 七、Remaining open questions

主要决策已锁(见顶部 "Decisions locked in")。剩余两个待定:

1. **AGENTAO.md 的处理** —— 现在 `_load_project_instructions` 直接从 `working_directory` 读。in-place 路线下,这个文件读取可以保留在 `Agentao` 里(因为它必然依赖 workspace),但不应该 fallback `Path.cwd()`。PR 3 同步迁过去即可;无需额外讨论 —— 列在这里只是提醒实施时不要漏。

2. ~~PR 5(workspace 必传)的发布时机~~ **已确认:0.2.16 patch 走 soft deprecation,0.3.0 minor 做 hard break(标准 SemVer breaking)。** 拆成 PR 5a(soft + 内部下游迁移,在 0.2.16 与 PR 3b 同 release)+ PR 5b(hard break,0.3.0)。详见第四节 PR 5。决策证据:`Agentao` 在 `agentao/__init__.py:19` 是 `__all__` 暴露的 public API;README + 多处 docs + 自带 `examples/data-workbench` 与 `examples/batch-scheduler` 都直接 `from agentao import Agentao` + `Agentao()`,PyPI 已发布。Hard break 会让 README 抄作业的下游拿到 `TypeError`,而 soft deprecation 的代价仅 ~5 行 `warnings.warn`,不延误清理目标。

