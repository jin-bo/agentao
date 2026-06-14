# Host LLM 请求直通：`extra_body`（v1）

**状态：** **已实现（v1）。** 由 goose 2026-06-13 pull 的反向评审发现（发现项 "B"）：LLM 请求 kwargs 是一个封闭集，host 够不到 `reasoning_effort` / `top_p` / `seed` / `response_format` 或任何 provider 专有字段。落地于 `agentao/llm/client.py`（字段 + `_build_request_kwargs` + overlap 告警）、`agentao/llm/_logging.py`（脱敏日志）、`agentao/agent.py`（构造接线 + 守卫 + `_llm_config` 快照）、`agentao/agents/tools/_wrapper.py`（子 agent 继承，§4.2）、`agentao/embedding/factory.py`（`LLM_EXTRA_BODY` 环境变量）、`docs/reference/configuration.md` 及 `tests/test_llm_client_extra_body.py`。
**机制说明：** 早期草案曾提议一个"顶层合并"的 `extra_params` 字典。一次反向评审（对 `openai 2.24.0` 实测）发现 `.create()` **没有 `**kwargs`**——未知顶层键会抛 `TypeError`，而 SDK 传任意 body 字段的官方逃生舱是 **`extra_body`**。故 v1 改为原样转发 `extra_body`，而非把键合进顶层。完整理由见 §2。
**读者：** 构建 host LLM 配置面的 agentao 维护者；实现 PR 的评审者。
**配套文档：**
- `docs/design/host-llm-extra-params.zh.md` —— 英文版为 `host-llm-extra-params.md`
- `docs/design/embedded-host-contract.md` —— host 契约的稳定性边界（本设计归属之处）
- `docs/design/host-tool-injection.md` / `.zh.md` —— 同类 host 注入原语（同样是"把一个显式 kwarg 穿过构造路径、延后 settings.json 层"的形状）
- `docs/reference/configuration.md` —— §2（`.env`）已文档化 `LLM_EXTRA_BODY` 环境变量（settings.json 文件层延后——见 §8）
- `agentao/llm/client.py` —— `__init__`（`def` 在 `90`）、`chat()` kwargs（`318-330`）+ 非流式 `with_raw_response.create`（`339`）、`chat_stream()` kwargs（`450-462`）+ 流式 `create`（`613`）、`reconfigure()`（`def` 在 `251`）——主改动点
- `agentao/llm/_logging.py` —— `_log_request`（`def` 在 `23`）
- `agentao/agent.py` —— `_build_llm_client` 的 `llm_kwargs`（`665-676`）、互斥守卫（`284`）
- `agentao/embedding/factory.py` —— `discover_llm_kwargs()`（`57-82`）

---

## 1. 问题：agentao 没有 LLM 请求直通面

OpenAI 兼容请求在**两处**被装配成一个**封闭的 `kwargs` 字典**：

- `chat()` —— `client.py:318-330` → `client.chat.completions.with_raw_response.create(**kwargs)`（`client.py:339`）
- `chat_stream()` —— `client.py:450-462` → `client.chat.completions.create(**kwargs)`（`client.py:613`）

两处都只构建 `{model, messages, temperature?, tools?, tool_choice?, max_tokens|max_completion_tokens?}`。构造函数（`client.py:90`）只暴露 `api_key / base_url / model / temperature / max_tokens / log_file / logger`——**没有任何字段**承载额外请求参数。

因此 host 无法设置以下任何一项：

- `reasoning_effort`（o 系列 / gpt-5 的推理深度）
- `top_p`、`seed`（可复现）、`response_format`（JSON 模式 / schema）
- `frequency_penalty`、`presence_penalty`、`stop`、`logprobs` ……
- 任何 **provider 专有** body 字段（`top_k`、`repetition_penalty`、厂商扩展……）

今天唯一的绕过手段是**继承 `LLMClient` 并覆写 `chat()`/`chat_stream()`**，或 monkeypatch——两者都触及运行时内部，且不在 `agentao.host` 契约之内。这是一个缺失的 **harness 原语**，与 `host-tool-injection.md` 中补齐的工具注入缺口同构。

## 2. 范围决策：转发 `extra_body`，而非顶层合并字典

**v1 只交付一个 `extra_body: dict`**，原样转发给 `.create()` 作为 SDK 自身的 `extra_body` 请求选项——*而非*把它的键合并进顶层请求 kwargs。

**为何用 `extra_body` 而非顶层合并（对 `openai 2.24.0` 实测）：**

| 理由 | 细节 |
|---|---|
| `.create()` 拒绝未知顶层键 | SDK 签名**没有 `**kwargs`**（`inspect.signature(...)` 无 `VAR_KEYWORD`）。一个扁平未知键——`top_k`、`repetition_penalty`、任意 provider 扩展，或固定下限 `openai>=1.0.0` 上的某个 OpenAI *新*参数——会抛 `TypeError: unexpected keyword argument`，让**每一次**调用崩溃。顶层合并只对装好的 SDK 已类型化的参数有效。 |
| `extra_body` 是 SDK 官方逃生舱 | `extra_body`（`.create()` 的一个类型化参数，1.x 起就有）被 SDK 合并进 JSON 请求体，**绕过类型化签名**。它对 SDK 已知参数（`reasoning_effort`/`top_p`/`seed`/`response_format` 经 `extra_body` 都能正常打到真实 OpenAI）与任意 provider 专有字段**一视同仁**。 |
| 无保留键机制 | `extra_body` 被命名空间隔离——它是 `.create()` 的单个参数，而非一堆顶层键——所以**没有**针对 `messages`/`tools` 等的每请求冲突检查，也没有要维护的 `_RESERVED_PARAMS`。（唯一残留的重叠——`extra_body` 内容被 SDK 合进 *body* 后可能遮蔽某个结构性 body 字段——由一个廉价的**构造时**告警处理，§3.3，而非热路径检查。） |
| 改动更小 | `extra_body` 本就是 `.create(**kwargs)` 的合法键，故它能装在 `kwargs` 字典里穿过两个现有调用点，**零**调用点签名改动（§3.2）。 |

**v1 明确不做：**
- 把逐参数键合并进顶层请求 kwargs（被否的早期草案——见机制说明）。
- 校验 body *值*——host 配置的是它**自己的** LLM 端点；由 SDK / provider 校验。（不属于"不静默代理第三方"——直通是 host 的显式意图，而非被重定向的目的地。）
- 交付 **`extra_headers`**——**延后**（见 §8）。请求头是凭据载体，值得为其日志/脱敏做专门设计；发现 "B" 是 body 参数，单凭 `extra_body` 已完全满足。
- 加 `.agentao/settings.json :: llm.extra_body` 文件层——**延后**（见 §8）。settings.json 今天只是运行时模式 + 内置 agents（`configuration.md:70`）；`_load_settings`（`factory.py:37`）不喂任何 LLM 配置，也不存在可接入的"env > settings" LLM 优先级规则。v1 的两个面是构造 kwarg 与 `LLM_EXTRA_BODY` 环境变量。
- 引入运行时变更面——见 §8（延后的 `/param`）。

## 3. 核心机制

### 3.1 新字段

在 `LLMClient.__init__`（`client.py:90`）的 `max_tokens` 之后：

```python
extra_body: Optional[Dict[str, Any]] = None,
...
# host 提供的请求体直通，原样转发给 .create()。
# 显式 isinstance 守卫：裸 dict(extra_body or {}) 会静默接受 pairs 列表
# （[("x", 1)]），并对其他畸形形状抛 ValueError（而非 TypeError）——
# 改为快速失败、契约清晰。
if extra_body is not None and not isinstance(extra_body, dict):
    raise TypeError("LLMClient.extra_body must be a dict or None.")
self.extra_body: Dict[str, Any] = dict(extra_body or {})
```

`None`/空 → 不转发 → 行为与今天逐字节一致（向后兼容）。

**顺序注意：** 上面的类型校验 + 拷贝不需要 logger，可紧跟在 `self.max_tokens`（`client.py:136`）之后。但 §3.3 的重叠**告警**不行——`self.logger` 要到 `client.py:160`/`162`（在 `self.client` 构建之后）才存在。把它放在 §3.1 类型校验旁会 `AttributeError`。请把告警放到 logger 初始化之后，或让它用模块级 logger。

### 3.2 转发（装在现有 kwargs 字典里）

`extra_body` 本身就是 `.create()` 的合法参数，故把它加进请求 `kwargs` 字典，它便穿过**两个**现有调用点——`with_raw_response.create(**kwargs)`（非流式）与 `_consume_stream` → `create(**kwargs)`（流式）——**无需**改动调用点或 `_consume_stream` 签名。封闭字典目前在 `chat()` 与 `chat_stream()` 中**重复**（正是该缺口易被忽视的原因）；抽出一个构建器，并在其中加这一行：

```python
def _build_request_kwargs(self, messages, tools, max_tokens, *, stream):
    kwargs = {"model": self.model, "messages": messages}
    if stream:
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}
    if not self.omit_temperature:
        kwargs["temperature"] = self.temperature
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    if max_tokens:
        key = "max_completion_tokens" if self._use_max_completion_tokens else "max_tokens"
        kwargs[key] = max_tokens
    if self.extra_body:                 # 空时省略 → 与今天逐字节一致
        kwargs["extra_body"] = self.extra_body
    return kwargs
```

- `chat()` → `_build_request_kwargs(..., stream=False)`
- `chat_stream()` → `_build_request_kwargs(..., stream=True)`

`_emit_nonstreaming`（`client.py:671`）委托给 `chat()`，因此**不是**第三处——只需改两个调用方。抽出构建器是 altitude 清理（去重封闭字典）；特性本身只是 `extra_body` 字段 + 上面那一行。

### 3.3 结构性重叠守卫（构造时、warn-once）

`extra_body` 会被 SDK 合并**进请求体**，故其中的键可能遮蔽某个结构性 body 字段（`model`、`messages`、`stream`、`stream_options`、`tools`、`tool_choice`、`temperature`、`max_tokens`、`max_completion_tokens`）。这是 host 显式、被命名空间隔离的选择——并非与普通 kwargs 静默混合——故 v1 **不**拒绝它。但因遮蔽 `messages` 会很难调试，构造函数在 `extra_body` 与该结构性/受管集有任何键重叠时发出**一次性**告警（而非每请求检查——那会刷屏热路径）。按 §3.1 的顺序注意，此告警须在 logger 初始化**之后**（`client.py:160`）发出，而非紧挨 `__init__` 顶部的类型校验。

### 3.4 与现有一次性 latch 的交互

`chat()`/`chat_stream()` 原样保留其 `except` 内的修正：
- provider 报错时把 `max_tokens` → `max_completion_tokens` 改名。
- temperature 被拒 → 置 `omit_temperature` 并重试。

这些改的是顶层 `kwargs`；`extra_body` 是 latch 从不触碰的独立嵌套对象，故不会冲突。（若 host *有意*把 `temperature`/`max_tokens` 放进 `extra_body`，§3.3 告警会触发，并由 SDK 的 body 合并 last-wins——host 的选择。）

## 4. 构造签名与接线（两条构造路径）

与 `temperature` / `max_tokens` 同样的形状：

**位置告诫（Codex 评审 P1）：** 在 `Agentao.__init__` 上,`extra_body` 必须是**仅关键字**(声明在 `*` 之后),**不能**插进顶部 raw-config 组紧挨 `max_tokens`。与 `LLMClient.__init__`(`self` 后紧跟 `*`,全员仅关键字)不同,`Agentao.__init__` 为向后兼容把 `api_key … plan_session` 保留为**位置-或-关键字**。把 `extra_body` 插进它们中间会让 `max_tokens` 之后的每个旧位置参数错位——调用方写 `Agentao(key, url, model, temp, max_tok, confirmation_cb)` 会把回调绑到 `extra_body`(并触发 dict 类型守卫)。声明为仅关键字则所有既有位置绑定不变;树内每个调用方本就按关键字传它。

| 路径 | 改动 |
|---|---|
| **嵌入式 host** | `Agentao(..., extra_body={"reasoning_effort": "high"})`(仅关键字)→ 穿入 `_build_llm_client` 的 `llm_kwargs`（`agent.py:665-676`），复刻既有的 `temperature` / `max_tokens` 条件分支。**外加 §4.1 的守卫。** |
| **CLI / 环境变量** | `discover_llm_kwargs()`（`factory.py:57`）将 `LLM_EXTRA_BODY` 读为 JSON **对象**，置于 `try/except` 内解析：JSON 非法 → `告警 + 跳过`。**还须拒绝合法但非对象的 JSON**——`LLM_EXTRA_BODY=[]` / `"x"` / `3` 解析得通却是无效配置；要求 `isinstance(parsed, dict)`，把非对象按畸形同等对待（告警 + 跳过），从而由**环境告警策略**治理 env 路径，而非在构造时抛令人困惑的 `TypeError`（§3.1）。**注意：** 这是*有意比*既有 `LLM_TEMPERATURE` / `LLM_MAX_TOKENS` *更宽容*——后者直接调 `float()` / `int()`，遇畸形值会**抛错**（`factory.py:79-82`）；`build_from_environment` 今天只是在提供 `llm_client` 时整体跳过 discovery 才绕开（`factory.py:134`）。`try/except` 与 `isinstance` 检查都必须显式加上——两者都不是从某个既有容错继承来的。 |

### 4.1 构造互斥守卫（必需）

`extra_body` 必须加入构造函数互斥守卫（`agent.py:284`）的原始-LLM-配置集——该集今天只列了 `(api_key, base_url, model, temperature, max_tokens)`：

```python
if llm_client is not None and any(
    v is not None for v in (api_key, base_url, model, temperature, max_tokens, extra_body)
):
    raise ValueError("Agentao(): pass either llm_client= or "
                     "api_key/.../extra_body, not both.")
```

**为何必需而非装饰：** `_resolve_llm_client()` 会立即原样返回注入的 `llm_client`（`agent.py:655`）。若实现者只遵循"穿入 `_build_llm_client`"那条注记，则 `Agentao(llm_client=client, extra_body={...})` 是个**静默空操作**——build 路径根本不执行。注入自有 `LLMClient` 的 host 必须把 `extra_body=` 直接传给*那个*客户端；守卫让这个错误响亮而非静默，与"已完整构造的对象永远胜过其原始-配置同胞"（`agent.py:280`）一致。

### 4.2 子 agent 继承（完整性所需）

子 agent 是**从父 agent 的实时配置快照重建**的——`Agentao._llm_config`（`agent.py`）喂给 `AgentToolWrapper`（`agentao/agents/tools/_wrapper.py`），后者经**原始-配置路径**重建一个全新 `Agentao`。该快照必须包含 `extra_body`（父为空时映射为 `None`），否则父的直通会对**每个子 agent 的 LLM 调用静默丢失**——`reasoning_effort` 丢失、provider 强制 body 字段 400，且只在子 agent 内发生。它与 `temperature` / `max_tokens` 同样被继承（当子 agent 定义钉了不同模型时，适用 §5 同款"host 负责丢弃模型专有键"的告诫）。由实现 PR 的代码评审发现；快照 + wrapper 的 `Agentao(...)` 调用上的 `extra_body=` 参数都是本特性的一部分，而非后续项。

## 5. `reconfigure()` / 切模型语义

`reconfigure()`（`client.py:251`）**保留 `self.extra_body`**——它是实例级 host 配置，而非模型探测出的怪癖（后者是 `reset_capability_latches()` 重置的 latch）。

**已载明的注意事项——无自动恢复（如实写出不对称）：** 不同于 `temperature`——它在模型拒绝时经一次性 `omit_temperature` latch **自动恢复**——残留的 `extra_body` 字段（如切到不支持的模型后仍带 `reasoning_effort`）**没有 latch**：之后**每次**调用都硬 400，host 不清除就不恢复。所以这**并不**完全对标 temperature 先例，而是严格更不宽容。v1 把切换时丢弃模型专有 `extra_body` 键定为 **host 的责任**。未来可加一个针对 `extra_body` 键的"被拒即丢" latch，对标 `omit_temperature`——不在 v1 范围（gap≠need），但点出以免低估严重性。

## 6. 优先级小结

1. 结构性/受管 body 字段（`model`、`messages`、`stream`、`stream_options`、`tools`、`tool_choice`、`temperature`、`max_tokens`/`max_completion_tokens`）由客户端在常规请求构建中设置。
2. `extra_body` 作为 `.create()` 的独立参数转发；SDK 把它合并**进 body**。键冲突时 SDK 的 body 合并是 last-wins（`extra_body` 遮蔽客户端字段）——在构造时一次性提示（§3.3），绝不与顶层 kwargs 静默混合。

## 7. 边界情况

- **日志（v1 显式改动——并非"免费"，必须脱敏）**：`_log_request`（`agentao/llm/_logging.py:23`）记录的是**固定字段集**——`model`、`temperature`、`max_tokens`、`messages`、`tools`——*而非*任意请求 kwargs，故 `extra_body` **不会**自动出现。v1 把 `kwargs.get("extra_body")` 作为单个专门字段记录（无需"扣除保留集"的猜测——它就是一个已知键），且值在 dict、list **和 tuple** 上**递归脱敏**：把键名（小写后）精确等于敏感键集之一的值——任意嵌套深度——在记录前替换为 `***`。该集涵盖 body 式与**头部式**凭据名——`authorization`、`proxy-authorization`、`api_key`/`apikey`/`api-key`/`x-api-key`、`api_token`/`api-token`/`x-api-token`、`token`/`access_token`/`auth_token`/`auth-token`/`x-auth-token`、`secret`/`client_secret`/`client-secret`、`password`、`cookie`/`set-cookie`——因为 host 经 `extra_body` 透传网关头部（如 `{"extra_headers": {"X-Api-Key": "…"}}`）时不能泄漏它们（Codex 评审 P2）。**精确键名匹配，而非子串**，故 `max_tokens` 式或 `*_tokens` 的良性键不会被过度脱敏。理由：`extra_body` 可能嵌套 provider 凭据（有些网关在 body 里收 API key），而当前 logger 有意把 `api_key` 挡在日志外；裸记录 `extra_body` 会重新引入该泄漏。脱敏器是 `_logging.py` 里的一个小递归 helper；针对嵌套、tuple 嵌套、头部式凭据键有测试（§9）。
- **向后兼容**：空/省略的 `extra_body` 不会加进 `kwargs`，故请求 kwargs 与日志都与今天逐字节一致；既有测试不受影响。
- **类型安全**：非字典的 `extra_body` 经 §3.1 的**显式** `isinstance` 守卫在构造时抛 `TypeError`——*而非*仅靠 `dict(extra_body or {})`（后者会接受 pairs 列表，并对其他形状抛 `ValueError`）。

## 8. 延后

三个面有意排除在 v1 之外：

- **`extra_headers` 直通。** 与 `extra_body` 同样的接线，但请求头是**凭据载体**（`Authorization`、`x-api-key`、网关路由令牌），需要专门的日志策略（只记录头**键名**，绝不记录值）。发现 "B" 是 body 参数，已由 `extra_body` 完全满足。待出现具体的网关/鉴权需求时再加 `extra_headers`，届时设计其脱敏。
- **`settings.json :: llm.extra_body` 文件层。** settings.json 今天没有 LLM 配置块（`_load_settings` 在 `factory.py:37` 只喂运行时模式 + 内置 agents），也没有可扩展的"env > settings" LLM 优先级。加它是更宽的决策——会把 settings.json 确立为通用 LLM 配置层（model / temperature / max_tokens 都可能跟进）。待出现具体的"CLI 用户想把 `extra_body` 持久化到文件"需求时再做，且届时设计整个 LLM-settings 块。（与 `host-tool-injection.md` 延后 `tool_options`/settings 同一姿态。）
- **运行时变更（`/param`）。** setter——`LLMClient.update_extra_body(**kw)` 加 CLI `/param set seed 42` / `/param show`——不在 v1。用例（`reasoning_effort`、`top_p`、`seed`、`response_format`）在一个会话内是静态的，构造期路径已完全满足。

## 9. 测试计划

- `extra_body` 被转发进 `chat()` 与 `chat_stream()`——spy 各路径**正确**的 SDK 方法：`with_raw_response.create`（非流式，`client.py:339`）与 `create`（流式，`client.py:613`）；断言调用收到 `extra_body=<该字典>`。
- **向后兼容：** 空/省略的 `extra_body` → `.create()` 调用中不含 `extra_body` 键；请求 kwargs 与当前一致（golden-dict 断言）。
- **结构性重叠告警（§3.3）：** `extra_body={"messages": [...]}` → 构造时一次告警；**无**每请求告警（断言告警只触发一次，而非每次调用）。
- **类型守卫（§3.1）：** `LLMClient(extra_body=[("x", 1)])` 抛 `TypeError`；`extra_body=None` 被接受。
- **构造守卫（§4.1）：** `Agentao(llm_client=<client>, extra_body={...})` 抛 `ValueError`——而非静默空操作。
- **环境容错：** `discover_llm_kwargs()` 解析合法 `LLM_EXTRA_BODY` JSON 对象；非法 JSON → 省略该键 + 告警，不抛错。
- **环境非对象：** `LLM_EXTRA_BODY=[]` / `"x"` / `3`（合法 JSON、非对象）→ 经环境策略省略该键 + 告警，**而非**构造时 `TypeError`。
- **日志脱敏：** 当 `extra_body={"reasoning_effort":"high","api_key":"sk-x"}`（含嵌套用例）时，`_log_request` 显示 `reasoning_effort` 但凭据值为 `***`；良性的 `*_tokens` 式键**不**脱敏；无 `extra_body` 时该行缺省。
- **reconfigure()：** 保留 `extra_body`；`reset_capability_latches()` 仍清除 latch。

## 10. 改动点 / 影响范围

| 文件 | 改动 |
|---|---|
| `agentao/llm/client.py` | 加 `extra_body` 字段 + 显式 `isinstance` 守卫（§3.1）+ 构造时结构性重叠告警（§3.3）；经 `_build_request_kwargs`（§3.2）加 `kwargs["extra_body"]` 那一行；`reconfigure()` 中保留 |
| `agentao/llm/_logging.py` | 在 `_log_request` 中记录 `kwargs.get("extra_body")`，敏感键的值**递归脱敏**（精确键名匹配）（§7） |
| `agentao/agent.py` | 加 `extra_body` kwarg；穿入 `_build_llm_client`；**加入互斥守卫**（§4.1）；把 `extra_body` 加进 `_llm_config` 快照让子 agent 继承（§4.2） |
| `agentao/agents/tools/_wrapper.py` | 从实时配置快照读 `extra_body` 并传给子 agent 的 `Agentao(...)`（§4.2） |
| `agentao/embedding/factory.py` | 在 `try/except` + `isinstance(parsed, dict)` 中解析 `LLM_EXTRA_BODY` JSON（畸形**或**非对象 → 告警 + 跳过） |
| `docs/reference/configuration.md` | 文档化 `LLM_EXTRA_BODY` 环境变量（§2）——**非** settings 字段（延后，§8） |
| `tests/test_llm_client_*.py` | 按 §9 新增覆盖 |

净效果：没有 `_RESERVED_PARAMS` 集，没有每请求冲突/告警循环，没有 `_consume_stream` 签名改动——特性就是 `extra_body` 字段加上（去重后）请求构建器里的一行。抽出 `_build_request_kwargs` **移除**了重复的封闭字典——是简化，而非纯增量。
