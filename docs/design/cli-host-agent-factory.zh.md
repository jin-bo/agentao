# 交互式 CLI 宿主注入：`agent_factory`

**状态：** **已实现。** 2026-07-19 为 [issue #132](https://github.com/jin-bo/agentao/issues/132) 落地——接缝（§3）、后置条件校验（§3.1）与 transport guard（§3.2，即 Q3 的结论）均已实现。仍开放：Q1（type alias 是否导出——当前保持模块内部）、Q2（参数命名——当前 `agent_factory`）、Q4（`main()` 错误展示——未改）、Q5（`agentao run`——范围外）。本文提议在 `AgentaoCLI` 与 `cli.main()` 上增加 keyword-only `agent_factory` 接缝；不扩展 plugin，也不引入全局工具注册表。
**读者：** agentao 维护者，以及复用 agentao 交互式 CLI、同时需要自行配置 `Agentao` runtime 的 Python 宿主。
**同伴文档：**
- `docs/design/cli-host-agent-factory.md` — English version
- `docs/design/host-tool-injection.zh.md` — 已落地的构造期 `extra_tools` / `disable_tools` 契约
- `docs/design/runtime-tool-injection.zh.md` — 已落地的运行期 `add_tool` / `remove_tool` 契约
- `docs/reference/host-api.md` — 稳定的进程内宿主 API
- `agentao/acp/session_new.py` — 已有的 `agent_factory` 依赖注入先例

---

## 1. 问题

agentao 已有可用的宿主工具注入契约，但 Python 宿主若要嵌入**交互式 CLI**，就没有受支持的 API 能触达它。

在 `main@8266de1` 上核实：

| 事实 | 证据 | 结果 |
|---|---|---|
| `Agentao` 接受 `extra_tools`、`disable_tools`、`enabled_tools` | `agentao/agent.py:53-104` | 直接嵌入可用 |
| `build_from_environment(..., **overrides)` 转发构造参数 | `agentao/embedding/factory.py:124-127,253-262` | factory 嵌入可用 |
| `Agentao.add_tool()` 是运行期对偶 | `agentao/agent.py:853` | 仅在宿主持有实例后可用 |
| `AgentaoCLI.__init__()` 不接受宿主注入 | `agentao/cli/app.py:43` | 交互式 CLI 封死了构造接缝 |
| `AgentaoCLI` 以固定参数调用 factory | `agentao/cli/app.py:84-88` | 无法转发 `extra_tools` |
| `main()` 构造 `AgentaoCLI()` 后立即运行 | `agentao/cli/entrypoints.py:29,74-78` | 既不接 builder，也不在首轮前暴露 agent |
| CLI 自己的测试套件已经在 patch 一个死名字 | `agentao/cli/app.py:30`、`tests/test_menu_confirmation.py:12` | 下述静默失效并非假设，仓内已实际存在 |

现有绕法是 patch 模块全局属性。它很脆弱，因为 `cli/app.py:33` 按名字导入 factory：

```python
from ..embedding import build_from_environment
```

之后替换 `agentao.embedding.build_from_environment`，不会更新已经绑定的
`agentao.cli.app.build_from_environment`。宿主必须了解 CLI 的内部 import 拓扑、修改进程全局状态，并在未来每增加一个绑定式 import site 时同步补 patch。漏掉时不会报错：CLI 正常启动，但宿主工具悄然缺失。

这种静默失效在本仓库内已可实证。`agentao/cli/app.py:30` 导入了 `Agentao` 却全文再无引用——这是旧构造路径遗留的死 import。而交互式 CLI 的测试（`tests/test_menu_confirmation.py`、`tests/test_status_pause.py`、`tests/test_clear_resets_confirm.py`、`tests/test_readchar_confirmation.py`）恰恰通过 `patch('agentao.cli.app.Agentao')` patch 这个名字；实际构造走的是 `build_from_environment`，而它在函数内从 `..agent` 局部导入 `Agentao`。因此这些 patch 拦截不到任何东西：测试实际在构造真 runtime，却照样通过（`uv run python -m pytest tests/test_menu_confirmation.py` → 7 passed）。一个已经失效的 patch 式接缝，没有产生任何失败信号。本设计要做的，正是让宿主不可能落到这个结局。

这是 API 边界缺口，不是工具校验或注册实现失效。构造期与运行期注入的既有测试都能通过；缺的是一个稳定接口，让宿主控制交互式 CLI 所构造的 runtime。

## 2. 目标与非目标

### 目标

1. 让 Python 宿主复用原生交互式 CLI，同时构造宿主配置过的 `Agentao`。
2. 保留 CLI 对生命周期对象的所有权：CLI transport、`PlanSession` 与 context limit 仍必须进入 runtime 构造。
3. 默认 `agentao` console 行为不变。
4. 注入按 CLI 实例隔离，不增加进程全局 registry 或 patch。
5. 复用仓库已有模式，不发明第二套依赖注入词汇。

### 非目标

- 不把 Python 工具实现塞进 JSON/settings。
- 不给 plugin manifest 增加 `tools` 字段，不改变 plugin 信任语义。
- 不改变 `extra_tools`、`add_tool`、MCP、plan tool 或 plugin 的优先级。
- 不增加通用 post-construction callback pipeline。
- 本 issue 不修改 `agentao run`；见 §8。
- 不保证任意伪装成 `Agentao` 的对象都能配合 CLI。factory 返回真实、与 CLI 兼容的 `Agentao` runtime。

## 3. 决策：注入 agent factory

给两个公开的交互入口增加 keyword-only `agent_factory` 参数：

```python
# agentao/cli/app.py
AgentFactory = Callable[..., Agentao]


class AgentaoCLI:
    def __init__(self, *, agent_factory: Optional[AgentFactory] = None):
        ...
        factory = (
            build_from_environment
            if agent_factory is None
            else agent_factory
        )
        self.agent = factory(
            transport=self,
            max_context_tokens=context_limit,
            plan_session=self._plan_session,
        )
```

```python
# agentao/cli/entrypoints.py
def main(
    resume_session: Optional[str] = None,
    *,
    agent_factory: Optional[AgentFactory] = None,
):
    ...
    cli = AgentaoCLI(agent_factory=agent_factory)
```

签名默认值使用 `None`，而不是直接放 factory 函数，以便明确在运行时解析默认 factory，并避免把一个可替换 callable 捕获进默认参数。受支持的扩展机制仍然是参数，不是 patch 这个模块名。这一点刻意偏离了 §5.1 引用的 ACP 先例——后者用的是 `agent_factory: AgentFactory = default_agent_factory`（`agentao/acp/session_new.py:302,475`）。形态相同、默认值不同，属于有意选择而非疏漏。

factory 会收到且仅收到 CLI 所有的构造参数：

| kwarg | 所有者 | 必需行为 |
|---|---|---|
| `transport` | `AgentaoCLI` | 转发给 `Agentao`，让交互提示、流式输出、权限请求和事件走 CLI |
| `max_context_tokens` | CLI 环境策略 | 作为 runtime context limit 转发 |
| `plan_session` | `AgentaoCLI` | 转发，让 runtime 与 CLI plan 状态共享同一对象 |

宿主通常用 `functools.partial` 组合环境 factory：

```python
from functools import partial

from agentao.cli import main
from agentao.embedding import build_from_environment

main(
    agent_factory=partial(
        build_from_environment,
        extra_tools=[NewsSearchTool(), PublishTool()],
        disable_tools={"web_search"},
    )
)
```

同一接缝也能触达其它既有构造契约，不需要每多一个契约就扩一次 CLI 签名：

```python
factory = partial(
    build_from_environment,
    working_directory=host_project_root,
    filesystem=host_filesystem,
    shell=host_shell,
    extra_tools=host_tools,
)
cli = AgentaoCLI(agent_factory=factory)
cli.run()
```

示例中刻意不含 `llm_client=`：它对主 runtime 有效，但**不被子 agent 继承**，`/agent` 会绕过它。见 §11 Q7。

宿主 factory 必须接受上述三个关键字参数。它可以接受 `**kwargs`、包装
`build_from_environment`，也可以直接构造 `Agentao`。忽略或替换 CLI 所有的依赖不属于契约；CLI 不静默修补不合规返回值。

### 3.1 对返回 runtime 的后置条件

上表是契约的**输入**一半。CLI 对**返回值**同样有硬性要求——构造后它会立刻从返回的 agent 上绑定若干属性。包装 `build_from_environment` 的 factory 天然满足；而 §3 明确允许的「直接构造 `Agentao`」路径必须自行保证：

| 返回 agent 上必须具备 | 消费位置 | 缺失后果 |
|---|---|---|
| `working_directory` | `app.py:317` | `.agentao/` 读写落到错误的根目录 |
| `permission_engine`，且**非 `None`** | `app.py:326,374` | CLI 初始化期 `AttributeError: 'NoneType' object has no attribute 'set_mode'` |
| `tools`（`ToolRegistry`） | `app.py:336-337` | 无法注册 `plan_save` / `plan_finalize`，plan 模式失效 |
| `tool_runner` | `app.py:340,382` | 无法绑定 session id 与只读模式 |
| `_plan_session`，且与 CLI 的**同一对象** | `agent.py:503`、`agent.py:1126` | `/plan` 只切换了 CLI 未切换 runtime；模型看不到 `plan_save` / `plan_finalize`，无法收尾 |
| 可赋值的 `_session_id` | `app.py:339` | 事件携带构造期 UUID，而非 CLI session id |
| `messages`、`memory_manager`、`context_manager`、`skill_manager`、`clear_history`、`get_current_model` | `input_loop.py`、`commands/sessions.py` | `/clear`、`/new`、`/sessions`、`/replay`、`/model` 在会话中途报错——且发生在 `on_session_end()` 已触发之后 |

`permission_engine` 是最尖锐的一条：`build_from_environment` 总会建一个，所以这条要求在宿主自行 `Agentao(...)` 并保留其 `None` 默认值之前完全不可见。若不加校验，报错发生在 §4 第 3 步之后，且既不指向 factory 也不指向缺失的 kwarg。

**已实现**为 `agentao/cli/app.py::_check_agent_postconditions`，在 factory 返回后立即调用。缺失属性会**合并进同一个 `TypeError`** 一次报全，而不是每跑一次修一条。该校验在默认路径（`agent_factory=None`）同样执行——都是廉价探测，换来的是让原生启动成为宿主契约的活体回归测试。

**这些检查不是什么。** 它们只是 `hasattr` / `is` 探测，并不能证明 runtime 可用。`Mock` 或任何基于 `__getattr__` 的 proxy 都能**空洞地**通过全部属性探测；`_session_id` 的可赋值性则完全没检查（proxy 把写入存到自己身上会静默成功——其故障是上表中的「取值错误」那一行，而非抛异常）。要堵死这些需要 `isinstance(agent, Agentao)`，但那会同时禁掉本接缝正要服务的 wrapper / proxy runtime。故留作 Q6 开放项，不单方面拍板。

### 3.2 覆盖 CLI 所有的 kwarg 会静默失败

本文主推的 `functools.partial` 组合有一个必须明说的 footgun，而且它有**两个相互独立的方向**，需要两种不同的缓解手段。

**方向一：宿主的值被丢弃。** partial 的 keyword 会被**调用时 keyword 覆盖**，因此 `partial(build_from_environment, transport=host_transport)` 不会报错：CLI 的 `transport=self` 胜出，宿主 transport 被丢掉，而 runtime 恰好正确绑定到 CLI。于是所有后置条件**全部通过**。**下游没有任何手段能发现它**——构造后检查在结构上就做不到，因为产出的对象与正确对象无法区分。预绑 `max_context_tokens`（宿主的上限被静默忽略）与 `plan_session` 同理。

缓解：`_reject_prebound_kwargs` 在**调用之前**检查 factory，若某个 `functools.partial` 预绑了 `transport`、`max_context_tokens` 或 `plan_session` 则抛 `TypeError`。只检查 `partial`，因为那是本 API 文档化并推荐的形态。

**方向二：CLI 的值被丢弃。** 在委派前改写 `kwargs["transport"]` 的 wrapper 会返回一个绑到别处的 runtime：流式输出、权限询问与事件全都到不了终端，CLI 表现为「卡住」而非报告违约。

缓解，**已决（Q3）：CLI 加检查。** `_check_agent_postconditions` 要求 CLI 从 runtime 的 transport **可达**——直接相同，或经由一条以 `inner` 暴露被包装 transport 的 wrapper 链。

用**可达性**而非 identity，是因为「包装」本就是 agentao 自己的约定：`ReplayManager.start()` 做的正是这件事——`agent.transport = ReplayAdapter(agent.transport, recorder)`（`agentao/replay/manager.py:104-107`，`ReplayAdapter.inner` 见 `agentao/replay/adapter.py:48-51`）。严格 `is` 检查会拒绝「返回前已开启录制」的 factory，也会拒绝宿主 tee adapter，而换不来任何安全收益：真正会弄坏 CLI 的，是一条**完全回不到 CLI** 的 transport。

该检查覆盖**两个** transport 字段。`ToolRunner` 在构造期捕获 transport，并通过自己那份 `_transport` 转发权限询问；因此两个字段不一致的 runtime 会在第一次确认时卡死——哪怕 `agent.transport` 看着没问题。这也正是 `ReplayManager` 要同时设置两者的原因。

## 4. 生命周期与优先级

改动只替换现有构造点使用的 callable。启动顺序保持：

1. 初始化 CLI 状态及其 `PlanSession`，并从进程 cwd **暂定**读取已保存的权限模式。
2. 先拒绝预绑了 CLI 所有 kwarg 的 factory（§3.2），再调用
   `agent_factory(transport=self, max_context_tokens=..., plan_session=...)`。
3. 校验返回的 runtime（§3.1），随后从中绑定 `_project_root` 与 `permission_engine`。**若 factory 自带 `working_directory`，则重新读取已保存模式**——第 1 步读的是另一个 `.agentao/settings.json`；不重读的话，项目已保存的权限姿态会在启动时被忽略，随后又被下一次 `/mode` 覆盖写回。该顺序陷阱在本接缝之前不存在：那时 CLI 不传 `working_directory`，两个根必然相等。
4. 注册 CLI 所有的 `plan_save` / `plan_finalize` 工具。
5. 把 CLI session id 绑定到 agent 与 tool runner。
6. 加载 CLI plugins。
7. 创建 prompt session 并进入输入循环。

由此得到：

- 宿主提供的 `extra_tools` 在 `Agentao` 构造时注册，首轮即可见。
- 既有的 `extra_tools` 校验与 capability binding 仍是唯一权威；CLI 不自行注册工具。
- 现有 runtime guard 继续保留 plan tool 名，因此宿主 factory 不能通过
  `extra_tools` 替换绑定 CLI plan 状态机的工具。
- plugin 加载顺序不变。Plugin agent 使用 namespace-qualified runtime name
  （`<plugin>:<agent>`）；本文不重定义 plugin collision 或优先级语义。
- 每个 `AgentaoCLI` 实例调用 factory 一次。两个 CLI 实例可以使用不同 factory 与**宿主工具集合**，不共享可变注册状态。该隔离性断言仅限于经 factory 构造的工具：inline plugin 目录仍是进程全局的（`agentao/cli/entrypoints.py:373` 写入 `_globals._plugin_inline_dirs`），因此同一进程内 plugin 贡献的工具仍被多实例共享。本文不改变这一点。

## 5. 为什么选择这个形态

### 5.1 与 ACP 先例一致

`agentao/acp/session_new.py` 已定义 `AgentFactory = Callable[..., Agentao]`，并在
`handle_session_new()` / `register()` 中接受它。ACP handler 自己拥有 transport、权限与 session 状态，再把这些对象交给注入 factory。交互式 CLI 面临相同的所有权问题，应采用同一种依赖注入形态。

两处有意偏离：CLI 的参数默认值是 `None` 而非默认 factory（§3）；且两个 `AgentFactory` alias 的**调用契约并不相同**——ACP 的是 `(cwd, client_capabilities, transport, permission_engine, mcp_servers, model)`，CLI 的是 `(transport, max_context_tokens, plan_session)`。用同一个 alias 名字承载不兼容签名是真实的混淆风险，见 §11 Q1。

### 5.2 解开构造循环

直接传预构造 `Agentao` 不是正确接缝。CLI transport 就是 `AgentaoCLI` 实例本身，plan session 也由该实例创建；二者在 CLI 构造前都不存在。factory 把 runtime 构造推迟到 CLI 所有的依赖就绪之后。

### 5.3 不镜像每个 runtime kwarg

只增加 `extra_tools=` 能狭义解决 issue #132，但 `disable_tools`、`enabled_tools`、
`filesystem`、`shell`、`llm_client` 与未来宿主契约仍不可达。在
`AgentaoCLI` 上重复 `Agentao` 构造参数，会制造两份不断漂移的公开签名。factory 直接转接已有契约。

### 5.4 保持所有权边界

曾考虑 `agent_overrides: Mapping[str, Any]`，但否决：它需要为 `transport`、
`plan_session`、`max_context_tokens` 定义合并优先级，还会把构造参数拼写错误变成弱类型 CLI 表面。callable 的责任更明确：消费 CLI 所有的依赖并返回 runtime。

## 6. 兼容性与失败行为

- 两个新参数都是可选、keyword-only；现有 Python 与 console 调用保持源码兼容。
- `agent_factory=None` 时仍走完全相同的
  `build_from_environment(transport=..., max_context_tokens=...,
  plan_session=...)` 路径。
- `resume_session` 保持既有含义与位置参数兼容性。
- 直接构造 `AgentaoCLI(...)` 时，factory 异常像今天的构造异常一样向调用者传播。
- `main(...)` 保持现有顶层异常处理和 fatal error 展示；本文不改 exit code，也不让 `main()` 返回 agent。但要看清这继承了什么：`agentao/cli/entrypoints.py:82-84` 捕获裸 `Exception`，只打印一行 `Fatal error: {e}` 便 `sys.exit(1)`，**没有 traceback**。宿主 factory 签名写错抛出的 `TypeError`，因此只会得到一行文本，没有任何指向宿主自身代码的栈帧——对一个受众全是嵌入方的接口，这个体验很差。见 §11 Q4。
- factory 按实例持有，不需要锁或全局清理。
- 该接缝成为有文档的公开 CLI-embedding API，未来若改变调用契约，需要走正常 deprecation。

## 7. 被否决的替代方案

| 方案 | 决定 |
|---|---|
| 只给 `AgentaoCLI` / `main()` 增加 `extra_tools=` | 不作为主设计：只修一个构造契约，之后仍会重复扩签名 |
| 传预构造 `Agentao` | 否决：无法自然接收 `transport=self` 和 CLI 所有的 `PlanSession` |
| 让 `main()` 返回 agent | 否决：交互循环结束后才返回，赶不上首轮注入 |
| 增加 `configure_agent(agent)` 构造后 callback | 否决：重复 runtime 构造期契约，并把它相对 plan tools/plugins 的顺序变成新 API |
| 全局 entry point 或 registry | 否决：进程全局变更、发现/顺序问题、多实例隔离差 |
| 扩展 plugin manifest 承载 tools | 否决：显著扩大代码加载、信任、权限、打包与命名空间表面 |
| 把 monkey-patch 写成支持姿势 | 否决：耦合 import 拓扑、非局部、多实例不安全、可静默失效 |
| 用 MCP 绕过 | 不视为等价：适合远程/进程工具，但增加 transport 与生命周期成本，不能替代进程内 `extra_tools` |

## 8. 范围边界：`agentao run`

`agentao/cli/run.py:527` 同样通过固定的
`build_from_environment(**factory_kwargs)` 构造 agent。这个自动化入口也有抽象上的扩展性问题，但它的公开表面不同：`RunSpec`、退出 envelope、signal handling 与非交互 transport。

Issue #132 的真实触发方是嵌入**交互式 CLI** 的薄宿主。因此本文不把 Python callable 一路穿过 `execute()` / `_execute_with_args()`。若未来出现真实宿主需要嵌入
`agentao run`，应另开设计并配自动化专属测试，而不是暗中扩大本次改动。

ACP 无需对应修改：它已经暴露 `agent_factory`。

## 9. 实现改动面

| 改动 | 位置 | 状态 |
|---|---|---|
| `AgentFactory` alias、可选 keyword-only `agent_factory`、factory 调用与 `_check_agent_postconditions` | `agentao/cli/app.py` | 已落地 |
| 可选 keyword-only `agent_factory` 转发给 `AgentaoCLI` | `agentao/cli/entrypoints.py` | 已落地 |
| 聚焦的 factory 接缝与后置条件测试 | `tests/test_cli_agent_factory.py` | 已落地（12 项） |
| 从不受支持的 `build_from_environment` patch 接缝迁出 | `tests/test_clear_resets_confirm.py` | 已落地（5 项） |
| 为 typing 导出 `AgentFactory`，或保持内部 | `agentao/cli/__init__.py` | **暂缓——Q1** |
| 在 reference 或 embedding guide 增加编程式交互 CLI 示例 | `docs/reference/` 或 embedding guide | **待办** |

`Agentao`、工具 registry、plugin models、MCP 与 `agentao.host` exports 均无需修改。

有两项顺带的清理由本改动自然带出，应随本改动一并落地，而非另起 PR：

- `agentao/cli/app.py:30` 当前的死 import `from ..agent import Agentao` 会因作为 `AgentFactory = Callable[..., Agentao]` 的指代对象而重新变为有效引用。
- §1 列出的交互式 CLI 测试可以从今天拦截不到任何东西的 `patch('agentao.cli.app.Agentao')` 迁移到注入 fake factory，从而真正测到它们声称在测的东西。

## 10. 测试矩阵与验收标准

### 测试

1. Recording factory 收到 `transport is cli`、CLI 的同一个 `_plan_session`，以及环境解析出的 context limit。
2. 转发 `extra_tools` 的 partial factory 让工具在第一次 `cli.run()` turn 前可见。
3. `main(agent_factory=factory)` 把同一个 callable 转给 `AgentaoCLI`，且 resume 行为不变。
4. 不传 factory 的 `AgentaoCLI()` 仍走当前默认路径。
5. 两个 CLI 实例用不同 factory，**宿主**工具集合不同且不泄漏。plugin 贡献的工具不在该断言范围内（§4）。
6. factory 异常分别走既有的直接构造和 `main()` 错误路径。
7. 返回未带 `permission_engine` 的 runtime 时，报错应可诊断，而不是裸的 `NoneType` `AttributeError`（§3.1）；若最终决定不加 guard，则改为文档化后置条件并有意删除本条。
8. 既有 interactive CLI、resume、plan、plugin、status 测试继续全绿。单看这条是弱信号：按 §1，这些测试今天就在 patch 一个拦截不到任何东西的名字却照样绿，所以「仍然全绿」并不能证明接缝被覆盖。真正覆盖它的是第 1 条与 §9 中迁移后的测试。

### 验收标准

- 下游宿主能删除全部 monkey-patch，通过有文档的 callable 启动带
  `extra_tools` 的原生交互式 CLI。
- 注入工具首轮可见，并保留既有 capability binding 与校验行为。
- 默认 console 启动行为不变。
- 修复不增加全局 registry、plugin feature、配置格式或新的工具优先级规则。

## 11. 待评审问题

1. **是否导出 type alias，以及用什么名字？** 这是一个决定里的两个问题。`AgentFactory` 可以保持内部、只文档化 callable 契约，也可以作为 lazy `agentao.cli` export 改善 typing 体验。但**以该名字导出**会让树内出现两个调用契约不兼容的公开 `AgentFactory`（§5.1）。可选项：保持内部；以 `CliAgentFactory` 导出；或坚持从 `agentao.cli` 导出 `AgentFactory`，接受由模块限定路径来消歧。倾向：保持内部；若要导出则用 `CliAgentFactory`。
2. **参数命名：** `agent_factory` 与 ACP 一致，优先采用。`runtime_factory` 更直白，但会为同一种模式制造两个名字。（注意本问与 Q1 正交——**参数名**可以与 ACP 一致，**type alias** 不必。）
3. ~~**是否为 transport 后置条件加 guard？**~~ **已决：加检查**，形式为对 `agent.transport` 与 `agent.tool_runner._transport` 两者做**链式可达性**判定。开启该检查后立刻暴露出 `tests/test_clear_resets_confirm.py` 中 5 个测试——它们 patch `agentao.cli.app.build_from_environment` 并返回裸 `Mock()`；现已改为经 `agent_factory=` 构造真实 runtime。这正是 §1 论证的缩微复现：一个不受支持的接缝，在契约检查存在的那一刻才由「静默通过」变为「响亮失败」。
4. **`main()` 下的 factory 错误：** `main()` 是否应对 factory 异常特殊处理，输出 traceback 或面向嵌入方的提示（§6）？这么做会改变 `main()` 的错误展示，而 §2 已将其列为范围外——但现状是嵌入方为自己的 bug 只拿到一行报告。
5. **`agentao run` 后续：** 只在真实编程式自动化宿主出现后开启；默认不并入 issue #132。
6. **是否要求 `isinstance(agent, Agentao)`？** 后置条件是 `hasattr` 探测，`Mock` 或 `__getattr__` proxy 会空洞通过（§3.1）。加 `isinstance` 能堵死这点，也契合非目标 6（「factory 返回真实、与 CLI 兼容的 `Agentao` runtime」）——但它会禁掉本接缝正要服务的 proxy / wrapper runtime，并强制每个 test double 都必须是真 runtime。当前**不**强制；属性清单是折中方案。
7. **`llm_client=` 对子 agent 不安全。** 注入的 client 对主 runtime 有效，但不被子 agent 继承：`AgentToolWrapper` 会从原始 `api_key` / `base_url` / `model` 标量重新解析并构造一个原生 client，于是 `/agent <name> <task>` 绕过宿主的代理、鉴权与埋点；而缺少 `api_key` 属性的鸭子类型 client 会在那里抛错。此为既存问题、非本接缝引入，但它使 `llm_client=` 对 CLI 宿主构成过度承诺。修复需把 client 贯穿到子 agent 构造，属另一次改动。

