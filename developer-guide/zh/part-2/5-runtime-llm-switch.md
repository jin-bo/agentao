# 2.5 运行时切换 LLM

一个 `Agentao` 实例构造时绑定一个 LLM。但你常常想**动态决定下一轮用哪个模型**：小问题走便宜模型、规划类问题走贵模型、主 provider 超时就退到备用。本节讲三个公开 API，教你不重建 agent 就做到这件事。

## 2.5.1 三个方法

```python
# 查询
current = agent.get_current_model()   # -> "gpt-5.4"

# 只换模型——provider / API key 不变
agent.set_model("gpt-5.4")

# 整套切换——key + endpoint + model
agent.set_provider(
    api_key="sk-deepseek-xxx",
    base_url="https://api.deepseek.com",
    model="deepseek-chat",
)

# 列出当前 endpoint 声明的模型
models = agent.list_available_models()  # -> ["gpt-5.4", "gpt-5.4", ...]
```

四个都是**同步**、**便宜**（`set_*` 本身不发网络请求），只改进程内状态；下一次 `chat()` 就会用新设置。

## 2.5.2 切换会变什么，不会变什么

| 切换会改 | 切换不改 |
|---------|---------|
| LLM 客户端的 `model` / `api_key` / `base_url` | `agent.messages`（整段历史） |
| tiktoken 编码（用于 token 估算） | 激活中的技能 |
| token 计数缓存（`_last_api_prompt_tokens` 置空） | 记忆（两种作用域） |
| `agentao.log` 里一行 `"Model changed from X to Y"` | 工作目录 / transport / MCP |

**重要**：对话历史**不会清空**。新模型会看到旧模型看过的所有历史，包括工具调用轨迹。如果不希望这种上下文渗透，先调用 `clear_history()` 再 swap。

## 2.5.3 常见路由模式

### 快便宜 / 慢聪明 的路由

```python
CHEAP_MODEL     = "gpt-5.4"
SMART_MODEL     = "gpt-5.4"
SMART_KEYWORDS  = ("规划", "设计", "重构", "架构", "plan", "design")

def route(agent: Agentao, user_message: str) -> str:
    wants_smart = any(kw in user_message.lower() for kw in SMART_KEYWORDS)
    target = SMART_MODEL if wants_smart else CHEAP_MODEL
    if agent.get_current_model() != target:
        agent.set_model(target)
    return agent.chat(user_message)
```

### 多 provider 级联退路

```python
PROVIDERS = [
    {"api_key": os.environ["OPENAI_API_KEY"], "base_url": None,                         "model": "gpt-5.4"},
    {"api_key": os.environ["DEEPSEEK_API_KEY"], "base_url": "https://api.deepseek.com", "model": "deepseek-chat"},
    {"api_key": os.environ["MOONSHOT_API_KEY"], "base_url": "https://api.moonshot.cn/v1","model": "moonshot-v1-8k"},
]

def chat_with_fallback(agent: Agentao, msg: str) -> str:
    last_err = None
    for p in PROVIDERS:
        try:
            agent.set_provider(**p)
            return agent.chat(msg)
        except Exception as e:      # 限流 / 超时 / refusal …
            last_err = e
            continue
    raise RuntimeError(f"所有 provider 都失败：{last_err}") from last_err
```

注意：这是在**同一轮**上让下一个 provider 重试。历史保留，所以第二个 provider 会看到同一条用户消息 + 可能的半截 assistant 输出——这通常是你要的。但如果第一个 provider 已经提交了 tool call，就**不是**你要的——这种情况下先 `clear_history()` 再重放最小上下文。

### 多租户模型选择（SaaS）

```python
def build_agent_for(tenant: Tenant) -> Agentao:
    agent = Agentao(
        working_directory=tenant.workdir,
        model=tenant.plan.default_model,     # "gpt-5.4" 或 "gpt-5.4"
    )
    return agent

# 租户中途升级套餐时
agent.set_model(tenant.plan.default_model)
```

### 在线 A/B 测试

```python
shadow_reply = None
if random.random() < 0.05:                 # 5% 流量打 challenger
    agent.set_model("gpt-5.4")
    shadow_reply = agent.chat(msg)
    agent.set_model("gpt-5.4")         # 恢复默认
real_reply = agent.chat(msg)
log_ab(real=real_reply, shadow=shadow_reply)
```

⚠️ 单轮调两次 `chat()` 会把费用翻倍，并且给历史里塞**两条** assistant 回复。认真做 A/B 请用**两个独立的 `Agentao` 实例**，把同一 prompt 分别跑。

## 2.5.4 `list_available_models()` — 何时调，何时别调

```python
def list_available_models(self) -> List[str]
```

会打 provider 的 `/models` 端点。适合：

- 填充 UI 模型下拉框
- 构造后立刻验证凭据是否可用（401 在这里就暴露，不必等 3 轮后才炸）
- 在 `set_model(...)` 前校验目标模型确实存在

**不要**每轮都调——这是网络 IO，结果请缓存。

失败（网络错误、Key 错）时会**抛** `RuntimeError`，自己兜住：

```python
try:
    models = agent.list_available_models()
except RuntimeError:
    models = [agent.get_current_model()]  # 降级用当前模型
```

## 2.5.5 坑

1. **跨 provider 的 tokenizer 不一致**
   `set_provider()` 会按新模型名换 tiktoken 编码；但 tiktoken 本身是 OpenAI 的，对 DeepSeek/Moonshot/Qwen 等非 OpenAI 家只能**近似估算**。预算留点余量

2. **工具调用 schema 依赖 provider**
   所有 Agentao 支持的 provider 必须讲 **OpenAI 兼容**的 tool-calls schema。换到不兼容的 provider，`chat()` 要么报 "function not supported"，要么悄无声息地丢 tool call。级联链里每个 provider 都要测

3. **流式行为可能不同**
   不同 provider 的 chunk 切分、首 token 延迟不同。切换后 UI 感受可能明显变化。`Transport.emit(...)` 仍会照样触发——只是 chunk 边界不一样

4. **上下文窗口会变**
   `gpt-5.4` 128k、`gpt-5.4` 128k、`deepseek-chat` 64k。`set_model("deepseek-chat")` 后，原本装得下的长历史可能触发压缩。上下文管理器会自动处理——但会出现摘要块，心里有数

5. **chat() 进行中切换是未定义行为**
   不要在 `chat()` 里面调 `set_model()`——`chat()` 持有 LLM 客户端的引用，切换要下一轮才生效。要做请用 `clear_history()` + `set_model()` + 新的 `chat()` 这个组合

## 2.5.6 为什么 provider 不是构造参数

你会注意到 `Agentao(...)` 直接收 `api_key / base_url / model`，没有抽象的 `provider=`。这是故意的：

- Agentao 只说 OpenAI 兼容协议
- 任何暴露 OpenAI schema 的 provider（OpenAI、Azure、DeepSeek、Moonshot、Together、本地 Ollama / vLLM……）都能直连
- "切换 provider" 就等于"切换 key + base_url + model"

所以没有 `OpenAIProvider` / `AnthropicProvider` / `GoogleProvider` 抽象——路由代码保持简单。需要原生 Anthropic / Gemini 请套一个 OpenAI 兼容网关。

---

下一节：[2.6 取消与超时 →](./6-cancellation-timeouts)
