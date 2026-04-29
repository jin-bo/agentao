# Embedded Harness Addendum:AsyncTool

**日期:** 2026-04-29
**状态:** Current-state addendum。本文档唯一仍未实现的 P0 决策是 `AsyncTool`;"Already in master" 列出的内容均已落地,不被本 addendum 重新打开。
**关联文档:** `docs/implementation/EMBEDDED_HARNESS_IMPLEMENTATION_PLAN.md`
**对照英文版:** `EMBEDDED_HARNESS_PROTOCOL_PLAN.md`

---

## Already in master(已核对,本 addendum 不重开)

- `Agentao.arun()` async public surface —— `agentao/agent.py:556`。通过 `loop.run_in_executor(None, self.chat, ...)` 桥接 sync `chat()`(`agentao/agent.py:580`)。
- `Agentao.__init__(*, working_directory: Path)` 已是 keyword-only required —— `agentao/agent.py:74`。docstring 写明 "required since 0.3.0;was a deprecated optional in 0.2.16"。
- Factory:`agentao/embedding/factory.py::build_from_environment` 接管 env / dotenv / cwd / home 发现,显式构造好子系统后传给 `Agentao(...)`。
- `MemoryStore` Protocol —— `agentao/capabilities/memory.py:33`,经 `agentao/capabilities/__init__.py` re-export。`MemoryManager.__init__` 直接接收 `MemoryStore` 实例(`agentao/memory/manager.py:57`)。
- `FileSystem` / `ShellExecutor` capability,以及 `Tool` 内的 lazy `LocalFileSystem` / `LocalShellExecutor` 默认 —— `agentao/tools/base.py:32-44`、`agentao/capabilities/{filesystem,shell}.py`。
- `BackgroundTaskStore`、`SandboxPolicy`、`ReplayConfig`、`MCPRegistry` 都是 `Agentao.__init__` 的显式 injection kwargs(`agentao/agent.py:83-91`);factory 负责按 CLI 行为从 `<wd>/.agentao/*` 装填默认。

本 addendum 不重新定义这些,也不再列 "PR 3b / PR 5b" 这类阶段性 tightening。残留的非 factory fallback(例如 `LLMClient` log-file 解析在 `agentao/llm/client.py:208,216`)不属本文档范围,如需收紧请单独建 issue 跟踪。

---

## Non-Goals

以下不在范围,且不阻塞 AsyncTool:

- `Run`、`RunStatus`、`Run.events()`、多订阅 fan-out、event backpressure。
- 在现存 sync `Transport.emit` 之外另立的 `StructuredEventSink`。
- `AsyncTransport`。
- `LLMCapability`、流式 `LLMDelta`、provider-normalized reasoning deltas。
- 超出现有 confirmation flow 的 `ToolGovernanceResult`。
- `MetacognitiveBoundary` runtime protocol —— 设计已在 `docs/design/metacognitive-boundary.md` / `.zh.md` 落档,实施推迟。
- 暴露给 tool 的 public `AgentaoContext` / run-context 对象 —— 等到第一个真正消费它的 `AsyncTool` 出现再定义。AsyncTool 本身不带 `ctx` 参数。
- 把 `McpTool`(目前 `McpTool(Tool)`,通过 `McpClientManager` 做 sync→async 桥接)迁到 AsyncTool。等 in-tree 至少有一个 `AsyncTool` 消费者真跑起来再做。
- 把 `agentao.tools.base.Tool` 本身改成结构性 `Protocol`。它继续作为 base class。
- 重开 `MemoryStore` Protocol 契约。

---

## AsyncTool

P0 仅剩这一个值得文档化的协议层决策:**让 async 工具能干净地接入现存的 tool runtime,不破坏 registry / planner,也不破坏 host-loop-affine 的资源**。

### 为什么用具体 base class,而不是只有 metadata 的 `Protocol`

当前 runtime 不是只通过 `name / description / parameters / execute` 消费工具。它还会读:

- `tool.to_openai_format()` —— `ToolRegistry.to_openai_format(plan_mode=...)` 遍历所有工具调用这个方法(`agentao/tools/base.py:182`)。
- `tool.is_read_only` —— `ToolCallPlanner._decide` 根据它决定 `readonly_mode` 行为(`agentao/runtime/tool_planning.py:225`);`ToolExecutor` 在 deny 消息里也会回显(`agentao/runtime/tool_executor.py:152`)。
- `tool.working_directory`、`tool.output_callback`、`tool.filesystem`、`tool.shell` —— 由 `Agentao` 在注册期/执行期写入或读取(`agentao/tools/base.py:17-44`)。

如果 `AsyncTool` 只声明 `name / description / parameters / requires_confirmation / async_execute` 这套 metadata-only Protocol,把一个 fixture 注册进 `ToolRegistry` 之后,**还没走到 `async_execute`,在第一次 schema 导出或 readonly_mode 评估时就会 AttributeError**。

P0 让 AsyncToolBase 成为真正的 drop-in sibling。为避免 cargo-cult `Tool` 的 surface(且与之静默漂移),抽出一个 private 共享基类 `_BaseTool`,承载所有"非 execute"的关切;`Tool` 与 `AsyncToolBase` 都继承它,两个叶子类只在 `execute` vs `async_execute` 上有差异。

### 形态

`agentao/tools/base.py` —— 抽出共享基类:

```python
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..capabilities import FileSystem, ShellExecutor


class _BaseTool(ABC):
    """内部用:sync 与 async 工具共享的所有"非 execute"关切。

    含 slot、capability accessor、path resolution helper、完整 metadata
    surface、OpenAI schema 序列化。不是 public class —— 调用方注册
    :class:`Tool` 或 :class:`AsyncToolBase` 实例;public 的联合类型是
    下面的 ``RegistrableTool``。
    """

    def __init__(self) -> None:
        self.output_callback: Optional[Callable[[str], None]] = None
        self.working_directory: Optional[Path] = None
        self.filesystem: Optional["FileSystem"] = None
        self.shell: Optional["ShellExecutor"] = None

    # --- capability accessor(与现 Tool 不变)---------------------
    def _get_fs(self) -> "FileSystem":
        if self.filesystem is None:
            from ..capabilities import LocalFileSystem
            self.filesystem = LocalFileSystem()
        return self.filesystem

    def _get_shell(self) -> "ShellExecutor":
        if self.shell is None:
            from ..capabilities import LocalShellExecutor
            self.shell = LocalShellExecutor()
        return self.shell

    # --- path policy(与现 Tool 不变)----------------------------
    def _resolve_path(self, raw: str) -> Path: ...
    def _resolve_directory(self, raw: str) -> Path: ...

    # --- metadata --------------------------------------------------
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def parameters(self) -> Dict[str, Any]: ...

    @property
    def requires_confirmation(self) -> bool:
        return False

    @property
    def is_read_only(self) -> bool:
        return False

    def to_openai_format(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class Tool(_BaseTool):
    """Sync 工具。现存 public class;子类全部不动。"""

    @abstractmethod
    def execute(self, **kwargs: Any) -> str: ...


class AsyncToolBase(_BaseTool):
    """:class:`Tool` 的 async 兄弟。surface 完全相同,只是 ``async_execute``。"""

    @abstractmethod
    async def async_execute(self, **kwargs: Any) -> str: ...
```

`Tool` 的对外 surface 在构造上保持不变:它过去暴露的所有 helper 与 accessor(`_get_fs`、`_get_shell`、`_resolve_path`、`_resolve_directory`、slot、metadata properties、`to_openai_format`)现在都在 `_BaseTool` 上,以继承的方式保留。**任何现存 `Tool` 子类都不需要改动**。这是这次抽取的关键价值:这是让 AsyncToolBase 拿到"完全等同 `Tool`"的 surface 而不靠 copy-paste 漂移的唯一办法。

规则:

- 现存 `Tool.execute(**kwargs)` 继续生效、签名不变。
- `AsyncToolBase` 是 additive 的,P0 内不要求任何现存 sync 工具迁移。
- `AsyncToolBase` 实例直接注册到现有 `ToolRegistry`,不需要 adapter / wrapper。
- 任何分发器都 **MUST NOT** 把 run-context kwargs 传给 sync `Tool.execute(**kwargs)`(分发器侧的硬不变量,不是软偏好);AsyncTool 分发与 sync Tool 分发是两条独立代码路径。

### 类型边界

允许 `AsyncToolBase` 进入 registry,意味着 registration / planning / execution 路径上的注解必须更新,否则 type checker 与读代码的人看到的契约和实际不一致。具体引入一个 alias:

```python
# agentao/tools/base.py
RegistrableTool = Tool | AsyncToolBase
```

并向以下位置传播:

- `ToolRegistry.tools: Dict[str, RegistrableTool]`,
- `ToolRegistry.register(tool: RegistrableTool) -> None`,
- `ToolRegistry.get(...) -> RegistrableTool`,
- `ToolRegistry.list_tools() -> List[RegistrableTool]`(`agentao/tools/base.py:174`),
- `ToolCallPlan.tool: RegistrableTool`(`agentao/runtime/tool_planning.py:75`),
- 经由同一 registry 流转的 sub-agent 表面:`AgentManager.create_agent_tools(..., all_tools: Dict[str, RegistrableTool], ...)`(`agentao/agents/manager.py:144`)与 `AgentToolWrapper.__init__(..., all_tools: Dict[str, RegistrableTool], ...)`(`agentao/agents/tools.py:235`;类定义在 `:221`),
- 任何当前读 `plan.tool: Tool` 的 executor / formatter 签名。

不做这步,duck-type 在 runtime 工作,但每条注解都在撒谎,IDE/`mypy` 会对 AsyncTool 子类上的 `tool.async_execute` 访问全部报错。alias 是 opt-in 的:确实只处理 sync 工具的辅助代码(例如 `Tool.execute`-only 的 helper)继续保留更窄的 `Tool` 注解。

### 分发(Dispatch)—— host-loop-aware 桥接

朴素的"每次调用在 chat 线程上 `asyncio.run()`"方案**不能作为唯一路径**。`arun()` 已经把 host 主线程的 loop 与 chat 线程隔离 —— `chat()` 跑在 `loop.run_in_executor(None, ...)` 派生的 worker 线程上(`agentao/agent.py:580`)。在 chat 线程新起的 fresh loop 摸不到任何绑定在 host loop 上的资源 —— 比如 `aiohttp.ClientSession`、async DB client、`anyio` task group,任何在 host loop 上创建的对象。**持有这种资源的 async 工具是常态,不是特例**,把这个问题推到 P1 是可预见的坑。

P0 因此在 `arun()` 入口捕获 host loop,并把它透传给 dispatcher:

1. `Agentao.arun(...)`:在 `run_in_executor` 之前 `host_loop = asyncio.get_running_loop()`,把 `host_loop` 暴露给 chat runtime —— 具体可以挂在已经透传到 `chat()` 的 `CancellationToken` 上,或者新增一个 `runtime_loop` kwarg 一路透到 `ChatLoopRunner` → `ToolRunner` → `ToolExecutor`。两种实现都行,只有 dispatcher 需要读它。

2. `ToolExecutor` 分发到一个 `AsyncToolBase` 时:

   ```python
   # ToolRunner.execute 与 ToolExecutor.execute_batch 的当前签名都
   # 允许 ``cancellation_token=None``(见 agentao/runtime/tool_runner.py:108、
   # agentao/runtime/tool_executor.py:68)。dispatcher 必须容忍 None token,
   # 不能直接 AttributeError。两种等价写法,实施 PR 二选一即可:
   #
   #   (a) 入口处归一化:    token = token or CancellationToken()
   #   (b) callback 加守卫:  remove = (token.add_done_callback(...)
   #                                   if token is not None
   #                                   else (lambda: None))
   #
   # 下面的 snippet 用 (b),让显式传 None 的 caller 不需要分配一个永远不用的 token。

   if runtime_loop is not None and runtime_loop.is_running():
       # Async 路径:arun() 捕获了 host loop。把协程跑在 host loop 上,
       # 让 loop-affine 资源继续可用;chat 线程阻塞在 future 上拿结果。
       fut = asyncio.run_coroutine_threadsafe(
           tool.async_execute(**args), runtime_loop
       )
       remove = (
           token.add_done_callback(lambda: fut.cancel())
           if token is not None
           else (lambda: None)
       )
       try:
           result = fut.result()  # 阻塞;fut.cancel() 通过 CancelledError 解阻塞
       finally:
           remove()
   else:
       # Sync 路径:未捕获 host loop。仅支持 loop-independent
       # 的 async 工具(详见下面 "Sync 路径范围")。
       result = asyncio.run(tool.async_execute(**args))
   ```

3. `ToolRunner` 的对外接口仍然是 sync 的。`asyncio` 桥接逻辑只放在 `ToolExecutor`(或它内部的小 helper)里。Sync `Tool` 执行路径不变。

#### Cancellation 状态映射

token callback 触发 `fut.cancel()` 后,`fut.result()` 抛 `concurrent.futures.CancelledError` —— 这是 `concurrent.futures.Future.result()` 用的异常类,继承自 `concurrent.futures._base.Error` → `Exception`(在 3.8+ 中保持不变)。它**不是**与 `asyncio.CancelledError` 同一个类:自 Python 3.8 起两者已分开 —— `asyncio.CancelledError` 被移到 `asyncio.exceptions` 并改为继承 `BaseException`,而 `concurrent.futures.CancelledError` 仍继承 `Exception`。实施者必须显式 catch `concurrent.futures.CancelledError`;catch `asyncio.CancelledError` 不会匹配 bridge 的失败模式。

当前 `ToolExecutor` 工具执行体用 `except Exception as exc: ... status = "error"` 包裹(`agentao/runtime/tool_executor.py:243-251`)。由于 `concurrent.futures.CancelledError` *是* `Exception`,如果让它落到那个 handler,token 触发的取消会被静默错标为 `status="error"`。async dispatch helper 因此必须**先于**那个 handler 显式 catch,并走**已有**的 cancelled 出口 —— `ToolExecutor` 在 permission-deny / readonly-mode 取消场景下已经产生 `ToolExecutionResult(..., status="cancelled", ...)` 并 emit `status="cancelled"` 的 `TOOL_COMPLETE`(`agentao/runtime/tool_executor.py:164,175,192`)。AsyncTool 取消复用同一契约。

#### Cancel 分支在 `_execute_one` 里的层级 —— 以及为什么需要显式 ack

这个分支**不是**一个"返回字符串"的 helper 嵌进现有 `try / except Exception / status / _emit_complete / return` 尾段;那样会要么 double-emit `TOOL_COMPLETE`,要么把取消错标为 `status="error"`。dispatcher 的取消分支必须 short-circuit 整个 success/error 尾段:emit 一次 `status="cancelled"` 的 `TOOL_COMPLETE`,然后直接返回 `_execute_one` 本身的 `(call_id, ToolExecutionResult)` 元组。

还有一个更细的时序问题。`concurrent.futures.Future.cancel()` 把 future 状态翻成 `CANCELLED` 是同步的,worker 线程的 `fut.result()` 会立刻抛 `concurrent.futures.CancelledError`。但 host loop 上对应的 asyncio task 在那一刻只是**刚开始**处理 cancellation —— 它自身的 `try / finally` cleanup(包括 `aiohttp.ClientSession.close()`、async DB 回滚、`anyio.CancelScope.__aexit__` 之类)是在 dispatcher 已经看到 `CancelledError` **之后**才异步发生的。直接立刻返回 `status="cancelled"` 因此是 racy 的:acceptance 中"协程收到 `CancelledError`"和"host loop 上没有遗留 future"两条断言只在测试恰好 drain 了 loop 时才稳定。

dispatcher 因此把调用包进一个薄 wrapper coroutine,wrapper 自己的 `finally` 在用户 coroutine 真正完成 cleanup 后置一个 `threading.Event` ack。cancel 分支 emit `TOOL_COMPLETE` 之前,带上 bounded timeout 等这个 ack:

```python
import concurrent.futures
import threading

# 在 _execute_one 内部,AsyncTool 分发路径:

ack = threading.Event()

async def _bridged():
    try:
        return await tool.async_execute(**args)
    finally:
        # 在用户 coroutine 自己的 try / finally cleanup 之后、在
        # host loop 上运行。通知 worker 线程 "把 cancel 报到
        # TOOL_COMPLETE 是安全的"。
        ack.set()

fut = asyncio.run_coroutine_threadsafe(_bridged(), runtime_loop)
remove = (
    token.add_done_callback(lambda: fut.cancel())
    if token is not None
    else (lambda: None)
)

# Bounded ack 超时。要长到能容纳常见 async cleanup
# (关闭 client session、回滚事务),也要短到一个卡死的
# host loop 不会无限期挂住 worker。5s 是起点,实施时调。
_ASYNC_CANCEL_ACK_TIMEOUT_S = 5.0

try:
    try:
        result_text = fut.result()
        # 成功路径:_bridged() 的 finally 已经跑过,ack 已 set。
    except concurrent.futures.CancelledError:
        # Token 触发的取消。等 _bridged() 的 finally 在 host loop 上跑完,
        # 让用户 coroutine 的 cleanup 完成后再上报 TOOL_COMPLETE。
        # 超时则记 warning 后继续,避免卡死 host loop 时把 worker 也挂死。
        if not ack.wait(timeout=_ASYNC_CANCEL_ACK_TIMEOUT_S):
            self._logger.warning(
                "AsyncTool %s: cancel ack timeout after %.1fs; emitting "
                "TOOL_COMPLETE without confirmed coroutine cleanup.",
                fn, _ASYNC_CANCEL_ACK_TIMEOUT_S,
            )
        duration_ms = round((time.monotonic() - t0) * 1000)
        # 与 executor 现有 cancelled 路径(:162 用
        # "denied by permission engine")保持一致,error 字段
        # 放一段简短人类可读的 reason。``token.reason`` 由
        # Agentao.arun() / chat 调用方提前设好(典型为
        # "async-cancel" 或 CLI Ctrl+C 的 "user-cancel")。
        reason = token.reason if token is not None and token.reason else "async-cancel"
        self._emit_complete(fn, call_id, "cancelled", duration_ms, reason)
        return call_id, ToolExecutionResult(
            fn_name=fn, result="Tool execution cancelled.",
            status="cancelled", duration_ms=duration_ms, error=reason,
        )
    # Success:落入现有的 status / _emit_complete / post-tool hook
    # 尾段。其它异常(TypeError、RuntimeError 等)正常传播给已有的
    # `except Exception` handler,与 sync 工具一样标为 status="error"。
finally:
    remove()
```

由此得到三条性质:

1. **不双发**。`TOOL_COMPLETE` 每次调用恰好 emit 一次:成功走原 tail,取消走这条分支,其它异常走原 `except Exception`。
2. **Cleanup-ordered cancel 上报**。emit `TOOL_COMPLETE(status="cancelled")` 时,用户 coroutine 自己的 `finally` 已经在 host loop 上跑完(或 bounded timeout 已到并记了 warning)。这是让 acceptance 的 "no dangling future" / "coroutine 收到 `CancelledError`" 从 flaky 变成 stable 的关键。
3. **error 字段约定保持**。executor 现存的 cancelled 路径在 error 字段放简短 reason(`agentao/runtime/tool_executor.py:162` 是 `"denied by permission engine"`);AsyncTool 的 token 取消用 `token.reason` 镜像同一约定(典型为 `Agentao.arun()` 在 `agentao/agent.py:585` 设的 `"async-cancel"`,或 CLI Ctrl+C 的 `"user-cancel"`),token 没有 reason 时回退到 `"async-cancel"`。

chat loop 已经把 `token.is_cancelled` 作为 unwind turn 的触发(在 `chat()` 边界抛 `AgentCancelledError`)。

#### Cancellation —— 具体机制

当前 `CancellationToken`(`agentao/cancellation.py:15-51`)只是一个 `threading.Event` 的薄包装,只暴露 `is_cancelled` / `check()` / `cancel()`,没有 callback / watcher API,而 `arun()` 也只把 `asyncio.CancelledError` 转成 `token.cancel("async-cancel")`(`agentao/agent.py:585`)。所以 dispatcher 阻塞在 `fut.result()` 期间根本观察不到 token 翻转 —— 上一稿那句 "dispatcher 调 `fut.cancel()`" 在现状下不可实现。

本 addendum 做一处小而 contained 的扩展:给 `CancellationToken` 加 callback 注册。

```python
# agentao/cancellation.py
class CancellationToken:
    ...
    def add_done_callback(self, fn: Callable[[], None]) -> Callable[[], None]:
        """注册 fn,在 ``cancel()`` 被调用时同步执行。

        若 token 已 cancelled,``fn`` 在调用线程上立刻执行,然后此方法返回。

        返回一个 unregister callable,让调用方在自己的临界区结束后摘掉
        callback。idempotent —— 重复调用 unregister 是 no-op。
        """
```

实现要点:callback 列表用一把内部 `threading.Lock` 保护;`cancel()` 在锁内拍快照,锁外触发 callback,避免重入。callback 抛错统一 catch 并 log,避免一个坏 callback 阻塞另一个。

有了这个原语,前面那段 dispatcher 代码就能照写实现。无需 polling、无需新增 long-lived loop。这次改动只动 `cancellation.py` + dispatcher;其他调用点无须迁移。

#### Sync 路径范围(收窄)

sync `chat()` fallback(`asyncio.run(tool.async_execute(...))`)**仅支持 loop-independent 的 async 工具** —— 即在单次 `async_execute` 调用内部完成创建/释放所有 loop-bound 资源的工具(例如 coroutine 内打开并关闭一个 self-contained `httpx.AsyncClient`)。

它**不支持**持有 long-lived loop-affine 资源的工具(例如 tool 实例上缓存的 `aiohttp.ClientSession`、注册期就建好的 async DB pool、跨调用的 `anyio` task group)。需要这类工具的 sync host 必须:

- 从自己控制的 event loop 里调 `agent.arun(...)`(让 `arun()` 把它捕获为 `runtime_loop`),或者
- 等待未来在 sync 路径上的显式 host-loop 注入(P0 不做;在 "Future Protocol Work" 里跟踪)。

持有 loop-affine 状态的 `AsyncToolBase` 子类应当在文档里写明这个约束,可选地在 `async_execute` 入口处 `assert asyncio.get_running_loop() is self._bound_loop`,在被 sync fallback 路径误调时立刻报错。

**这条设计是 P0 的强制项,不是 P1 follow-up**。没有它,第一个真实 async 工具(任何通过 `arun()` 持有 loop-affine 资源的)就直接坏。

### Acceptance(仅就本 addendum 而言)

本 addendum 满足以下条件即视作完成:

- 一个 `AsyncToolBase` 具体子类能直接注册到 `ToolRegistry`,经过 `ToolRegistry.to_openai_format(plan_mode=...)` 和 `ToolCallPlanner._decide` 对 `is_read_only` 的访问都不抛 AttributeError。(测试:注册 + 走这两个路径 + 断言无异常)
- 类型边界已发布:`RegistrableTool = Tool | AsyncToolBase` 存在,正文 "类型边界" 一节列出的每个 surface 都使用它。当前项目并未配置 `mypy` / `pyright`(已查 `pyproject.toml` dev deps),因此用两条更轻的检查替代:
  - (a) `python -m compileall agentao/tools/base.py agentao/runtime/tool_planning.py agentao/runtime/tool_executor.py agentao/agents/manager.py agentao/agents/tools.py` 通过。
  - (b) 一条单测,用 `typing.get_type_hints` 断言下列每一处解析为 `RegistrableTool`(或视情况是 `Dict[str, RegistrableTool]` / `List[RegistrableTool]`):
    - `ToolRegistry.register["tool"]`,
    - `ToolRegistry.get["return"]`,
    - `ToolRegistry.tools` —— 注意当前代码中 `self.tools` 仅在 `__init__` 内做实例赋值(`agentao/tools/base.py:145`),`get_type_hints(ToolRegistry)` 看不到。实施 PR 必须把注解提到 class body(`class ToolRegistry: tools: Dict[str, RegistrableTool]` 加上现有 `__init__` 内的赋值),才能让这条检查可达。
    - `ToolRegistry.list_tools["return"]`,
    - `ToolCallPlan` 的 `tool` 字段(经 `get_type_hints(ToolCallPlan)["tool"]`),
    - `AgentManager.create_agent_tools["all_tools"]`,
    - `AgentToolWrapper.__init__["all_tools"]`。

  引入 mypy 显式不在本 addendum 范围;若后续 PR 引入,此条 acceptance 可顺势收紧。
- `_BaseTool` 抽取保持 `Tool` surface 字节等价:每个现存 `Tool` 子类(file_ops、shell、web、search、memory、agents、skill 等)无需改代码即可 import 与运行。`pytest tests/` 在不修改任何现存 tool 模块的前提下通过即视作满足。
- public import surface 已发布:`agentao/tools/__init__.py` 在现有 `Tool` / `ToolRegistry`(`agentao/tools/__init__.py:3`)旁边 re-export `AsyncToolBase` 与 `RegistrableTool`,并在 `__all__` 中列出。一条 smoke test 断言 `from agentao.tools import AsyncToolBase, RegistrableTool` 成功、`RegistrableTool` 与 `agentao.tools.base` 中定义的 alias 一致、且测试内的 `AsyncToolBase` 子类可以实例化。(`_BaseTool` 刻意**不** re-export —— 保持 private。)
- `CancellationToken.add_done_callback(...)` 存在并满足:(a) 另一线程调 `cancel()` 时,callback 同步执行;(b) 若 token 已 cancelled,callback 立刻执行;(c) 返回的 unregister callable 是 idempotent 的。在 `cancellation.py` 上覆盖直接的单元测试。
- 一条端到端测试经由 `await agent.arun(...)` 调用一个 `AsyncToolBase`,在其 `async_execute` 中断言 `asyncio.get_running_loop() is host_loop`(协程**确实**跑在 host loop 上,而不是 chat 线程的 fresh loop)。
- 一条端到端测试经由 sync `agent.chat(...)` 调用一个 **loop-independent** 的 `AsyncToolBase`,验证 fresh-loop fallback 路径正常返回。测试 docstring 显式说明 sync-path 范围限制。
- `arun()` 路径下的 cancellation 测试:注册一个 `AsyncToolBase`,其 `async_execute` 内 `await` 在某个 `asyncio.Event` 上,且 `try/finally` 内置 `cleanup_ran = True` 记录位;取消 host task(或直接 `token.cancel("async-cancel")`);断言 (a) 协程收到 `asyncio.CancelledError`、(b) 用户 coroutine 自己的 `finally` 已经跑过(`cleanup_ran is True`)且发生在 `TOOL_COMPLETE` **被观察到之前**(这是 dispatcher ack 机制要保证的核心性质)、(c) `run_coroutine_threadsafe` future 已 cancelled(dispatcher 捕获的是 `concurrent.futures.CancelledError`)、(d) 记录的 `ToolExecutionResult.status` 为 `"cancelled"`,emit 出的 `TOOL_COMPLETE` 事件携带 `status="cancelled"`(不是 `"error"`)且 `error="async-cancel"`(与 `token.reason` 一致)、(e) 这个被取消的调用 `TOOL_COMPLETE` **恰好只 emit 一次**(没有从 success 尾段重复 emit)、(f) chat 调用 unwind 完毕、(g) 测试 drain 后 host loop 上没有遗留 task。
- ack-超时测试:注册一个 `AsyncToolBase`,其 `async_execute` 的 `finally` 故意阻塞超过配置的 `_ASYNC_CANCEL_ACK_TIMEOUT_S`;触发取消;断言 dispatcher 记 timeout warning、仍然 emit 一次 `TOOL_COMPLETE(status="cancelled", error="async-cancel")`、chat 调用 unwind 完毕。这条钉住"卡死的 host loop 不能挂住 worker"的性质。
- 任何分发器都不向 sync `Tool.execute()` 传 `ctx` / run-context kwarg。补一条单测:注册一个 `execute()` 对未知键抛错的 sync 工具,确认能正常调用。

更广义的 P0 acceptance 仍以 `EMBEDDED_HARNESS_IMPLEMENTATION_PLAN.md` 为准。

---

## 与未来协议工作的关系

由 CLI/ACP/examples 之外的真实消费者驱动,未来 P1/P2 工作可能引入:

- public 的 `AgentaoContext` / run-context 对象 —— 等到第一个 `AsyncToolBase` 真正需要 run-local metadata,
- 把 `McpTool` 迁到 `AsyncToolBase`(顺带消除 `McpClientManager` 的 sync bridge),
- 独立的 `AsyncToolRunner` —— 若 chat 线程阻塞模型成为瓶颈,
- run lifecycle 对象、structured event stream、async transport,
- host 注入的 LLM capability,
- 更丰富的 governance result,
- metacognitive boundary 注入(设计已在 `docs/design/metacognitive-boundary.md`),
- 在现有 `MemoryStore` Protocol 之上叠加的更多 public 契约。
