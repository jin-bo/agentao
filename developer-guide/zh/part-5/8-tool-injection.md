# 5.8 宿主工具注入

> **本节你将学到**
> - 决定工具面的三个构造参数：`extra_tools`、`disable_tools`、`enabled_tools`
> - 在两轮之间动态增删工具面的两个运行时方法：`add_tool` / `remove_tool`
> - 哪些组合合法、每个机制**不是**什么（都不是安全边界），以及完整可跑示例在哪

[5.1](./1-custom-tools) 讲的是如何**写**一个 `Tool`。本节讲**宿主**如何把工具注入嵌入式 `Agentao`、又如何移除——作为 `agentao.host` 契约的稳定一部分，而不是去戳运行时内部。

分两个平面：

| 平面 | API | 对模型可见时机 |
|---|---|---|
| **构造期** | `Agentao(extra_tools=, disable_tools=, enabled_tools=)` | 从首次 `chat()` 起 |
| **运行期** | `agent.add_tool(...)` / `agent.remove_tool(name)` | 在**下一次** `chat()` / `arun()`（schema 每轮快照一次，绝不在一轮中途变化） |

所有注入的工具都走与内置工具**相同的能力绑定**（`working_directory` / `filesystem` / `shell`），因此自动继承会话的 cwd 隔离与宿主的 FS/shell 重定向——绝不会变成「裸」工具。直接去戳 `agent.tools.register(...)` 会绕过这层绑定和下文的校验，请优先用契约 API。

## 构造期注入

### `extra_tools` —— 增加或替换（代码）

一组已构造好的 `Tool` / `AsyncToolBase` 实例。它们**最后**注册（在内置工具与 agent 工具之后），因此同名条目会静默**替换**内置或 agent 工具。这也是*配置*内置工具的方式：传入一个预构造实例，而不是依赖环境变量。

```python
from agentao import Agentao

# 用自研后端替换内置 web_search —— 同名即替换。
agent = Agentao(
    working_directory=wd,
    extra_tools=[WebSearchTool(backend="bocha", api_key=key)],
)
```

- 名字必须唯一，且**不得**带 `mcp_` 前缀（该命名空间保留——替换 MCP 工具走 `mcp_manager=` / `extra_mcp_servers=`，不走这里）。
- `extra_tools` **绝不**从 JSON 加载——实现无法序列化。

### `disable_tools` —— 隐藏内置工具（数据）

一组要在注册时跳过的内置工具**名字**。纯数据，便于序列化（不过 v1 还没有读它的 settings.json 加载器）。

```python
# 只读部署：去掉 shell 与默认的 web 工具。
agent = Agentao(
    working_directory=wd,
    disable_tools={"run_shell_command", "web_search", "web_fetch"},
)
```

- 每个名字**必须**是真实的内置工具，否则构造时 `ValueError`——拼写保护（`{"web_serach"}` 会响亮报错，而不是静默空操作）。
- 校验针对*静态可注册资格*，而非运行时可用性：即使没装 `[web]` extra，`disable_tools={"web_search"}` 也是合法的空操作。
- 它只跳过**内置工具**注册，不影响 `extra_tools`、MCP 或 agent 工具。

### `enabled_tools` —— 白名单（数据）

`disable_tools` 的加法对偶：不再列出要去掉哪些，而是列出要保留的**唯一一批** agentao 自有工具。

```python
# 最小编码工具面 —— 其余 agentao 自有工具全部裁掉。
agent = Agentao(
    working_directory=wd,
    enabled_tools={"read_file", "write_file", "edit_file", "search_text", "run_shell_command"},
)
```

语义取决于 `None` 与非 `None`——**不是**取决于是否为空：

| `enabled_tools` 取值 | 效果 |
|---|---|
| `None`（默认） | 维持现状——无白名单，所有合格工具都注册 |
| 任意可迭代对象，**含 `set()`** | 白名单*开启*——裁掉每一个未列出的 agentao 自有工具 |

所以 `enabled_tools=set()` 表示「开启白名单，但不允许任何 agentao 自有工具」——这是把 agent 收缩到仅剩你的 `extra_tools` + MCP 的一种刻意且合法的写法。

**作用域 = 仅 agentao 自有工具。** 裁剪只动内置工具与 agent 路径工具。它**始终保留**：
- 你注入的 `extra_tools`（你已经显式选过了），
- MCP 工具（`mcp_*`——由 MCP 生命周期管理），
- 仅 plan 工具（`plan_save` / `plan_finalize`——绑定到 plan 状态机）。

**与 `disable_tools` 互斥**——同时传两者会 `ValueError`（白名单和黑名单并存语义有歧义）。即使是 `enabled_tools=set()` + 非空 `disable_tools` 也照样报错。

校验分两段：**构造期**检查互斥冲突并拒绝保留名（`mcp_` 前缀、仅 plan 工具）；**应用期**检查（在 `extra_tools` 注册之后）拿*活的*注册表跑拼写保护——像 `read_fil` 这样的未知名字在这里才报错，因为 agent 路径工具名直到构造完成后才已知。

## 运行期注入

在 `chat()` / `arun()` 两次调用之间，宿主可以改动工具面——构造期注入的对偶，面向无法重建的长生命周期会话（例如 ACP 会话在对话中途挂载连接器，或登录后才暴露某工具）。

```python
agent.add_tool(my_tool)                 # 增加；名字冲突 → ValueError
agent.add_tool(my_tool, replace=True)   # 刻意替换（静默，打 INFO 审计行）
removed = agent.remove_tool("web_fetch")  # 存在则 True，缺失则 False（不抛异常）
```

- `add_tool` 走与 `extra_tools` **相同**的校验 + 能力绑定。注意它比底层 `agent.tools.register` 更**严格**：名字冲突且 `replace=False` 时会抛异常，而不是 warn 后覆盖——刻意的宿主调用就应显式写 `replace=True`。
- 两者都拒绝**保留名**：`mcp_` 前缀（MCP 生命周期）与仅 plan 工具（`plan_save` / `plan_finalize`）。两端都封死，因此不存在 `add_tool(name="plan_save", replace=True)` 这种后门。
- **可见时机 = 下一次调用。** `to_openai_format()` 在每次 `chat()` 进入 LLM 迭代循环*之前*快照一次 schema，所以中途改动不会改变本轮模型所见——这是「单次调用内 schema 一致」的刻意不变量。不要在 `chat()` 进行中并发改动注册表。

## 这些机制都不是什么

::: warning 不是安全边界
`disable_tools` / `enabled_tools` / `remove_tool` 缩减的是**模型所见的 schema**——它们让 LLM 不去*尝试*不适用的能力。它们**不是**授权。安全与授权由 **[权限引擎](./4-permissions)** 负责；一个对本租户永远不许运行的工具，应落在权限规则里，而不是（仅）白名单里。
:::

- 它们都不碰 MCP：MCP 工具的增删走 MCP 生命周期（`mcp_manager=` / `extra_mcp_servers=`），绝不走这些参数或方法。
- 它们都不序列化实现：`extra_tools` 仅限代码；基于名字的参数虽是数据，但 v1 中仅限构造 API（无 settings.json 字段）。

## 完整示例：Jina 后端的 `web_fetch` / `web_search`

[`examples/tool-injection/`](https://github.com/jin-bo/agentao/tree/main/examples/tool-injection) 是两个平面的可跑、可离线测试 demo——它按名字把两个内置工具换成 [Jina](https://jina.ai) 后端：

| 注入面 | 时机 | 工具 | Jina 端点 |
|---|---|---|---|
| `Agentao(extra_tools=[...])` | 构造期 | `web_fetch` | `https://r.jina.ai/{url}`（Reader） |
| `agent.add_tool(...)` | 运行期 | `web_search` | `https://s.jina.ai/{query}`（Search） |

冒烟测试通过 `httpx.MockTransport` 驱动两个工具，断言用到了正确的端点 + `Authorization: Bearer <JINA_API_KEY>` 头，且**无任何网络调用**：

```bash
cd examples/tool-injection
uv sync --extra dev
PYTHONPATH=. uv run pytest tests/ -v
```

## 如何选择

| 你想要… | 用 |
|---|---|
| 增加自定义工具，或替换内置工具的实现 | `extra_tools=`（构造期）/ `add_tool()`（运行期） |
| 隐藏少数不适用的内置工具 | `disable_tools={...}` |
| 把工具面钉死在一个小白名单 | `enabled_tools={...}`（其余 agentao 自有工具全裁掉） |
| 收缩到仅剩自有工具 + MCP | `enabled_tools=set()` |
| 会话中途（两轮之间）改动工具面 | `add_tool()` / `remove_tool()` |
| 强制某工具永不*运行* | [权限引擎](./4-permissions)——不是这些 |

## TL;DR

- **构造期：** `extra_tools`（代码；增加/替换，最后注册）、`disable_tools`（数据；跳过内置）、`enabled_tools`（数据；白名单——`None`=关，任意可迭代含 `set()`=开）。
- **运行期：** 两轮之间用 `add_tool` / `remove_tool`；下一次调用可见；冲突语义比裸注册表更严格。
- **作用域：** `disable_tools` 只跳过内置工具；`enabled_tools` 裁剪内置 / agent 路径工具，并保留 `extra_tools`、MCP 与仅 plan 工具。运行期 `remove_tool()` 可以移除内置、extra 或 agent 工具，但不能移除 MCP 或仅 plan 工具。
- **能力绑定**通过契约 API 自动获得；直接戳 `agent.tools.register(...)` 会绕过它。
- **不是安全边界**——是 schema 缩减，不是授权。授权是[权限引擎](./4-permissions)的事。

→ 设计记录：[`host-tool-injection.md`](https://github.com/jin-bo/agentao/blob/main/docs/design/host-tool-injection.md) · [`host-tool-allowlist.md`](https://github.com/jin-bo/agentao/blob/main/docs/design/host-tool-allowlist.md) · [`runtime-tool-injection.md`](https://github.com/jin-bo/agentao/blob/main/docs/design/runtime-tool-injection.md)。契约面：[`docs/api/host.md`](https://github.com/jin-bo/agentao/blob/main/docs/api/host.md)。

→ 下一站：[第六部分 · 安全与生产化部署](/zh/part-6/)
