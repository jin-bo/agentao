# MCP OAuth —— 设计

**状态：** **已批准（2026-10-02）** —— 批准构建，并认可 §11 的全部建议。**PR 1 已实现**（认证模块与凭据存储，
已作为 #398 合入），**PR 2 已实现**（CLI 登录流程）；见附录 A 的*实施记录*。**PR 3 已实现**（文档，§13.5）。**凭据档案**（`oauth.profile`，同一 URL 登录两个账号）在 0.5.11 加入，见 §6.4。设计历程：提案 rev 8（2026-10-02）。已通过四轮设计评审；之后又并入了对 rev 5 的一次反向评审
（→ rev 6），以及一次外部评审对 rev 6 的意见（→ rev 7，两条 P1：认证失败一旦被传输层处理就看不见了，
所以每条连接都必须由认证对象给出判定）和它的第二轮意见（→ rev 8：被 shield 的刷新还必须扛过 manager
的关闭）。外部评审的结论：这条修完即可实现，不需要扩大范围（附录 A）。§13 是实施计划。

**这份设计加了什么。** 远程 MCP 服务器的原生 OAuth：用户添加一个服务器 URL，运行一次
`/mcp login <name>`，agentao 保存凭据、负责刷新、重新连接 —— 在 CLI、`agentao run`、ACP 会话和嵌入式
宿主里都一样，而且浏览器永远不会自己弹出来。

**来由。** 它重新审视 `openworker-borrow-review.zh.md` §9（rev 3，2026-07-29）。那一节把 MCP OAuth
降级为一条原则，理由是它「需要一个 loopback HTTP 路由和一个浏览器 —— 属于宿主侧，agentao 两者都不该有」。
本设计保留这条边界 —— 浏览器和回调仍归宿主，而 agentao 自己的 CLI 就是这样一个宿主 —— 并说明这条边界
已不再意味着「不做」（§1）。设计如何一轮轮走到现在，见附录 A。

**读者：** agentao 维护者；任何改动 `agentao/mcp/` 的人。**英文孪生：** `mcp-oauth.md`，两份内容同步
维护。

**资料来源**（均取于 2026-10-02）：MCP 规范 2025-11-25 与 2026-07-28 两个修订版的原始 `.mdx` 文件
（`basic/authorization/…`）以及 `ext-auth` 仓库；已安装的 SDK（`uv.lock` 中的 `mcp` 2.0.0，以及通过
`uv run --with` 取得的 1.26.0 和 1.30.0）；按编号引用的 python-sdk issue 和 PR；同类项目源码：codex
`4dd51f4a`、gemini-cli `fb972b2f`、goose `b9db895a`、opencode `1ddb0873`、pi-mono `8562bcf6`；
Claude Code 公开的 MCP 文档。下文规范措辞除加引号处外均为转述；加引号处为原始文件的英文原文。

---

## 要点

- **缺口。** agentao 的 MCP 客户端除了静态 `headers`（`mcp/config.py:34`）之外没有任何授权机制。
  要求 OAuth 的远程服务器 —— 托管的 Streamable HTTP 服务器越来越默认如此 —— 完全用不了。我们对照的
  每一个同类项目（codex、gemini-cli、goose、opencode、Claude Code）都支持它。
- **SDK 负责登录，不负责稳态。** `mcp.client.auth.OAuthClientProvider`（两个大版本都有）能正确完成
  发现、客户端注册、PKCE、`resource`、`iss`（2.x）和授权码交换。出问题的是它对**已登录**连接的处理
  （§3.2，全部经过实测或读源码确认，上游全部未修）。
- **拆分。** *登录* —— 显式、少见 —— 在一条专用连接上运行 SDK provider，由宿主提供的 UI 打开浏览器、
  接收回调。*其他所有连接* 都用 agentao 自己的 `StoredTokenAuth`：附上已存的 access token；在临近过期
  或收到一次 401 时，在跨进程文件锁下刷新它；刷新被拒绝即 `needs_auth`，刷新失败（其他原因）即普通错误。
  它从不构造 SDK provider，所以**不可能**发起交互式流程 —— 七月那条原则，由构造方式来保证。
- **宿主边界** 与七月相同：CLI 实现 UI（`/mcp login`）；ACP 会话永远不实现，而是报告 `needs_auth`。
  面向嵌入式宿主的公开登录 API 推迟到有宿主提出需求时再做（§8.2）。
- **八个决策** 交给维护者（§11），每个都附建议。D6 是稳态认证由 agentao 自己负责（建议）还是子类化
  SDK provider；D7 是 `login()` 能否通过 patch 一个 SDK 函数来扩大 scope（建议：首版不做）；D8 是
  服务器首次登录后是否在运行时注册它的工具（建议：首版不做 —— 重启，与 `/mcp add` 现在的要求一致）。
- **计划**（§13）：三个 PR —— 认证模块与凭据存储、CLI 登录流程、文档 —— 按此顺序合入 `main`、在同一
  版本发布，文档中写明三条局限。计划以 §11 的全部建议为前提；任一决策选了别的答案，会改变它所指的那个 PR。

---

## 1. 2026-07-29 结论之后变了什么

那条结论对浏览器和回调*应该放在哪里*的判断是对的，本设计没有挪动它们。变的是 agentao 这一侧的成本
和需求：

1. **SDK 负责交互式登录。** 七月时的另一选项看起来是重新实现 OAuth 2.1 + RFC 9728/8414/8707/7636。
   其实不必：SDK 的 provider 在两个大版本上都完成了整个授权码流程；harness 只需提供存储和 UI。
2. **CLI 是一个宿主，而且是我们自己的。** 「宿主侧」不等于「本仓库里没人负责」：`agentao` CLI 已经拥有
   终端、确认提示和 `--login` 流程（#382）。CLI 里的一个 loopback 监听器是同一类代码。
3. **用户现在从编辑器和远程服务器进来。** ACP Registry 上架（#380/#644）以及远程优先的 MCP 生态，意味着
   「用我团队已经在用的托管 MCP 服务器」是第一次会话就会提出的需求，而每个同类项目都已支持。
4. **规范定型了。** 2026-07-28 修订版把客户端职责写得很具体（issuer 绑定、RFC 9207 `iss`、step-up 时的
   scope 并集、refresh token 处理），并弃用 DCR、改推 CIMD（§4）。

§9 保留的那条原则被原样采纳为核心不变式（§5.2）：*后台上下文永远不发起交互式流程；只有用户明确要求的
连接才可以。*

---

## 2. 现状（在 `main` @ `946d11f` 上 grep 核实）

| 事实 | 证据 |
|---|---|
| MCP 客户端里完全没有 OAuth | `grep -rni oauth agentao/mcp` → 无匹配 |
| 静态 headers 端到端可用，支持 `$VAR` 展开 | `mcp/config.py:34,250-251`；`client.py:890`（`_prepare_url_connect`） |
| 内容类型预检在两个 SDK 大版本上都用普通 `httpx` | `client.py:850-863`（`import httpx` → `httpx.AsyncClient`）—— 不是 `httpx_for_mcp` |
| 在 mcp 2.0 上，请求收到的 401 到达 agentao 时变成 `"Server returned an error response"` —— 状态码和响应头都没了 | `mcp/client/streamable_http.py:342-370`（2.0.0）；1.26 用 `raise_for_status()` |
| 工具调用的 401/403 以文本形式报告，状态仍是 `CONNECTED` | `client.py:69-80`（`McpErrorKind.AUTH`，字符串标记），`call_tool` 的 AUTH 分支 |
| MCP 工具只在构造 agent 时注册一次；`/mcp add` 会要求重启 | `tooling/mcp_tools.py`（`init_mcp` → `register_mcp_tools`）；`cli/commands/mcp.py:107` |
| 所有 MCP I/O 都跑在一个所有服务器共享的事件循环线程上 | `client.py:1335`（`agentao-mcp-loop`） |
| 内容类型预检把 401 放行给真正的握手 | `client.py:872-875`（"a 4xx/5xx may be an auth challenge"） |
| 服务器状态没有「需要认证」这一态；连接失败即 `ERROR` | `client.py:261-265`、`:551` |
| 连接失败在报告前会从 `ExceptionGroup` 中解包 | `client.py:550`（`_first_failure`） |
| Streamable HTTP 自建 httpx 客户端并交给 SDK | `client.py:978-993`（`create_mcp_http_client` → `streamable_http_client(http_client=…)`） |
| SSE 把 `headers` 传给 `sse_client`，后者也接受 `auth=` | `client.py:928-934`；SDK 签名（两个大版本） |
| `agentao.log` 中的 Bearer 值会被脱敏 | `security/secret_scan.py:72-77` |
| `filelock` 是核心依赖 | `pyproject.toml:51` |
| CLI 的 `/mcp` 只有 `list`、`add`、`remove` | `cli/commands/mcp.py:21,50,109` |
| 在 ACP 中，编辑器按会话提供 MCP 服务器 | `acp/mcp_translate.py`、`acp/schema.py:191` |

---

## 3. SDK 提供什么（按大版本）

### 3.1 接口面

通过阅读已安装源码、并对每个版本做 `inspect.signature` 核实。

| | mcp 1.26.0 / 1.30.0 | mcp 2.0.0 |
|---|---|---|
| Provider | `OAuthClientProvider(httpx.Auth)`（1.30：经由 `RedirectAwareAuth`） | `OAuthClientProvider(httpx2.Auth)` |
| 构造参数 | `server_url, client_metadata, storage, redirect_handler, callback_handler, timeout=300.0, client_metadata_url` | 同左，去掉 `timeout`，增加 `validate_resource_url` |
| `callback_handler` 返回 | `tuple[code, state]` | `AuthorizationCodeResult(code, state, iss)` |
| RFC 9207 `iss` 校验 | 无 | 有（`validate_authorization_response_iss`） |
| 按 issuer 绑定注册（SEP-2352）：issuer 存入客户端信息，在发现当前 AS 后按字符串精确比较 | **1.26：无**（没有 `issuer` 字段，也没有 `credentials_match_issuer`）；1.30：有 | 有（`utils.py:337-354`）—— **但见下面两条注意事项** |
| Step-up scopes | 替换 | 之前与挑战的并集（2026-07-28 规则） |
| 传输层挂钩 | `streamablehttp_client(auth=)`、`sse_client(auth=)`；`streamable_http_client` 接受预建客户端 | `streamable_http_client(http_client=)`、`sse_client(auth=)` |

`TokenStorage` 是每个 provider 一个实例上的四个 async 方法（`get/set_tokens`、`get/set_client_info`）；
如何分键由调用方负责。

关于 issuer 绑定的两条注意事项（2.0.0，rev 6 反向评审时读源码得出）：

- **空 issuer 与所有 issuer 都匹配。** 当 `client_info.issuer is None` 时，`credentials_match_issuer`
  返回 `True`（`utils.py:349-353`："carry no binding to enforce and are left as-is"）。当 DCR 注册走的是
  资源源站的 `/register` 回退路径、而不是 AS 公布的 `registration_endpoint` 时，SDK 不填 issuer
  （`oauth2.py:727-739`）；而在 1.26 下做的所有注册根本没有这个字段。
- **绑定的值是 `context.auth_server_url`，不是 `oauth_metadata.issuer`。** 打上的值是
  `auth_server_url or str(oauth_metadata.issuer)`（`oauth2.py:703`），比较对象是 `auth_server_url`
  （`:641`）—— 也就是受保护资源元数据（Protected Resource Metadata）里 `authorization_servers[0]`
  的那个字符串。相比之下，RFC 9207 的 `iss` 校验比较的是 `oauth_metadata.issuer`（`utils.py:250`）。

### 3.2 稳态缺陷（rev 2 为什么不让普通连接使用 provider）

| # | 缺陷 | 证据 | 上游（除注明外均未关闭） |
|---|---|---|---|
| **F1** | `context.lock` 从发出请求一直持有到**响应头**到达。以 JSON 模式响应工具调用的服务器会在工具执行完才发响应头，于是并行调用变成一个接一个 —— 对这类服务器等于撤销了 #241 | §9 S1：两个并发的 2 秒调用，**不带 provider 2.0 秒，带 provider 4.0 秒**，JSON 模式，2.0.0、1.26.0、1.30.0 皆然；SSE 模式不受影响。把锁缩小到「加载 token + 加头」后，两个大版本都回到 2.0 秒 | PR #2858（已关闭的 #2660 的 rebase）。其 head 目前无法 import（`MCP_PROTOCOL_VERSION` 在 `main` 上挪了位置） |
| **F2** | 同一把锁是跨 `yield` 持有的 `anyio.Lock`；当 httpx 从另一个任务驱动该生成器时会抛 `RuntimeError: The current task is not holding this lock` | 1.26.0 上多个 OAuth 服务器并发连接时的生产报告 —— 正是 agentao 的形态（#241/#243） | Issue #2847，由同一个 #2858 修复 |
| **F3** | `_initialize()` 加载已存 token，但从不恢复其过期时间，所以重启后已过期的 access token 被当作有效并发送出去 | `oauth2.py:550-554`（2.0），1.26 相同；`update_token_expiry` 只在拿到新 token 响应时调用（`:486,541`） | Issue #3250、#1318；PR #1784、#2492 |
| **F4** | 401 分支直接进入发现和交互式流程，从不尝试已存的 refresh token | §9 S2：已存过期 token + refresh token → 调用了 `redirect_handler`，假 AS 的 `/token` **从未被请求**，2.0.0 和 1.26.0 皆然 | PR #2875 |

F3 + F4 合起来意味着：照原样使用 provider，每个 access token 已过期（通常一小时后）的新进程都需要一次
交互式登录。在后台上下文中，这就是每次 CLI 启动、每个 ACP 会话都 `needs_auth`；在不谨慎的设计中，这就是
每次启动都弹浏览器（goose PR #8386 正是如此）。

provider 做得好的部分 —— 也是登录要用它的部分 —— 是授权码流程本身：按规范回退顺序做发现、注册
（预注册 / CIMD / DCR）、PKCE、`resource`、`state` 和（2.x）`iss` 校验，以及授权码交换。

---

## 4. 规范对客户端的要求（2026-07-28，core）

授权在 MCP 中是可选的（"Authorization is **OPTIONAL** for MCP implementations"），只适用于 HTTP 传输
（stdio "**SHOULD NOT** follow this specification, and instead retrieve credentials from the
environment"），也不禁止静态 headers。客户端一旦做 OAuth，与这里相关的职责，以及 rev 2 拆分中由哪一侧
承担：

| 职责 | 由谁满足 |
|---|---|
| 从受保护资源元数据发现授权服务器（`WWW-Authenticate` 的 `resource_metadata`，再到 well-known 回退）；支持 RFC 8414 和 OIDC 发现；拒绝 `issuer` 与获取地址不符的元数据 | 登录（SDK） |
| 注册顺序：预注册 → CIMD（若 `client_id_metadata_document_supported`）→ DCR（2026-07-28 中已弃用）→ 询问用户 | 登录（SDK），取决于我们的输入（§6.3） |
| CLI/桌面客户端的 DCR 使用 `application_type: "native"` | 登录 —— 我们传入的客户端元数据 |
| PKCE S256；若 AS 未公布 `code_challenge_methods_supported` 则拒绝 | 登录（SDK） |
| 授权请求**和 token 请求**都带 `resource`（RFC 8707） | 登录（SDK）；**刷新（我们）** —— 每次刷新都发送 |
| Scope：以 `WWW-Authenticate` 挑战为准；遇到 403 `insufficient_scope` 时用并集重新授权，最多几次 | **部分满足。** 登录请求 401 挑战所列的 scope（SDK）。首版不支持 step-up 并集（D7）；403 `insufficient_scope` 变为 `needs_auth`，消息中如实说明（§5.3）—— 绝不自动重试循环 |
| 对授权响应做 RFC 9207 `iss` 校验 | 登录（SDK，仅 2.x —— D4） |
| 每个授权服务器用独立凭据；AS 变化时重新注册 | 记录绑定到一个 issuer，针对另一个 issuer 的登录会替换它（§6.2）；注册能否复用，在 SDK 有 issuer 绑定时由它决定，没有时不尝试复用（§5.5） |
| "**MUST** keep refresh tokens confidential in transit and storage"；"implement secure token storage" | 存储（§6.2） |
| "**MUST NOT** assume refresh tokens will be issued" | 刷新（我们）：没有 refresh token → 用 access token 直到过期，然后 `needs_auth` |
| 重定向 URI "**MUST** be either `localhost` or use HTTPS"；使用并校验 `state` | URI 由 CLI UI 负责（§7）；`state` 由 SDK 负责 |
| 每个请求都带 `Authorization: Bearer`，绝不放进查询串 | 稳态（我们） |

core 里**没有设备流**。唯一不需要浏览器的路径是草案扩展 *OAuth Client Credentials*（预注册的机器凭据）；
它和 *Enterprise-Managed Authorization* 都不在范围内（§10）。

---

## 5. 分层

### 5.1 谁负责什么

| 部分 | 负责 | 不负责 |
|---|---|---|
| 认证模块（`agentao/mcp/oauth.py`，内部） | 普通连接用的 `StoredTokenAuth`；`login(name, ui)`，运行一次 SDK provider 并写入记录；`logout(name)`；`needs_auth` 状态 | 打开浏览器、绑定端口、读终端 |
| 凭据存储（内部） | 每个服务器 URL 一条记录、每条记录一把锁、原子写入（§6） | 决定何时刷新 |
| CLI 登录流程 | UI：绑定 loopback 监听、打开浏览器或打印 URL、接受粘贴；`/mcp login`、`/mcp logout`、`/mcp list` 中的状态；`agentao mcp login` | 存储、发现、刷新 |
| ACP 会话 | 不做任何交互 —— 报告 `needs_auth`；登录在终端里完成（§8.3） | —— |

UI 是一个四步的内部接口 —— `prepare(preferred_port) -> redirect_uri`、`open(authorization_url)`、
`wait() -> (code, state, iss)`、`close()` —— 这样 CLI 现在就能实现它，以后也能不经重新设计就提供给
嵌入式宿主（§8.2）。

### 5.2 不变式

> **只有 `login()` 会构造 `OAuthClientProvider`。** 普通连接 —— 启动时的 `connect_all()`、重连、
> 工具调用 —— 携带的是 `StoredTokenAuth`，它没有任何能到达浏览器、回调或终端的代码路径。

rev 1 的做法是给后台 provider 一个会抛异常的 `redirect_handler`。S2 说明了为什么这是较弱的形式：SDK 会
在收到 401 的那个请求上内联运行交互式流程，所以后台连接在走到抛异常的 handler 之前，仍会执行发现、并
（用 DCR 时）**注册一个新客户端** —— goose 的「一天 6 个以上 client_id」（PR #11324）就是这种代价在每次
启动时重复。根本不构造 provider，去掉的是整条路径，而不只是它的最后一步。

`login()` 保留会抛异常的 handler，只作为它自己超时和取消时的保护。S2 也确认了 handler 抛出的异常从外面
看是什么样子：在两个大版本上，它都以包着原异常的 `ExceptionGroup("unhandled errors in a TaskGroup")`
出现，所以 `login()` 用连接路径同一个 `_first_failure` 解包（`client.py:550`）。

### 5.3 `StoredTokenAuth`（稳态）

一个由服务器已存凭据记录（§6.2）构建的 `httpx.Auth`（或 `httpx2.Auth` —— 取决于 `_compat` 为已安装 SDK
解析出哪一个）：

1. **每次请求前：** 如果 access token 将在 60 秒内过期且存在 refresh token，先刷新（第 3 步）。附上
   `Authorization: Bearer`。进程内锁只在这一步持有 —— 绝不跨越请求（F1/F2 的教训）。
2. **收到 401 时：** 如果 token 在附上之后已被其他请求或进程刷新过，用新 token 重试一次；否则刷新一次并
   重试一次。第二次 401 → `needs_auth`。
   在认证流程内部，「`needs_auth`」的含义是：把这个判定记录在该服务器的 `McpClient` 上（认证对象在构造时
   拿到它的引用，设一个标志），然后让 401 响应照常通过。
   **认证对象是唯一还能看到 401 的地方。** 在 mcp 2.0 上，Streamable HTTP 传输层会把请求的任何 ≥ 400 且
   非 404 的状态变成 `ErrorData(INTERNAL_ERROR, "Server returned an error response")`
   （`streamable_http.py:342-370`）—— 状态码和响应头都没了；在 1.26 上是 `raise_for_status()`，其文本里
   有 `401`，但响应头同样到不了 `_fail_connect`。所以传输层下游的任何地方都不能根据错误判断「认证」：
   `_fail_connect` 不能，`classify_mcp_error` 也不能 —— 它的字符串标记（`client.py:69-80`）会把 2.0 的
   这段文本归为 `OTHER`。判定在两个出口处、**先于**任何分类读取：
   - `_fail_connect` 先查标志；已设 → `NEEDS_AUTH`，否则走现有路径。
   - `call_tool` 唯一的那个 `except Exception`（现在调用 `classify_mcp_error` 的那个）先查标志；已设 →
     `NEEDS_AUTH` 加上「运行 `/mcp login <name>`」提示，不重连，不重试。只有标志未设才进入字符串分类。
     像 rev 6 那样只在 AUTH 分支读它，会漏掉 2.0 上的所有认证失败 —— 那个分支根本走不到。

   连接开始时（`connect()`）清除该标志，所以重新登录过的服务器不会把旧判定带进新会话。
3. **刷新** —— 进程内每个服务器单飞（single-flight），跨进程则**从重新读取到写入都持有记录的
   `filelock`**。在锁内：重新读取记录；若已不存在（已登出）→ `needs_auth`；若别的进程已经轮换过 token
   → 直接用它。否则以 `grant_type=refresh_token`、`refresh_token`、`resource` 和客户端注册时的认证方式
   `POST token_endpoint`，并处理结果：
   - 成功 → 把响应**合并**进记录，绝不整体替换，并在释放锁之前原子写入：
     - 省略 `refresh_token` → 保留旧的（RFC 6749 §6 允许服务器不轮换；若替换，第一次刷新成功、第二次就
       不可能了）。SDK 2.0.0 两者都保留（`oauth2.py:536,538`），pi 也是如此
       （`packages/mcp/src/oauth/flow.ts:263` @ `1c1e9c0e`：`{ refresh_token: options.refreshToken, ...tokens }`）。
     - 省略 `scope` → 保留之前授予的 scope（RFC 6749 §5.1：省略即不变）。
     - 省略 `expires_in` → 过期时间未知：一直用到收到 401。
     - `token_type` 不是 `Bearer`（不区分大小写）→ 视为刷新失败。
   - `400 invalid_grant`（或 `invalid_client`）→ `needs_auth`；**记录保留**，直到被一次登录替换。
   - 网络错误、超时、5xx → 普通的连接/工具错误；**记录保留**；不标记 `needs_auth`。

   因为锁跨越网络请求，登出或登录的提交会等待进行中的刷新，然后在其结果之上生效；两者都不会被它覆盖。
   token 请求有自己的超时，这限定了等待时长。

   **在哪里拿锁。** 这个流程运行在 `agentao-mcp-loop`（`client.py:1335`）上，即进程内所有 MCP 服务器
   共享的那一个事件循环；`filelock.FileLock` 的两个特性让最直接的写法在那里是错的：
   - 在循环线程上做阻塞式 `acquire()`，会在另一个进程持有该记录期间 —— 最长到对方 token 请求的超时 ——
     卡住**所有**服务器的 I/O。
   - `FileLock` **在同一线程内可重入**（默认 `thread_local=True`）。rev 6 反向评审时在 filelock 3.25.2 上
     实测：同一线程上的两个协程、同一个 `FileLock` 实例，都打印了「acquired」，而另一个仍持有着锁。循环
     线程上任何拿同一实例的其他代码都不会被排斥。

   **选定一种方案**（rev 7；rev 6 留了两种）：
   - **所有记录写入都在 MCP 循环上执行。** 刷新本来就是；登录的提交和 `logout` 像其他所有 manager 调用
     一样提交到该循环（`run_coroutine_threadsafe`），于是三类持有者都在同一线程上。
   - **一个 manager 内部：** 每条记录一把 `asyncio.Lock`，归 `McpClientManager` 所有、绑定到它的循环。
     每个持有者都先拿它再拿文件锁，所以在该循环上，文件锁的可重入计数永远不会被查询两次。刷新单飞就是这
     把锁：发现它被占用的请求会等待，然后重新读取记录。每个 manager 运行自己的循环（每个有 MCP 服务器的
     `Agentao` 一个 manager —— 子代理不连接任何服务器），所以这些锁从不在 manager 之间共享，每个 manager
     也各自构造自己的 `FileLock` 实例。
   - **同一进程内的不同 manager 之间** —— 不同循环、不同线程 —— 由文件锁负责互斥，与跨进程时相同：同一路径
     上的两个 `FileLock` 实例、从两个线程持有，彼此互斥（实测：第二个 `acquire(timeout=0)` 得到
     `Timeout`）。
   - **跨进程：** 轮询 `FileLock(path).acquire(timeout=0)` —— 遇到 `filelock.Timeout` 就
     `await asyncio.sleep(0.05)` 再试，直到「token 请求超时 + 余量」的期限；超过则报普通错误（不是
     `needs_auth`）。不用线程，循环上没有阻塞调用。实测：在 filelock 3.0.0 和 3.25.2 上，第二个进程的
     `acquire(timeout=0)` 都立即得到 `Timeout`；PR 1 把下限从 `>=1.4.0` 提到 `>=3.0`，而不是去测一个
     2017 年的版本。

   **取消。** 两把锁在同一个 `finally` 里释放；在轮询时被取消的等待者什么也没持有。一旦拿到锁，临界区 ——
   重新读取、token 请求、写入 —— 作为独立任务在 `asyncio.shield` 下运行，受 token 请求超时约束，因此被
   取消的工具调用不会在「已用掉旧 refresh token」和「写入新 token」之间放弃一次轮换（这个空档正是 §6.1
   的那类 bug）。被取消的调用方立即返回；被 shield 的任务继续完成、写入、释放。登出或登录的提交出于同样
   理由，也从不在写入中途被取消。

   **关闭。** `shield` 防的是调用方的取消，防不了事件循环停止。manager 的关闭流程（`client.py:1512-1543`，
   `_shutdown`）只等待 `self._calls` 中的调用任务，然后断开客户端并停止循环 —— 被 shield 的临界区不在其中，
   所以「刷新中途取消工具调用，紧接着 `disconnect_all()`」会在轮换尚未写盘时停掉循环（rev 7 的外部评审
   在现有 manager 上复现了这一点）。因此由 manager **持有**这些任务：每个被 shield 的临界区都登记到一个
   manager 持有的集合里（完成即移除），`_shutdown` 在等完调用**之后**、断开客户端和停止循环**之前**等待
   这个集合，上限为 token 请求超时。`disconnect_all` 的外层期限（目前是
   `timeout + _CANCEL_WAIT_S + _OWNER_STOP_S + 1.0`，`:1488`）也加上同样的上限，否则线程 join 会提前放弃，
   在刚加上的等待中途停掉循环。不需要新的调度层：一个集合加一次 `asyncio.wait`，就放在现有等待调用的那个
   旁边。

   这覆盖不了的是：进程在收到 token 响应之后、写入之前死掉（第二次 Ctrl+C、kill）。后果是有限的：记录里
   仍是那个旧的、已被用掉的 refresh token，下一次刷新得到 `invalid_grant`，服务器报告 `needs_auth`，记录
   保留（§5.3 第 3 步）—— 多登录一次，绝不会出现损坏或被删除的记录。
4. **收到带 `insufficient_scope` 的 403 时：** `needs_auth`，消息里写明被挑战的 scope，并直说本版本无法
   请求额外的 scope：重新登录请求的是服务器 401 挑战所列的 scope，未必包含它，所以重新登录未必能消除这个
   错误。不自动重新登录，不重试循环。（step-up 并集见 D7。）

这是同类项目各自独立收敛到的部分：codex 和 goose 在连接时提前 30 秒刷新，gemini 提前 5 分钟；codex 区分
「无法刷新」和「刷新失败」（PR #43947）。其规模大约是一个基于 httpx Auth 协议的 ~200 行模块，两个 httpx
大版本共享这一协议。

### 5.4 什么时候才用 OAuth

- **stdio** 服务器永远不用 OAuth（规范：凭据来自环境）。
- `headers` 里已经有 `Authorization` 的 URL 服务器永远不用 OAuth；那里的 401 是普通失败。（Claude Code
  对同样情况报告的是「failed」，而不是「needs auth」。）
- 其余情况 **`StoredTokenAuth` 总是挂上**，不论有没有记录。有记录时它附上并刷新 token（§5.3）。没有记录时
  它不发送 `Authorization`，只**观察**：带有 `WWW-Authenticate: Bearer` 挑战的 401 会设置 `needs_auth`
  判定（§5.3 第 2 步），服务器报告 `needs_auth`。rev 6 只在有记录时才挂上它，并指望 `_fail_connect` 发现
  挑战 —— 但到那时传输层已经丢弃了状态和响应头（2.0："Server returned an error response"），所以首次连接
  根本无法与其他失败区分。在认证对象里观察，不需要额外的探测请求。不带挑战的 401 —— 典型情况是服务器要求
  一个用户还没配置的 API key 请求头 —— 什么也不设，仍是 `ERROR`：让这样的用户去运行 `/mcp login`，只会把
  他们带进一次在发现阶段就失败的登录。服务器配置里的 `"oauth": false` 表示什么都不挂。
- 内容类型预检（`client.py:829`）**永远不带认证。** 它在两个大版本上都用普通 `httpx`，而 `StoredTokenAuth`
  在 2.x 上是 `httpx2.Auth`，一个对象没法两边通用；而且预检本来就把所有非 2xx 放行给真正的握手，所以未带
  认证地探测受保护服务器（401）的结果与今天完全一样。在那里挂认证，还可能让一次探测触发刷新。
- 可选的每服务器配置，所有键都可选：
  `"oauth": {"client_id", "client_secret", "callback_port", "redirect_host"}`。没有 `scopes` 键：SDK 会
  用 401 挑战的 scope 替换请求的 scope（§5.5 第 4 步），所以这个键会被接受然后被忽略。
  `client_secret` 像 `headers` 一样支持 `$VAR` 展开，并且永远不会被 `/mcp add` 写回。

### 5.5 `login(name, ui)`

步骤顺序，每一步都对照 S5 的观察或评审意见确定：

1. **先准备回调。** `ui.prepare(preferred_port)` 绑定监听器，返回实际的 `redirect_uri`。
   `preferred_port` 是已存注册所用重定向 URI 的端口，所以重新登录通常复用它；配置中的 `callback_port`
   优先。
2. **决定向 SDK 提供哪个注册。** 这里只判断能在发现*之前*判断的事 —— 第 1 步得到的 `redirect_uri` 是否在
   已存注册的 `redirect_uris` 之中。SDK 不检查这一点（它总是发送我们传入元数据的 `redirect_uris[0]`，
   `oauth2.py:397,452`，并复用存储返回的任何客户端信息），所以端口一变，就会带着一个授权服务器从未登记的
   URI 过去。若不在其中：配置的 `client_id` 让登录失败，消息提示设置 `callback_port`；动态注册的客户端
   不提供，于是 SDK 重新注册。

   **注册是否属于当前授权服务器，不在这里判断** —— 当前 AS 要等 SDK 发现之后才知道。在 SDK 按 issuer 绑定
   注册的版本上（1.30、2.0：它把 issuer 记录进客户端信息，发现后按字符串精确比较，不符则丢弃），已存的
   动态注册**只有在其 `issuer` 不为空时**才提供，然后由 SDK 决定。`issuer` 为空的永远不提供：SDK 把空
   issuer 当作与所有 issuer 都匹配（§3.1 注意事项），提供它就等于把一个 AS 的客户端交给另一个 AS —— 这一步
   要排除的正是这种情况。这涵盖了 SDK 经资源源站 `/register` 回退路径做的注册，以及在 1.26 下保存的所有
   注册 —— 在已有记录的情况下升级 SDK 时，后者就会冒出来。在 1.26 本身上，因为没有绑定，动态注册的客户端
   **永远**不提供：每次登录都重新注册。登录是显式且少见的。配置的 `client_id` 是用户为这个服务器预注册的，
   总是提供。
3. **以零 token 运行 SDK provider。** 它的存储在 `get_tokens()` 上返回 `None` —— 旧 token 永远不加载，
   所以第一个请求不带 `Authorization`，服务器回 401，流程开始运行。若加载旧 token，握手会用旧 token 成功，
   登录就悄悄什么也没做。（S5：空存储在两个大版本上都触发 DCR → 授权 → token。）
4. **Scopes：** SDK 从 401 挑战中选取 scope，并**覆盖**客户端元数据里原有的值（`oauth2.py:691`；1.26 上
   是同一调用）。S5 实测：元数据 scope 为 `a b step`、挑战为 `a` → 授权 URL 请求的是 `a`。所以 `login()`
   请求的是服务器挑战的那些，step-up 并集见 D7。
5. **事后从 provider 构建记录：** token 来自它的存储；token 端点和支持的认证方式来自
   `provider.context.oauth_metadata`；issuer 取 `context.auth_server_url`，仅在它未设置时回退到
   `str(oauth_metadata.issuer)` —— 与 SDK 给注册打标时用的是同一表达式（`oauth2.py:703`），因此记录与它
   所含的注册对 AS 的称呼完全一致。这些是内部属性，1.26 和 2.0 上都有（S5）；`login()` 通过 `_compat`
   探测读取它们，缺失则**让登录失败**，而不是写入一条无法刷新的记录。issuer **按 SDK 报告的原样**存储，
   并精确比较 —— SDK 自己的绑定和 RFC 9207 `iss` 校验都是精确字符串比较（`utils.py:354`、`:254`），
   RFC 8414 的 issuer 标识符也按字符串比较。规范化（比如去掉末尾的 `/`）可能让两个不同 issuer 的记录看起来
   是同一个。不做规范化的代价已知且很小：1.26 的模型把 issuer 渲染为 `http://host:port/`，2.0 渲染为
   `http://host:port`（S5），所以切换 SDK 大版本后，第一次登录会看到一个不同的 issuer，于是替换记录并
   重新注册。
6. **在记录锁下提交记录。** 只有这一步持有锁；等待浏览器时不持有。
7. **关闭 UI**（成功、失败、超时、取消时都关闭监听器），并**重连**该服务器的普通连接。如果该服务器的
   工具在启动时已注册（它曾连接成功，后来刷新被拒），重连就够了。对一开始就 `needs_auth` 的服务器，重连
   **不够**：MCP 工具只在构造 agent 时注册一次（`tooling/mcp_tools.py::init_mcp`），这个服务器没有工具可以
   恢复。首版如实告知 ——「已登录；重启 agentao 以加载 `<name>` 的工具」，与 `/mcp add` 给出的提示相同
   （`cli/commands/mcp.py:107`）。运行时注册工具见 D8。

`logout(name)`：等待记录锁（进行中的刷新先完成），删除凭据文件，释放；然后断开该服务器、丢弃内存中的
`StoredTokenAuth`。此后开始的刷新会在锁内重新读取、发现没有记录、报告 `needs_auth` —— 它不可能把已登出的
记录写回去。

---

## 6. 存储

### 6.1 要防范的那类 bug

在五个同类项目中，最常见的 OAuth 缺陷是同一个：**两次刷新用掉同一个会轮换的 refresh token**，输的一方
删除或覆盖了有效凭据。codex 做了跨进程刷新锁（PR #42413），仍有未解决的竞态（#45944、#46028、#48507）；
gemini 在任何刷新失败时都删除凭据（#29048）；opencode 整文件重写会丢掉其他服务器的 token（#46128、
#42875）。SDK 只在单个 provider 内加锁。agentao 具备触发它的多进程形态：CLI、ACP 服务器和嵌入式宿主可能
共用一个 home 目录。

### 6.2 凭据记录

- **每个服务器 URL 一个文件**：`user_root() / "mcp-oauth" / <sha256(规范化的服务器 URL)>.json`
  （`paths.user_root()`，即 `~/.agentao`，与其他所有用户级文件的解析方式相同），权限 0600（目录 0700），
  原子写入（临时文件 + `os.replace`，与 `LocalFileSystem` 相同）。以 **URL 而不是服务器名** 为键 —— 按名称
  分键的存储（gemini、goose）会让 token 跟着一个被改名或改指向的服务器走。
- **issuer 被记录并绑定，但不是键的一部分。** 普通连接只知道 URL，按 issuer 分文件会让它无法在多个文件间
  选择。2026-07-28 的规则（「每个授权服务器用独立凭据」）通过「绝不把一条记录的凭据交给其他 issuer」来满足：
  issuer 与记录不同的登录会**替换**该记录；服务器换了授权服务器，在稳态下表现为刷新失败 → `needs_auth` →
  一次替换它的登录。首版不保留跨 issuer 的历史。
- **内容：** 服务器 URL、`resource`、issuer（按报告原样，§5.5 第 5 步）、`token_endpoint` 和 AS 支持的
  token 端点认证方式、注册时的客户端信息（包括其 `redirect_uris`）、access token、**绝对**过期时间、
  refresh token（如有）、已授予的 scope。绝对过期时间和 token 端点正是 SDK 存储协议承载不了的（F3；上游
  PR #2492 补上了后者）—— 这就是为什么记录是我们自己的，而不是一个 `TokenStorage`。
- 记录写明它是为哪个 URL 写的，不匹配时忽略（opencode 的 `getForUrl` 保护）。
- **每条记录一把锁，所有写入都用：** 刷新（重新读取 → 请求 → 写入）、登录的提交和登出都拿记录的
  `filelock` —— 外面套一把每记录的进程内锁，且绝不在 MCP 循环上阻塞式获取（§5.3「在哪里拿锁」）—— 所以
  它们互不交错（§5.3、§5.5）。锁文件与凭据文件分开（`<hash>.json.lock`），并且**在记录删除后仍保留**：
  在另一个进程等待时删掉锁文件，会让两个持有者同时存在。
- **绝不因为刷新失败而删除记录**（§5.3）。`logout` 是唯一的删除。
- **不用操作系统钥匙串。** 它会增加一个依赖，以及同类项目反复踩到的一种失败模式（codex #34943、#41071、
  #32799）。0600 文件与 `~/.agentao/memory.db` 和 `.env` 中的提供商凭据是同一防护等级。
- **日志。** token 值绝不能进入 `agentao.log`。`secret_scan` 已经会脱敏 `Bearer` 值；该 PR 再增加针对
  JSON 和表单正文中 `access_token` / `refresh_token` / `code` / `code_verifier` 的规则，以及一个测试：完整
  登录和一次刷新之后，日志里不残留其中任何一个。
- **哪个 issuer。** 记录的 `issuer` 是 §5.5 第 5 步定义的值 —— SDK 的绑定键 `auth_server_url` —— 而不是
  `oauth_metadata.issuer`；「issuer 不同的登录替换记录」比较的就是这个字符串。

### 6.3 客户端注册的输入

按规范顺序：配置的 `client_id`（+ `client_secret`）→ CIMD，**仅当 agentao 发布了客户端元数据文档（D2）**
→ 使用 `application_type: "native"` 且 `grant_types` 包含 `refresh_token` 的 DCR。没有 D2 时，实际顺序是
配置 → DCR，这正是 gemini-cli 和 opencode 目前的做法。注册得到的客户端存入记录，只在 §5.5 第 2 步的条件下
提供给下一次登录：重定向 URI 仍然匹配、SDK 按 issuer 绑定注册（1.30、2.0）、且已存注册的 `issuer` 不为空
—— 在 1.26 上，或 `issuer` 为空时，动态注册永远不提供。

### 6.4 凭据档案（0.5.11）

只按 URL 区分意味着一个 URL 只能有一个登录：`mcp.json` 里指向同一 URL 的两个条目（工作账号和个人账号），
或连接同一 URL 的两个项目，会共用最后登录的那个账号，因为凭据按用户存储。可选的 **`oauth.profile`**
为同一 URL 指定一份独立的凭据。

- **键。** 不设档案时，键是规范化后的 URL，与 §6.2 完全相同，因此档案出现之前写入的记录仍在原来的文件名下，
  升级不会让任何人退出登录。设了档案时，键是规范化后的 URL、一个换行符、`profile=<名字>`。URL 中不会出现换行符，
  所以任何 URL 都拼不出另一个 URL 的档案键。文件名是键的 SHA-256；锁文件和进程内锁用同一个键。
- **记录写明自己的档案**，加载时与 URL 一起校验（§6.2 的防护），因此复制到另一份凭据文件名下的文件会被忽略。
  没有该字段的记录就是默认凭据。记录格式仍是版本 1：旧版 agentao 不会打开带档案的文件，因为文件名不同。
- **显式启用，不按 server 名区分。** Pi 1.0 按 server 名 + URL 区分；§6.2 已经否定了按名字区分，因为 token
  会跟着改名或改指向的条目走，改条目名也会让它退出登录。档案是*账号*的名字，所以不同项目里的条目可以有意共用
  一个档案，改条目名也没有影响。
- **不回退。** 设了档案的条目从不读取默认凭据或其他档案的凭据：借用就是以错误的账号操作。它的初始状态是
  `needs login`。
- **所有路径都用这个键。** 稳定状态下的加载和刷新（`StoredTokenAuth`）、登录时读取已存注册和提交、退出登录，
  以及在另一个进程登录后唤醒 server 的 `needs_auth` 复查。每个档案的记录各自保存自己的客户端注册。
- **登录用哪个账号由浏览器决定。** agentao 对每个档案发出相同的授权请求；已经登录的身份提供方可能直接用当前账号
  完成授权。`--no-browser` 会打印 URL，可以在另一个浏览器档案里打开。
- **校验**与其他键一样失败即拒绝：非空字符串，首尾不能有空白，精确比较（`Work` 和 `work` 是两个档案）。

测试：`tests/test_mcp_oauth_profiles.py`。

---

## 7. CLI 的认证 UI

| 选择 | 方案 | 同类项目证据 |
|---|---|---|
| 绑定地址 | 只绑 `127.0.0.1`，始终如此 | gemini 绑定所有网卡（`oauth-flow.ts:369`）；opencode #41255 |
| 端口 | 默认由操作系统分配；需要精确登记 URI 的身份提供方用 `callback_port` | goose PR #9209，Claude Code `--callback-port` |
| 重定向主机 | 默认 `localhost`，可用 `redirect_host` 覆盖 | Claude Code v2.1.229 改成 `127.0.0.1`，弄坏了精确匹配的服务器，v2.1.231 回退 |
| 端口被占用 | 失败并在消息中写明端口；绝不假定是另一个进程占着 | opencode 的 19876「被占用即另一个 opencode」会让回调落进另一个进程 |
| 路径 | `/callback/<server-id>`，两个并发登录不会收到彼此的 code | codex `oauth_callback.rs`（AS 不支持 RFC 9207 时的混淆防御） |
| 超时 | 300 秒，然后关闭监听器 | goose PR #9536（WSL 卡住）、gemini #28279（监听器未关） |
| 无浏览器 / SSH | `webbrowser.open` 失败或使用 `--no-browser` 时：打印 URL，接受粘贴的重定向 URL（隐藏输入、限制长度），同时监听器继续等待 | codex PR #44629；Claude Code 文档 |

命令：`/mcp login <name>`、`/mcp logout <name>`（删除该服务器的记录），`/mcp list` 在状态旁显示
`needs login`。`agentao mcp login <name>` 子命令 —— 同样的交互式流程，只是从 shell 而不是 REPL 运行 ——
覆盖 ACP 的场景（§8.3）。

---

## 8. 各个入口

### 8.1 CLI 启动

`connect_all()` 只用 `StoredTokenAuth`。需要登录的服务器在启动时列出一次（「`linear` 需要登录 —— 运行
`/mcp login linear`」），与 codex 和 opencode 一样。启动时不会打开浏览器，并且在要求任何人登录之前会先用
已存的 refresh token（F3/F4 正是这部分必须由我们自己来做的原因）。

### 8.2 嵌入式宿主

推迟。嵌入式宿主免费获得 `StoredTokenAuth` 和 `needs_auth` 状态（凭据存储与 CLI 共享，所以在终端里运行
`agentao mcp login` 对它同样有效）。公开的登录入口 —— 把 §5.1 的 UI 接口移入 `agentao.host.protocols`，
再在 `Agentao.__init__` 的 `*` 之后加一个凭据存储工厂参数 —— 等到有宿主需要在自己的 UI 里运行登录时再做。

### 8.3 ACP

ACP 会话不能发起流程：编辑器通过 stdio 驱动 agent，没有终端可读（gemini 的无头授权读的是 **stdin**，在
stdio 协议上那会读到 JSON-RPC 通道）。来自 agentao 自己 `mcp.json` 的服务器报告 `needs_auth`；用户在终端
里运行 `agentao mcp login <name>`，写入的记录会在 ACP 进程下次连接时被读到。

**编辑器**在 `session/new` 中传入的服务器由编辑器负责授权。rev 2 假定它们带着编辑器的 headers；其实不一定
—— 翻译器只在编辑器确实发来 headers 时才设置 `headers`（`acp/mcp_translate.py:231-235`）。如果没有显式退出，
默认规则（§5.4）会把 agentao 已存的 token 附到 URL 相同的、编辑器提供的服务器上。所以翻译器给它产生的每个
服务器都设置 `"oauth": false`。

## 9. Spike 结果

脚本和夹具放在仓库之外（scratchpad）；每个都小到可以在实现 PR 中重建为测试。

### S1 —— provider 会把并行调用串行化吗？**会，在 JSON 模式下。**

一个只有一个工具（`slow`，睡 2 秒）的服务器，分 JSON 响应和 SSE 响应两种模式；一个客户端在同一会话上发
两个并发调用，分别带和不带 provider（有效的已存 token，交互式 handler 会抛异常）。

| SDK | 模式 | 不带 provider | 带 provider | 带 provider，锁已缩小 |
|---|---|---|---|---|
| 2.0.0 | JSON | 2.05 s | **4.05 s** | 2.05 s |
| 2.0.0 | SSE | 2.05 s | 2.05 s | — |
| 1.26.0 | JSON | 2.02 s | **4.02 s** | 2.02 s |
| 1.26.0 | SSE | 2.02 s | 2.02 s | — |
| 1.30.0 | JSON | 2.02 s | **4.04 s** | — |
| 1.30.0 | SSE | 2.04 s | 2.03 s | — |

JSON 模式下带 provider 时，**两个**调用都在 4 秒完成 —— 第一个调用的结果也被拖住了。「锁已缩小」是一个测试
用子类，只在加载 token 和加头时持锁；它展示的是修法，不是生产代码。上游修复（#2858）无法运行：其 head 在
当前 `main` 上 import 失败。

### S2 —— 重启后 token 已过期会怎样？**交互式登录；从未尝试刷新。**

一个提供 PRM、AS 元数据、DCR 和 token 端点的假源站，其 MCP 端点总是回 401。客户端存储返回一个过期的
access token **和一个 refresh token**；token 端点依次配置为回 `400 invalid_grant`、回 `503`、以及直接断开连接。

在 2.0.0 和 1.26.0 上，三种配置下服务器看到的都是同样四个请求 ——
`GET /.well-known/oauth-authorization-server`、`POST /mcp`（带着过期 token）、`GET` PRM、`GET` AS 元数据
—— 然后客户端调用了 `redirect_handler`。`/token` 从未被请求，所以三种刷新结果甚至无从区分。handler 的
异常以包着它的 `ExceptionGroup` 出现。这就是 F3 + F4，也是刷新由我们来做的原因（§5.3）。

### S5 —— 一次登录是什么样子？**从空存储走完整流程；scope 取自挑战。**

一个假源站，提供 PRM（`scopes_supported: a b step`）、AS 元数据、DCR、一个直接带 code 重定向回来的
`/authorize`，以及 token 端点；MCP 端点在看到签发的 token 之前一直回带 `scope="a"` 的 401。重定向 handler
扮演浏览器（请求授权 URL，从 302 中读出 code）。2.0.0 和 1.26.0 结果相同：

| 变体 | 注册时的 scope | 授权请求的 `scope` | 说明 |
|---|---|---|---|
| 元数据无 scope | `a` | `a` | DCR → 授权 → token；两处都发送了 `resource` |
| 元数据 scope 为 `a b step` | `a` | `a` | **被挑战覆盖** |
| patch scope 选择函数以加入 `a b step` | `a b step` | `a b step` | 可行，但 patch 的是 SDK 模块级函数（D7） |

运行之后，两个大版本上 `context.auth_server_url`、`context.oauth_metadata`（issuer、token 端点）和
`context.token_expiry_time` 都已填充；issuer 在 2.0.0 上是 `http://127.0.0.1:<port>`，在 1.26.0 上是
`http://127.0.0.1:<port>/`。

### 仍待验证

| # | 问题 | 计划 |
|---|---|---|
| S3 | SSE 传输配合 `StoredTokenAuth` | 同一个假服务器，`type: "sse"`；属于 PR 1 的测试。**已在 PR 1 中运行：2.0.0、1.26.0、1.30.0 均通过** |
| S4 | 两个进程刷新同一个会轮换的 token，以及刷新与登出竞争 | 两个进程、一个立即过期的 token、一个用过即作废旧 refresh token 的 token 端点；然后在一次慢刷新期间登出；属于 PR 1 的测试。**已在 PR 1 中运行：只发出一次 token 请求，两个进程最终都持有新 token；登出会等待刷新完成** |

## 10. 不在范围内

- *Client Credentials*（草案）和 *Enterprise-Managed Authorization* 扩展。
- 设备流（规范中没有）、操作系统钥匙串存储（§6.2）、stdio 服务器的 OAuth（规范说不要）。
- 为 ACP 编辑器传入的服务器做授权（§8.3）。
- 任何对静态 `headers` 工作方式的改动。
- 在上游修复 F1–F4。值得在 #2858 和 #2875 下附上 S1/S2 的数据留言；不是依赖项。

---

## 11. 交给维护者的决策

| # | 决策 | 选项 | 建议 |
|---|---|---|---|
| **D1** | 接受修订后的边界（harness：稳态认证、存储、不变式、`login()`；宿主：UI；CLI 实现一个 UI），取代七月「只保留原则」的结论 | 接受 / 维持七月结论 | 接受，并更新 `openworker-borrow-review.zh.md` §9 指向这里 |
| **D2** | 发布 CIMD 文档（例如 `https://agentao.cn/oauth/client.json`） | 是 / 否 | 首个 PR 不做。它需要一个我们承诺长期稳定的 HTTPS URL，以及与我们所用每种回调形态都匹配的 `redirect_uris`（opencode #50510 发布的那份就不匹配）。DCR 今天就能用：gemini-cli 和 opencode 只用 DCR，codex 和 goose 回退到它 |
| **D3** | 没有 `Authorization` 头的 URL 服务器启用 OAuth | 默认开启（带 `Bearer` 挑战的 401 标记 `needs_auth`；使用已存记录）/ 每服务器选择开启 | 默认开启 —— 服务器不要求就什么也不做，不带 `Bearer` 挑战的 401 仍是 `ERROR`（§5.4） |
| **D4** | OAuth 要求 2.x SDK | 要求 ≥2 / 两个都支持并记录 1.x 的缺口 | 两个都支持；在 1.x 上登录时记一次日志说明 `iss` 校验不可用（1.26 和 1.30 都是 —— 已核实），在 1.26 上说明注册不会复用（§5.5 第 2 步）。稳态是我们自己的，在两个版本上完全一样 |
| **D5** | 面向嵌入式宿主的公开登录入口 | 现在 / 有宿主提出时 | 有宿主提出时（§8.2）。首版只有内部实现加 CLI |
| **D6** | 稳态认证 | (a) 我们自己的 `StoredTokenAuth`（§5.3）· (b) 子类化 SDK provider 来修 F1–F4 · (c) 原样使用 provider，等上游修 | **(a)。** (b) 要覆盖私有方法（`_initialize`、`async_auth_flow`），而它们在 1.26、1.30（`RedirectAwareAuth`）和 2.0 之间各不相同 —— 正是 `_compat.py` 存在所要避免的那种耦合。(c) 会把 F1–F4 交给用户：每个进程每小时重新登录一次，以及被串行化的 JSON 模式服务器。选 (a)，**稳态**只触及公开的 `httpx.Auth` 协议和我们自己拥有的记录；**登录仍要读 provider 的内部 `context`**（§5.5 第 5 步），通过一个缺失即让登录失败的 `_compat` 探测，并由两个大版本上的测试钉住 |
| **D7** | 登录时的 step-up scope 并集 | (a) 请求服务器 401 挑战的 scope；403 `insufficient_scope` 在状态中写明缺少的 scope · (b) 在 `login()` 期间包装 `mcp.client.auth.oauth2.get_client_metadata_scopes` 以加入记录的 scope（S5：两个大版本上都可行） | **先 (a)。** (b) patch 的是模块级函数 —— 进程级、私有，且需要一把锁防并发登录。等发现一个真实服务器在 401 时挑战的 scope 少于它之后在 403 时要求的，再重新考虑 |
| **D8** | 启动时就需要登录的服务器的工具 | (a) `login()` 之后在运行时注册 · (b) 登录提示「重启 agentao 以加载它的工具」 | **先 (b)。** MCP 工具只在构造时注册一次（`tooling/mcp_tools.py::init_mcp`），`/mcp add` 已经要求重启。(a) 是对工具注册表的运行时改动 —— 涉及模型看到的工具块、prompt 缓存前缀、之后派生的子代理 —— `/mcp add` 同样会需要它，应作为单独的改动来做。工具已注册过的服务器（曾连接、后来刷新被拒）只靠重连就能拿回工具（§5.5 第 7 步） |

---

## 12. 测试计划

- **一个假授权服务器和受保护的 MCP 服务器**，进程内运行，在 S2 夹具基础上扩展（PRM、AS 元数据、DCR、签发
  code 的 `/authorize`、行为可切换的 `/token`）。协议对象用 SDK 自己的模型 —— SDK 要解析的东西一律不用
  `MagicMock`。CI 中跑两个 SDK 大版本，与现有 `mcp-compat` 作业相同。
- **不变式：** 普通连接永不构造 `OAuthClientProvider` —— 通过 patch 其构造函数让它抛异常来断言，覆盖启动
  连接、重连、过期后的工具调用和一次 403 step-up；每种情况都以 `needs_auth` 或成功结束，绝不走到 UI 的
  `open`。一个变异检查（在重连时构造它）必须让某个测试变红。
- **带过期 token 重启**（S2 的形态）：使用已存的 refresh token，`/token` 被请求一次，不出现 `needs_auth`。
  然后逐一检查刷新结果：`invalid_grant` → `needs_auth`，记录保留；503 和断开连接 → 普通错误，记录保留，
  不是 `needs_auth`。
- **刷新合并：** 不含 `refresh_token` 的响应保留旧值，随后第二次刷新成功；不含 `scope` 的响应保留已授予的
  scope；不含 `expires_in` 时一直用到收到 401。
- **登录：** 不加载旧 token（已有有效旧记录时，登录仍会走到 `/authorize`）；回调端口变化时，DCR 客户端重新
  注册，配置的客户端失败并给出 `callback_port` 提示；登录后构建的记录在两个大版本上都有 issuer、token 端点
  和过期时间，issuer 按报告原样存储（1.26 下写、2.0 下读的记录在下一次登录时被替换，而不是合并）；在 1.26
  上，已存的 DCR 注册永远不提供；**在 2.x 上，`issuer` 为空的已存 DCR 注册永远不提供** —— 用 SDK 自己的
  `OAuthClientInformationFull` 构建，一次按资源源站回退路径留下的样子、一次按 1.26 写入的样子 —— 针对*另一个*
  AS 的第二次登录会重新注册，而不是把它交出去；记录的 issuer 等于 `context.auth_server_url`；登录前已连接过的
  服务器靠重连拿回工具，启动时就 `needs_auth` 的服务器得到重启提示（D8）；成功、失败、超时、取消时都关闭
  监听器。
- **登出：** 在慢刷新期间发起的登出会等它完成后再删除记录；登出后开始的刷新报告 `needs_auth`；锁文件仍在
  （S4）。
- **Step-up：** 403 `insufficient_scope` 产生 `needs_auth`，带「无法请求额外 scope」提示，不重试。
- **会话中途被拒：** 刷新得到 `invalid_grant` 的工具调用返回登录提示，`get_server_status()` 中服务器为
  `NEEDS_AUTH` —— 不是 `CONNECTED`。
- **不带挑战的 401：** 回 401 但没有 `WWW-Authenticate: Bearer` 的服务器仍是 `ERROR`，不是 `needs_auth`。
- **预检：** 有记录时，预检请求不带 `Authorization` 头，也不触发刷新。
- **经由真实传输层的认证失败**（两个大版本，SDK 真实传输层对接假服务器 —— 绝不用抛出的假异常）：没有记录、
  首次连接到回 401 + `Bearer` 的服务器，以 `NEEDS_AUTH` 结束；回裸 401 的以 `ERROR` 结束；在 2.0 上，401 在
  刷新后依然存在的工具调用以 `NEEDS_AUTH` 结束，此时错误文本是 "Server returned an error response"，
  `classify_mcp_error` 判为 `OTHER` —— 如果标志只在 AUTH 分支读取，这个测试会失败；成功登录并重连后，标志
  已清除。
- **锁的位置：** 另一个进程持有某条记录的锁时，*另一个*服务器上的工具调用照常完成（循环没有被阻塞）；同一
  记录上有刷新进行中时发起的登录提交或登出会等待它 —— 对一个裸的可重入 `FileLock` 会失败的测试；刷新期间
  被取消的工具调用，留下已写入的轮换后 token，两把锁都已释放；**刷新期间取消工具调用、紧接着
  `disconnect_all()`**（token 端点被放慢，使刷新仍在进行）要等轮换后的 token 写盘、锁文件释放后才返回 ——
  对一个只等待调用的 `_shutdown` 会失败的测试；同一进程中两个 manager 刷新同一条记录，只发出一次 token 请求。
- **ACP：** 来自 `session/new`、没有 headers、其 URL 有记录的服务器，连接时不带 `Authorization` 头。
- **并行调用**（S1 的形态）：在 JSON 模式服务器上带 `StoredTokenAuth` 的两个并发 2 秒调用，在 3 秒内完成。
- **存储：** 0600/0700 权限（POSIX）；URL/issuer 不匹配时忽略；写入进程被杀时仍是原子写入；S4 的两进程竞争
  在 token 端点只留下一次刷新，两个进程都持有新 token。
- **日志：** 完整登录和一次刷新之后，`agentao.log` 中不残留 token、code 或 verifier。
- **CLI UI：** 绑定地址是 loopback；端口被占用时的消息；超时关闭监听器；隐藏输入的粘贴路径。

## 13. 实施计划

### 13.1 PR 1 之前

1. **批准。** *已完成（2026-10-02）：* 维护者批准构建，并认可 §11 的全部建议。
2. **本文档先落地**：rev 2–8 提交到 PR #396 并合入，使每个实现 PR 都能引用 `main` 上的设计。
3. **中文孪生** `mcp-oauth.zh.md`（即本文），以及 D1 的后续：`openworker-borrow-review.zh.md` §9 指向这里
   （*已完成* —— 它的汇总表和 §9 现在都写明已被本设计取代）。
4. **可选、对外、需要单独批准：** 在 python-sdk #2858 和 #2875 下附上 S1/S2 数据留言（§10）。这里没有任何
   东西依赖它。

### 13.2 首版包含什么

| 包含 | 不包含（以及论证所在） |
|---|---|
| agentao 自己 `mcp.json` 中 Streamable HTTP 和 SSE 服务器的 OAuth，默认开启（D3） | stdio 服务器；ACP 编辑器传入的服务器（§8.3） |
| 通过 SDK provider 登录：发现、预注册或 DCR 客户端、PKCE、`resource`、2.x 上的 `iss` | CIMD（D2）、Client Credentials 和 Enterprise-Managed 扩展（§10） |
| 我们自己的稳态：附加、过期前刷新及收到 401 时刷新一次、跨进程锁（§5.3） | 子类化或 patch SDK provider（D6） |
| 每个服务器 URL 一条记录，位于 `user_root()/mcp-oauth/`，即 `~/.agentao/mcp-oauth/`（§6.2） | 操作系统钥匙串（§6.2） |
| CLI：`/mcp login|logout`、`/mcp list` 中的 `needs login`、`agentao mcp login|logout`、loopback + 粘贴 | 面向嵌入式宿主的公开登录 API（D5、§8.2） |
| SDK 1.26 – 2.x，记录 1.x 的缺口（D4） | step-up scope 并集（D7） |
| 登录后重连；对没有已注册工具的服务器给出重启提示 | 运行时注册 MCP 工具（D8） |

### 13.3 PR 1 —— 认证模块与凭据存储

UI 以下的所有部分。合入后，回 401 的 URL 服务器报告 `needs_auth` 而不是 `error`，测试写入的记录会被使用和
刷新；但还无法从 CLI 创建记录。

| 文件 | 改动 |
|---|---|
| `agentao/mcp/oauth_store.py`（新） | 记录（§6.2）：由规范化 URL 得出的 `paths.user_root()` 下的路径、0600/0700、原子写入、URL 不匹配保护、独立的 `.lock` 文件；每记录的 `asyncio.Lock`（每个 manager 一套）、轮询的 `FileLock`、被 shield 的临界区（§5.3「在哪里拿锁」） |
| `agentao/mcp/oauth.py`（新） | `StoredTokenAuth`（§5.3），把 `needs_auth` 判定记录在它的 `McpClient` 上而不是抛异常；`login(name, ui)` 和 `logout(name)`（§5.5），只在 `issuer` 不为空时提供已存的动态注册；内部四步 UI 协议（§5.1）；401 / `invalid_grant` / 403 `insufficient_scope` 的分类 |
| `agentao/mcp/_compat.py` | 探测，绝不看版本字符串：`httpx.Auth` / `httpx2.Auth` 基类；回调返回形态（tuple 还是 `AuthorizationCodeResult`）；登录要读的 provider `context` 字段（`auth_server_url`、`oauth_metadata`），缺失即失败；SDK 是否按 issuer 绑定注册（1.26 对 1.30+） |
| `pyproject.toml` | `filelock>=3.0`（原为 `>=1.4.0`；§5.3） |
| `agentao/mcp/config.py` | `oauth` 键：`false`，或包含 `client_id`、`client_secret`（`$VAR`）、`callback_port`、`redirect_host` 的对象；像 `resolve_transport` 一样失败即关闭地校验 |
| `agentao/mcp/client.py` | `ServerStatus.NEEDS_AUTH`；决定服务器是否获得 `StoredTokenAuth` 的 §5.4 规则（所有符合 OAuth 条件的 URL 服务器，无论有无记录）；判定标志，在 `connect()` 中清除，在 `call_tool` 唯一的 `except` 中**先于** `classify_mcp_error` 读取、在 `_fail_connect` 中先于现有路径读取；把它作为 `auth=` 传给 `create_mcp_http_client`（Streamable HTTP，`:981`）和 `sse_client`（`:932`）—— 两个大版本都接受；预检（`:918`）**不带**认证（§5.4）；标志已设时，每个出口都设 `NEEDS_AUTH` 并给出登录提示（带 `Bearer` 挑战的 401，或刷新被拒）；供登录和登出使用的 `reconnect(name)`；manager 持有的被 shield 的刷新/提交任务集合，在 `_shutdown` 中于等完调用之后、断开连接之前等待，并把 `disconnect_all` 的期限（`:1488`）加上 token 超时（§5.3「关闭」） |
| `agentao/acp/mcp_translate.py` | 编辑器传入的每个服务器都设 `"oauth": false`（§8.3） |
| `agentao/security/secret_scan.py` | 针对 JSON 和表单正文中 `access_token`、`refresh_token`、`code`、`code_verifier` 的规则（§6.2） |

**测试** —— §12 中除 CLI UI 那一行外的全部：两个 SDK 大版本上的假授权服务器；不变式及其变异检查；带过期
token 重启及三种刷新结果；刷新合并；登录（不加载旧 token、端口变化、1.26 永不提供 DCR 客户端、2.x 永不提供
`issuer` 为空的客户端、记录的字段、重连、每种退出都关闭监听器 —— 通过测试用 UI）；登出与刷新竞争；step-up；
会话中途被拒；不带挑战的 401；不带认证的预检；锁的位置；ACP；3 秒内完成的并行调用；存储权限和被杀的写入进程；
日志。**S3**（SSE）和 **S4**（两个进程、一个会轮换的 refresh token）在这里首次运行。

**完成标准：** 测试套件和 `ruff check .` 在所有 CI 作业上都通过（包括 Windows 和 macOS：`filelock` 和
`os.replace` 是跨平台的部分；权限检查仅限 POSIX），并且变异检查能让某个测试变红。

### 13.4 PR 2 —— CLI 登录流程

| 文件 | 改动 |
|---|---|
| `agentao/cli/mcp_login_ui.py`（新） | §7 的 UI：监听 `127.0.0.1`，操作系统分配端口或 `callback_port`，路径 `/callback/<server-id>`，300 秒超时，`webbrowser.open` 或打印 URL，在监听器继续等待的同时接受隐藏输入、限长的重定向 URL 粘贴。只用标准库，所以与 `--login` 一样能在裸 `pip install agentao` 上运行（#388） |
| `agentao/cli/commands/mcp.py` | `/mcp login <name>`、`/mcp logout <name>`；`/mcp list` 中的 `needs login`（`:31` 目前只把 `connected` 标绿）；登录后若该服务器没有已注册的工具，提示「重启 agentao 以加载 `<name>` 的工具」（D8） |
| `agentao/cli/entrypoints.py`、`agentao/cli/subcommands.py` | `agentao mcp login|logout <name>`，与现有的 `plugin` / `skill` / `config` 子命令并列（`entrypoints.py:221-243`），供 ACP 和嵌入式用户使用（§8.3） |
| CLI 启动 | 每个需要登录的服务器一行提示（§8.1） |
| `agentao/cli/help_text.py` | 新命令 |

**测试** —— §12 的 CLI UI 那一行：loopback 绑定；端口被占用的消息；超时关闭监听器；粘贴路径；以及一次端到端
的 登录 → 工具调用 → 登出，对接 PR 1 的假服务器、经由真实 UI 和脚本化的浏览器。

### 13.5 PR 3 —— 文档

- `docs/reference/configuration.md` §5（`mcp.json`）：`oauth` 键和凭据目录；
  `developer-guide/{en,zh}/cli/8-mcp-acp-plugins.md`：登录、登出、`needs login`、ACP 和无头场景的路径；
  中英文都改。
- `developer-guide/{en,zh}/part-1/6-compared-to-vendor-sdks.md`：MCP 那一行（`:25`）去掉「no OAuth yet」，
  并把「MCP OAuth」从它与 Linux/容器沙箱、OpenTelemetry 共用的那句话里拿掉（`:45`）—— 改写这句话，而不是
  删掉；中英文都改。
- `CHANGELOG.md` `[Unreleased]`，以及 `CLAUDE.md` 的 MCP 一节。
- **三条局限**，写进文档和 CHANGELOG 条目，而不是只写「支持 OAuth」：SDK 1.x 上没有 RFC 9207 `iss` 校验，
  且 1.26 每次登录都注册一个新的动态客户端（D4）；登录通过 `_compat` 读取 SDK provider 的内部 `context`，
  由测试钉住（§5.5 第 5 步）；没有 step-up scope 并集 —— `insufficient_scope` 只报告、不解决（D7）。
  另加一条使用说明（不是协议层面的局限）：启动时就需要登录的服务器，要重启后才加载它的工具（D8）。

### 13.6 顺序与发布

PR 1 → PR 2 → PR 3，每个都在前一个合入后再对 `main` 开（叠在其他分支上的 PR 不跑 CI）。三个 PR 在**同一个
版本**中发布；PR 1 和 PR 3 之间不发版，因为单独发布 PR 1 会让用户看到 `needs_auth`，却没有能消除它的命令。

### 13.7 风险

| 风险 | 如果发生 |
|---|---|
| S3 失败：`sse_client(auth=)` 驱动我们 401 重试的方式与 Streamable HTTP 不同 | SSE 上的 OAuth 移出首版，文档如实说明；旧式 SSE 是少数传输 |
| S4 暴露锁的缺口（比如刷新超出了它的 token 请求超时） | 在 PR 1 合入前修复 —— 这正是 §6.1 要防的那类 bug，所以不推迟 |
| 新的 SDK 版本挪动了 `login()` 要读的某个 `context` 字段 | `_compat` 探测让登录失败并给出消息；稳态不受影响，已登录的服务器照常工作 |
| 上游修好 F1–F4 | 什么都不用改：稳态从未使用 provider。只有在哪天丢掉我们的模块反而更简单时，才重新考虑 D6 |
| 未来的 SDK 改变空 `issuer` 的含义，或改用 `oauth_metadata.issuer` 而不是 `auth_server_url` 打标 | 用 SDK 自己的模型构建的 2.x 登录测试（§12）会变红；§5.5 第 2、5 步跟随 SDK 行为，绝不看版本字符串 |

---

## 附录 A —— 修订记录

**Rev 1（2026-10-02）** 把 SDK 的 `OAuthClientProvider` 接到了每一条连接上。

**Rev 2（同日）—— spike 改变了架构。** rev 1 把 SDK 的 `OAuthClientProvider` 接到了每一条连接上。实际运行
（§9）表明，provider 的*稳态* token 处理有三个缺陷，每个都有未关闭的上游 issue 和未合入的修复，在 agentao 支持
的每个 SDK 版本上都存在：它把以 JSON 模式响应的请求串行化（实测 2 秒 → 4 秒），它在重启后忘记 token 的过期
时间，它对 401 的反应是完整的交互式登录而不是使用已存的 refresh token（实测：`/token` 从未被调用）。rev 2
**只在显式登录时**使用 SDK provider；普通连接使用我们自己的一个小 `httpx.Auth`，负责附上已存 token 并刷新它
（§5.3）。这也让 §5.2 的不变式从约定变成了结构性保证。

**Rev 3（同日）—— 补上 rev 2 隐含未写的行为契约。** 对 rev 2 的评审接受了架构，并要求五项契约；每项都对照
SDK 核对过，凡依赖 SDK 行为的都实测过（§9 S5）：`login()` 如何对待旧 token 和请求的 scope（§5.5）、刷新响应的
合并规则（§5.3）、一把锁下的回调 / 登录 / 登出生命周期（§5.5、§6.2）、每个服务器 URL 一条记录（§6.2），以及
ACP 提供的服务器显式退出（§8.3）。面向宿主的公开部分（`agentao.host` 中的认证 UI 协议、凭据存储工厂）推迟；
首次实现由三个内部部分组成：认证模块、凭据存储、CLI 登录流程。

**Rev 4（同日）—— 第 3 轮评审的三处局部修正，架构不变。** issuer 保持协议携带的原样字符串，绝不规范化：SDK
精确比较它们，规范化可能让两个不同 issuer 的注册看起来是同一个（§5.5）。去掉了代数计数器：刷新从重新读取到
写入都持有记录锁，登录或登出不可能落在其中，计数器没有可检测的东西（§5.3、§6.2）。step-up scope 并集的推迟
（D7）也贯彻到底 —— §4 和 §5.4 不再承诺它，删除了 `scopes` 键，并写明了这条局限（§5.3）。核对第 1 点时还修正
了 §3.1：按 issuer 绑定注册在 1.30 和 2.0 上存在，**在 1.26 这个下限上不存在**。第 4 轮评审以无阻塞项通过
设计，建议构建；它唯一的补充在 §13 —— 文档与代码同版本发布，并写明三条局限。

**Rev 5（同日）—— 整理，并写出计划。** 设计无改动。修订说明从文件头移到这里；状态记录评审已通过；§13 成为
实施计划（前置条件、内容、每个 PR 的文件、发布规则、风险）；文档目标修正为 `configuration.md` §5 和 CLI 指南
第 8 章；§6.3 和 §7 与 §5.5、§6.2 对齐。

**Rev 6（同日）—— 对照代码和已安装 SDK 对 rev 5 做的反向评审。** 第 4 轮评审以无阻塞项通过了 rev 5；拿它的
论断对照 `main` 和 SDK 源码核对，查出计划中的三个缺陷和三个较小问题。架构（D6 (a)）不变。

1. **issuer 为空的已存注册会被提供给任何 AS。** rev 5 在 SDK 绑定 issuer 的地方「原样提供已存的动态注册，
   由 SDK 决定」—— 但 2.0 的 `credentials_match_issuer` 对空 issuer 一律放行，而 SDK 在资源源站 `/register`
   回退路径之后不填 issuer；1.26 写入的注册则根本没有。现在只在 `issuer` 不为空时提供（§3.1 注意事项、§5.5
   第 2 步、§6.3）。同一次阅读还确定了记录所称的 issuer 是哪个值：SDK 绑定的是 `auth_server_url`，不是
   `oauth_metadata.issuer`（§5.5 第 5 步、§6.2）。
2. **工具不重启就无法出现。** rev 5 承诺重连会带回新登录服务器的工具；但工具只在构造时注册一次，启动时就
   `needs_auth` 的服务器没有工具可带回。首版提示「重启」，与 `/mcp add` 一致；运行时注册成为新的 D8。
3. **记录锁在 MCP 循环上。** 在那里阻塞式 `FileLock.acquire` 会卡住所有服务器，而且 `FileLock` 在同一线程内
   可重入 —— 实测：同一线程上的两个协程同时持有同一个实例。现在锁在循环之外获取，外面套一把每记录的进程内锁
   （§5.3）。
4. **会话中途被拒时服务器仍是 `CONNECTED`** —— `call_tool` 的 AUTH 分支只返回文本。`needs_auth` 现在是记录在
   客户端上的判定，在两个出口处读取（§5.3 第 2 步）。
5. **预检带不了认证对象**（两个大版本上都是普通 `httpx`；2.x 上是 `httpx2.Auth`），也没有理由带；现在不带
   （§5.4）。
6. **不带 `Bearer` 挑战的 401** 不再被当作 `needs_auth`（§5.4、D3）。

另外：`agentao mcp login` 改述为「从 shell 运行的交互式流程」，而不是「非交互式」（§7）；记录路径改为
`paths.user_root()`（§6.2）；§2 增加了四行 grep 核实的事实、修正了一个行号；PR 3 的文档目标点名了 `:45` 那句
共用的话。核实无误、保持不变的：F3 在 1.26/1.30/2.0 上都成立，两个 1.x 都没有 `iss` 校验，issuer 绑定在 1.26
上不存在、在 1.30 上存在，以及 §5 引用的每一处 `oauth2.py` 行号。

**Rev 7（同日）—— 外部评审对 rev 6 的意见：两条 P1，一种锁方案。** 范围不变；评审结论是「修完这两条就实现」。
两条都先对照已安装 SDK 核实后才采纳。

1. **没有记录的首次连接无法被识别为 `needs_auth`。** rev 6 只在有记录时挂 `StoredTokenAuth`，并让
   `_fail_connect` 去找 `Bearer` 挑战 —— 但 mcp 2.0 的 Streamable HTTP 传输层会把任何非 404 的错误状态替换为
   `"Server returned an error response"`（`streamable_http.py:342-370`），状态和响应头都到不了 `_fail_connect`。
   现在 `StoredTokenAuth` 挂在每个符合 OAuth 条件的 URL 服务器上，没有记录时不发 token、只观察 401（§5.4）。
   不需要额外的探测请求。
2. **在 `call_tool` 的 AUTH 分支读判定，会漏掉 2.0 上的所有认证失败。** 同一段文本被分类为 `OTHER`，那个分支
   根本走不到。现在标志在唯一的 `except` 中、先于任何分类读取，并在连接时清除（§5.3 第 2 步），附带一个经由
   真实传输层的测试（§12）。
3. **锁方案定下来了**，不再给出两种：所有记录写入在 MCP 循环上、每记录一把 `asyncio.Lock`、轮询的非阻塞
   `FileLock`（在 filelock 3.0.0 和 3.25.2 上核实；下限提到 `>=3.0`），以及被 shield 的临界区，使取消无法让
   一次轮换半途而废（§5.3）。

未采纳：评审建议把正文压缩为「流程、存储与锁、首版边界、验收条件」，同类项目和历史移到附录 —— 这是值得在写
中文孪生之前做的一次编辑整理，不是设计改动。

**Rev 8（同日）—— 外部评审第二轮：一条 P1。** 它确认了 rev 7 的两处修正，并发现 `asyncio.shield` 能让刷新
免受调用方取消的影响，却防不了 manager 的关闭：`_shutdown` 只等待 `self._calls`，然后停止循环，所以取消后紧接
着 `disconnect_all()` 可能丢掉一次服务器已经完成的轮换。已通过阅读 `client.py:1512-1543` 核实。现在由 manager
持有被 shield 的任务并等待它们，外层期限相应延长（§5.3「关闭」），并附上复现它的测试（§12）。剩余情况 ——
进程在 token 响应和写入之间被杀 —— 写明了其有限的后果。按评审的提示澄清：`asyncio.Lock` 按 manager 划分（每个
manager 有自己的循环）；同一进程内不同 manager 之间由文件锁互斥，已用两个线程上的两个实例实测。

### 实施记录 —— PR 1（2026-10-02）

认证模块与凭据存储，按 §13.3 所列：新增 `agentao/mcp/oauth.py` 和 `oauth_store.py`，并改动 `_compat.py`、
`config.py`、`client.py`、`acp/mcp_translate.py`、`security/secret_scan.py` 和 `pyproject.toml`
（`filelock>=3.0`）。测试：`tests/test_mcp_oauth.py`，基于 `tests/support/oauth_server.py` —— 一个假源站，
在 SDK 真实传输层之后扮演 MCP 服务器、PRM、AS 元数据、DCR、`/authorize` 和 `/token`，只替换了 socket。
共 90 个测试，在 mcp 2.0.0、1.26.0 和 1.30.0 上都通过。**S3**（SSE）和 **S4**（两个进程、一个会轮换的
refresh token：只发出一次 token 请求，两个进程最终都持有新 token）在这里首次运行，三个版本上都通过。

手工做了八项变异检查，每项都针对为它而写的那个测试，每项都让该测试变红：连接时构造 SDK provider；只在
AUTH 分支读判定；忽略被 shield 任务的 `_shutdown`；不按记录划分的锁（可重入锁的情形）；阻塞式获取文件锁；
提供 issuer 为空的注册；只在有记录时才挂认证对象；SSE 不带认证对象。阻塞式获取那一项第一次结果是**绿的** ——
测试在循环已经卡住之后才开始计时 —— 写这份记录之前已修好该测试。

代码比设计写得更多、或与设计不同的地方：

1. **锁文件。** POSIX 上 filelock ≥ 3.x 释放时会自行删除锁文件，并在它自己那一侧处理由此产生的竞态。所以
   「锁文件在记录删除后仍保留」（§6.2）在要紧的意义上成立 —— agentao 从不删除它，登出也不删 —— 但两次持有之间
   这个文件不一定存在。测试断言的是行为（登出会等待），不是文件。
2. **每个请求最多一次 token 请求。** 同一请求里刷新已经失败之后再收到的 401，只重新读取记录，不会再去请求一个
   不可达的 token 端点。这是在以下情形中发现的：一次非 `Bearer` 的刷新响应之后，强制的第二次刷新用掉了已轮换的
   token，把一个普通错误变成了 `needs_auth`。
3. **处于 `NEEDS_AUTH` 的服务器不会在每次调用时都重连。** `call_tool` 一直返回登录提示，直到记录文件发生变化
   （与判定一起记下的 mtime）—— 即本进程或其他进程完成了一次登录 —— 才会重连。
4. **判定放在哪里。** 放在 `StoredTokenAuth` 对象上，而 `connect()` 每次都会重建它；这就是 §5.3 第 2 步所说的
   「连接开始时清除该标志」。
5. **登录发什么请求。** 通过 provider 发一个请求 —— `initialize` POST（Streamable HTTP）或 SSE 的 `GET` ——
   用的是 SDK 自己的客户端工厂，不读取响应体，尽力删除它打开的会话；然后普通连接重连。不是一次完整的 MCP 连接。
6. **配置了 `client_id` 且带 `client_secret` 的客户端**以 `client_secret_basic`（RFC 7591 的默认值）提供；
   不带 secret 时作为公开客户端（`none`）。
7. **`CHANGELOG.md`** 这里没有改：§13.5 把它放在 PR 3，而 PR 3 与本 PR 在同一个版本发布。
8. **提交前的评审修复**，每处都有一个撤掉修复就会失败的测试。一轮 `/code-review`：重试成功的 401 会清除它
   所恢复的那次刷新失败；那次刷新失败不再阻止会话过期或连接断开时的重连；不带 `token_type` 的刷新响应按
   Bearer 处理，与 SDK 的读法一致；登录和登出在重连锁下重连。一轮 Codex 评审：判定记下得出它的那个请求，
   一次成功只清除比它更早的判定或它自己的判定 —— 此前并发的一次成功会在另一个请求的调用方读到判定之前就把它
   清掉；重试得到的 403 与首次响应一样分类；刷新有总耗时上限，因为 httpx 的超时只约束每一次读取，不约束整个
   交换过程。第二轮 Codex 评审：`NEEDS_AUTH` 判定永远不会被一次成功清除 —— 另一个请求开始并成功时，它的调用方
   可能还在读错误响应体，而一旦读到，它本来就会把整个服务器置为 `NEEDS_AUTH`；成功清除规则现在只作用于
   `REFRESH_FAILED`。另外，一次拒绝记下的是被拒那份凭据的时间戳，而不是 401 到达时文件里的内容，所以请求进行中
   完成的一次登录，会在下一次调用时被尝试，而不会被当作已被拒绝。第三轮 Codex 评审：密钥扫描器跳过短于 20 个字符
   的字符串，所以较短的 `?code=…` 绕过了新规则（较短的 `token=…` 此前就已绕过 `kv_secret`）；下限现在是 13，
   即最短的匹配长度。第四轮：并发请求的一次刷新失败会覆盖尚未被读取的 `NEEDS_AUTH`；现在拒绝的优先级高于刷新失败，
   正如它本来就不会被一次成功清除。第五轮：连接会一直附加缓存的 token，直到一次刷新或一次 401，所以别处的一次登出
   （另一个 manager、同一 URL 的另一个别名、另一个进程）之后，一个仍然有效的 token 还在被使用；以另一个账号登录后，
   它也仍以旧账号行事。现在每个请求都比对记录文件的时间戳（一次 `stat`），文件有任何变化就重新加载。第六轮：只有
   *暂时性*的刷新失败才保留旧 token。终局结果之后 —— 等锁期间已被登出，或授权被拒 —— 请求不带 token 发出，即使旧
   token 仍在有效期内；一份已被拒的凭据（401 或授权被拒，不包括 scope 的 403）在文件变化之前既不再发送也不再刷新，
   所以一个失效的授权只花一次 token 请求，而不是每个请求一次。不是合法 UTF-8 的凭据文件与格式错误的 JSON 一样
   视为无法读取，登录可以替换它。第二轮 `/code-review`：格式错误的 `oauth` 配置和刷新失败不再附带「设置
   `type: sse`」的提示；`REFRESH_FAILED` 判定也不再影响 `call_tool` 的处理流程 —— 它属于整条连接，此前另一个调用的
   刷新失败会给本次调用自己的错误换上标签，并跳过它的重连。现在它只在错误本身是认证失败、或是刷新失败会产生的那种
   不透明 HTTP 错误时作为补充说明。第七轮 Codex 评审：同时进入刷新窗口的几个请求，会各自再次提交第一个请求刚被拒的
   授权。现在会在记录锁内重新检查拒绝状态，发现已被拒的等待者不带 token 发出请求；一次不带 token 的 401 也不再把拒绝
   重新记成「没有凭据」—— 那样会丢掉被拒凭据的时间戳，让下一个请求又把它发出去。第八轮：存储的 URL 无法解析的记录
   （`https://h:bad/mcp`）会让 `load()` 抛出异常，使连接失败，也挡住了本可替换它的登录；现在它与格式错误的 JSON
   一样视为无法读取。

### 实施记录 —— PR 2（2026-10-02）

CLI 登录流程，按 §13.4 所列：新增 `agentao/cli/mcp_login_ui.py`（§7 的 UI）和 `agentao/cli/mcp_auth.py`
（两个入口共用的登录与登出，以及 `agentao mcp` 子命令解析）；修改 `cli/commands/mcp.py`（`/mcp login <name>
[--no-browser]`、`/mcp logout <name>`、`/mcp list` 中显示 `needs login`）、`cli/ui.py`（§8.1 的启动提示行）、
`cli/_light.py`、`cli/__init__.py` 和 `cli/entrypoints.py`（`agentao mcp login|logout <name>`，走轻量入口，
不需要 `[cli]` 额外依赖即可运行），以及 `cli/help_text.py`。测试：`tests/test_mcp_oauth_cli.py`，45 个 ——
监听器走真实的回环 socket，粘贴读取走伪终端，并通过真实 UI 对接 PR 1 的假服务器跑一遍 登录 → 工具调用 → 登出，
其中脚本化的浏览器会向监听器真实地请求重定向地址。在 mcp 2.0.0、1.26.0、1.30.0 以及 Python 3.10 上均通过。

十二项变异检查，每一项都让对应测试变红：监听所有网卡；去掉 state 检查；去掉路径检查；`close` 不停止粘贴提示；
等待没有超时；不关回显；规范模式；不恢复终端设置；粘贴没有长度上限；监听器不关闭；关闭 `SO_REUSEADDR`；
记住的端口被占用时不回退。其中两项起初表现为**挂住**而不是失败（没有超时、规范模式），一项三次里只红两次
（`SO_REUSEADDR`，取决于回调连接哪一端先关闭）；写这份记录之前，这些测试已加上时限并改为确定性的。

代码比 §7 说得更多或有出入的地方：

1. **回调会检查 `state`。** 任何本地进程都能访问监听器，所以只有带着本次登录所打开授权 URL 的 `state` 的重定向
   才能完成登录；其他请求一律回 400，登录继续等待。SDK 也检查 `state`，但那是在第一个重定向已被收下之后 ——
   没有这一步，一个无关请求就能让登录结束。
2. **处于 TIME_WAIT 的端口可以重用。** 回调连接由服务端先关闭，所以一分钟内再次登录时，存储的注册所用端口会被
   报告为「已占用」。POSIX 的 `SO_REUSEADDR` 让这次绑定成功，同时仍然拒绝其他程序正在监听的端口；Windows 上
   不开启，因为在那里它恰恰会允许后者。
3. **只有配置了的端口被占用时才失败。** 仅从存储的注册中记住的端口（没有 `oauth.callback_port`）会回退到系统
   分配的端口：动态注册直接重新注册，配置了 `client_id` 的则得到 §5.5 中「请设置 `callback_port`」的提示。
4. **粘贴提示以非规范模式、关闭回显读取 `/dev/tty`**，并轮询，这样监听器先收到重定向时 `close` 能让它停下。
   规范模式下一行受终端驱动上限约束（macOS 上 1024 字节），比真实的重定向 URL 短。它在自己的线程上运行，
   不占用事件循环的默认 executor —— httpx 解析主机名要用那个 executor。
5. **没有显示器的 Linux 会话不打开浏览器。** `webbrowser` 会退回到控制台浏览器并占用当前终端，所以改走粘贴路径。
6. **`agentao mcp login` 的退出码：** `0` 已连接，`1` 失败或凭据已存储但未连上，`2` 用法错误，`130` 已取消。
7. **提交前的评审修复**，每一项都配有去掉修复就会失败的测试。一次 `/code-review`：`mcp` 命令行的解析错误会打印
   两次（轻量入口把它交给了完整入口）；`mcp` 前面的 `-h` 会直接执行登录；无浏览器提示丢了 IPv6 主机的方括号；
   Windows 粘贴提示下的 Ctrl+C 在读取线程上抛出，会冲出 MCP 事件循环。Codex 共五轮：脚本化浏览器的测试在
   无显示器的 Linux CI 上会一直等到超时（没有显示器时不开浏览器），无显示器路径现在有单独的测试；登录在 REPL
   中的输出除 Rich 转义外还经过终端清理，因为错误描述由授权服务器写；查询参数超过 32 个的重定向会让粘贴提示
   退出；取消的登录现在会（有上限地）等待 UI 关闭，粘贴提示不会在 REPL 下一个提示之下占着终端；
   `agentao mcp login` 能找到插件提供的 MCP 服务器（已安装的或 `--plugin-dir`），与 REPL 一致；
   `oauth.redirect_host` 会在它指定的回环地址上监听（包括 `::1`），非回环主机在登录开始前就被拒绝；
   授权 URL 打开之前的回调一律拒绝，发现阶段的过期重定向不会让登录结束；无效的传输配置会被报告而不是抛出。
   第五轮无意见。
