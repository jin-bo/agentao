# Host LLM 额外参数直通：`extra_params`（v1）

**状态：** **设计阶段——尚未实现。** 由 goose 2026-06-13 pull 的反向评审发现（发现项 "B"）：LLM 请求 kwargs 是一个封闭集，host 够不到 `reasoning_effort` / `top_p` / `seed` / `response_format` 或任何 provider 专有字段。
**读者：** 构建 host LLM 配置面的 agentao 维护者；实现 PR 的评审者。
**配套文档：**
- `docs/design/host-llm-extra-params.md` —— 英文版
- `docs/design/embedded-host-contract.md` —— host 契约的稳定性边界（本设计归属之处）
- `docs/design/host-tool-injection.md` / `.zh.md` —— 同类 host 注入原语（同样是"把一个显式 kwarg 穿过构造路径、延后 settings.json 层"的形状）
- `docs/reference/configuration.md` —— §2（`.env`），`LLM_EXTRA_PARAMS` 环境变量文档所在（settings.json 文件层延后——见 §2 / §8）
- `agentao/llm/client.py` —— `__init__`（`90-100`）、`chat()` kwargs（`318-330`）、`chat_stream()` kwargs（`450-462`）、`reconfigure()`（`270-282`）——主改动点
- `agentao/agent.py` —— `_build_llm_client` 的 `llm_kwargs`（`665-676`）
- `agentao/embedding/factory.py` —— `discover_llm_kwargs()`（`57-82`）

---

## 1. 问题：agentao 没有 LLM 额外参数面

OpenAI 兼容请求在**两处**被装配成一个**封闭的 `kwargs` 字典**：

- `chat()` —— `client.py:318-330`
- `chat_stream()` —— `client.py:450-462`

两处都只构建 `{model, messages, temperature?, tools?, tool_choice?, max_tokens|max_completion_tokens?}`，再传给 `client.chat.completions.create(**kwargs)`。构造函数（`client.py:90-100`）只暴露 `api_key / base_url / model / temperature / max_tokens / log_file / logger`——**没有任何字段**承载额外请求参数。

因此 host 无法设置以下任何一项：

- `reasoning_effort`（o 系列 / gpt-5 的推理深度）
- `top_p`、`seed`（可复现）、`response_format`（JSON 模式 / schema）
- `frequency_penalty`、`presence_penalty`、`stop`、`logprobs` ……
- SDK 自带的 `extra_body` / `extra_headers` 逃生舱（用于非标准 provider）

今天唯一的绕过手段是**继承 `LLMClient` 并覆写 `chat()`/`chat_stream()`**，或 monkeypatch——两者都触及运行时内部，且不在 `agentao.host` 契约之内。这是一个缺失的 **harness 原语**，与 `host-tool-injection.md` 中补齐的工具注入缺口同构。

## 2. 范围决策：一个通用字典，而非四个具名参数

**v1 只交付一个 `extra_params: dict`**，合并进请求 kwargs——*而非*逐参数的构造函数实参。

| 为何通用 | 理由 |
|---|---|
| 覆盖四个具名场景及其余一切 | `reasoning_effort` / `top_p` / `seed` / `response_format` 只是字典键；未来或 provider 专有参数同理。 |
| provider 加旋钮时零改动 | OpenAI 新增参数无需任何 agentao 改动。 |
| 内置逃生舱 | `extra_params={"extra_body": {...}}` 经 OpenAI SDK 自身机制转发任意非 SDK 字段。 |
| 与现有姿态一致 | 复刻 `temperature` / `max_tokens` 穿过构造路径的方式——不引入新的配置*层*，只多一个显式 kwarg。 |

**v1 明确不做：**
- 不加具名的 `reasoning_effort=` / `seed=` 构造实参（字典已涵盖；仅当某参数日后需要 agentao 侧校验或 latch 行为时才提升为具名参数）。
- 不校验参数*值*——host 配置的是它**自己的** LLM 端点；由 SDK / provider 校验。（这不属于"不静默代理第三方"的场景——直通是 host 的显式意图，而非被重定向的目的地。）
- 不加 `.agentao/settings.json :: llm.extra_params` 文件层——**延后**（见 §8）。settings.json 今天只是运行时模式 + 内置 agents（`configuration.md:70`）；`_load_settings`（`factory.py:37`）不喂任何 LLM 配置，也**不存在**可供接入的"env > settings" LLM 优先级规则。`host-tool-injection.md` 已立下"待出现具体需求再加 settings 文件层"的先例（gap≠need）。v1 的两个面是构造 kwarg 与 `LLM_EXTRA_PARAMS` 环境变量。
- 不引入运行时变更面——见 §8（延后的 `/param`）。

## 3. 核心机制

### 3.1 新字段

在 `LLMClient.__init__`（`client.py:90-100`）的 `max_tokens` 之后：

```python
extra_params: Optional[Dict[str, Any]] = None,
...
# host 提供的直通参数，合并进每次请求（保留键除外）。
# 显式 isinstance 守卫：裸 dict(extra_params or {}) 会静默接受 pairs 列表
# （[("x", 1)]），并对其他畸形形状抛 ValueError（而非 TypeError）——
# 改为快速失败、契约清晰。
if extra_params is not None and not isinstance(extra_params, dict):
    raise TypeError("LLMClient.extra_params must be a dict or None.")
self.extra_params: Dict[str, Any] = dict(extra_params or {})
```

`None` → 空字典 → 行为与今天逐字节一致（向后兼容）。

### 3.2 集中化的请求 kwargs 构建器（altitude 修复）

封闭字典目前在 `chat()` 与 `chat_stream()` 中**重复**——这正是该缺口容易被忽视的原因。抽出一个构建器，让两处都走它：

```python
# 客户端在结构上拥有、或经一次性 latch 管理的键；host 不可经 extra_params 覆盖。
_RESERVED_PARAMS = frozenset({
    "model", "messages", "stream", "stream_options",
    "tools", "tool_choice",
    "temperature", "max_tokens", "max_completion_tokens",
})

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
    # host 直通最后合并；保留键丢弃 + 告警。
    for k, v in self.extra_params.items():
        if k in _RESERVED_PARAMS:
            self.logger.warning("extra_params: ignoring reserved key %r", k)
            continue
        kwargs[k] = v
    return kwargs
```

- `chat()` → `_build_request_kwargs(..., stream=False)`
- `chat_stream()` → `_build_request_kwargs(..., stream=True)`

`_emit_nonstreaming`（`client.py:671`）委托给 `chat()`，因此**不是**第三处——只需改两个调用方。

### 3.3 与现有一次性 latch 的交互

`chat()`/`chat_stream()` 原样保留其 `except` 内的修正：
- provider 报错时把 `max_tokens` → `max_completion_tokens` 改名。
- temperature 被拒 → 置 `omit_temperature` 并重试。

由于两个被管理键都是**保留键**，`extra_params` 永不会与这些 latch 冲突。保留键保护正是合并安全的根本。

## 4. 构造签名与接线（两条构造路径）

与 `temperature` / `max_tokens` 同样的形状：

| 路径 | 改动 |
|---|---|
| **嵌入式 host** | `Agentao(..., extra_params={"reasoning_effort": "high"})` → 穿入 `_build_llm_client` 的 `llm_kwargs`（`agent.py:665-676`），复刻既有的 `temperature` / `max_tokens` 条件分支。**外加 §4.1 的守卫。** |
| **CLI / 环境变量** | `discover_llm_kwargs()`（`factory.py:57`）将 `LLM_EXTRA_PARAMS` 读为 JSON 对象，置于 `try/except` 内解析：JSON 非法 → `告警 + 跳过`。**注意：** 这是*有意比*既有 `LLM_TEMPERATURE` / `LLM_MAX_TOKENS` *更宽容*——后者直接调 `float()` / `int()`，遇畸形值会**抛错**（`factory.py:79-82`）；`build_from_environment` 今天只是在提供 `llm_client` 时整体跳过 discovery 才绕开（`factory.py:134`）。`try/except` 必须显式加上——它**不是**从某个既有容错继承来的。 |

### 4.1 构造互斥守卫（必需）

`extra_params` 必须加入构造函数互斥守卫（`agent.py:284`）的原始-LLM-配置集——该集今天只列了 `(api_key, base_url, model, temperature, max_tokens)`：

```python
if llm_client is not None and any(
    v is not None for v in (api_key, base_url, model, temperature, max_tokens, extra_params)
):
    raise ValueError("Agentao(): pass either llm_client= or "
                     "api_key/.../extra_params, not both.")
```

**为何必需而非装饰：** `_resolve_llm_client()` 会立即原样返回注入的 `llm_client`（`agent.py:655`）。若实现者只遵循"穿入 `_build_llm_client`"那条注记，则 `Agentao(llm_client=client, extra_params={...})` 是个**静默空操作**——build 路径根本不执行。注入自有 `LLMClient` 的 host 必须把 `extra_params=` 直接传给*那个*客户端；守卫让这个错误响亮而非静默，与"已完整构造的对象永远胜过其原始-配置同胞"（`agent.py:280`）一致。

## 5. `reconfigure()` / 切模型语义

`reconfigure()`（`client.py:270-282`）**保留 `self.extra_params`**——它们是实例级 host 配置，而非模型探测出的怪癖（后者是 `reset_capability_latches()` 重置的 latch）。

**已载明的注意事项：** 模型专有参数（如在不支持的模型上设 `reasoning_effort`）由 **host 负责**在切换时清除。这与既有契约一致：temperature *仅*在 provider 拒绝时才自动 latch 关闭；agentao 不预校验逐模型的参数适用性。若出现真实痛点，未来可在切换时丢弃已知的模型专有键——不在 v1 范围（gap≠need）。

## 6. 优先级与保留键保护（小结）

1. 结构键（`model`、`messages`、`stream`、`stream_options`、`tools`、`tool_choice`）——永远归客户端。
2. 被管理键（`temperature`、`max_tokens`/`max_completion_tokens`）——经 latch 归客户端。
3. 其余一切——经 `extra_params` 归 host，最后合并。
4. `extra_params` 中出现的保留键被**丢弃并告警**，绝不静默生效。

## 7. 边界情况

- **日志（v1 显式改动——并非"免费"）**：`_log_request`（`agentao/llm/_logging.py:23`）记录的是**固定字段集**——`model`、`temperature`、`max_tokens`、`messages`、`tools`——*而非*任意请求 kwargs，故直通参数**不会**自动出现。v1 增加一行，记录合并后的非保留额外键（如 `Extra params: {'reasoning_effort': 'high'}`），以便 host 确认实际发送了什么。这在范围内且有测试（§9），不是顺带的福利。
- **向后兼容**：省略 `extra_params` 产生逐字节一致的请求 kwargs；既有测试不受影响。
- **类型安全**：非字典的 `extra_params` 经 §3.1 的**显式** `isinstance` 守卫在构造时抛 `TypeError`——*而非*仅靠 `dict(extra_params or {})`（后者会接受 pairs 列表，并对其他形状抛 `ValueError`）。

## 8. 延后

两个面有意排除在 v1 之外：

- **`settings.json :: llm.extra_params` 文件层。** settings.json 今天没有 LLM 配置块（`_load_settings` 在 `factory.py:37` 只喂运行时模式 + 内置 agents），也没有可扩展的"env > settings" LLM 优先级。加它是比本特性*更宽*的决策——会把 settings.json 确立为通用 LLM 配置层（model / temperature / max_tokens 都可能跟进）。待出现具体的"CLI 用户想把 `extra_params` 持久化到文件"需求时再做，且届时设计整个 LLM-settings 块，而非一次性的 `extra_params` 键。（与 `host-tool-injection.md` 延后 `tool_options`/settings 同一姿态。）
- **运行时变更（`/param`）。** setter——`LLMClient.update_extra_params(**kw)` 加 CLI `/param set seed 42` / `/param show`——不在 v1。所列用例（`reasoning_effort`、`top_p`、`seed`、`response_format`）在一个会话内是静态的，构造期路径已完全满足。待出现具体的"会话中途改参数"需求时再建运行时面。

## 9. 测试计划

- `extra_params` 合并进 `chat()` 与 `chat_stream()` 的 `create(**kwargs)`（spy SDK 调用；断言键存在）。
- `extra_params` 中的保留键（`messages` / `temperature` / `model`）被丢弃 + 告警；结构 kwargs 完好。
- `reconfigure()` 保留 `extra_params`；`reset_capability_latches()` 仍清除 latch。
- **构造守卫（F1）：** `Agentao(llm_client=<client>, extra_params={...})` 抛 `ValueError`——而非静默空操作。
- **类型守卫（F4）：** `LLMClient(extra_params=[("x", 1)])` 抛 `TypeError`；`extra_params=None` 被接受。
- **环境容错（F3）：** `discover_llm_kwargs()` 解析合法 `LLM_EXTRA_PARAMS` JSON；非法 JSON → 省略该键 + 告警，不抛错。
- **日志（F5）：** 设了 `extra_params` 时，`_log_request` 输出合并后的非保留键；未设时该日志行缺省。
- 向后兼容：无 `extra_params` → 请求 kwargs 与当前一致（golden-dict 断言）。

## 10. 改动点 / 影响范围

| 文件 | 改动 |
|---|---|
| `agentao/llm/client.py` | 加字段 + 显式 `isinstance` 守卫（§3.1）+ `_build_request_kwargs`；让 `chat()` / `chat_stream()` 走它；`reconfigure()` 中保留 |
| `agentao/llm/_logging.py` | 在 `_log_request` 中记录合并后的非保留额外键（§7） |
| `agentao/agent.py` | 加 `extra_params` kwarg；穿入 `_build_llm_client`；**加入互斥守卫**（§4.1） |
| `agentao/embedding/factory.py` | 在 `try/except` 中解析 `LLM_EXTRA_PARAMS` JSON（畸形 → 告警 + 跳过） |
| `docs/reference/configuration.md` | 文档化 `LLM_EXTRA_PARAMS` 环境变量（§2）——**非** settings 字段（延后，§8） |
| `tests/test_llm_client_*.py` | 按 §9 新增覆盖 |

净效果：抽出 `_build_request_kwargs` **移除**了重复的封闭字典——是简化，而非纯增量。
