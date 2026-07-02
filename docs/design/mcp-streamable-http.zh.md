# MCP Streamable HTTP 客户端支持 — 设计

**状态：** 设计阶段 — 尚未实现。为 `McpClient` 增加 MCP **Streamable HTTP**
传输（`type: "http"`），并放开三处此前故意保留、专门拒绝 `http` 的 ACP 闸门
（它们一直等客户端能真正分发该传输）。按维护者 **2026-07-01 决策（D2）**，裸 `url`
现在**默认**走 Streamable HTTP（SSE 改为经 `type: "sse"` 显式选入）—— 这是一个
刻意的**破坏性变更**；见 §3 D2 与 §10。

**读者：** Agentao 维护者；DeepChat/TensorChat 的 ACP 集成负责人。

**对应英文：** `mcp-streamable-http.md`。

**相关：**
- `docs/reference/configuration.md` — MCP 配置 schema（`§ MCP` 传输表）。
- `project_mcp_connect_preflight`（PR #71）— 本设计原样复用的 content-type 预检；
  它当初就写成了与传输无关。
- `project_opencode_pull_review_20260629`（PR #119）— 两种 URL 传输都已共享的
  `resolve_timeouts`（startup/request 拆分）。
- `embedding-vs-acp.md` — ACP 是嵌入式内核之上的前端；传输分发位于 runtime，
  ACP 只做配置翻译。

**方法：** 下文每一条都锚定到 `main`@`8bcb1b1` 的源码。MCP SDK 的接口
（`sse_client`、`streamablehttp_client`）来自对已安装 `mcp` 包的**实测内省**，
非记忆推断。无凭直觉的映射。

---

## 1. 背景 — 当前如何选择传输

`McpClient`（`agentao/mcp/client.py`）只认两种传输，并且是**按结构**从配置中
出现了哪个键来推断的 —— 原生 `.agentao/mcp.json` 里**没有**显式的传输选择字段：

```python
# client.py:152-158
@property
def transport_type(self) -> str:
    if self.config.get("command"):
        return "stdio"
    if self.config.get("url"):
        return "sse"
    return "unknown"

# client.py:183-188  (connect() 内)
if self.config.get("command"):
    await self._connect_stdio()
elif self.config.get("url"):
    await self._connect_sse(startup_timeout, request_timeout)
else:
    raise ValueError("No transport configured ... (need 'command' or 'url')")
```

- `_connect_stdio`（232-255）→ `stdio_client`。
- `_connect_sse`（318-353）→ `sse_client`，跑 `_preflight_content_type`
  （257-316），把 `startup` 作为 HTTP 打开超时，并把 `sse_read_timeout` 抬高
  以容纳较大的单次请求预算。
- 超时由 `resolve_timeouts`（`config.py:87-124`）一次性解析，它**本就与传输无关**
  —— 其 docstring 虽写着 legacy int "交给 SSE 传输"，但语义（"约束 connect/startup
  阶段"）对 Streamable HTTP 原样成立。
- `call_tool`（355-441）按 `classify_mcp_error`（85-99）在 `SESSION_EXPIRED` /
  `TRANSPORT_DROPPED` 时重试一次。

**ACP 层已经在用显式 `type` 字段** —— 而且早已为本工作预留，在三处拒绝 `http`，
每处都有一句"等客户端学会 Streamable HTTP 再删"的注释：

| 闸门 | 位置 | 当前行为 |
|---|---|---|
| 能力声明 | `acp/initialize.py:75-78` | `mcpCapabilities = {"http": False, "sse": True}` |
| `session/new` 解析 | `acp/session_new.py:226-232` | `type` 必须是 `stdio`\|`sse`；`http` → `INVALID_PARAMS` |
| ACP→原生翻译器 | `acp/mcp_translate.py:189, 232-244` | `http` 条目被记日志并丢弃（防御性兜底） |

也就是说，配置词表（`type: "stdio" | "sse" | "http"`）在 ACP 侧**已经在树内确立**。
本设计把原生配置和客户端拉齐到同一词表，并把线接通。

## 2. 缺口与时机

MCP 规范已在 2025-03-26 版把 **HTTP+SSE 传输标记为弃用**，改用 **Streamable HTTP**。
新的远程 MCP 服务器都发 Streamable HTTP，很多**只**暴露它。Agentao 目前只有在这些
服务器同时保留 legacy SSE 端点时才够得着。客户端依赖已就位 ——
`mcp.client.streamable_http.streamablehttp_client` 就在钉住的 `mcp>=1.26.0` SDK 里
—— 所以这是接线工作，不是新增依赖。

## 3. 设计决策

### D1 — 传输选择器用显式 `type` 字段

原生 `.agentao/mcp.json` 每个 server 新增可选 `type`：`"stdio" | "sse" | "http"`。
接受 `"streamable-http"`、`"streamable_http"`、`"streamablehttp"` 作为大小写不敏感
的别名，归一化到 `"http"`。

**为何用 `type`：** 它既是 ACP 的线上词表（`acp/mcp_translate.py:189`），也是整个
生态的通用约定（Claude Code `.mcp.json`、VS Code、Cursor 都用 `type`/`transport`
来键远程传输）。复用它，让原生配置、ACP 边界、外部世界共用一套词表 —— 不必再对付
第三种方言。

### D2 — 省略 `type` 时默认 Streamable HTTP（裸 `url` → `http`）

**维护者决策（2026-07-01）。** 省略 `type` 时，`url` → **`http`（Streamable
HTTP）**；`command` → `stdio` 不变。SSE 保留，但改为经 `type: "sse"` **显式选入**。
裸 `{"url": "..."}` 现在表示 Streamable HTTP。

**为何：** MCP 规范已弃用 HTTP+SSE，改用 Streamable HTTP；新服务器 http 优先
（Cursor 已把裸 `url` 定为 Streamable HTTP）。默认走存活的传输，正是大多数新配置
所想要的，也让 agentao 跟随规范方向，而非钉死在一个已弃用的传输上。

**接受的代价 —— 这是破坏性变更。** 每一份指向 SSE 端点的现有裸 `url` 配置，现在都会
尝试 Streamable HTTP，并在握手时失败，直到加上 `"type": "sse"`。此决策归维护者
（产品方向决策 —— 参项目"痛点判断归用户"规则）。两项缓解让这个破坏**响亮、而非静默**：

1. **可操作的 connect 错误（§5.7）。** 当一个在*推断出的* http 默认上建立的连接
   （裸 `url`、无显式 `type`）握手失败时，错误追加：*"按 Streamable HTTP 尝试
   （裸 `url` 的默认）；若这是 legacy SSE 端点，请设 `\"type\": \"sse\"`。"*
   一词修复随失败一同抵达。
2. **发布说明迁移行（§10）。**

**已考虑并否决的替代方案 —— 自动回退**（先试 Streamable HTTP，遇 405/404 回退
SSE）。否决：它违背 agentao "显式优先于魔法"的姿态 —— 本设计复用的那个预检本身就是
**白名单**、刻意不做内容嗅探（`client.py:113-119`）。自动回退还会让 `transport_type`
不确定（破坏 `/mcp list` 状态与 `get_server_status`），在未命中路径上把 connect
延迟预算翻倍，并模糊错误来自哪种传输。Legacy SSE 用户写 `type: "sse"` 即可 —— 一个词。

### D3 — 一个传输解析器，且它**失败即关闭（fail closed）**

在 `config.py` 增加 `resolve_transport(config) -> str` 作为唯一真相源。
`McpClient.transport_type` 与 `connect()` 都调它 —— 那两处零散的 `command?/url?`
阶梯（152-158、183-188）坍缩成对其返回值的单点分发。契约：

1. **显式 `type` 存在** —— 归一化（转小写 + 别名折叠），然后：
   - 在 `{stdio, sse, http}` 内 → 返回它；
   - **其余一律抛 `McpTransportConfigError`**（`ValueError` 子类），错误信息可操作，
     列出可接受的取值。
2. **显式 `type` 缺失** —— 套用 D2 推断：`command` → `stdio`，`url` → `http`，
   两者皆无 → `"unknown"`（由 `connect()` 转成既有的"No transport configured"错误）。
3. **必需键校验（两分支都做）** —— 一旦选定具体传输，它所需的键必须存在：`stdio`
   需 `command`，`sse`/`http` 需 `url`。不匹配（`{"type":"stdio","url":...}`、
   `{"type":"http","command":...}`）**抛 `McpTransportConfigError`**，而非下游 `KeyError`。

**为何失败即关闭，不同于 `_coerce_timeout` 的告警-取默认（Finding 1）。**
坏的超时回退到一个*安全*默认 —— 只是数字略偏。坏的*传输*性质完全不同：在 D2 下，
像 `"type": "see"` 这样的拼写错误，若按告警-推断处理，会静默解析成 Streamable HTTP
（裸 `url` 默认）并连到**错误的协议** —— 正是整个设计要避免的那类静默错路由。ACP 侧
这里已经失败即关闭（`session_new.py:227-232` 用 `INVALID_PARAMS` 拒绝未知 `type`）；
原生配置必须与该姿态一致，不能拆它的台。

**保持状态路径为全函数（total）。** 若 `resolve_transport` 会抛，`transport_type`
属性（供 `/mcp list` + `get_server_status`，绝不能抛）就不安全了。故该属性**仅为
展示**吞掉错误 —— `try: return resolve_transport(...) except McpTransportConfigError:
return "unknown"` —— 而 `connect()` 让它传播进既有 `except`（212），把可操作信息记入
`error_message`。净效果：状态显示 `transport=unknown` + 错误列里的真实原因；connect
尝试失败即关闭且信息清楚；无任何静默错路由。

## 4. 配置面

```jsonc
{
  "mcpServers": {
    "remote-http":    { "type": "http", "url": "https://host/mcp",
                        "headers": { "Authorization": "Bearer $TOKEN" } },
    "remote-default": { "url": "https://host/mcp" },        // 无 type → Streamable HTTP (D2)
    "remote-sse":     { "type": "sse",  "url": "https://host/sse" },  // 选入 legacy SSE
    "local":          { "command": "npx", "args": ["-y", "server"] }
  }
}
```

解析表（`resolve_transport`）：

| 配置 | 结果 |
|---|---|
| `type:"stdio"` + `command` | stdio |
| `type:"sse"` + `url` | SSE |
| `type:"http"`（或别名）+ `url` | **Streamable HTTP（新）** |
| 无 `type`，有 `command` | stdio |
| 无 `type`，有 `url` | **Streamable HTTP**（D2 默认） |
| 无 `type`，也无 `command`/`url` | `"unknown"` → connect 抛 "No transport configured" |
| `type` 存在但非 stdio/sse/http | **抛 `McpTransportConfigError`**（失败即关闭，Finding 1） |
| 选定传输的必需键缺失（`type:"http"` 无 `url`、`type:"stdio"` 无 `command`） | **抛 `McpTransportConfigError`**（失败即关闭，Finding 3） |

`http` 与 `sse` **形状同为 URL、且每个字段都一致**（`url`、`headers`、`timeout`、
`trust`）—— 只有客户端工厂不同。正是这份对称让 §5 能共享几乎全部代码。

## 5. 客户端改动（`agentao/mcp/client.py`）

### 5.1 导入 —— 用**规范**客户端，而非弃用别名

```python
from mcp.client.streamable_http import (
    create_mcp_http_client,
    streamable_http_client,
)   # 新增
```

钉住的 SDK（`mcp` 1.26.0，pin 下限）同时提供两个函数：`streamablehttp_client`
被标 `@deprecated("Use streamable_http_client instead.")`，每次连接都会发
`DeprecationWarning`；`streamable_http_client` 是规范替代。我们导入规范的那个
（两者在 1.26.0 都在，无需抬 pin）。代价是一点 API 差异（§5.3）：规范函数收一个
预建的 httpx 客户端，而非 `headers`/`timeout`/`sse_read_timeout` kwargs —— 我们用
SDK 自己的 `create_mcp_http_client` 工厂（本模块也导出）来建，这正是弃用包装内部
所做的。

SDK 已在模块顶部即时导入（`from mcp.client.sse import sse_client`，第 14 行）；
Streamable HTTP 属同一包，故无新的懒加载顾虑 —— `agentao.mcp/__init__.py` 的
PEP-562 闸门已把整个 SDK 推迟到首次触碰 `McpClientManager` 时。

### 5.2 `transport_type` 与分发 → 委托 `resolve_transport`

该属性用于*展示*、绝不能抛（`get_server_status`、`/mcp list`），故它把失败即关闭的
错误吞成 `"unknown"`；真实信息改由 `connect()` 给出（D3）：

```python
@property
def transport_type(self) -> str:
    try:
        return resolve_transport(self.config)     # "stdio" | "sse" | "http" | "unknown"
    except McpTransportConfigError:
        return "unknown"                          # 可操作原因随 error_message 带出
```

`connect()` 调*会抛*的解析器，故坏 `type` / 必需键缺失经既有 212 处 `except`
失败即关闭（其中会设 `error_message`）：

```python
# connect()
transport = resolve_transport(self.config)        # 抛 McpTransportConfigError，被 connect 的 except 捕获
if transport == "stdio":
    await self._connect_stdio()
elif transport == "sse":
    await self._connect_sse(startup_timeout, request_timeout)
elif transport == "http":
    await self._connect_streamable_http(startup_timeout, request_timeout)
else:  # "unknown" —— 无 type 且无 command/url
    raise ValueError(
        f"No transport configured for server '{self.name}' "
        f"(need 'command', or 'url' with type 'sse'/'http')"
    )
```

由于 `resolve_transport` 已校验过必需键（D3 第 3 步），`_connect_stdio` 的
`self.config["command"]`（234）与 URL 传输的 `self.config["url"]` 必然存在 ——
无下游 `KeyError`。

### 5.3 结构差异 —— 建客户端 + 元组元数

对钉住的 SDK 的内省（实测，非记忆）：

- `sse_client(url, headers=, timeout=, sse_read_timeout=)` yield
  **`(read_stream, write_stream)`** —— 2 元组。
- `streamable_http_client(url, *, http_client=None, terminate_on_close=True)`
  yield **`(read_stream, write_stream, get_session_id)`** —— 3 元组；第三个元素
  是返回协商出的 `Mcp-Session-Id`（或 `None`）的可调用对象。它收一个**预建的
  httpx 客户端**（非 header/timeout kwargs），且当调用方自带客户端时，SDK **不**
  管其生命周期。

因此 `_connect_streamable_http` 与 `_connect_sse` 有三处不同：(1) 用
`create_mcp_http_client` 建 httpx 客户端（headers + `httpx.Timeout(startup,
read=sse_read_timeout)` —— 与弃用包装同一映射）；(2) 把该客户端**先于**传输入栈，
LIFO 退栈时先拆传输再关客户端；(3) 解 3 元组，非 2 元组：

```python
async def _connect_streamable_http(self, startup_timeout, request_timeout):
    import httpx
    url, headers, sse_read_timeout = await self._prepare_url_connect(startup_timeout, request_timeout)
    http_client = create_mcp_http_client(
        headers=headers or None,
        timeout=httpx.Timeout(startup_timeout, read=sse_read_timeout),
    )
    await self._exit_stack.enter_async_context(http_client)   # 调用方管生命周期
    transport = await self._exit_stack.enter_async_context(
        streamable_http_client(url, http_client=http_client, terminate_on_close=False)
    )
    read_stream, write_stream, _get_session_id = transport   # 3 元组，非 2
    self._session = await self._exit_stack.enter_async_context(
        ClientSession(read_stream, write_stream)
    )
```

**`terminate_on_close=False`（评审 Finding 1）。** 显而易见的 `True` 会在关闭时发
session-terminate `DELETE` —— 但该 `DELETE` 复用同一 httpx 客户端，其 `read` 超时
已抬高以覆盖长工具调用预算（最高到 `request`；默认 ≥300 s）。若服务器接受连接却
挂住 `DELETE`，则 `disconnect()`（以及 `call_tool` 里的瞬时错误*重连*路径）会阻塞
整个窗口。用 `wait_for` 包退栈在此明确不安全（connect() 注释警告：超时取消不得跨
anyio cancel scope 进入传输清理）。故跳过该 teardown 请求：SSE 与 stdio 也都不发，
服务器自行过期空闲 session，且 terminate `DELETE` 是规范 SHOULD 而非 MUST。

### 5.4 抽出共享的 URL-connect 前置

`_connect_sse` 与 `_connect_streamable_http` 有四步逐字相同：读 `url`/`headers`、
算 `sse_read_timeout`（相对 `_DEFAULT_SSE_READ_TIMEOUT` 只升不降，client.py:332-336）、
跑 `_preflight_content_type`、再进入传输上下文管理器。把前三步抽成小助手
`_prepare_url_connect(startup, request) -> (url, headers, sse_read_timeout)`
（其内跑预检），使两个 `_connect_*` 只差一行：客户端工厂与元组解包。

**预检无需改动。** `_preflight_content_type`（257-316）已白名单
`application/json` + `text/event-stream`，其 docstring/错误也已点名 "Streamable
HTTP" —— PR #71 时就写成了传输无关。一个用 `application/json` 或
`text/event-stream` 回应 HEAD/GET 探测的 Streamable HTTP 端点会通过；对探测回
405 的端点返回非 2xx，直接放行给真正的握手（client.py:302-303）。对照现有
`test_mcp_preflight.py` 逻辑核实过 —— 白名单无需改。

### 5.5 超时 —— 语义同 SSE

`resolve_timeouts` 与传输无关。`startup` 成为 httpx 客户端的 connect 超时
（`httpx.Timeout(startup, read=...)` 喂给 `create_mcp_http_client`），
`sse_read_timeout` 成为其 read 超时，`request` 与 SSE 完全一样地经
`read_timeout_seconds` 约束每次 `call_tool`（client.py:368-371），只升不降规则同样
适用。只需修 `config.py:100-103` 一处措辞（"交给 SSE 传输"→"交给 URL 传输
（SSE 或 Streamable HTTP）"）。

### 5.6 错误分类 / 重连 —— v1 保持不动，留一个观察项

Streamable HTTP 的 session 过期在服务端表现为 **对 `Mcp-Session-Id` 的 HTTP 404**。
现有 `SESSION_EXPIRED` 标记（client.py:51-59："session expired/not found/unknown
session/session terminated"）已覆盖 SDK 抛出的**文本**形态，而 `TRANSPORT_DROPPED`
加重连一次的循环（372-402）已覆盖被掐断的长轮询流。**裸** `404`（无 session 字样）
刻意**不**加入 `SESSION_EXPIRED`：`404` 太宽（真正的 tool-not-found 会滚成重连风暴）。
按项目"观察项而非臆测性修复"的惯例（参 `project_codex_pull_review_20260614`），v1
沿用现有规则，**仅当**观察到真实 Streamable HTTP 服务器在过期时发无文本 404 时，
才加针对性标记。此处记录，以便下一位读者知道这是决策而非疏漏。

### 5.7 推断 http 默认的 connect 失败提示（D2 缓解）

D2 让裸 `url` 表示 Streamable HTTP，这会打断那些用裸 `url` 连 legacy SSE 服务器的
配置。为让这个破坏响亮，`connect()` 在推断路径上丰富失败信息。两道守卫保证提示准确：

1. **仅推断路径** —— `url` 存在且 `type` 缺失（用户并未显式选 http）。
   `resolve_transport` 已能区分"显式 http"与"推断 http"；把这一位信息（如
   `resolve_transport(config, return_source=True) -> (transport, "explicit"|"inferred")`，
   或姊妹谓词 `transport_is_inferred(config)`）传入 `except`，让提示对 `{"url": ...}`
   触发，但对显式 `{"type": "http", ...}`（那里 SSE 并非可能意图）**不**触发。
2. **不是非-MCP 判定** —— 当错误是 `NonMcpEndpointError` 时跳过提示。
   该异常是预检的"这是网页、根本不是 MCP"判定（`client.py:102-110`），本就带有自己
   可操作的信息；在其上再追加"设 `type:\"sse\"`"会错误暗示该端点是 legacy SSE 服务器，
   而它根本不是 MCP。此提示是给*握手/传输*失败用的 —— 那里 SSE-vs-HTTP 是可能原因 ——
   而非给 content-type 拒绝用的。
3. **不是 auth 失败（评审 Finding 5）** —— 当 `classify_mcp_error(e) is
   McpErrorKind.AUTH` 时跳过。真实 Streamable HTTP 服务器返回 401/403 并不能靠切
   SSE 修复；提示会把用户引到错误方向而非去查凭证。

```python
except Exception as e:
    message = str(e)
    if (
        transport == "http"
        and source == "inferred"                      # 来自 resolve_transport(..., return_source=True)
        and not isinstance(e, NonMcpEndpointError)    # 不覆盖非-MCP 判定
        and classify_mcp_error(e) is not McpErrorKind.AUTH   # 401/403 切 SSE 也修不了
    ):
        message += (
            "  (tried as Streamable HTTP — the default for a bare 'url'; "
            "if this is a legacy SSE endpoint, set \"type\": \"sse\".)"
        )
    self.error_message = message
    ...
```

低噪（仅在真实 connect 失败时触发，绝不对 stdio/sse/显式 http，绝不对非-MCP-content-type
判定，绝不对 auth 失败）且高价值（一词修复只在真正适用的失败上随之带出）。

## 6. ACP 改动 —— 放开三处闸门

三处当初拒绝 `http` 都是**因为客户端无法分发**。§5 移除了那个理由，故三处齐放。
同一处改动里更新解释拒绝的 docstring（否则它们会说谎 —— 参
`project_hermes_pull_review_20260629` 的"注释说谎"观察项）。

1. **`acp/initialize.py:75-78`** —— `mcpCapabilities = {"http": True, "sse": True}`。
   同时更新 24-26 的 docstring 与 70-74 的行内注释。
2. **`acp/session_new.py:226-232`** —— 类型集合接受 `"http"`。URL 字段校验分支已是
   `else: # http or sse`（第 248 行），故只需放宽 227 处的守卫集合：
   `("stdio", "sse", "http")`。修 204-210 的 docstring。
3. **`acp/mcp_translate.py:217-244`** —— `sse` 分支改为
   `elif transport_type in ("sse", "http")`，且**关键地，为_两种_ URL 传输都把
   显式传输盖进产出的 cfg**：

   ```python
   elif transport_type in ("sse", "http"):
       ...
       cfg = {"url": url, "type": transport_type}   # 双向都盖章
       ...
   ```

   在 D2 的 http 默认下，盖章**双向都必须**：一条产出裸 `{"url": ...}` 的 ACP `sse`
   条目会被 `resolve_transport` 读回成 *http*（新默认）而连错传输 —— 正是翻转前隐患的
   反面。盖上显式 `type` 让翻译独立于原生默认值，从而在任何未来默认变更下都保持正确。
   更新 63-69 的模块 docstring 与 233-237 的丢弃分支注释（那个 `else` 现在只兜真正
   未知的类型）。

## 7. CLI 改动（`agentao/cli/commands/mcp.py`）

`/mcp add` 目前对任何 `http(s)://` 端点都写 `{"url": endpoint}`（53-54）—— 恒为
SSE。把无标志的 URL 默认翻为 Streamable HTTP（与 D2 一致），并加 `--sse` 退出项：

```
/mcp add <name> <url>                            # → { "url": ... }（裸 → 推断 http，D2）
/mcp add --sse  <name> <url>                     # → { "type": "sse",  "url": ... }（legacy SSE）
/mcp add --http <name> <url>                     # → { "type": "http", "url": ... }（显式）
/mcp add <name> <command> [args...]              # stdio（不变）
```

解析可出现在**名字之前或之后**的 `--sse`/`--http` 标志（评审 Finding 2 ——
`gh --http <url>` 是常见顺序，不能落到 `{"command": "--http"}` 的 stdio 配置）。写入：

- 无标志 URL → **裸 `{"url": endpoint}`**（无 `type`）。它解析为 http（D2）但保持
  *推断*，故若端点其实是 legacy SSE，连接失败提示（§5.7，仅对推断触发）会引导用户加
  `--sse`（评审 Finding 4 —— 显式 `type:"http"` 会压掉该提示，而那正是 CLI 添加的
  服务器所需的恢复引导）。
- `--http` → `{"type": "http", "url": endpoint}`（显式选择，无提示）。
- `--sse` → `{"type": "sse", "url": endpoint}`。

更新用法/示例块（44-48），以 Streamable HTTP 形态打头。

## 8. 文档

- `docs/reference/configuration.md` —— 传输表（197-198）新增 **Streamable HTTP**
  行：必需键 `url`（+ 可选 `type: "http"`），可选 `headers` / `timeout` / `trust`；
  并加一句 **裸 `url` = Streamable HTTP（D2 默认）**、`type: "sse"` 选入 legacy SSE。
  更新 202 的超时条目（"SSE HTTP 连接打开"→"URL 传输 HTTP 打开"）。在传输表附近加上
  §10 迁移说明。
- `config.py` `McpServerConfig` docstring（14-32）—— 加 `type` 键，并仿照 "SSE
  transport" 段增一段 "Streamable HTTP transport"。
- `CLAUDE.md` § MCP —— 传输列表（"`command`（stdio 子进程）或 `url`（SSE）"）改为
  "`command`（stdio）或 `url`（默认 Streamable HTTP；加 `type: "sse"` 用 legacy SSE 传输）"。

## 9. 测试计划

新增 `tests/test_mcp_streamable_http.py`：

- `resolve_transport` 正常行：§4 表每一有效行，含别名归一化
  （`streamable-http`/`streamable_http`/大小写）。
- `resolve_transport` **失败即关闭**（Finding 1 & 3）：
  - 显式但未知的 `type`（`"see"`、`"streamable"`、`""`）→ 抛
    `McpTransportConfigError` —— **不会静默变成 http**；
  - 必需键不匹配（`{"type":"http"}` 无 `url`、`{"type":"stdio"}` 无 `command`、
    `{"type":"http","command":...}` 无 `url`）→ 抛；
  - 上述所有情况，`transport_type` *属性*返回 `"unknown"`（绝不抛），而 `connect()`
    把可操作信息记入 `error_message`。
- 分发：`connect()` 把 `type:"http"` **以及裸 `url`（无 type）** 都路由到
  `_connect_streamable_http`（monkeypatch `streamable_http_client` +
  `create_mcp_http_client` 为假实现，http 那个 yield 3 元组），而 `type:"sse"`
  路由到 `_connect_sse` —— **D2 默认断言（裸 `url` = http）。**
- §5.7 提示门控：裸 `url` 握手失败时追加 SSE 提示；显式 `type:"http"` 失败时**不**
  追加；SSE 失败也不追加；裸 `url` 的 `NonMcpEndpointError`（HTML 页面）不追加；
  **且裸 `url` 的 auth 失败也不追加**（评审 Finding 5）。
- 3 元组解包：假 CM yield `(read, write, get_session_id)`；断言 session 建成、
  回调不被强求。
- 超时：`create_mcp_http_client` 收到 `httpx.Timeout(startup, read=sse_read_timeout)`
  （断言 `.connect` / `.read`）；`terminate_on_close` 为 **False**（评审 Finding 1）。
- CLI `/mcp add`（评审 Finding 2 & 4）：裸 URL 写 `{"url": ...}`（无 `type`）；
  `--http`/`--sse` 写显式 type 且在名字**前或后**都被识别；stdio 添加不受影响。
- 预检复用：返回 `application/json` 的 Streamable HTTP 端点通过；`text/html`
  抛 `NonMcpEndpointError`（扩展 `test_mcp_preflight.py`）。

更新现有：

- `test_acp_initialize.py:89`、`test_acp_schema.py` 内联夹具 —— 期望的
  `mcpCapabilities` 翻为 `{"http": True, "sse": True}`；重新生成校验入库的
  `docs/schema/host.acp.v1.json` 快照（`type` enum 增 `http`）。
- `test_acp_session_new.py` —— `http`-拒绝用例改为 `http`-接受用例。
- `test_acp_mcp_injection.py` —— SSE 翻译断言现在也带 `"type": "sse"`；加一条
  `http` 条目断言 `"type": "http"` 盖章；translate-rejects-http 类改为 translate-http。
- `test_mcp_connect_timeouts.py` —— 两个 `connect()` 测试钉 `type:"sse"`
  （裸 `url` 现在分发到 http，绕过 `_connect_sse` 的 patch）。

## 10. 上线、非目标、后续

**上线 —— 破坏性变更（D2）。** 裸 `url` 从 SSE 翻为 Streamable HTTP，故对 URL 服务器
而言这**不**向后兼容。作为一个 PR 落地（客户端 + ACP + CLI + 文档 + 测试），因为没有
客户端时翻 ACP 闸门无意义，客户端落地后还让它继续拒绝也无意义。合并前核实合并后的树
跑绿（参"绝不合并红/不稳定 CI"）。

**迁移行（发布说明 / CHANGELOG）：** *"MCP：裸 `url` 服务器现在默认走 Streamable
HTTP 传输。若你的服务器是 legacy SSE 端点，请在 `.agentao/mcp.json` 的条目里加
`\"type\": \"sse\"`。Streamable HTTP 是规范对现已弃用的 HTTP+SSE 传输的替代。"*
§5.7 的 connect 失败提示会为漏看说明的人在上下文里给出同样的修复。

**非目标（v1）：**
- 暴露/使用 `Mcp-Session-Id`（被丢弃的第 3 个元组元素）或显式 session 续接 ——
  重连一次的循环已足够。
- `streamablehttp_client` 上的 OAuth/`auth=` —— headers（含经 env 展开的
  `Bearer $TOKEN`）已覆盖当前 bearer-token 场景；交互式 OAuth 是另一份更大的设计。
- 自动回退（被否决的 D2 替代方案）。
- 对每份裸 `url` 配置发一次性*运行时弃用告警*。否决为噪声 —— 它会连现在正确的 http
  场景也触发。§5.7 的失败时提示是针对性替代；§10 迁移行覆盖广播式公告。

## 11. 影响面

| 文件 | 改动 |
|---|---|
| `agentao/mcp/config.py` | `resolve_transport()`（失败即关闭）+ `McpTransportConfigError`；`McpServerConfig` docstring 加 `type`；超时文档措辞 |
| `agentao/mcp/client.py` | 导入（规范 `streamable_http_client` + `create_mcp_http_client`）；`transport_type`（吞成 `unknown`）/`connect` → `resolve_transport`；`_connect_streamable_http`（3 元组，`terminate_on_close=False`）；`_prepare_url_connect` 助手；§5.7 门控提示 |
| `agentao/acp/initialize.py` | `mcpCapabilities.http = True` + docstring |
| `agentao/acp/session_new.py` | 解析接受 `http` + docstring |
| `agentao/acp/mcp_translate.py` | 翻译 `http`，**双向盖 `type`** + docstring |
| `agentao/acp/schema.py` | `mcpCapabilities` 默认 + `AcpMcpServer.type` `Literal` 加 `http` + docstring |
| `docs/schema/host.acp.v1.json` | 重新生成快照（`type` enum + 能力默认） |
| `agentao/cli/commands/mcp.py` | `/mcp add [--http|--sse]`（标志在名字前/后；裸 URL → 无 `type`） |
| `agentao/cli/help_text.py` | 权威 `/help` 的 `/mcp add` 行 |
| `docs/reference/configuration.md` | Streamable HTTP 传输行 + 超时说明 |
| `CLAUDE.md` | MCP 传输行 |
| `tests/test_mcp_streamable_http.py`（新）+ `test_mcp_connect_timeouts.py` + 4 个 ACP 测试 | 见 §9 |

## 12. Commit 清单

按依赖排序 —— 每一阶段都在前一阶段之上可编译/可测，实现者可自上而下推进，PR 也能干净
二分。行锚为 `main`@`8bcb1b1`。整体作为**一个 PR** 落地（§10）；下列各阶段可作为该 PR
内的独立 commit。

### 阶段 0 —— 基线

- [ ] 从 `main` 切分支 `feat/mcp-streamable-http`。
- [ ] 记录绿色基线：`uv run python -m pytest tests/ -q`（记下数量；合并后的树必须
      ≥ 基线 + 新测试，全绿 —— 绝不合并红 CI）。
- [ ]（已验证，在目标机器再确认）钉住的 SDK：规范 `streamable_http_client` yield
      **3 元组**且接受 `terminate_on_close`；弃用别名 `streamablehttp_client` 会发
      `DeprecationWarning`（用规范的那个）—— `uv run python -c "import inspect,mcp.client.streamable_http as m; print(inspect.signature(m.streamable_http_client))"`。

### 阶段 1 —— `agentao/mcp/config.py`（地基，无树内依赖）

- [ ] 增加 `class McpTransportConfigError(ValueError)`。
- [ ] 增加 `resolve_transport(config, *, return_source=False) -> str`，实现 D3：
      `type` 转小写 + 别名折叠（`streamable-http`/`streamable_http`/`streamablehttp`
      → `http`）；显式但未知的 `type` **抛 `McpTransportConfigError`**；缺失时套 D2
      推断（`command`→stdio、`url`→http、否则 `"unknown"`）；**必需键校验**（stdio 需
      `command`，sse/http 需 `url`）→ 不匹配即抛。`return_source=True` 时额外返回
      `"explicit"|"inferred"` 供 §5.7 提示（或提供姊妹 `transport_is_inferred(config)`）。
- [ ] 扩展 `McpServerConfig` docstring（14-32）：加 `type` 键，仿 "SSE transport"
      增 "Streamable HTTP transport" 段。
- [ ] 改 `resolve_timeouts` docstring 措辞（100-103）："交给 SSE 传输" → "交给 URL
      传输（SSE 或 Streamable HTTP）"。

### 阶段 2 —— `agentao/mcp/client.py`

- [ ] 顶部（第 14 行 `sse_client` 导入旁）加规范导入
      `from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client`
      （**不**用弃用别名 `streamablehttp_client`）。
- [ ] `transport_type` 属性 → `try: return resolve_transport(self.config)
      except McpTransportConfigError: return "unknown"`（绝不抛 —— 状态路径保持全函数）。
- [ ] `connect()`（183-190）：用**会抛**的 `resolve_transport` 分发；`"unknown"`
      情形保留 "No transport configured" 的 `ValueError`。
- [ ] 抽出 `_prepare_url_connect(startup, request) -> (url, headers,
      sse_read_timeout)`（读 url/headers + 332-336 的只升不降 `sse_read_timeout` 计算
      + `_preflight_content_type`）；重构 `_connect_sse`（318-353，**2 元组**）用它。
- [ ] 增 `_connect_streamable_http`：用该助手建 `create_mcp_http_client(headers,
      httpx.Timeout(startup, read=sse_read_timeout))`，先入栈该客户端再入
      `streamable_http_client(url, http_client=..., terminate_on_close=False)` +
      **3 元组解包** `read, write, _get_session_id = transport`。
- [ ] `connect()` 的 `except`（211-221）加 §5.7 提示：**仅当** `transport == "http"`
      **且**传输是推断得来**且** `not isinstance(e, NonMcpEndpointError)`
      **且** `classify_mcp_error(e) is not McpErrorKind.AUTH` 时，追加设 `type:"sse"` 提示。
- [ ] `classify_mcp_error` / `_ERROR_RULES` 保持不变（§5.6 观察项）。

### 阶段 3 —— ACP（放开三处闸门；修 docstring 别让它说谎）

- [ ] `acp/initialize.py`：`mcpCapabilities`（75-78）→ `{"http": True, "sse":
      True}`；更新 docstring（24-26）与行内注释（70-74）。
- [ ] `acp/session_new.py`：类型守卫（227）接受 `"http"`；修 `_parse_mcp_servers`
      说 http 被拒的 docstring（204-210）。
- [ ] `acp/mcp_translate.py`：`elif transport_type in ("sse", "http")`（217）；
      `cfg = {"url": url, "type": transport_type}` —— **双向都盖**（§6.3）；更新模块
      docstring（63-69）与丢弃分支注释（233-237）。

### 阶段 4 —— CLI `agentao/cli/commands/mcp.py`

- [ ] `/mcp add`（41-69）：解析在名字**前或后**均可的 `--sse`/`--http` 标志；无标志
      URL → **裸 `{"url": endpoint}`**（推断，可触发提示），`--http` → `{"type":"http",...}`，
      `--sse` → `{"type":"sse",...}`。更新用法/示例块（44-48）。
- [ ] `cli/help_text.py`（85）：把权威 `/help` 的 `/mcp add` 行更新为
      `[--http|--sse]`（评审 Finding 7）。

### 阶段 5 —— 文档与发布说明

- [ ] `docs/reference/configuration.md`：Streamable HTTP 传输行（197-198）；超时条目
      （202）措辞；传输表附近加 §10 迁移说明；写明裸 `url` = Streamable HTTP、
      `type:"sse"` 用 legacy SSE。
- [ ] `CLAUDE.md` § MCP：传输行 → "`command`（stdio）或 `url`（默认 Streamable HTTP；
      加 `type:"sse"` 用 legacy SSE）"。
- [ ] CHANGELOG / 发布说明：§10 的**破坏性**迁移行。

### 阶段 6 —— 测试

- [ ] 新增 `tests/test_mcp_streamable_http.py` —— §9 全部用例：`resolve_transport`
      正常行 + **失败即关闭**（未知 `type`、必需键不匹配、属性返回 `"unknown"`）；分发
      （`type:"http"` **及裸 `url`** → `_connect_streamable_http`，`type:"sse"` →
      `_connect_sse`）；3 元组解包；超时 + `terminate_on_close=False`；**§5.7 提示门控含
      `NonMcpEndpointError` 与 AUTH 跳过**；CLI `/mcp add` 标志/裸 URL 用例。
- [ ] `tests/test_acp_initialize.py:89` → `{"http": True, "sse": True}`（真正的
      声明值断言）。
- [ ] `tests/test_acp_schema.py` 内联 `mcpCapabilities` 夹具 → `{"http": True,
      "sse": True}`；**重新生成 `docs/schema/host.acp.v1.json`**（`type` enum 增 `http`）。
- [ ] `tests/test_acp_session_new.py` —— `http`-拒绝用例改为 `http`-接受用例。
- [ ] `tests/test_acp_mcp_injection.py` —— SSE 翻译断言现在还带 `"type": "sse"`；
      加一条 `http` 条目断言 `"type": "http"` 盖章；translate-rejects-http 类改为 translate-http。
- [ ] `tests/test_mcp_connect_timeouts.py` —— 两个 `connect()` 测试钉 `type:"sse"`。

### 阶段 7 —— 验证（失败即关闭式自审，遵 grep-first 惯例）

- [ ] 全套绿：`uv run python -m pytest tests/ -q`。
- [ ] 定向：`uv run python -m pytest tests/test_mcp_*.py tests/test_acp_*.py -q`。
- [ ] 无残留旧说法：
      `grep -rn "streamable_http_client\|http is not supported\|only supports.*sse\|mcpCapabilities.*http.*[Ff]alse" agentao/`
      只剩预期匹配（注释描述历史，无断言当前行为者）。
- [ ] 无说谎 docstring：三个 ACP 文件不再说 `http` 被拒。
- [ ] 冒烟：把 `streamable_http_client` + `create_mcp_http_client` monkeypatch 成
      假实现（3 元组）驱动 `McpClient.connect()` → CONNECTED + 列出 tools；若有可达的
      真实 Streamable HTTP 服务器，`/mcp add <url>` 后 `/mcp list` 显示 connected 且
      一次工具调用能往返。（`verify` 技能。）

### 阶段 8 —— commit / PR

- [ ] 建议 commit：`feat(mcp): add Streamable HTTP transport; bare url now
      defaults to http`（conventional-commit scope，与仓库历史一致）。
- [ ] PR 正文：**BREAKING CHANGE** 提示 + §10 迁移行 + 指向本设计文档
      （`docs/design/mcp-streamable-http.md`）的链接。
- [ ] commit 信息以 `Co-Authored-By` trailer 结尾。
- [ ] 合并前 CI 绿；若 rebase，在合并后的树上重跑套件（语义冲突能过文本合并却会坏 ——
      绝不合并红 CI）。
