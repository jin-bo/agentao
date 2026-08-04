# MCP 工具列表分页 — 设计

**状态：** **已于 2026-08-03 实现。** 修复了一个现存的静默截断缺陷：agentao 对每个
MCP 服务器只发一次 `tools/list`，且从不读取响应里的分页游标，于是第一页之后的所有
工具对模型都不可见 —— 没有报错，没有告警。借鉴来源是 codex 的
`2026-07-24..2026-08-03` 窗口，它为这个循环交付了**边界**（#36039、#35724）——
边界和循环是一起拿的，而不是事后再补。

落地于 `agentao/mcp/client.py`（`_list_all_tools`、`_MAX_TOOL_PAGES` /
`_MAX_TOOLS` / `_MAX_CURSOR_BYTES`），配套 `tests/test_mcp_tool_list_pagination.py`
以及 `tests/support/mcp.py` 中共享的 `tools_result` / `paging_session` 假件。三处
声明为 `list_tools(self)` 的旧假件已更新为真实 SDK 的关键字专用 `params`，并改为返回
真实 `ListToolsResult` 而非 `MagicMock` / `SimpleNamespace`。已在**两个 SDK 大版本**
上验证通过 —— 2.0.0 锁定版与 1.26.0 下限 —— 且下述三处正确性修正各自都用变异验证过：
把 bug 改回去，恰好只让为它写的那条测试失败，其它都不受影响。

**第 1 轮评审（2026-08-03）** 修正了初稿的三处正确性缺陷并收敛了未决决策：
`if cursor` → `cursor is not None`（§5.2）、条目上限移到 `tools.extend` 之前
（§5.2/§5.3）、游标上限改为按 UTF-8 字节计（§5.3）、§6.2 的权限表述在被证伪后重写，
以及 D1 在把隔离归因追溯到 `connect()` 自身的 handler（而非 `_connect_one`）之后，
收敛为单一的「让该服务器连接失败」规则（§5.4）。

**第 2 轮评审（2026-08-03）** 修正了四处准确性缺陷，全部在文字而非设计上：空串游标
造成的是一次浪费的请求加一个**错误结论**，不是死循环（§5.2 —— 重复游标守卫会在第二轮
抓到它）；条目上限是**目录累加**边界，不是 DoS / 线路边界，因为 SDK 早已解析完响应
（§5.3、§8）；单页溢出测试**无法**钉住「extend 前检查」，也不应假装能（§7）；以及
`permissions.json` **确实**覆盖 MCP 工具，因此非边界的只有 `enabled_tools` 与名称
前缀（§6.2）。§5.2 的文字也已对着 §5.3 的表格做了压缩。

**第 3 轮评审（2026-08-03，xhigh 多智能体）** 修复了已落地代码与测试中的五处问题，
并记录了三处不修的：各边界失败改抛 `McpCatalogError`，并排除出 `connect()` 的
「试试 `type: sse`」提示（该提示只在传输层被证明可用**之后**才会被触及）；去掉了多余的
`list(result.tools or [])` 拷贝（`tools` 在每个大版本上都是必填非空）；`paging_session`
由按调用计数改为按游标索引，假件不再可能对错误的游标交回第 2 页；`connect_all` 测试
现在关闭自己的事件循环，也不再声称钉住它根本无法失败的 `gather` 级隔离；以及一条
**非 `@modern_only` 的真实线路测试**现在通过真正的 `ClientSession` 钉住 `params=`
契约 —— 这正是 1.x CI 单元此前从未覆盖到的地方。第 2 轮对 CLAUDE.md 的权限重写本身也
被更正：read-only 模式预设在引擎**之前**短路。三处有意不修的回归画像见 §9。

英文对照件：[mcp-tool-list-pagination.md](mcp-tool-list-pagination.md)。

已对照 `main`@`6383d23`（2026-08-03）核验：`agentao/mcp/client.py:290-292`
（单次调用的握手）、`:310-347`（`connect()` 的兜底 catch —— 置 `ERROR`，且**不**
重抛）、`:349-356`（`_handshake`）、`:912-920`（`_connect_one` / `gather`，本路径上
不会触发的第二道保险）、`:958-972`（`get_server_status`）、
`agentao/mcp/_compat.py:37-60`（`field`）、`agentao/mcp/config.py:74`
（`_DEFAULT_STARTUP_TIMEOUT = 60.0`）、`:125-163`（`resolve_timeouts`）、
`agentao/mcp/tool.py:20-22`（`make_mcp_tool_name`）、`:111-117`
（`requires_confirmation` —— 可信服务器跳过确认）、
`agentao/tooling/registry.py:170-178`（`apply_enabled_tools` 不动 `mcp_*`）、
`agentao/tooling/mcp_tools.py:115-118`（连接错误处理）。

**读者：** Agentao 维护者。

**相关：**
- `mcp-streamable-http.zh.md` —— 本设计所依附的传输契约；§5.1/§5.3 带有 mcp-2.0
  更新块，下文 §4 遵循的正是那里的兼容纪律。
- `project_mcp_protocol_negotiation`（PR #158）—— `_negotiate` 就在本设计所包裹的
  `list_tools` 调用之前运行。
- `project_mcp_sdk_2x_compat`（PR #148）—— `_compat.py`，以及那条规则：每一处跨大版本
  差异都要**从已安装的 SDK 探测**，绝不从版本字符串嗅探。
- `docs/reference/configuration.md` —— MCP `timeout` schema（`startup` / `request`），
  §5.4 原样复用的预算。

**方法：** 下文每条论断都锚定到 `main`@`6383d23` 的源码。SDK 表面是**从三个真实安装的
大版本探测得来** —— 1.26.0（声明的下限）、1.29.0（最新 1.x）、2.0.0（锁定版）——
不是抽象地读签名，也不是从版本字符串推断。这三个探测单元正是
`.github/workflows/ci.yml:148` 的三个 CI 单元。

---

## 1. 缺口

`McpClient` 用一次往返发现服务器的工具：

```python
# agentao/mcp/client.py:349-356
async def _handshake(self):
    """Settle the protocol era, then run the ``list_tools()`` round-trip."""
    await self._negotiate()
    return await self._session.list_tools()

# agentao/mcp/client.py:290-292
self._tools = (
    await asyncio.wait_for(self._handshake(), timeout=startup_timeout)
).tools
```

`.tools` 是第一页。那个表示"还有更多"的游标从未被读取：

```
$ grep -rn --include='*.py' -E 'next_cursor|nextCursor|PaginatedRequestParams' agentao/ tests/
（零匹配）
```

**全仓库零匹配，测试也算在内。** 一个对目录分页的服务器，第 1 页之后的工具全部丢失。

## 2. 为什么这是最糟的那类 bug

没有任何错误路径。SDK 返回一个格式完好的 `ListToolsResult`，agentao 取走 `.tools`，
连接成功。被截断的目录随后正常注册（`tooling/mcp_tools.py:120`），缺失的工具在模型
看来根本不存在，而任何地方 —— 日志行、`/mcp list`、`get_server_status()` —— 都无法
区分"这个服务器有 12 个工具"和"这个服务器的 400 个工具里有 12 个"。用户的反馈会是
*"模型不肯用我的工具"*，而所有显而易见的排查位置都是干净的。

这一点值得直说，因为它改变优先级：代价不在于丢失的工具，而在于**故障与正常运行不可
区分**。

## 3. 借鉴来源

codex 是从相反的一端抵达同一段代码的 —— 它本来就分页，加固的是一个无界循环。评审
窗口内的两个提交：

| 提交 | 加了什么 |
|---|---|
| `be2e4afcd7`（#35724） | `collect_paginated` —— 共享游标循环，拒绝重复游标 |
| `3e3ae08839`（#36039） | 各项上限：100 页、1,024 条目、64 KiB 游标、整体超时 |

收集器约 35 行（`codex-rs/codex-mcp/src/pagination.rs`）。其结构可直接移植；**取值**
则需要针对 agentao 的论证，见 §5.3。

把边界和循环放在同一次改动里，正是这里选择"借鉴"而非"从零写循环"的全部意义。对着
一个不可信的对端写无界 `while cursor:` 就是一次挂起，而重复游标则是永久挂起。

## 4. 跨大版本 SDK 探测 —— 承重约束

agentao 支持 `mcp>=1.26.0,<3`，CI 跑三个单元（`ci.yml:148`：`mcp==1.26.0`、
`mcp>=1,<2`、`mcp>=2,<3`）。分页同时触及一个**方法签名**和一个**线上字段名**，两者
在大版本分界处行为不同。以下是探测结果，非回忆：

```
mcp 1.26.0  list_tools(self, cursor: str|None = None, *, params: PaginatedRequestParams|None = None)
            ListToolsResult 字段:        ['meta', 'nextCursor', 'tools']
            PaginatedRequestParams 字段: ['task', 'meta', 'cursor']

mcp 1.29.0  list_tools(self, cursor: str|None = None, *, params: PaginatedRequestParams|None = None)
            ListToolsResult 字段:        ['meta', 'nextCursor', 'tools']
            PaginatedRequestParams 字段: ['task', 'meta', 'cursor']

mcp 2.0.0   list_tools(self, *, params: PaginatedRequestParams|None = None)
            ListToolsResult 字段:        ['meta', 'ttl_ms', 'cache_scope', 'next_cursor', 'tools', 'result_type']
            PaginatedRequestParams 字段: ['meta', 'cursor']
```

两个发现，方向相反：

**(a) 调用本身不需要新的兼容垫片。** 位置参数 `cursor=` 只存在于 1.x —— 在 2.0.0 中
已**消失** —— 但 `params=PaginatedRequestParams(cursor=…)` 在**三个单元上全部存在
且接受关键字传参**，包括 1.26.0 下限。所以 `params=` 是唯一处处可用的写法。若下限
恰好没有 `params`，本设计就需要一个 `_compat.py` 探测；实际不需要。注意这是关于
**下限**的事实，因而由那个 CI 单元钉住 —— 若将来下调下限，先重新探测再假定它仍成立。

**(b) 字段读取确实需要现有垫片。** `nextCursor`（1.x）→ `next_cursor`（2.x）正是
`_compat.field` 存在的那个 camelCase→snake_case 改名，也正是该 helper 被写来处理对的
那个场景：`ListToolsResult` 在两个大版本上都是 `extra='allow'`，所以在 1.x 上
`hasattr`/`getattr` 探测会优先解析到**服务器提供的 `next_cursor` extra**，而不是
SDK 校验过的 `nextCursor` 字段。请使用 `field(result, "nextCursor", "next_cursor")`，
不要用别的写法。

## 5. 设计

### 5.1 循环放在哪

放进 `_handshake`（`client.py:349-356`），替换那次单独调用。这个位置零成本，因为
`connect` 已经在 `:290-292` 用 `asyncio.wait_for(..., timeout=startup_timeout)` 包裹了
整个 `_handshake`。codex 必须显式补上的整体循环超时（#36039："用配置的工具超时限定
整个分页操作，无配置时兜底 30 秒"）**agentao 白得** —— 每一页都落在现有的 60 秒
`startup` 预算内（`config.py:74`），并可通过 `timeout: {"startup": N}` 由用户配置。

`:281-288` 那段解释 `wait_for` 为何在此安全的注释 —— 对已建立会话的普通 await，不进入
任何 exit-stack 上下文，因此取消不会跨过 anyio 作用域进入传输层清理 —— 对 N 次往返
同样成立。那段论证没有一处是"每次调用"限定的。

### 5.2 形态

```python
# agentao/mcp/client.py
_MAX_TOOL_PAGES = 100
_MAX_TOOLS = 1024
_MAX_CURSOR_BYTES = 64 * 1024


async def _handshake(self):
    await self._negotiate()
    return await self._list_all_tools()

async def _list_all_tools(self) -> list:
    tools: list = []
    seen_cursors: set[str] = set()
    cursor: Optional[str] = None

    for _ in range(_MAX_TOOL_PAGES):
        # 第一页传 params=None —— 与今天的调用逐字节一致。
        # 用 ``cursor is not None``，绝不用 ``if cursor``：见下。
        params = (
            PaginatedRequestParams(cursor=cursor) if cursor is not None else None
        )
        result = await self._session.list_tools(params=params)

        # 在 extend **之前**检查 —— 单页自身就可能超限。
        if len(tools) + len(result.tools) > _MAX_TOOLS:
            raise McpCatalogError(
                f"MCP server '{self.name}' exceeded the {_MAX_TOOLS}-tool "
                f"catalog limit"
            )
        tools.extend(result.tools)

        cursor = field(result, "nextCursor", "next_cursor")
        if cursor is None:
            return tools
        if len(cursor.encode("utf-8")) > _MAX_CURSOR_BYTES:
            raise McpCatalogError(
                f"MCP server '{self.name}' returned a tools/list pagination "
                f"cursor larger than {_MAX_CURSOR_BYTES} bytes"
            )
        if cursor in seen_cursors:
            raise McpCatalogError(
                f"MCP server '{self.name}' returned a repeated tools/list "
                f"pagination cursor"
            )
        seen_cursors.add(cursor)

    raise McpCatalogError(
        f"MCP server '{self.name}' exceeded {_MAX_TOOL_PAGES} pages of "
        f"tools/list"
    )
```

两处 §5.3 表格承载不了的选择：

1. **用 `cursor is not None`，不是 `if cursor`。** MCP 游标是不透明**字符串**，
   `""` 是合法取值 —— 它的含义是"这是你的续传令牌"，而非"没有下一页"。真值判断会把它
   改写成 `params=None`，从而**重新请求第一页**。危害不是挂死：`""` 在第一轮末尾已经
   进过 `seen_cursors`，所以重复游标守卫会在第二轮触发。结果是浪费一次请求，随后得出
   一个**错误结论** —— 一个完全合规的服务器被判定为"返回了重复游标"，并按 §5.4 被整个
   丢弃。§7 用 `nextCursor=""` 的测试把它钉住。
2. **用有界 `for`，不用 `while True`。** 循环走完时手里还攥着游标，本身就是页数上限
   的失败条件，因而不需要另外维护一个计数器。

另外两处顺序细节 —— 条目上限把住 `tools.extend`、游标上限按 UTF-8 字节计 —— 记在
§5.3 的表格里。

第一次迭代传 `params=None`，即 SDK 默认值 —— 所以对绝大多数单页服务器，线上流量是
**不变**，而不仅仅是"等价"。正是这个性质使得本改动无需灰度即可安全落地，§7 用一个
测试把它钉住。

`_handshake` 返回 `list` 而非 `ListToolsResult`，同时消除了 `:292` 处的 `.tools`
解包；那次解包是唯一的消费者。

### 5.3 边界

| 边界 | 取值 | 执行位置 | 理由 |
|---|---|---|---|
| 重复游标 | 拒绝 | 读到下一个游标之后 | 服务器重复游标就是**死循环**。不可协商；正是这条边界让这个循环值得写。 |
| 游标大小 | 64 KiB，**UTF-8 字节** | 读到下一个游标之后 | 原样采用 codex 取值。游标是不透明的续传令牌；64 KiB 已属荒谬，且判断只是一次比较。按字节计 —— 对多字节游标用 `len()` 会放行至多 4 倍预算。 |
| 页数 | 100 | 循环上界；走完仍有游标即失败 | codex 取值。有条目上限后它属于双保险，但能限住一次只返回一个工具的服务器。 |
| 条目数 | 1,024 | **`tools.extend` 之前** | codex 取值 —— 但见下面的告诫。限的是**累加后的目录**：把住合并动作，可以让超大页根本不进累加器，而不是先完整拷进去再来反对。 |
| 整个循环 | 现有 `startup_timeout`（默认 60s） | `:290-292` 的 `asyncio.wait_for` | 已存在；见 §5.1。 |

**条目上限管不到的东西：线路本身。** 等 `_list_all_tools` 看到 `result.tools` 时，
SDK 早已把响应从传输层读完、解析完并构造成模型。所以单页携带十万个工具的响应体与
反序列化开销，在这里任何上限开口之前就已经发生了。这条边界是**目录累加 / 注册**限制，
不是传输层限制 —— 传输层限制为何不在本设计范围内，见 §8。

**关于条目上限的告诫 —— 它在 agentao 的含义和在 codex 不同。** codex 之所以能承受
1,024 个工具，是因为它有 `tool_search` 延迟加载，大目录不会进入模型上下文。agentao
没有这种机制（`docs/design/tool-search.md` 状态为*草案、已推迟*）；每个注册的 MCP
工具都会进入每次请求的函数列表。因此 agentao 的**实际**天花板远低于 1,024，一个逼近
此上限的服务器早在触发它之前就已摧毁上下文窗口。

诚实的表述是：**1,024 是目录累加边界 —— 既不是上下文边界，也不是线路边界。**
修复 agentao 缺少工具数量治理不是本设计的职责，本改动也不会让那个问题变糟 ——
它第一次为一个既有的无界面收口
（今天一个服务器可以在**第 1 页**返回 5,000 个工具，agentao 会照单全收）。上下文治理
是另一项工作，不要让这个上限冒充它。

### 5.4 边界触发时怎么办 —— **单一规则：让该服务器失败**

每条边界都抛异常。重复游标、超长游标、条目上限、页数上限 —— 四者都让**这一个**服务器
连接失败。没有残缺目录，没有截断模式。

**隔离到底来自哪里。** `connect()` 在 `client.py:310-347` 用 `except Exception` 兜住
整个函数体：置 `status = ServerStatus.ERROR`、记录 `error_message`、拆掉 session 与
exit stack —— 且**不重抛**（该块内没有任何 `raise`）。所以 `_list_all_tools` 抛出的
异常根本逃不出 `connect()`。`:912-920` 处 `_connect_one` 自己的 `except` 与
`gather(return_exceptions=True)` 是第二道保险，在本路径上完全不会触发。本文档的早期
修订把隔离归因于 `_connect_one`，那是错的；而这处更正**强化**而非削弱了单一规则策略：

- 失败已经通过既有通道被暴露。`get_server_status()`（`client.py:958-972`）为每个
  服务器上报 `status` 与 `error`，因此一次越界会显示为 `ERROR` 加一条点名该上限的
  消息。**不需要 `"truncated"` 字段** —— 状态通道已经说清了截断标志想说的一切，而且
  说法与其它所有连接失败保持同一形状。
- 其它服务器不受影响，因为每个 `McpClient.connect()` 都自理。

相对于「抛异常/截断」二分策略，这**删掉了**：`_truncated` 生命周期状态、
`get_server_status()` 扩展、截断告警分支，以及「保留多少条」「是否保留部分消费的溢出
页」这些额外语义。而且残缺目录正是 §2 意在消除的那种失败形态 —— 把它的安静版本保留为
一种受支持的结果，会架空整个改动。

代价是真实的，值得说清：一个格式完好、拥有 1,025 个工具的服务器会丢掉全部而非丢掉
其中 1,024 个。在这个上限取值下这是对的取舍，因为 §5.3 的告诫在此适用 —— 这么大的
目录在 agentao 的扁平工具列表里本就不可用，所以有用的信号是"这个服务器放不下"，而不是
它被悄悄裁剪过的前缀。

## 6. 行为变化

1. **单页服务器：线上流量完全一致（§5.2）—— 但行为**并非**无条件一致。** 本行的早期
   修订写的是"无变化"，那是错的：`_MAX_TOOLS` 在第 1 页同样会检查，所以一台**在单次
   无游标响应里返回超过 1,024 个工具**的服务器，现在连接失败，而此前它的工具会被全部
   注册。这类服务器根本不会进入本设计新增的循环，是本改动唯一「纯回归而非修复」的画像。

   刻意保留，因为另一种做法 —— 让第 1 页豁免该上限 —— 会让最大的单响应目录仍然无界，
   而那正是 §5.3 所说本设计第一次堵上的既有窟窿。但这是一个真实取舍，在 fail-closed
   规则被批准时它并不可见，并且一旦将来把上限做成可配置，它是第一个要重估的点
   （见 §9）。
2. **多页服务器：更多工具出现，且其中一些可能直接可调用。** 这正是所修之处，但它扩大
   了已注册工具面。各道闸门究竟哪些管用：

   - **`permissions.json` 是真正的边界，且确实覆盖 MCP 工具。**
     `PermissionEngine.decide_detail(tool_name, tool_args)` 对每次通过了 mode 预设的
     工具调用都会求值，且发生在任何 `requires_confirmation` 兜底**之前**
     （`agentao/runtime/tool_planning.py:391-399`），规则按工具名匹配并支持 `*` 通配
     （`agentao/permissions.py:447-455`、`:220`）。运维方因此可以拒绝或强制询问
     `mcp_*`——或某个服务器的前缀——该规则治理第 2 页工具的方式与治理第 1 页完全一致。
     （优先级注记：read-only 模式预设在 `tool_planning.py:381-389` 处**先于**引擎
     短路为 `DENY`，所以 permissions.json 的 `allow` 无法在只读模式下重新放开一个
     工具。这与下面那条「放宽方向」的风险无关，但不要从本条读出"引擎决定一切"。）
   - **`enabled_tools` 不是边界。** 这是设计如此 —— `apply_enabled_tools`
     "removes every built-in / agent-path tool whose name is absent from the
     allowlist, leaving `extra_tools`, MCP (`mcp_*`), and plan-only tools
     untouched"（`agentao/tooling/registry.py:170-178`）。
   - **`mcp_` 前缀也不是边界。** 它保证 MCP 工具无法**遮蔽**内建工具
     （`tool.py:20-22`）；它本身不授予任何策略 —— 它的唯一用处是给权限规则当匹配对象。

   **残余风险，精确表述：** 当**没有任何权限规则命中**时，决策落回工具自身的
   `requires_confirmation`，而 `McpTool` 对 `trust: true` 的服务器返回 `False`，除非
   该服务器标了 `destructiveHint`（`agentao/mcp/tool.py:111-117`）。所以在一台可信且
   无规则命中的服务器上，第 2 页及之后到达的工具可能无需提示即可调用，而在本改动之前
   它们根本不可达。

   这是正确的结果 —— 运维方信任了该服务器，而截断的目录从来不是预期的安全姿态 ——
   但值得说出来而不是一笔带过。**本设计不新增任何策略**；想要收口的运维方已经有
   `permissions.json`。`enabled_tools` **是否应当**覆盖 MCP 工具，是一个独立且既有的
   问题 —— 见 `docs/design/host-tool-allowlist.md`。
3. **60 秒 startup 预算现在覆盖 N 次往返。** 一个缓慢的分页服务器可能新触发那个单次
   调用本来放得下的超时。这是正确行为，但 `:297-301` 的超时消息目前写的是
   `"initialize / server-discover / list_tools handshake"`，应当说明分页也包含在内 ——
   它上方的注释讲的恰恰就是"别把用户指向一个已经完成的步骤"。

## 7. 测试

依据 `CLAUDE.md` 与 `project_mcp_sdk_2x_compat`：**用真实的 `mcp.types` 模型构造
输入。** `SimpleNamespace`/`MagicMock` 假件曾把 1.x→2.x 的四处破坏全部藏在绿色测试
套件后面；而 `MagicMock` 在这里更是有害 —— 它对任何名字都回答 `hasattr`，因而会在
两个大版本上都满足 `field()` 的兜底分支，对哪一个都证明不了。

- **常见路径无回归** —— 单页且游标为空时，恰好发出**一次**调用且 `params=None`。
  钉住 §6.1。
- **多页收集** —— 真实的 `ListToolsResult` 页；断言每一页的每个工具都按序注册。
- **空串游标** —— `nextCursor=""` 必须产生**第二次**请求，携带
  `params=PaginatedRequestParams(cursor="")`，而不是重发第一页。没有这条测试，§5.2 的
  `if cursor` bug 是不可见的：一套只用非空游标的测试怎么写都能过。
- **重复游标** —— 抛异常。
- **超长游标** —— 抛异常。游标要用**多字节**字符构造，长度刚好能过 `len()` 检查但过不了
  UTF-8 字节检查，这样测试才能区分两者。
- **单页条目溢出** —— 单页携带超过 `_MAX_TOOLS` 时抛异常。只断言抛异常，别的不要断：
  这条测试**无法**区分「extend 前检查」与「extend 后检查」。`tools` 是局部累加器，而
  `self._tools` 只在成功时才被赋值（`:290-292`），所以"没有工具被注册"两种实现都成立，
  断言它等于写了一条「因为无关原因而通过」的测试。这个顺序是代码评审不变式，不是可测
  不变式 —— 不要为追它而增加机制。
- **页数上限** —— 100 页每页都带游标时抛异常。
- **失败隔离** —— 上述任一情形下，该服务器落到 `status == ERROR` 且 `error_message`
  点名该上限（经由 `:310-347` 处 `connect()` 自己的 handler），而同一次 `connect_all`
  中的兄弟服务器仍然到达 `CONNECTED`。断言状态，不要断言被传播的异常 —— `connect()`
  不重抛。
- **跨大版本字段读取** —— `nextCursor`/`next_cursor` 的读取必须在两个大版本上都被执行
  到。CI 的三个单元（`ci.yml:148`）免费做到这点，**前提是**测试构造真实的
  `ListToolsResult`，而不是对着手工设置的属性做断言。

## 8. 不在范围内

- **工具描述加界。** codex #35941 把面向模型的 MCP 描述限制到 1,000 字节；
  `agentao/mcp/tool.py:69` 无界透传。这是真实问题，但属于另一个上下文预算议题，需要
  另一个决定。
- **其它可分页的 MCP 面。** 不存在：
  `grep -rn 'list_resources|list_prompts|list_resource_templates' agentao/`
  零结果。agentao 只发现工具，因此 `tools/list` 就是全部可分页面 —— codex 在
  `resources/list` 与 `resources/templates/list` 上的并行工作在这里没有对应目标。
- **工具数量 / 上下文治理。** 见 §5.3 告诫。
- **传输层响应体大小限制。** codex 对 JSON、SSE、stdio 消息统一施加 8 MiB 上限
  （#35725）。agentao 无法低成本对齐：MCP SDK 拥有传输层，交给 `_list_all_tools` 的
  已经是解析好的模型，agentao 侧在"线路"与"对象"之间没有接缝。这正是 §5.3 的条目上限
  只能是目录边界而非线路边界的原因。要做真正的线路边界得从 SDK 走，那是另一件工作。

## 9. 已知回归画像 —— 记录在案，未做修复

第 3 轮 xhigh 评审发现三类服务器画像：本改动之前能连上，之后直接失败。三者都是
fail-closed 规则（§5.4）按批准方式在工作，因此这里都不当作缺陷处理 —— 但该规则获批
时这些画像尚未被列举出来，它们正是这条规则的具体代价。记录下来，使这个取舍可以被
重新决定，而不是被重新发现。

| 画像 | 结果 | 注 |
|---|---|---|
| 单个无游标页含 **> 1,024 个工具** | `ERROR`、0 工具 | 根本不进入循环；纯回归。见 §6.1。 |
| 合规服务器以 **≤ 10 工具/页**分页且总量超过 100 页 | `ERROR`、0 工具 | 120 个工具按 1/页会在条目上限的约 12% 处触发 `_MAX_TOOL_PAGES`。§5.3 称页数上限是「与条目上限互为双保险」，这只在每页 > 10 时成立。 |
| 服务器把 `"nextCursor": ""` 当**零值**发出（无 `omitempty`） | `ERROR`、0 工具 | 按规范 `""` 是游标，所以 §5.2 会把它发出去；而以此表示"没有了"的服务器会再次返回第 1 页，从而触发重复游标守卫。此处 agentao 与 codex 的参考行为一致。 |

**三者显而易见的缓解手段是同一个：** 让这四条边界可按服务器配置，与
`.agentao/mcp.json` 中既有的 `timeout: {startup, request}` 并列。这里刻意**不做** ——
它是新的配置面和新的兼容契约，而且尚无用户命中过其中任何一条。动工的触发条件是上表
任一行的第一份真实报告。

同一轮评审的两点较小结论，同样是有意不修：

- **60 秒 `startup` 预算现在跨越至多 100 次往返**，因此高延迟的分页服务器可能失败于
  一次此前放得下的连接。这就是 §6.3，已经写明，且该预算本就可由用户配置 —— 真被咬到
  时的修法是 `timeout: {"startup": N}`，不是改代码。
- **第 2 页及之后的瞬时错误会中止整个连接**，丢弃已取到的页。加入分页中途重试属于新
  设计，而 MCP 连接从来不重试；不在范围内。
