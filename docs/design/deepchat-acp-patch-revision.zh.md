# DeepChat ACP 集成 Patch —— 修订方案

**状态:** 设计记录。起草于 2026-05-29。实现进行中 —— PR-1/PR-2/PR-3
已落地(见 PR 排序);PR-4 起尚未开始。
**读者:** Agentao 维护者;DeepChat/TensorChat 集成 fork 的负责人。
**相关文档:** `docs/design/embedded-host-contract.md`,
`docs/architecture/embedding-vs-acp.md`(若存在),
`agentao/embedding/factory.py`,`agentao/acp/`。

## 范围(先读这段)

一份约 405 KB 的本地改动 patch(`agentaolocal-changes.patch`,56 文件,
+4448 / −6153),目的是让 Agentao 作为 **ACP 子进程后端接入
DeepChat / TensorChat(Electron)桌面聊天 UI**。本文是对该 patch 的
分类 + 修订方案:哪些回流 Agentao core、哪些重做、哪些留在 fork、哪些拒绝。

这份 patch **不是单个特性**,它捆绑了四件互不相关的事:
(1)真实的 harness 能力;(2)一个暴露方式错误的能力;
(3)只属于 DeepChat 的 glue 与打包;(4)会**回退当前 `main`** 的删除。
本方案把它们拆开。

**本方案在 agentao `main` 上的可执行范围是 A 系列、B 系列与 D1。**
下文的处置总表是 *patch 分类裁决*,不是 main 工作清单:只有 A 组(上游)、
B 组(重做 + 上游)、以及 **D1(还原被删的 `acp_client` 测试)** 是 `main`
动作。C 组与 D3 是**留 fork / 丢弃**裁决 —— 它们描述的是什么*不*进 `main`,
其中 fork 侧的那些集中收录为**建议**,放在「给 DeepChat fork 的建议」一节。
核心重设计与 PR 落地顺序才是真正的 `main` 工作;本方案**不**实现 fork 侧事项。
agentao `main` 只提供 fork 要消费的接缝。

唯一**承重的设计决策**是**运行时切换 provider/model 时的凭据处理**:
patch 把 `apiKey` / `baseUrl` 经 ACP 线缆传输(并写入 `agentao.log`)。
本文用 ACP 标准的 `session/set_config_option` 机制 + 一个 server 端、
host 可注入的 `provider_resolver` 取而代之。

## 背景 —— 这份 patch 做了什么

| 主题 | 代表文件 |
|---|---|
| ACP handler 长出 DeepChat 线缆形状(`modelId`、`_meta`、`apiKey`、`baseUrl`) | `agentao/acp/session_set_model.py`、`session_set_mode.py`、`models.py`、`server.py`、`transport.py` |
| 多模态(图片)输入贯穿一个 turn | `agentao/agent.py`、`agentao/runtime/turn.py`、`agentao/runtime/chat_loop/_runner.py`、`agentao/llm/client.py` |
| 结构化 `ask_user`(options/header/multiple/custom) | `agentao/tools/ask_user.py`、`agentao/tools/base.py` |
| 第二套平行 ACP 传输实现 | `agentao/transport/acp.py`(新)、`transport/acp_server.py`(新) |
| PyInstaller 二进制打包,6 平台 CI | `run.py`、`pyinstaller.spec`、`scripts/build_binaries.sh`、`.github/workflows/build-matrix.yml` |
| 基于 `$HOME` 的路径解析 | `agentao/paths.py` 及调用方 |
| 删除 | `tests/test_acp_client_*.py`、`docs/dev-notes/*` |

## 已核实的结论(边界分析)

以下结论均经 grep 对工作树、以及对 ACP 规范(`agentclientprotocol.com`)
核对,**非凭记忆**。证据见附录。

1. **`reconfigure()` 已在 core**(`agentao/llm/client.py:233`,
   `reconfigure(api_key, base_url, model)`)。运行时切 provider 本就是
   harness 原语;patch 只是从一个新线缆形状去*调用*它。无需新增能力,
   只需一个安全的触发方式。

2. **server 端凭据解析已存在**(`agentao/embedding/factory.py:71-77`):
   `LLM_PROVIDER` 选中 provider,再从环境读 `{PROVIDER}_API_KEY` /
   `_BASE_URL` / `_MODEL`。凭据在构造期就已在 server 端解析,线缆从来
   不需要携带 key。

3. **ACP 没有任何选模型方法。** 经规范核实:不存在 `session/set_model`、
   `session/select_model`、`session/list_models`。选模型由通用的
   **`session/set_config_option`**(`category: "model"`)或 agent 内部
   机制承担。因此 Agentao 现有的 `session/set_model` / `session/list_models`
   **本就是非标准扩展**,却裸占了标准 `session/` 命名空间 —— 与正确使用
   厂商前缀的 `_agentao.cn/ask_user` 不一致。

4. **`session/set_mode` 用 `modeId` *才是* ACP 标准。** 规范字段是
   `modeId`(不是 `mode`),配 `availableModes` / `currentModeId`。
   patch 改成 `modeId` 反而*靠近*标准;Agentao 改之前的 `mode` 才是
   非标准那个。

5. **`agentao/acp_client/` 及其测试在 `main` 里是活的。** patch 删除
   `tests/test_acp_client_*` 是对现有覆盖的**回退**,不是清理。

6. **`agentao/transport/` 当前没有任何 ACP 实现。** 新增的
   `transport/acp.py` + `acp_server.py` 是*第二套*平行 ACP 实现,
   与完整的 `agentao/acp/` server 包重复 —— fork 债。

7. **`main` 上的私有 schema 完整清单。** 对 `agentao/acp/` 逐项对标规范,
   发现以下非标准面*已在 `main`*(与 patch 无关):

   | 私有面 | schema | 评价 |
   |---|---|---|
   | `session/set_model` | `AcpSessionSetModelRequest/Response`(`model`/`contextLength`/`maxTokens`) | 非标准方法,裸占**标准 `session/` 命名空间** —— 伪装成标准。 |
   | `session/list_models` | `AcpSessionListModelsRequest/Response`、`AcpModelInfo`(`extra="allow"`) | 非标准方法,同样的命名空间问题。 |
   | `_agentao.cn/ask_user` | `AcpAskUserParams`、`AcpAskUserAnswered/Cancelled` | 非标准但**正确用厂商前缀**且在 `initialize` 声明 —— 模范。 |
   | `initialize` 响应的 `extensions: [...]` | `AcpInitializeExtension` | **非标准顶层字段。** ACP 用 **`_meta`** 声明扩展,没有顶层 `extensions` 数组。 |
   | `session/set_mode` 的 `mode` 字段 | `AcpSessionSetModeRequest`(`mode: Literal["read-only","workspace-write","full-access","plan"]`) | **两处偏离:** ACP 字段是 `modeId`(非 `mode`);取值锁死为 Agentao **权限预设**,把 ACP 的「mode」(UI/行为选择器)与权限姿态混为一谈。 |

   作为对照,以下经核实为**标准**、无需改动:`protocolVersion`、
   `agentCapabilities`(`loadSession` / `promptCapabilities` /
   `mcpCapabilities`)、`agentInfo`(`name`/`title`/`version`)、
   `authMethods`、全部 `session/update` 变体(`agent_message_chunk`、
   `agent_thought_chunk`、`tool_call`、`tool_call_update`、
   `user_message_chunk`)、`stopReason` 枚举(`end_turn` / `cancelled` /
   `max_turn_requests` / `refusal`)、`session/new` · `prompt` · `cancel` ·
   `load` · `request_permission`。

## 处置总表

图例:✅ 上游 · 🔧 重做后上游 · 🟠 留 fork · ❌ 丢弃 · 🚫 拒绝(回退)

| 分组 | 处置 | 动作 |
|---|---|---|
| **A1 —— 多模态图片输入**(`agent.py`、`runtime/turn.py`、`runtime/chat_loop/_runner.py`、`llm/client.py` 日志、`cli/display.py`、`tests/test_logging.py`) | ✅ | 提成独立 PR。图片数据走标准 ACP content block,本就与 DeepChat 解耦。日志改动(摘要多模态而非 dump base64)随之上游。 |
| **A2 —— 结构化 `ask_user`**(`tools/ask_user.py`、`tools/base.py`、`cli/app.py`) | ✅ | 上游(决议 #1),但回调契约必须**向后兼容**:`ask_user_callback` 是 deprecated 1 参 `Callable[[str], str]`(`agent.py:52`),裸加 `options`/`header`/`multiple` 会让传 `lambda q: ...` 的 embedded host `TypeError`。保留 1 参形式可用(变长 / 新增可选 structured 回调),形状与 host 无关(非 DeepChat 选项卡)。补单测。 |
| **A3 —— `$HOME` 路径健壮性**(`paths.py` + `memory/storage.py`、`skills/manager.py`、`llm/client.py` fallback、`tests/test_memory_store.py`) | ✅ | 小 PR。确认 `$HOME` 未设时的回退。 |
| **B1 —— secret-wire 修复(PR-4,核心)**(`acp/session_set_model.py`、`models.py`、`server.py`、`transport.py`、`initialize.py`、`schema.py`、`session_new.py`、`test_acp_set_model.py`) | 🔧 | **丢弃** `apiKey`/`baseUrl`/`modelId`/`_meta`。新增 `session/set_config_option`(仅 `configId="model"`;单 `category:"model"` 选项、`provider/model` value)+ 注入式 `provider_resolver`(server 端 secret;**handler 白名单 + `extra="forbid"` 拒 `apiKey`/`baseUrl`/`_meta`**)。**新增 `_agentao.cn/set_model`**(`{sessionId, model}`、free-form、本就无 secret;共用 core 代码路径 —— 决议 #4),并**保留既有 `session/set_model` 原样**作为一个版本的兼容别名 —— 其现有形状 `{sessionId, model?, contextLength?, maxTokens?}` 本就 `extra="forbid"`、无 secret;PR-4 只是**不采纳 patch 给它加的 `modelId`/`apiKey`/`baseUrl`/`_meta`**(CHANGELOG 标弃用;与 `list_models` 一起在 PR-7 退休)。默认 catalog = **当前 env** 那一条 `provider/model`(model 取自实时 `agent.llm.model`);更丰富 catalog 由 host 注入。本 PR **`session/list_models` 保留为兼容端点**。见「核心重设计」。 |
| **B2 —— `session/set_mode` 字段(PR-5,独立)**(`acp/session_set_mode.py`、`schema.py`、`test_acp_set_mode.py`) | 🔧 | 最小:`mode` → **`modeId`** 且**接受未知值**(始终持久化;命中 preset 才映射)——让 DeepChat 的 `code`/`ask` 不被拒。**推迟**(决议 #6 —— 解耦是大重构):权限轴拆分 *以及* `availableModes`/`currentModeId` + `current_mode_update`。不进 model/provider PR。 |
| **B3 —— `initialize` 的 `extensions` 数组 → `_meta`(PR-6,低优先)**(`acp/initialize.py`、`acp/schema.py`) | 🔧 | **决议 #5:挪到 `_meta`**(合规)。agentao 自己的 client 不读 `extensions`;只影响 schema 快照 + `test_acp_schema.py`。独立小 PR;**不**捆进 secret-wire 修复;排最后。bump 快照(`docs/schema/host.acp.v1.json`)。 |
| **B4 —— 退休遗留选模型方法(PR-7,后续)** | 🔧 | 待有 host 消费标准 `configOptions` 路径后:**两个**兼容端点一起删 —— `session/list_models` **与** `session/set_model` 名称别名(规范名 `_agentao.cn/set_model` 保留)。方向是标准对齐;跨版本分批。 |
| **C1 —— 重复 ACP 传输**(`transport/acp.py`、`transport/acp_server.py`、`transport/__init__.py`、`transport/sdk.py`) | ❌ | 整组丢弃。`agentao/acp/` 已是完整 server 包。 |
| **C2 —— PyInstaller 打包**(`run.py`、`pyinstaller.spec`、`scripts/build_binaries.sh`、`.github/workflows/build-matrix.yml`、`pyproject.toml`) | 🟠 | 留 DeepChat fork。与 Agentao「嵌入式库」定位(`pip install agentao`)冲突。仅当项目决定发二进制(独立产品决策)时才上游。 |
| **C3 —— skill-creator 前端漂移**(`skills/skill-creator/assets/eval_review.html`、`eval-viewer/viewer.html`) | ❌ | 无关前端漂移(无 ACP/provider 引用)。从 patch 移除。 |
| **D1 —— 删除 `tests/test_acp_client_*`** | 🚫 | 拒绝。`acp_client` 在 `main` 里是活的;还原测试。 |
| **D2 —— 删除 `docs/dev-notes/*`** | ⚪ | 中性。若确要做,走独立 housekeeping PR,别与特性捆绑。 |
| **D3 —— 新增中文 fork 笔记**(`docs/agentao/*.md`) | ⚪ | 留 fork;不上游。 |

## 核心重设计 —— ACP 上的 provider/model 切换

### 决策

ACP 线缆只传**标识符**,绝不传 secret。凭据经一个 host 可注入的
`provider_resolver` 在 server 端解析。这一点**协议并不保证**——JSON-RPC
params 仍可能多塞字段——所以它是一条**实现要求**:`set_config_option`
handler 只解析 `configId` / `value`,请求 schema `extra="forbid"`,任何
`apiKey` / `baseUrl` / `_meta.*` 凭据字段一律**拒绝**,不予采纳。

### 单 `model` 选项 + `provider/model` value

一个 config option,不是两个。`value` 把两个轴编码成 `provider/model`。
选它是为了**原子性与简化**,**不是**为了合规:在线缆层,合并的一个选项和
拆开的 `provider` + `model` 两个选项**同等合规**(ACP 让 `value` 是 agent
自定义的不透明字符串,且允许多个 config option)。合并胜出是因为一次
`set_config_option` 调用 = 一个**保证合法**的 `(provider, model)` 对 = 一次
原子 `reconfigure()`——没有非法中间态,也不需要为「provider 变了重筛
model」去多走一轮 `config_option_update`。还有一处**弱**的概念偏向也指向
合并:ACP 的 `category` 枚举是 `{mode, model, thought_level}`——有 `model`、
**没有 `provider`**,所以 spec 的心智模型是「选一个模型」,而非「先选
provider 再选 model」。

`provider/model` 是 **Agentao 的 value 约定,不是 ACP 标准**——在选项上要
这么标注清楚。

### 线缆形状

`session/new`(及 `session/load`)广播该选项:

```json
{
  "configOptions": [
    { "id": "model", "name": "Model", "category": "model", "type": "select",
      "currentValue": "anthropic/claude-opus-4",
      "options": [
        { "value": "openai/gpt-4o",           "name": "GPT-4o" },
        { "value": "anthropic/claude-opus-4", "name": "Claude Opus 4" },
        { "value": "azure-openai/gpt-4o",     "name": "GPT-4o (Azure)" }
      ] }
  ]
}
```

客户端切换(一次原子调用):

```json
{ "sessionId": "s1", "configId": "model", "value": "openai/gpt-4o" }
```

### Handler 草图

```python
def handle_set_config_option(server, params):
    session = require_active_session(server, params, METHOD_SET_CONFIG_OPTION)
    if params["configId"] != "model":
        raise JsonRpcHandlerError(INVALID_REQUEST, f"unknown configId {params['configId']!r}")
    # schema 为 extra="forbid";只有 configId/value 到这 —— 没有 apiKey/_meta。
    value = params["value"]                          # "openai/gpt-4o"
    provider_id, _, model_id = value.partition("/")  # 首个 "/" 切分
    with hold_idle_turn_lock(session, METHOD_SET_CONFIG_OPTION):
        if model_id:                                 # provider/model 形式
            creds = server.provider_resolver(provider_id)   # server 端 secret
            session.agent.llm.reconfigure(
                api_key=creds["api_key"], base_url=creds.get("base_url"),
                model=model_id)                      # 一次原子切换
        else:                                        # 裸 value,无 provider 前缀
            session.agent.set_model(value)           # 只换模型、不动 provider
        return {"configOptions": _current_config_state(session)}
```

三条 value 规则:
1. **首个 `/` 切分**(`partition`,不是 `split`)。provider id 不含 `/`,
   但 model id **会**含(`huggingface/meta-llama/Llama-3` → provider
   `huggingface`、model `meta-llama/Llama-3`)。
2. **裸 value(无 `/`)** = 只换模型、保持当前 provider —— 保留「不重述
   endpoint 只换模型」的便利。
3. **同模型不同 endpoint** = 不同条目(`openai/gpt-4o` vs
   `azure-openai/gpt-4o`)。这就是「切 provider、模型名相同」的建模方式
   —— 不需要单独的 provider 轴。

### `provider_resolver` 接缝 —— 以及 catalog 从哪来

两件事,别混为一谈:

- **凭据解析** —— `provider_resolver(provider_id) -> {"api_key",
  "base_url"}`。只有两条路:**host 注入**的 resolver;或——未注入时——
  **默认**实现,从既有 `factory.py` env(`LLM_PROVIDER` + 其 `{PROVIDER}_*`
  变量)解析**当前那一个** provider。默认实现**只接受** `provider_id ==
  LLM_PROVIDER`;**其它任何 `provider_id` → `INVALID_REQUEST`**。它**不**
  扫描环境去拼 provider 列表,也**不**为任意 id 去拼 `{PROVIDER}_*` 查找——
  那正是「猜 provider 列表」的陷阱。多 provider 切换必须由 host 注入 resolver
  (并配 host 注入的 catalog)。
- **模型 catalog** —— 即 `configOptions` 里 `model` 选项广播的 `options`。
  **默认 catalog 只有一条**:provider 取自 `LLM_PROVIDER`,model 取自
  **实时 `agent.llm.model`**(构造期解析出的值)——**不**再去读
  `{PROVIDER}_MODEL`,所以即便 env 缺 `{PROVIDER}_MODEL` 也不影响广播当前模型。
  agentao 现在是**单 provider**(`LLM_PROVIDER` 构造期选一个;没有
  `providers.json`、没有注册表),所以默认 agent 老实只广播一个选项,
  在 host 充实它之前 `set_config_option` 是个空切换。**多条 catalog 必须由
  host 注入**——实现**绝不可扫 env 或猜 provider 列表**。
- **不建 `.agentao/providers.json`。** 新增一种 secret-at-rest 配置格式
  与「secret 不过线」正交,只会扩大攻击面。host 想要更丰富的凭据库(如 OS
  钥匙串)或多 provider catalog,就**注入自己的 resolver / catalog**——接缝
  正是为此而设。
- 两个接缝都 host 可注入,符合 embedded-harness 原则:capability
  injection、no globals、host 可覆盖。**初版实现:仅构造 kwargs** —— 无需
  新设宿主协议;日后若真有 host 需要,再扩出更广的 host-contract 面。

### 退休遗留选模型方法 —— 方向定,但分批

**标准对齐是方向**:ACP 没有模型方法,所以 `session/list_models` /
`session/set_model` 最终应退休,改用 `set_config_option` + 标准的
`config_option_update` 通知。但实现**跨版本分批**——这个 patch 决不能变成
一次性大迁移。两个遗留方法**同样分批**(现在保留为兼容端点,PR-7 一起删),
不区别对待。原则:

> 先加标准路径;旧方法保留为**薄兼容端点**(不加新逻辑);只在已有触发点实现
> 推送刷新;待 client 迁移后,下一版再删旧方法。

具体:

1. **新增** `set_config_option(configId="model")` + `session/new` 及
   `session/load` 广播 `configOptions`。这是核心 PR 里**唯一**新增的**标准**
   面;PR-4 另加**一个厂商兼容方法** —— `_agentao.cn/set_model` —— 承接
   free-form 填模型(见下),但不新增其它标准方法。
2. **`session/list_models` 保留为兼容端点** —— 维持现状(per-session 缓存 +
   `warning` 回退)。**不**把它改写到 `config_option_update`,**也不**先加
   转调 shim 层。同样**保留既有 `session/set_model` 原样**作为一个版本的别名:
   其现有形状 `{sessionId, model?, contextLength?, maxTokens?}` 本就
   `extra="forbid"`、无 secret(`model`/`contextLength`/`maxTokens` 旋钮保留 ——
   **不**缩成只支持 `modelId`)。PR-4 在这里唯一要做的是**不采纳 patch 给它加的
   带 secret 字段**(`modelId`/`apiKey`/`baseUrl`/`_meta`)。还在发这些凭据字段的
   client 无论如何要改载荷 —— 这正是安全修复。DeepChat「随便填模型」的需求由新的
   `_agentao.cn/set_model` 承接,其 adapter 把 DeepChat UI 的 `modelId` 映射到
   `model` 字段。
3. **PR-4 只返回、不推送。** 一次*成功*的 `set_config_option` 切换,在其
   **响应里**返回当前 `configOptions` 状态。PR-4 **不**发任何
   `session/update` / `config_option_update` 通知 —— 为替代「手动刷新按钮」
   去建推送*系统*是 YAGNI,而「回显推送」只会多一套通知测试面。真正的
   `config_option_update` 推送留到后续,且只在已有刷新触发点。
4. **后续版本退休两个遗留方法** —— `session/list_models` **与**
   `session/set_model` 别名一起删 —— 待真有 host 消费标准 `configOptions`
   路径(规范名 `_agentao.cn/set_model` 保留)。届时 wire 废弃信号 = CHANGELOG
   说明 + 之后返回 `-32601 method not found`(二者都未在 `initialize` 广播,所以
   Python `DeprecationWarning` 对 wire client 不可见、毫无意义)。

这样既守住标准对齐目标,又不把 DeepChat 的 secret-wire 修复做成一次大的协议
迁移。

### Free-form 填模型名:`_agentao.cn/set_model`(已定,决议 #4)

DeepChat 发的模型串是 **free-form**(patch 里是一个 `modelId` 字段、任意非空
字符串、无白名单;直接 `agent.set_model()` —— 已核实)。select-only 的
`set_config_option` **表达不了**,所以 free-form 路径必须活着。决议(#4):新增
厂商方法 **`_agentao.cn/set_model`,最小载荷 `{sessionId, model}`** —— free-form、
无 secret(无 `apiKey`/`baseUrl`/`_meta`;不动 provider,只换模型)。它刻意复用
**`model`** 字段名(与 core `session/set_model`、`set_config_option` 的 value 一致),
所以 DeepChat 的 adapter 只需把 UI 的 `modelId` 映射成 `model`;三个面统一一个字段名,
线缆上**不出现 `modelId`**。厂商前缀同时修掉 finding-#7 原罪(裸占标准 `session/`
命名空间的非标准方法),与 `_agentao.cn/ask_user` 一致。

按设计**两条 model-set 路径并存**:
- **`session/set_config_option(model)`** —— 标准,从广播 catalog 里 `select`
  (带 `provider/model`;可经 resolver 切 provider)。给遵循 spec 的 client。
- **`_agentao.cn/set_model`** —— 厂商,`{sessionId, model}` free-form 串、只换
  模型(保持 provider)。给 DeepChat「随便填模型」的 UX。

外加不动的 **`session/set_model`**(`{sessionId, model?, contextLength?,
maxTokens?}`)作为一个版本的兼容别名。

三者**必须共用一段 core 代码**(`reconfigure` / core `set_model()`)—— 不分叉,
否则入口状态漂移。DeepChat 要 free-form 厂商方法才能工作,所以厂商方法(及共享
core)落在 **PR-4**,不延后;别名只是原方法原样保留。

## PR 落地顺序

以下全部落在 agentao `main`。fork 侧事项(PyInstaller、重复传输、fork
笔记、DeepChat client 适配)**不**是这里的编号 PR —— 它们收录在下方
「给 DeepChat fork 的建议」一节。

**前置(非编号 PR)—— 还原 / 拒绝删除 `acp_client` 测试**(D1)。✅ **已完成**
—— 已核实 `tests/test_acp_client_*` 套件在 `main` 上是活的;patch 的删除从未被
应用,所以回归护栏在 extraction PR 落地前就已就位。

1. **PR-1 —— 多模态图片输入**(A1)。✅ **已完成** —— 在 `#53` 合并
   (`feat(multimodal): image input across engine, ACP, and CLI`)。自包含、最清晰
   可上游;先走。
2. **PR-2 —— 结构化 `ask_user`**(A2)。✅ **已完成** —— 在 `#54` 合并
   (squash `4292e4a`)。向后兼容回调已确认:structured 提示
   (`header`/`options`/`multiple`/`allow_custom`)只转发给签名能接受它们的回调
   (经共享的 `invoke_ask_user_callback` 内省辅助),因此 1 参 `Callable[[str], str]`
   回调 —— deprecated 的 `ask_user_callback` 构造参数、`SdkTransport(ask_user=...)`、
   直接构造的 `AskUserTool`、以及 1 参 replay 内层 transport —— 都照常工作。已测试
   (`tests/test_ask_user_structured.py`)。
3. **PR-3 —— `$HOME` 路径健壮性**(A3)。✅ **已完成** —— 在 `#55` 合并
   (squash `0b8b4f4`)。新增 `agentao.paths.user_home()` 并把散落的 `Path.home()`
   调用点都路由过去;无 home 时的兜底是私有、按用户隔离、校验属主/权限的临时目录
   (按进程缓存)。已测试(`tests/test_paths.py`)。
4. **PR-4 —— 最小核心 ACP 选模型修复**(B1)。只做核心 provider/model 面、不越界:
   - 拒收 patch 给请求加的 `apiKey`/`baseUrl`/`modelId`/`_meta`。
   - 新增 `session/set_config_option` 仅 `configId="model"`(`provider/model`
     value;裸 value 保持当前 provider)。
   - **新增 `_agentao.cn/set_model`**(`{sessionId, model}`、free-form、无 secret)
     —— 厂商 free-form 路径;共用 core 代码路径。**既有 `session/set_model` 原样
     保留**作为一个版本的兼容别名(`{sessionId, model?, contextLength?,
     maxTokens?}`,本就 `extra="forbid"`/无 secret;与 `list_models` 一起在 PR-7
     退休)—— **不**缩成只支持 `modelId`。
   - server 端 `provider_resolver`;handler 白名单 + `extra="forbid"` 拒
     `apiKey`/`baseUrl`/`_meta`。默认 resolver **只接受**当前 `LLM_PROVIDER`;
     其它任何 `provider_id` → `INVALID_REQUEST`。
   - `session/new` **及 `session/load`** 广播 `configOptions`,默认只含**当前
     env** 的 `provider/model`;更丰富 catalog 由 host 注入。
   - 在 `set_config_option` **响应里**返回当前 `configOptions` —— 本 PR **不**发
     `config_option_update` 通知。
   - **`session/list_models` 保留为兼容端点** —— 不改写、本 PR 不删。
5. **PR-5 —— `set_mode` 字段修正**(B2,最小)。`mode` → `modeId`,接受未知
   值(让 `code`/`ask` 不被拒)。权限轴拆分 + `current_mode_update` 各自另开
   设计。
6. **PR-6 —— `initialize.extensions` → `_meta`**(B3)。独立小 PR;把数组挪到
   `_meta` 下,重生 schema 快照。低优先。
7. **PR-7(后续版本)—— 退休遗留选模型方法**,待有 host 消费标准
   `configOptions` 路径:`session/list_models` **与** `session/set_model` 名称
   别名一起删。规范名 `_agentao.cn/set_model`(PR-4)保留。
8. **清理 —— `docs/dev-notes`**(D2)。若确要删,走独立 housekeeping PR。
   (`acp_client` 测试还原**不**在此 —— 它是上面的前置项。)

## 给 DeepChat fork 的建议(advisory —— 不在 `main` 范围内)

以下是**给 DeepChat / TensorChat fork 负责人的建议**,不是本方案要实现的
工作。它们位于 embedded-harness 边界的 fork 一侧;agentao `main` 只提供
fork 要消费的接缝(`provider_resolver` / catalog 注入点、无 secret 的线缆,
以及 PR-4 交付的厂商方法 `_agentao.cn/set_model`)。

> **本节对 agentao `main` 不具规范性(non-normative)。** 除非某条日后被明确
> 提升进 A/B/D1 范围,否则不要据此创建上游 PR。

- **PyInstaller 打包 & 6 平台 CI 留在 fork**(C2)。与 agentao 库优先定位
  (`pip install agentao`)冲突;仅当项目后续单独决定发二进制时才上游。
- **中文 fork 笔记留在 fork**(`docs/agentao/*.md`,D3)。不上游。
- **丢弃重复 ACP 传输**(C1)。直接对接 `agentao/acp/`,别再带第二套
  `transport/acp*.py` server;该重复是无上游路径的 fork 债。
- **丢弃 skill-creator 前端漂移**(C3)。无关前端改动;别带进集成分支。
- **把选模型 UI 适配到无 secret 的线缆。** 把 fork UI 的 `modelId` 映射成
  线缆 **`model`** 字段;线缆上绝不放 `apiKey` / `baseUrl` / `_meta`(agentao
  会拒)。凭据经 `provider_resolver` 在 server 端解析。
  - 自由「随便填模型」→ 调厂商方法 `_agentao.cn/set_model`
    (`{sessionId, model}`)。
  - catalog 驱动的选择 → 调标准 `session/set_config_option`
    (`configId="model"`、value `provider/model`)。
- **迁移掉遗留方法。** 待 fork 消费标准 `configOptions` 路径后,agentao 退休
  `session/list_models` 与 `session/set_model` 别名(PR-7)。该退休**以此迁移为
  闸** —— fork 的迁移就是触发条件。
- **需要真正的 provider 切换就注入多 provider resolver + catalog。** agentao
  默认单 provider(当前 `LLM_PROVIDER`)。想要多 provider 的 fork 经**构造
  kwargs / 注入接缝**(无需新设宿主协议)注入自己的 `provider_resolver` +
  catalog —— agentao 侧不建 `providers.json`、不扫 env。

## 决议(已定)

六项均经带引用证据研究(见附录)。结论:

1. **上游目标 → A1/A2/A3 与 B 系列核心修复上游 `agentao/main`;fork 打包(C2)
   与 fork 笔记(D3)不进。** 这*不是*「整个 patch 上游」—— 只有 harness 能力
   组与 ACP 合规组落 `main`。A1(图片输入)、A3(`$HOME`)
   LOW 风险:`add_message` 拓宽到 `Union[str, List]` 向后兼容(`agent.py:724`、
   `messages: List[Dict]` 在 `:259`),host 契约不暴露 messages,`$HOME` fallback
   已存在(`llm/client.py:211`)。A2(结构化 `ask_user`)上游但**必须做向后兼容
   回调** —— `ask_user_callback` 是 deprecated 1 参回调(`agent.py:52`),裸加新参
   会破坏 embedded host。该约束在 PR-2,不阻塞决定。
2. **PyInstaller → 留 fork。** 与库优先定位冲突(单 console script
   `pyproject.toml:63`;`CLAUDE.md:51`「embedded harness」;`pip install agentao`
   = 库)。发二进制是独立产品决策,不在此捆绑。
3. **退休 → `session/list_models` 与 `session/set_model` 别名一起退(PR-7)。**
   内部零阻塞:agentao 自己的 `acp_client` **不**调这两个 wire 方法,CLI
   `/model` / `/sessions resume` 用的是 **core** `agent.set_model()` /
   `list_available_models()`(`cli/commands/provider.py:87,120`),非 wire 方法。
   PR-4 新增厂商 free-form 方法 `_agentao.cn/set_model`(决议 #4);既有
   `session/set_model` 原样保留一个版本作为兼容别名,使新增厂商方法不打断现有
   `session/set_model` 的 caller,之后与 `list_models` 一起退休。退休仅 gated on
   DeepChat 迁移到 `configOptions`。
4. **自由填模型名 → 是,经 `_agentao.cn/set_model`。** DeepChat 发 free-form
   模型串(无白名单;patch 直接 `agent.set_model()`),select-only 的
   `set_config_option` 表达不了。决议:新增厂商方法 `_agentao.cn/set_model`
   (`{sessionId, model}`、无 secret),与标准 select 路径并存;**既有**
   `session/set_model`(`{sessionId, model?, contextLength?, maxTokens?}`,本就
   无 secret)**原样**保留一个版本作为兼容别名。DeepChat 的 adapter 把 UI 的
   `modelId` 映射成 `model` 字段 —— 统一一个字段名,线缆上不出现 `modelId`。
   落在 PR-4(DeepChat 要它才能工作)。见上「Free-form 填模型名」。
5. **`initialize.extensions` → 挪到 `_meta`(PR-6,低优先)。** agentao 自己的
   `acp_client` 不读 `extensions`(0 引用);只影响 schema 快照
   (`docs/schema/host.acp.v1.json`)+ `test_acp_schema.py`。改动小(加 `_meta`、
   重生快照)。不阻塞;排最后。
6. **`set_mode` 权限耦合 → 现在不拆。** 解耦是**大重构**:`_PRESET_RULES[mode.value]`
   查表(`permissions.py:385`)、规则求值顺序依 mode(`:482,570-577`)、子 agent
   传 `PermissionMode` 枚举(`_wrapper.py:525`)、CLI 按 mode 分支。本轮只做最小
   `mode`→`modeId` + 接受未知值(PR-5)。完整轴拆分单独立项、推迟 —— 非 DeepChat
   必需。

## 附录 —— 核实证据

- `agentao/llm/client.py:233` —— `def reconfigure(self, api_key, base_url=None, model=None)`。
- `agentao/embedding/factory.py:71-77` —— `LLM_PROVIDER` + `{PROVIDER}_API_KEY/_BASE_URL/_MODEL`。
- `agentao/acp/protocol.py:47-61` —— 方法常量;仅 `_agentao.cn/ask_user` 带厂商前缀;`set_model`/`list_models` 裸占 `session/` 命名空间。
- ACP 规范(`agentclientprotocol.com`):无 `session/set_model`;选模型走 `session/set_config_option`(`{sessionId, configId, value}`),`ConfigOption{id,name,description,category,type,currentValue,options}`,`category ∈ {mode, model, thought_level}`(有 `model`、**无 `provider`**),`type` 仅 `select`,`value` 为 agent 自定义不透明字符串;`session/set_mode` 字段为 `modeId`,配 `availableModes` / `currentModeId`;`initialize` 响应标准字段为 `protocolVersion` / `agentCapabilities` / `agentInfo` / `authMethods` —— **无顶层 `extensions` 数组**(扩展走 `_meta`)。
- ACP `session/update` 变体含 **`config_option_update`**(刷新 `configOptions`)与 **`current_mode_update`**(`currentModeId`);`session/new` / `session/load` 响应带初始 `configOptions` / `modes` —— 这是取代 `list_models` 的标准动态刷新路径。
- `agentao/` 与 `docs/` 全仓无 `providers.json`(grep:0 命中)。`factory.py` 是**单 provider**:`LLM_PROVIDER` 构造期选一个;无多 provider 注册表。
- `agentao/acp/schema.py:87-115` —— `AcpInitializeExtension` + `AcpInitializeResponse` 顶层 `extensions` 字段。`agentao/acp/initialize.py:140-145` —— 在该数组里声明 `_agentao.cn/ask_user`。
- `agentao/acp/schema.py:306-320` —— `AcpSessionSetModeRequest.mode: Literal["read-only","workspace-write","full-access","plan"]`(非标准 `mode` 字段,锁死为权限预设)。
- 工作树:`agentao/acp_client/` 与 `tests/test_acp_client_*.py` 在 `main` 存在;`agentao/transport/` 无 `acp.py` / `acp_server.py`;`agentao/tools/ask_user.py` 在 `main` 为 `execute(self, question)` 纯文本。
- 决议研究(3 个 Explore agent,2026-05-29):
  - **#3 消费方:** `agentao/acp_client/` 对 `list_models`/`set_model` **0 引用**;CLI 用 **core** `agent.set_model()`/`list_available_models()`(`cli/commands/provider.py:87,120`、`sessions.py:124`),非 wire 方法。只影响 `tests/test_acp_session_set_model.py` + 外部 client。
  - **#4 free-form:** patch 把 DeepChat 的 `modelId` 无白名单直接传 `agent.set_model()`(patch hunk `session_set_model.py` 行 538-539、589-591);现网 `session_set_model.py:52` 只校验非空。`select` 表达不了。
  - **#1 A2 风险:** `ask_user_callback` 是 8 个 deprecated 构造回调之一,签名 `Callable[[str], str]`(`agent.py:52`);也经 `transport/sdk.py:72-75` 暴露。加参破坏 1 参 host 回调。
  - **#5 extensions:** `acp_client/` 对 `extensions` 0 引用;快照 `docs/schema/host.acp.v1.json` + `tests/test_acp_schema.py:102-125` 覆盖;`AcpInitializeResponse` 现为 `extra="forbid"`、无 `_meta` 字段。
  - **#6 mode 耦合:** `_PRESET_RULES[mode.value]`(`permissions.py:385`)、规则顺序依 `active_mode`(`:482,570-577`)、子 agent 传 `PermissionMode` 枚举(`agents/tools/_wrapper.py:525`)、CLI 依 `current_mode` 分支(`cli/input_loop.py`)。解耦 = 大重构。
