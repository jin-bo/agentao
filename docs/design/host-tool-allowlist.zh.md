# Host 工具白名单：`enabled_tools`（设计草案 · 收敛版）

**状态：** **草案，待评审。** 实现未开始。是 `host-tool-injection`（`extra_tools` / `disable_tools`，已落地）的加法对偶。
**读者：** 要给 host 一个声明式「最小工具集」选择口的 agentao 维护者；以及本设计后续 PR 的评审者。
**配套：**
- `docs/design/host-tool-allowlist.md` — 英文版（本草案确认后再写）
- `docs/design/host-tool-injection.zh.md` — 减法/加法注入口 `extra_tools` / `disable_tools`（直接前身）
- `docs/design/runtime-tool-injection.zh.md` — 运行时对偶 `add_tool` / `remove_tool`
- `docs/design/embedded-host-contract.md` — host 契约稳定边界（本设计的归属处）
- `agentao/tooling/registry.py` — `register_builtin_tools` / `BUILTIN_TOOL_NAMES`
- `agentao/agent.py` — 构造器、注册时序、`remove_tool`

> **第二稿收敛说明：** 首稿曾把 `enabled_tools` 设计成「跨 built-in + agent + extra + 豁免 MCP/plan 的最终可见集」，并对 `extra_tools` 冲突 raise、配套导出 `CORE_TOOL_NAMES`。评审认定这过度设计。本稿据评审收敛到**最短可行路线**：`enabled_tools` 只筛 agentao-owned 的 built-in + agent-path；extra 恒保留；MCP / profile / `CORE_TOOL_NAMES` / 将 extra 纳入白名单 全部 demand-gated 后延（见 §8）。

---

## 1. 问题：只有减法，没有加法

`host-tool-injection` 落地后，host 控制「工具在不在」的构造期入口只有两个：

- `disable_tools={...}` — 从内置里**跳过**指定名字（减法）。构造期对照 `BUILTIN_TOOL_NAMES` 校验（`agent.py:366`）。
- `extra_tools=[...]` — 逐个**注入**实例（按实例的加法）。

「**只保留一个最小核心集**」这个最常见的 embedded 需求，今天只能用 `disable_tools` 把**不想要的逐个列出**。三个已 grep 核实的缺陷：

1. **冗长。** 要留 ~6 个文件/shell 工具，得手写 ~9 个排除名（`web_fetch`、`web_search`、`todo_write`、`activate_skill`、`save_memory`、`check_background_agent`、`cancel_background_agent` …）。
2. **会静默漏。** `BUILTIN_TOOL_NAMES` 是扁平常量、注释明写 **"NOT a tool-metadata registry"**（`registry.py:38-47`）。日后新增内置会**静默进入** host 的集合——黑名单天然无法表达「只要这些，其余一律不要」。
3. **够不到 agent-path 工具（仅限构造期）。** `disable_tools` 只作用于内置名集；project/plugin agent 工具走 `_register_agent_tools`（`agent.py:506`），不在其范围。

> **两点边界澄清（评审修正，已核实）：**
> - **运行期能移除 agent 工具。** `remove_tool()` docstring 明确「Built-in / extra / agent tools can be removed」（`agent.py:819`）。缺口准确表述是：**没有构造期声明式机制阻止 agent-path 工具进 schema**，而非「无法移除」。
> - **内置 subagent 默认就不在 schema 里。** `enable_builtin_agents: bool = False`（`agent.py:92`）。「不要 subagent」**只在**有 project/plugin agent、或显式开了内置 agent 时才成立。

## 2. 范围决策：v1 只加 `enabled_tools` 一个 kwarg

**v1 只交付构造器参数 `enabled_tools`**：一个**只作用于 agentao-owned（built-in + agent-path）工具**的加法白名单。它补齐 §1 的加法缺口，并一次性解决「新增内置静默进入」——白名单里没写的内置/agent 工具，无论何时新增都不会进。

| host 需求 | 现有机制 | 本设计 |
|---|---|---|
| 隐藏个别不适用内置 | `disable_tools={...}`（减法） | 不变 |
| 新增 / 替换工具 | `extra_tools=[...]` | 不变 |
| **只保留一个最小核心集** | （无，只能反向枚举黑名单） | **`enabled_tools={...}`（加法白名单）** |

**v1 明确不做（全部 demand-gated，触发条件见 §8）：**
- 不把 MCP 纳入白名单范围。
- 不做 profile 档位（`tool_profile="core"|...`）。
- 不导出 `CORE_TOOL_NAMES` 常量——文档示例里**手写**核心集。
- 不为「extra 不在白名单里」设计冲突策略——extra **恒保留**（§4）。
- 不做按轴开关（`enable_agent_tools=False` 之类）。

## 3. 构造器签名

接在 `host-tool-injection` 的注入区之后：

```python
def __init__(
    self,
    ...,
    *,
    working_directory: Path,
    extra_tools: Optional[Sequence["RegistrableTool"]] = None,
    disable_tools: Optional[Iterable[str]] = None,
    enabled_tools: Optional[Iterable[str]] = None,   # NEW
    ...,
):
    ...
    # None = 不启用白名单；任意 iterable（含空集）= 启用
    self._enabled_tools = frozenset(enabled_tools) if enabled_tools is not None else None
    self._validate_tool_injection()   # 仅做不依赖注册顺序的检查，见 §5
```

**为什么叫 `enabled_tools`，不叫 `tools=`：** `tools=` 会和 (a) 已有 kwarg `extra_tools=`、(b) 运行时实例属性 `agent.tools`（`ToolRegistry`）撞名/混淆。`enabled_tools` 与 `disable_tools` 成对、语义对称。

**启用判定一律用 `is not None`，不用「非空」：**

| 传入 | 含义 |
|---|---|
| `enabled_tools=None`（默认） | **不启用**白名单；built-in + agent + extra 全注册（与今天逐字节相同） |
| `enabled_tools={"read_file", ...}` | 启用；只保留白名单内的 built-in / agent 工具 |
| `enabled_tools=set()`（空集） | 启用；**移除全部 built-in + agent 工具**（extra / MCP / plan-only 仍在，见 §4）。合法的极小配置，不报错 |

## 4. 语义

**一条规则：`enabled_tools is not None` 时，移除所有名字不在白名单内的 built-in 与 agent-path 工具。其余一概不动。**

| 类别 | 受 `enabled_tools` 约束？ | 原因 |
|---|---|---|
| 内置工具（`BUILTIN_TOOL_NAMES`） | **是** | 要筛的主体 |
| agent-path 工具（project/plugin/内置 agent） | **是** | §1 缺口 3 的目标；注意下方「全有或全无」 |
| `extra_tools` 注入项 | **否，恒保留** | host 显式构造并传入实例，本身就是选择；再要求把名字写进白名单是重复配置。要去掉某 extra，host 不传它即可（那是 host 自己的代码），或运行期 `remove_tool()` |
| MCP 工具（`mcp_*`） | **否，不在本设计范围** | 不同生命周期/命名空间；host 经 `mcp.json` / `mcp_manager=` / `extra_mcp_servers=` 控制。**注意**：启用白名单不会隐藏已配置的 `mcp_*`——要最小化 MCP，请在 MCP 那一层做 |
| plan-only（`_PLAN_ONLY_TOOLS` = `plan_save`/`plan_finalize`，`base.py:256`） | **否，恒保留** | 绑定 plan 模式状态机，已由 `plan_mode` 在 schema 层 gate，非 host 可选项 |

**与 `disable_tools` 互斥：** `enabled_tools is not None` 且 `disable_tools` 非空 → `ValueError`（§5）。白名单已能表达「只要这些」，再叠减法只会让组合语义变绕，无净收益。

**agent-path 名是动态的 ⇒ 跨类别「全有或全无」（须在文档写明）：** 内置名是静态 15 个；project/plugin agent 工具名来自 frontmatter `name:`，**因项目而异**。白名单一旦启用，没写出的 agent 名 = 该 agent 被筛掉。后果：`enabled_tools` **无法表达**「保留所有 agent + 只要部分内置」——想留某 agent 就得枚举其（项目相关的）名字。对「最小核心」目标这正合意；若「全留 agent、只筛内置」是真实用例，那才是 §8 按轴开关的触发条件。

## 5. 校验：拆两处（关键，融合评审 Finding 2）

`_validate_tool_injection()` 在 `agent.py:183` 调用，**早于** `AgentManager` 创建（`502`）与 `_register_agent_tools()`（`506`）——此刻 agent 工具名**拿不到**。故校验必须拆开：

**(a) 构造期 `_validate_tool_injection()`——只做不依赖注册顺序的纯检查：**
1. **互斥**：`enabled_tools is not None` 且 `disable_tools` 非空 → `ValueError`。
2. **保留名拒绝**：`enabled_tools` 含 `mcp_` 前缀名或 `_PLAN_ONLY_TOOLS` 名 → `ValueError`（这两类本就不受白名单管，写进去无意义，且与 `extra_tools` / `add_tool` 的保留名规则一致）。这步只看字符串，无顺序依赖。

**(b) apply 期 `apply_enabled_tools()`——typo 守卫，对 live registry 校验：**
3. 全量注册完成后，`enabled_tools` 中每个名字必须存在于 **live registry ∪ `BUILTIN_TOOL_NAMES`**，否则 `ValueError`、列出未知名。
   （并 `BUILTIN_TOOL_NAMES` 是为了：装了 `[web]` 才会有 `web_search` 进 registry，但 `web_search` 本就是合法内置名，不该因当前未装而被判 typo——与 `disable_tools` 校验「注册资格≠依赖可用性」同一原则。）

> 白名单的 typo 比 `disable_tools` 危险：`disable_tools` 拼错只是 no-op，而 `enabled_tools` 拼错会让那个工具**被静默排除**且 host 难以察觉——所以 (b) 的守卫不可省。

## 6. 实现落点：注册全部完成后单次终筛

注册时序（`host-tool-injection §5b`，已核实 `agent.py:480-512`）：

```
self.tools = ToolRegistry()
self._register_tools()        # 内置（含 disable_tools 过滤）
self.mcp_manager = ...        # mcp_{server}_{tool}
self._register_agent_tools()  # agent-path
register_extra_tools(self)    # host extras（最后 pass）
apply_enabled_tools(self)     # NEW —— 终筛，见下
self.tool_runner = ToolRunner(tools=self.tools, ...)
```

```python
# tooling/registry.py
def apply_enabled_tools(agent: "Agentao") -> None:
    allow = agent._enabled_tools
    if allow is None:                       # 默认：不启用
        return

    # (b) typo 守卫：未知名 fail-fast（§5-3）
    from agentao.tools.base import ToolRegistry
    known = set(agent.tools.tools) | BUILTIN_TOOL_NAMES
    unknown = sorted(allow - known)
    if unknown:
        raise ValueError(f"Agentao(enabled_tools=): unknown tool name(s) {unknown}")

    # extra 恒保留（§4）——临时算，不存实例字段
    extra_names = {tool.name for tool in agent._extra_tools}

    # 终筛：只移除 built-in / agent-path 中不在白名单的名字
    for name in list(agent.tools.tools):
        if name.startswith("mcp_"):                 # §4 不在范围
            continue
        if name in ToolRegistry._PLAN_ONLY_TOOLS:   # §4 恒保留
            continue
        if name in extra_names:                     # §4 extra 恒保留
            continue
        if name not in allow:
            agent.tools.unregister(name)
            _logger.info("enabled_tools: pruned '%s' (not in allowlist)", name)
```

- `extra_names` 在 `apply_enabled_tools()` 里临时算（`agent._extra_tools` 此刻已存在），**不新增实例字段**。
- **可观测性**：剔除是显式意图，不 warning；但落 INFO 审计行，便于 host 排查「我的 agent 怎么没了」。

**改动面：** `Agentao.__init__`（收 `enabled_tools` + §5a 校验）、新增 `apply_enabled_tools` 并在 `register_extra_tools` 之后、`ToolRunner` 之前调用。不碰 `disable_tools` / `extra_tools` / MCP / plan 的既有路径，不新增任何常量/profile/实例字段。

## 7. 用法

```python
from agentao import Agentao

# 1) 最小核心：手写名字集（v1 不导出 CORE_TOOL_NAMES，见 §8）
CORE = {"read_file", "write_file", "replace",
        "list_directory", "glob", "search_file_content", "run_shell_command"}
agent = Agentao(working_directory=wd, enabled_tools=CORE)

# 2) 核心 + 自研检索：extra 恒保留，无需把名字再写进 enabled_tools（§4）
agent = Agentao(
    working_directory=wd,
    extra_tools=[MyRetrievalTool()],
    enabled_tools=CORE,            # MyRetrievalTool 仍在 —— 因为它是 extra
)

# 3) 极小：移除全部 built-in + agent（extra / MCP / plan 仍在）
agent = Agentao(working_directory=wd, enabled_tools=set())

# 4) 非法：互斥
Agentao(working_directory=wd,
        enabled_tools={"read_file"}, disable_tools={"web_search"})   # ValueError（§5a-1）
```

## 8. demand-gated 后续（v1 不做，附触发条件）

| 项 | 触发条件 |
|---|---|
| **导出 `CORE_TOOL_NAMES`** | 出现**第二个** host 也要同一核心集时再导出——届时一并解决「core 含不含 `ask_user`/`list_directory`」的边界争议，并配 pin 测试防漂移 |
| **profile 档位** `tool_profile=` | 多个 host 反复要**同一组**子集时；且做成 host 可扩展 dict，而非固定字符串（否则把 `BUILTIN_TOOL_NAMES` 刻意拒绝的工具分类请回来） |
| **MCP 纳入白名单** | 出现「要在 agentao 层而非 MCP 层最小化 `mcp_*`」的真实需求时——需另想 `mcp_` 前缀的匹配/通配语义 |
| **extra 也受白名单约束**（「单一最终集」语义） | 出现「可复用 extra 列表 + 每实例选子集」的真实需求时；届时再定冲突是 raise 还是 drop |
| **按轴开关** `enable_agent_tools=False` | 「全留 agent、只筛内置」（§4 全有或全无的反面）成为真实痛点时——今天只有管内置 agent 的 `enable_builtin_agents`，够不到 project/plugin agent |

## 9. 速查表

| 维度 | `enabled_tools`（本设计） | `disable_tools`（已有） | `extra_tools`（已有） |
|---|---|---|---|
| 方向 | 加法白名单 | 减法黑名单 | 逐个加法（实例） |
| 形态 | 纯数据（名字集） | 纯数据（名字集） | 代码（实例） |
| 启用判定 | `is not None`（含空集） | 非空 | 非空 |
| 缺省行为 | 全量（现状） | 不跳过 | 无 extra |
| 作用域 | **仅 built-in + agent-path**；不管 extra / MCP / plan-only | 仅内置 | 内置+agent 之后的最后 pass |
| 新增内置静默进入 | **不会**（白名单未列即排除） | 会（黑名单天然漏） | 不适用 |
| 与对方共用 | 与 `disable_tools` **互斥**（§5a-1） | 与 `enabled_tools` 互斥 | 不受 `enabled_tools` 影响（恒保留） |
| settings.json | v1 否 | v1 否 | 否（无法序列化） |
| 校验 | 构造期：互斥 + 拒保留名；apply 期：未知名 fail-fast | 构造期：未知内置名 | 构造期：重名 + 拒 `mcp_` |
