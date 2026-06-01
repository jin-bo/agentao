# Host 工具注入：`extra_tools` / `disable_tools`（首版）

**状态：** **首版已落地**(`extra_tools` + `disable_tools` + `WebSearchTool(backend/api_key)`,见 §11;测试 `tests/test_host_tool_injection.py`)。`tool_options` / settings.json 仍推迟,见 §10。
**读者：** 要给 host 一个声明式工具注入口的 agentao 维护者；以及后续 PR 的评审者。
**配套：**
- `docs/design/host-tool-injection.md` — 英文版
- `docs/design/pi-mono-tools-review.md` / `.zh.md` — pi-mono 工具设计对比的来源
- `docs/design/embedded-host-contract.md` — host 契约稳定边界（本设计的归属处）
- `agentao/tooling/registry.py` — `register_builtin_tools`，改造主战场
- `agentao/tools/base.py` — `Tool` / `AsyncToolBase` / `ToolRegistry.register`

---

## 1. 问题：agentao 没有 host 工具注入口

agentao 内置约 19 个工具，但「一个工具在不在」被**五种互不统一的机制**各自控制：

- `[web]` extra 检测（`registry.py:57`，无 `bs4` 则不注册 web 工具）
- `bg_store` opt-in（`registry.py:74`，无 store 则不注册 bg-agent 工具）
- plan 模式（`base.py:242`，`plan_*` 仅 plan 模式进 schema）
- agent 注册路径（`_register_agent_tools`，`codebase_investigator` / `cli_help`）
- MCP 运行时发现（`mcp_{server}_{tool}`）

**但没有一个是 host 注入口。** host 想做三件最基本的事都没有一等公民接口：

1. **新增**一个自定义工具
2. **替换**某个内置工具的实现（例：把 `web_search` 换成自研检索）
3. **移除**某个内置工具（例：只读部署里去掉 `run_shell_command`）

现状只能在构造后捅运行时注册表 `agent.tools.register(...)`——能用，但它直接操作运行时内部，**不在 `agentao.host` 那套有稳定性保证的契约面里**。

还有第四个相关缺口:**配置内置工具的行为**。`WebSearchTool.__init__`（`web.py:279`）在构造时 `os.getenv("BOCHA_API_KEY")` 是**进程全局**的——于是同进程两个 Agentao 实例无法用不同搜索后端，撞上了 `agent.py` docstring / CLAUDE.md 声明的「同进程内两个 `working_directory` 不同的实例可共存」不变量。本版的修法见 §7：给内置工具加构造参数 + 用 `extra_tools` 传配置好的实例，**不引入额外的配置层**。

## 2. 范围决策：首版只做两件事

**必要性来自 agentao 自身的 embedded-host 定位**:host 需要一个稳定 API 来**新增/替换工具**、**隐藏不适用的内置**,同时让注入的工具自动获得 capability 绑定(`working_directory` / `filesystem` / `shell`)——而不是构造后手改 `agent.tools.register(...)`(那直接操作运行时内部,不在契约面里)。这是 §1 描述的 agentao 自身缺口,与任何外部框架无关。

> 背景:pi-mono 的 `createTool` / preset / 裸 `AgentTool` 覆盖三件套(见 `pi-mono-tools-review`)是这个想法的来源参照,但仅作背景。首版范围由 §1 的 agentao 缺口决定,**不以 pi-mono 对齐为论据**。

**首版只交付 `extra_tools` + `disable_tools`**：

| host 需求 | 首版机制 | 形态 |
|---|---|---|
| 新增 / 替换工具 | `extra_tools`（同名即替换） | 代码（实例） |
| 隐藏不适用的内置 | `disable_tools={...}` | 纯数据（名字） |
| 配置内置行为 | **无专门机制** → 用 `extra_tools=[WebSearchTool(api_key=...)]` | 代码（实例） |

**为什么不在首版做 `tool_options`（见 §10）：**
- 配置内置工具的需求,首版用 `extra_tools` 传**已构造好、带配置的实例**即可满足——前提是内置类接受构造参数（§7），这本就该做。
- `tool_options` + settings.json + env 占位 + unset 规则会引入半公开 kwargs 契约、settings 字段、loader 行为差异——对首版 host 注入过宽。等真出现「CLI 用户要配置内置工具」的需求再做,且只从一个具体工具起步（gap≠need）。

**首版也明确不做：**
- 不照搬 pi-mono 的 per-tool `operations?` 能力 DI——agentao 已有单一 `FileSystem` / `ShellExecutor` Protocol（`capabilities/`），统一重定向用一个对象就够，是更优解。
- `extra_tools` 不从 JSON 加载——工具是实现，无法序列化。

## 3. 构造器签名

接在 `agent.py` 现有 embedded-injection kwargs 区（`extra_mcp_servers` 附近）:

```python
def __init__(
    self,
    ...,
    *,
    working_directory: Path,
    extra_mcp_servers: Optional[Dict[str, Dict[str, Any]]] = None,
    # ── Host tool injection (NEW) ─────────────────────────────────
    extra_tools: Optional[Sequence["RegistrableTool"]] = None,
    disable_tools: Optional[Iterable[str]] = None,
    ...,
):
    ...
    self._extra_tools = list(extra_tools or ())
    self._disable_tools = frozenset(disable_tools or ())
    self._validate_tool_injection()   # extra:名字唯一、禁 `mcp_`;disable:须属静态内置名集
```

- **`extra_tools`** — 已构造好的 `Tool` / `AsyncToolBase` 实例列表。构造期校验拒绝重名和 `mcp_` 前缀(MCP 命名空间保留;MCP 替换走 `mcp_manager=` 等,见 §4)。
- **`disable_tools`** — 要从内置里跳过注册的工具名集合。构造期校验:**每个名字必须属于静态内置工具名集,否则 `ValueError`**——纯防 typo(`{"web_serach"}` 当场报错,不静默无效)。校验对**静态注册资格**(全部可能的内置名),**不**对实时可用性——没装 `[web]` 时 `disable_tools={"web_search"}` 仍合法(no-op),与 §10 风险 3 的「注册资格≠依赖可用性」同一原则。agent 工具不在 disable 范围(走另一注册路径)。

## 4. 语义与优先级

两条规则,无歧义:

1. **`disable_tools` 只跳过内置工具的注册**——它**不是**全局 denylist,不影响 `extra_tools`、不影响 MCP。它也**不是安全边界**:安全/越权仍由 permission engine 负责;`disable_tools` 的价值是减少 schema、避免模型尝试不适用的内置能力。
2. **`extra_tools` 在内置和 agent 工具之后注册**——独立 pass(见 §5b),所以能覆盖**内置和 agent 工具**里的同名工具。**不进入 `mcp_` 命名空间**:extra 工具名禁止 `mcp_` 前缀(§3 校验),因此构造上就不会、也不能覆盖 MCP 工具。**MCP 工具的替换走已有的 host 注入口**(`extra_mcp_servers=` / `mcp_manager=` / `mcp_registry=`),不归 `extra_tools` 管——边界更清楚。

由此推出各组合的行为,**无需任何 warn-then-continue 的冲突仲裁**:

| host 传入 | 结果 |
|---|---|
| `extra_tools` 名字不撞已注册的内置/agent 工具 | 新增 |
| `extra_tools` 名字 == 某内置 / agent 工具 | 该工具先注册、extra 在最后 pass 覆盖之（显式替换） |
| `extra_tools` 名字含 `mcp_` 前缀 | §3 校验**拒绝**(`ValueError`)——MCP 命名空间保留 |
| `disable_tools={"web_search"}` | 跳过内置 `web_search` |
| `disable_tools={"web_search"}` + `extra_tools=[名为 web_search 的工具]` | 内置被跳过,extra 注册——**净效果就是 host 的 web_search 生效**。这是合法且有意义的组合(去掉内置、换上自己的),不报错 |
| `disable_tools={"web_serach"}`（拼错 / 未知名） | 构造期 **`ValueError`**(见 §3 校验)——防 typo,不静默 |

## 5. `register_builtin_tools` 改造：仅过滤内置

`disable_tools` 过滤内置列表。**`extra_tools` 不在这里注册**(见 §5b)。无 `tool_options` 注入,无 options-eligible / dependency-wired 分类:

```python
def register_builtin_tools(agent: "Agentao") -> None:
    disabled = agent._disable_tools

    tools = [ReadFileTool(), WriteFileTool(), EditTool(), ReadFolderTool(),
             FindFilesTool(), SearchTextTool(), ShellTool()]
    if importlib.util.find_spec("bs4") is not None:
        tools += [WebFetchTool(), WebSearchTool()]
    tools += [agent.memory_tool, ActivateSkillTool(agent.skill_manager),
              AskUserTool(...), agent.todo_tool]
    if agent.bg_store is not None:
        tools += [CheckBackgroundAgentTool(bg_store=agent.bg_store),
                  CancelBackgroundAgentTool(bg_store=agent.bg_store)]

    # disable_tools:仅跳过内置注册
    tools = [t for t in tools if t.name not in disabled]

    for tool in tools:
        _bind_and_register(agent, tool)   # 见 §5b 的共享绑定 helper
```

## 5b. `register_extra_tools`：真正的最后一个 pass

`extra_tools` 必须在**全部**内置、MCP、agent 工具注册完之后才注册,否则不是"最后生效"。当前注册时序(`agent.py`)是:

```
355  self.tools = ToolRegistry()
356  self._register_tools()        # register_builtin_tools  →  内置
366  self.mcp_manager = ...        # init_mcp / register_mcp_tools  →  mcp_{server}_{tool}
381  self._register_agent_tools()  # codebase_investigator / cli_help / bg-agent
387  self.tool_runner = ToolRunner(tools=self.tools, ...)
```

把 extra 注册插在 **381 之后、387 之前**,新增 `register_extra_tools(agent)` 并在 `agent.py` 调用:

```python
# agent.py,_register_agent_tools() 之后、ToolRunner 构造之前:
self._register_agent_tools()
register_extra_tools(self)        # NEW —— host extras 真正最后注册
self.tool_runner = ToolRunner(tools=self.tools, ...)
```

```python
# tooling/registry.py
def _bind_and_register(agent, tool, *, replace=False):
    """内置与 extra 共用:绑定 capability 后注册。"""
    tool.working_directory = agent._working_directory
    tool.filesystem = agent.filesystem    # 与内置同一套 capability 绑定
    tool.shell = agent.shell              # （原 registry.py:78-83）
    agent.tools.register(tool, replace=replace)

def register_extra_tools(agent: "Agentao") -> None:
    for tool in agent._extra_tools:
        # 对活注册表判断是否覆盖。此刻表里有内置 + MCP + agent 工具,
        # 但 extra 名禁含 `mcp_` 前缀(§3),所以实际只会撞上内置/agent 工具。
        replace = tool.name in agent.tools.tools
        _bind_and_register(agent, tool, replace=replace)
```

两个关键点:

1. **extras 走与内置完全相同的 capability 绑定**（`working_directory` / `filesystem` / `shell`）——注入的工具自动继承 ACP 会话 cwd 隔离和 host 的 FS/shell 重定向，不会变成「裸」工具。
2. **放在 `_register_agent_tools()` 之后的真实目的是覆盖 agent 工具**(codebase_investigator / cli_help / bg-agent)。`replace=` 对活注册表判断,实现上无需特判来源;但因 `mcp_` 前缀被禁,可覆盖的实际只有内置和 agent 工具,**不含 MCP**(MCP 替换走 `mcp_manager=` 等已有注入口)。

## 6. `ToolRegistry.register` 加显式 override

为支持 §5 的 `replace=`,给 `register` 加一个参数（`tools/base.py:209`）。语义:**显式覆盖（host 故意替换）可静默；非显式撞名（MCP / 插件意外同名）仍 warning**——保留现有 last-write-wins 行为,只是给有意替换一条免 warn 的路:

```python
def register(self, tool: RegistrableTool, *, replace: bool = False) -> None:
    if tool.name in self.tools and not replace:
        # 非显式撞名:沿用历史行为——覆盖并 warn(MCP/插件意外同名时可见)
        _logger.warning(
            "Tool '%s' already registered; overwriting with %s",
            tool.name, type(tool).__name__)
    self.tools[tool.name] = tool
```

不把"非显式撞名"改成直接 `raise`:那会波及 MCP / 插件的撞名路径,风险大于收益。

## 7. 内置工具补构造参数（配置内置的首版途径）

要让 `extra_tools=[WebSearchTool(api_key=...)]` 能配置内置工具,内置类需接受构造参数,且**保持零参默认**（向后兼容）。这也顺手修了 §1 的多实例裂缝——显式参数 > env,env 退为 fallback:

```python
class WebSearchTool(Tool):
    def __init__(self, *, backend: str | None = None, api_key: str | None = None):
        self._bocha_api_key = api_key or os.getenv("BOCHA_API_KEY")
        self._provider = backend or ("bocha" if self._bocha_api_key else "duckduckgo")
```

**首版只承诺 `WebSearchTool` 一个工具的构造参数**:

| 工具 | 首版 kwargs | 取代的 env | 为何首版必需 |
|---|---|---|---|
| `web_search` | `backend`、`api_key` | `BOCHA_API_KEY` | §1 的多实例 env 泄漏——这是被问题陈述**证明**的真实缺陷,不是"可能有用" |

**`web_fetch` 的 `fallback`:同类多实例裂缝(`WebFetchTool.__init__` 同样读进程全局 `AGENTAO_WEB_FETCH_FALLBACK`,`web.py:139/30`),但其 env 是非密钥、偏部署级的模式开关(none/jina/crawl4ai),每实例变化优先级低,首版暂缓。** 详细论证留后续 issue/ADR。

**真正的"可能有用"档**:`read/write` 的 `max_bytes/max_lines`、shell 的 `timeout/prefix` ——无 agentao 当前必须的证据,出现具体需求再逐个补,**一次一个工具**。

**契约负担提醒(并澄清它不是反对 `tool_options` 的理由)**:配置内置**必然**让被依赖的 kwarg 名成为**半公开契约**,改名要走 deprecation——**无论经 `extra_tools` 复用内置类、还是将来经 `tool_options`**。这份负担不是 `tool_options` 引入的新成本,而是"允许 host 配置内置"本身的成本:
- 首版用 `extra_tools` 承担它,代价是**敞口**——暴露整个构造签名 + 要求该类可 import;
- 将来 `tool_options` 是把同一份契约**收口成显式 option schema** 的机制(agentao 持构造权,只公开承诺的 option key),见 §10。

首版只承诺 `WebSearchTool` 的 `api_key`/`backend` 两个名,正是为把这份敞口契约压到最小——避免一个 PR 悄悄扩大公共契约面。

## 8. 全景用法

```python
from agentao import Agentao

# 配置内置（密钥由 host 代码持有,不落任何配置文件）
agent = Agentao(
    working_directory=wd,
    extra_tools=[WebSearchTool(backend="bocha", api_key=key)],  # 同名替换内置
)

# 移除内置搜索/抓取,另新增一个自研检索工具(不同名 —— 是新增,不是替换原 web_search 语义)
agent = Agentao(
    working_directory=wd,
    disable_tools={"web_search", "web_fetch"},
    extra_tools=[MyRetrievalTool()],
)
```

## 9. 落地前置依赖与遗留项

- **`RegistrableTool` 入契约面 = 仅 re-export,不建抽象层**:从 `agentao.host` 导出已有的 `Tool` / `AsyncToolBase` / `RegistrableTool` 即可。**不**新建 host-tool protocol / adapter / wrapper——那是另一项设计任务,与本设计无关。
- **改动面集中**:`Agentao.__init__`(收 `extra_tools` / `disable_tools` + 构造期校验)、`register_builtin_tools`(加 `disable_tools` 过滤)、新增 `register_extra_tools` 并在 `agent.py` 的 `_register_agent_tools()` 之后调用、`ToolRegistry.register(replace=...)`，外加给 `WebSearchTool` 补构造参数。路线简明。
- **静态内置名集 = registry.py 一个常量/小函数**:disable_tools 校验所需的"全部可能内置名"落成 `registry.py` 里一个简单常量(或从工厂表派生的小函数)即可,**不引入工具元数据注册中心**。
- **不碰的注册路径**:plan-only（`plan_*`）、agent-tool（`codebase_investigator` / `cli_help`）的注册路径与本设计正交,本版不动。

## 10. 未来需求（不进首版）：`tool_options` + settings.json

`tool_options: Dict[str, Dict[str, Any]]`（`name → kwargs`）的唯一不可替代价值是**可进 JSON**(让非编程 CLI 用户经 settings.json 调内置);其余增量 `extra_tools` 已覆盖。首版不做。

**触发闸门(任一满足即另起 ADR 重新评估):**
- **A(JSON 驱动)**:出现「非编程 / CLI host 要经 settings.json 配内置」需求——届时带 JSON 一起上。
- **B(规模驱动)**:可配置内置达 ~3+ 个,统一 map 优于 N 次构造。

> 已评估否决「上 tool_options 但不上 JSON」:砍掉唯一不可替代的 JSON,却留中等成本,≈ 弱类型版 `extra_tools`。担忧 JSON 风险的正解是整段不上(现状),非阉割版。

**届时 ADR 要覆盖的三类风险(细节留给 ADR):** settings schema 契约面、env secret 展开、unknown-vs-依赖缺失校验。其中唯一容易踩、值得现在就记下的非显然点:**env 占位未解析时须 warn + 丢键,不能沿用 mcp 的静默 `""`**(否则 `$BOCHA_API_KEY` 未设会让 web_search 静默退回 duckduckgo)。

## 11. 速查表（首版）

| 维度 | `extra_tools` | `disable_tools` |
|---|---|---|
| 形态 | 代码（实例） | 纯数据（名字集合） |
| 首版来源 | in-process `Agentao(...)` API | in-process `Agentao(...)` API |
| settings.json | 否（实现无法序列化） | **首版否**——纯数据虽序列化友好,但首版无 settings loader,不接 settings.json;CLI/JSON 需求以后再说 |
| 能做 | 增 / 替换实现 / 配置内置（传配好的实例） | 仅跳过内置注册（不是安全边界——安全归 permission engine） |
| 注册时机 | 内置 + agent 工具之后（独立 pass，§5b） | 过滤内置列表 |
| 撞名时 | 最后注册,覆盖**内置和 agent 工具**的同名项（`replace=True` 静默）；**不进 `mcp_` 命名空间**,MCP 替换走 `mcp_manager=` 等 | 不参与撞名仲裁——只决定内置在不在 |

（`tool_options` 见 §10 阶段二，首版不交付。）
