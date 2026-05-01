# 7.6 蓝图 F · 微信智能机器人（ilink-style）

::: tip ⚡ 端到端可跑
**产出** —— 一个长轮询守护进程，把每条进来的微信消息（私聊或群消息）跑一次 Agentao，把回复发回同一个联系人；联系人 ID 决定权限预设（白名单 → workspace-write；其余 → read-only）。
**技术栈** —— Python · `asyncio` 长轮询 · 自带的 ilink / wechaty / itchat 客户端通过 `WeChatClient` Protocol 接入 · 每条消息一个新 `Agentao` 实例 · `llm_client_factory` 测试钩子。
**源代码** —— [`examples/wechat-bot/`](https://github.com/jin-bo/agentao/tree/main/examples/wechat-bot)
**运行** —— `uv sync` 然后用任何一个能讲 ilink-style 协议的客户端去喂这个 daemon
:::

**场景**：你已经有个微信号（一台手机 / 一台服务器跑着 ilink、wechaty、itchat 之类的桥接客户端），想给这个号加上"自然语言对话 + 工具执行"的能力。**这不是公众号 / 企业微信开放平台**——那条路要走 §7.6 之外的官方 webhook 通道；本蓝图走的是个人号 bot API（长轮询一个本地/远程 HTTP 接口），形态参考 [`Wechat-ggGitHub/wechat-claude-code`](https://github.com/Wechat-ggGitHub/wechat-claude-code)（同一个形状的 TypeScript 版本，挂的是 Claude Code SDK；这里是 Python + Agentao）。

## 谁 & 为什么

- **产品形态**：单进程长轮询守护进程；不需要公网入口
- **用户**：自己 / 团队同事 / 群里的人；你把"机器人"当作一个会做事的微信好友用
- **痛点**：公众号开放平台门槛高（备案、ICP、企业认证、5 秒响应、48h 客服窗口），个人号 bot 本质是"挂在自己微信上的助手"——要做的只是把消息接进来、跑 agent、把回复发回去

## ilink-style ≠ 公众号 webhook

::: warning 别走错路线
两条线**架构差异极大**，不要混用：

| 维度 | ilink-style 个人号 bot（本蓝图） | 公众号 / 企业微信开放平台 |
|------|----------------------------------|---------------------------|
| 触发方式 | 守护进程**长轮询** bot 的 HTTP 接口 | 微信服务器**回调**到你的公网 webhook |
| 5 秒响应窗口 | **没有**——你拉，啥时候答完啥时候发 | 有，必须先 ACK 再异步推 |
| 签名 / AES 加密 | 看具体客户端，多数明文 | 必须验签；安全模式下 AES-CBC |
| 48 小时客服窗口 | **没有** | 有 |
| 用户 ID | wxid（私聊）/ `<id>@chatroom`（群） | OpenID / UnionID |
| 备案 / 认证 | 不需要 | 需要 |

如果你做的是**前者**（自己挂个号给自己用 / 给团队用 / 做内部小工具），继续读。
如果你做的是**后者**（面向公众的商家公众号 / 客服号），这本蓝图不适用——查微信公众平台官方文档。
:::

## 架构

```
微信手机 / 桥接客户端（ilink、wechaty、itchat、…）
       │ 暴露一个 bot API（HTTP / ws）
       ▼
Python daemon（一个 asyncio 进程）
       │
       ├─ run_polling_loop(WeChatClient)
       │    while not stop:
       │      msgs = await client.fetch_messages()
       │      for m in msgs:
       │          await handle_message(...)
       │
       └─ handle_message(text, contact_id, send)
            ├─ tempdir = mkdtemp("agentao-wechat-")
            ├─ engine = make_permission_engine_for_contact(contact_id)
            │     allowlist 命中 → WORKSPACE_WRITE
            │     其余        → READ_ONLY
            ├─ agent = Agentao(working_directory=tempdir,
            │                  llm_client=llm_client_factory(),
            │                  permission_engine=engine)
            ├─ reply = await agent.arun(text)
            ├─ agent.close() + rmtree(tempdir)
            └─ await send(contact_id=contact_id, text=reply)
```

## 关键代码

> 所有片段都来自 [`examples/wechat-bot/src/bot.py`](https://github.com/jin-bo/agentao/tree/main/examples/wechat-bot/src/bot.py)，整段不到 170 行。

### 1 · 客户端 Protocol——把 ilink / wechaty / itchat 都收进来

```python
class WeChatMessage(Protocol):
    text: str
    contact_id: str          # wxid 或 <id>@chatroom
    message_id: str

class WeChatClient(Protocol):
    async def fetch_messages(self) -> list[WeChatMessage]: ...
    async def send_message(self, *, contact_id: str, text: str) -> None: ...
```

这是这套设计最值得抄走的一点：**bot 逻辑只认 Protocol**。你今天用 ilink，明天换 wechaty，下周自己拿 HTTP hooks 拼一个，**daemon 不用改一行**。流式预览、扫码登录、限流重试、断线重连——这些 transport 关心的事情，**留给具体客户端去实现**。

### 2 · 联系人 → 权限模式

```python
WRITE_ALLOWLIST_CONTACTS: frozenset[str] = frozenset(
    {"wxid_owner_self", "ROOM_devops@chatroom"}
)

def make_permission_engine_for_contact(
    contact_id: str, *, project_root: Path
) -> PermissionEngine:
    engine = PermissionEngine(project_root=project_root)
    mode = (
        PermissionMode.WORKSPACE_WRITE
        if contact_id in WRITE_ALLOWLIST_CONTACTS
        else PermissionMode.READ_ONLY
    )
    engine.set_mode(mode)
    return engine
```

私聊 wxid 和群 `@chatroom` 同一个字段进来，可以**混在同一个白名单**里。生产里这套白名单要从配置 / 数据库读，不能写死。

### 3 · 一条消息 → 一次 turn

```python
async def handle_message(
    *,
    text: str,
    contact_id: str,
    send: Callable[..., Awaitable[Any]],
    llm_client_factory: Callable[[], LLMClient] = make_llm_client,
) -> str:
    work_dir = Path(tempfile.mkdtemp(prefix="agentao-wechat-"))
    agent = Agentao(
        working_directory=work_dir,
        llm_client=llm_client_factory(),
        permission_engine=make_permission_engine_for_contact(
            contact_id, project_root=work_dir
        ),
    )
    try:
        reply = await agent.arun(text)
    finally:
        agent.close()
        shutil.rmtree(work_dir, ignore_errors=True)   # close() 不会删 tempdir
    await send(contact_id=contact_id, text=reply)
    return reply
```

设计取舍：

- **每条消息一个新 agent**——简单、隔离好；高吞吐场景可以按 `contact_id` 做 agent 池
- **tempdir 必须显式 `rmtree`**——`agent.close()` 释放 handle 但不删工作目录，不清理就每条消息漏一个 tempdir
- **`llm_client_factory` 是测试钩子**——生产读 env，单测注入 `MagicMock`，不需要在代码里加 `if testing` 分支

### 4 · 长轮询主循环

```python
async def run_polling_loop(
    client: WeChatClient,
    *,
    poll_interval_s: float = 1.0,
    stop_event: Optional[asyncio.Event] = None,
    llm_client_factory: Callable[[], LLMClient] = make_llm_client,
) -> None:
    stop = stop_event or asyncio.Event()
    while not stop.is_set():
        messages = await client.fetch_messages()
        for msg in messages:
            await handle_message(
                text=msg.text,
                contact_id=msg.contact_id,
                send=client.send_message,
                llm_client_factory=llm_client_factory,
            )
        if stop.is_set():
            break
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_interval_s)
        except asyncio.TimeoutError:
            continue
```

`stop_event` 是优雅关闭钩子——测试里 `FakeWeChatClient` 把队列抽干后就 set 它，让循环干净退出。生产里挂到 SIGTERM 上同样有效。

## 离线 smoke test —— 不用真微信也能跑

这套例子的另一个优点：**`llm_client_factory` + 客户端 Protocol** 让整条链路在 CI 里跑，零外部依赖。

```python
# tests/test_smoke.py 节选
class _FakeWeChatClient:
    """In-memory 客户端：吐一批消息后 set stop_event 退出。"""
    async def fetch_messages(self) -> list[_Msg]:
        if self._queued:
            batch, self._queued = self._queued, []
            return batch
        self._stop.set()
        return []
    async def send_message(self, *, contact_id: str, text: str) -> None:
        self.sent.append({"contact_id": contact_id, "text": text})

async def test_run_polling_loop_processes_one_batch_then_exits() -> None:
    stop = asyncio.Event()
    client = _FakeWeChatClient(
        queued=[
            _Msg(text="ping",    contact_id="wxid_a", message_id="1"),
            _Msg(text="status?", contact_id="wxid_b", message_id="2"),
        ],
        stop=stop,
    )
    with patch("agentao.agent.Agentao._llm_call",
               lambda self, msgs, tools, token: _fake_response("ok")):
        await run_polling_loop(client, stop_event=stop, llm_client_factory=_fake_llm)
    assert client.sent == [
        {"contact_id": "wxid_a", "text": "ok"},
        {"contact_id": "wxid_b", "text": "ok"},
    ]
```

```bash
cd examples/wechat-bot
uv sync --extra dev
uv run pytest tests/ -v   # 没微信、没 API key、没网络
```

## 想要流式预览？接 `Agentao.events()`

参考 repo（`wechat-claude-code`）会把 LLM 输出的中间片段当成"打字中"实时回到聊天里。在 Agentao 这边，把 `agent.arun(text)` 换成订阅 `agent.events()` 流，把 `LLM_TEXT` 增量按"每 N 字符 / 每 M 毫秒"刷给 `client.send_message` 即可（事件契约见 [§4 事件流](/zh/part-4/)）。多数 ilink 客户端有单条消息频率限制，刷得太快会被截断——保守值是 **1.5 秒一段**。

## ⚠️ 陷阱

::: warning ilink-style 微信 bot 真实部署中的 Day-2 bug
下面每一行都是真实生产事故。**上线前先扫一遍**——现在改便宜，事后查代价大。
:::

| 上线第二天的 bug | 根因 | 修法 |
|------------------|------|------|
| `/tmp` 占满 | `agent.close()` 不删 tempdir，每条消息漏一个目录 | 显式 `shutil.rmtree(work_dir, ignore_errors=True)`，示例已有 |
| 同一群里两条消息回复乱序 | `for msg in messages` 串行没问题，但你改并行后没按 `contact_id` 串行 | 想并行就**按 `contact_id` 分桶 + 每桶一把 `asyncio.Lock`** |
| 群消息里没 @ 自己也回复 | 个人号 bot 默认收所有群消息 | 客户端层面或 `handle_message` 入口先过滤 `@<self>` 才进 agent |
| 假冒身份 | 仅凭 `contact_id` 就给 `WORKSPACE_WRITE` | 二次验证（口令、签名后的指令包）；`contact_id` 是 transport 标识，不是身份认证 |
| LLM 输出 5000 字撑爆单条消息 | 不同 ilink 客户端有单条上限（多见 1024–4096 字节） | `handle_message` 出口分块；或者技能里硬约束输出长度 |
| 群里突然刷屏（agent 死循环） | `max_iterations` 没限；agent 又触发了能发消息的工具 | `agent.arun` 设硬上限；坚持"出口只在 daemon 这一处"，不要让工具直接发消息 |
| 桥接客户端（ilink / wechaty / 微信号本身）被风控掉线 | 长轮询返回的不是空，是 401/网络错 | `fetch_messages` 抛异常时**响亮告警 + 重连退避**；不要 `try/except: pass` |
| 测试时改了 Protocol 但生产客户端没跟上 | 加了字段没同步给真实 ilink 客户端 | 在 `tests/` 里加一个 contract test，强迫 `WeChatClient` 实现保留 `fetch_messages` / `send_message` 这两个签名 |
| API key 进日志 | `LLMClient` 构造时被打到 trace | 走 [6.5](/zh/part-6/5-secrets-injection) 的 secrets scrubber |

## 进阶：每个联系人一个常驻 agent

例子里**每条消息新建一个 agent**，简单、好回收。如果你需要：

- 跨消息的对话上下文（多轮记忆）
- 共享的工作目录（agent 在第一条消息里写的文件，第三条消息能继续编辑）

把 `_agents: dict[str, Agentao]` 缓存按 `contact_id` 留下来即可——同时**配一把 `asyncio.Lock` 防并发**，并加 LRU + 空闲超时（比如 30 分钟没消息就关 agent + 删工作目录）。这是从"每消息隔离"切到"每联系人会话"的最常见演进，但**多租户安全**就要重新算账了——参考 [§6.4 多租户隔离](/zh/part-6/4-multi-tenant-fs)。

## 可运行代码

完整项目就在主仓 [`examples/wechat-bot/`](https://github.com/jin-bo/agentao/tree/main/examples/wechat-bot)：

```bash
cd examples/wechat-bot
uv sync --extra dev
uv run pytest tests/ -v          # 离线 smoke

# 真跑（需要自带 ilink / wechaty / itchat 客户端）
OPENAI_API_KEY=sk-... uv run python -c "
import asyncio
from src.bot import run_polling_loop
from your_wechat_client import IlinkClient   # 你自己的 ilink 客户端
asyncio.run(run_polling_loop(IlinkClient()))
"
```

---

## Part 7 结束——也是主干内容的终点

到这里你已经拥有：

- 两条嵌入路径（[Part 2](/zh/part-2/) SDK、[Part 3](/zh/part-3/) ACP）
- 事件 + UI 集成（[Part 4](/zh/part-4/)）
- 六个扩展点（[Part 5](/zh/part-5/)）
- 安全 + 生产部署（[Part 6](/zh/part-6/)）
- 六个真实蓝图（本部分）

接下来的附录——完整 API 参考、配置键索引、ACP 消息字段、错误码、框架迁移、FAQ、术语表——是落地过程中常翻的查询手册，紧随其后。

→ 附录（即将推出）
