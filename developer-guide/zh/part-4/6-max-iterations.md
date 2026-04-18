# 4.6 最大迭代数兜底策略

`max_iterations`（默认 100）是防止 Agent 陷入死循环的**保险丝**。当 Agent 连续调用了 100 轮工具还没给出最终回复时，Agentao 调用 `transport.on_max_iterations(count, messages)` 让你决定怎么办。

## 接口约定

```python
def on_max_iterations(self, count: int, messages: list) -> dict: ...
```

| 参数 | 说明 |
|------|------|
| `count` | 已跑过的迭代次数（= `max_iterations`，通常是 100） |
| `messages` | 完整对话历史（含工具调用），可供你做启发式判断 |

**返回值**：必须是含 `"action"` 键的 dict：

| `action` 值 | 效果 | 需要的额外 key |
|-----------|------|-------------|
| `"continue"` | 再给 `max_iterations` 轮继续跑 | 无 |
| `"stop"` | 终止，`chat()` 返回当前最后一条 LLM 文本 | 无 |
| `"new_instruction"` | 向历史注入一条 user 消息，重置计数器继续 | `"message": str` 必填 |

## 默认行为（NullTransport）

未设置该回调时，`NullTransport.on_max_iterations` 返回：

```python
{"action": "stop"}
```

Agent 立即停下、返回当前结果。这对**批处理**是合理的兜底，但对**交互式场景**可能丢失重要进度。

## 策略 1 · 直接停（最保守）

```python
def on_max_iterations(count, messages):
    return {"action": "stop"}
```

适合场景：
- 生产 API，宁可返回"未完成"也不跑飞
- 对耗时/成本敏感
- 你做了监控，愿意为"未完成"单独告警

## 策略 2 · 总结收尾

```python
def on_max_iterations(count, messages):
    return {
        "action": "new_instruction",
        "message": (
            "你已经连续调用了多轮工具。请**不要再调用任何工具**，"
            "基于当前已获取的信息给出最终答复。"
        ),
    }
```

这是**最实用**的策略——让 LLM 自己从"工具调用模式"切到"总结模式"，用户能拿到有意义的结果而不是啥也没有。

适合场景：
- 用户对话型产品（客服、助手）
- 可以接受"部分信息的回答"

## 策略 3 · 问用户

```python
def on_max_iterations(count, messages):
    # 交互式追问
    choice = user_transport.ask_user(
        f"Agent has made {count} tool calls without finishing. "
        f"Continue / Stop / Ask it to summarize?"
    )
    if choice.lower().startswith("c"):
        return {"action": "continue"}
    if choice.lower().startswith("s"):
        return {"action": "stop"}
    return {
        "action": "new_instruction",
        "message": "请基于现有信息给出最终答复，不再调用工具。",
    }
```

适合场景：
- CLI 或桌面应用
- 用户能及时看到进度并决策

## 策略 4 · 条件续跑

```python
_max_grants = {}  # 每会话续了几次

def on_max_iterations(count, messages):
    sid = get_current_session_id()
    _max_grants[sid] = _max_grants.get(sid, 0) + 1

    # 最多续 2 次，超过就强停
    if _max_grants[sid] >= 3:
        return {
            "action": "new_instruction",
            "message": "已多次扩展上限，请立即给出最终答复，禁止再调工具。",
        }

    # 检查最后一次工具调用——如果在重复做同样的事，很可能卡住了
    last_tools = [
        m.get("name") for m in messages[-20:] if m.get("role") == "tool"
    ]
    if len(set(last_tools)) <= 1 and len(last_tools) >= 5:
        # 重复调用同一个工具 >= 5 次 = 明显卡死
        return {"action": "stop"}

    return {"action": "continue"}
```

这个策略把简单启发式和分级限制放在一起，避免"无限续"。

## 策略 5 · 基于 token 预算

如果你按 token 收费（SaaS），续跑需要扣预算：

```python
def make_on_max_iterations(budget: TokenBudget):
    def handler(count, messages):
        tokens_used = rough_count_tokens(messages)
        if not budget.try_reserve(tokens_used * 2):  # 续一轮至少再烧这么多
            return {
                "action": "new_instruction",
                "message": "预算不足，请立即给出最终答复。",
            }
        return {"action": "continue"}
    return handler
```

## 如何读 `messages` 判断卡死模式

`messages` 是 OpenAI 格式的完整历史。常见"卡死"特征：

### 特征 1：反复调用同一工具

```python
def is_stuck_on_one_tool(messages, window=10, threshold=0.8):
    recent = [m for m in messages[-window:] if m.get("role") == "tool"]
    if not recent: return False
    names = [m.get("name") for m in recent]
    most_common = max(set(names), key=names.count)
    return names.count(most_common) / len(names) >= threshold
```

### 特征 2：工具总报错

```python
def is_all_errors(messages, window=10):
    recent = [m for m in messages[-window:] if m.get("role") == "tool"]
    if not recent: return False
    error_count = sum(1 for m in recent if "Error" in str(m.get("content", "")))
    return error_count == len(recent)
```

### 特征 3：没有任何文本回复进展

```python
def no_text_progress(messages, window=30):
    # 过去 window 条里没有一条 assistant 文本
    for m in messages[-window:]:
        if m.get("role") == "assistant" and m.get("content"):
            return False
    return True
```

## 完整综合示例

```python
from agentao.transport import SdkTransport

def make_smart_bailout(user_notify):
    _retries = {"count": 0}

    def handler(count, messages):
        _retries["count"] += 1

        # 第一次：自动总结（对用户透明）
        if _retries["count"] == 1:
            user_notify(f"Agent 已调用 {count} 轮工具，要求总结收尾。")
            return {
                "action": "new_instruction",
                "message": "请基于目前信息直接给出最终答复，不再调用任何工具。",
            }

        # 第二次：条件续跑或停
        if is_all_errors(messages, window=10):
            user_notify("检测到连续错误，停止运行。")
            return {"action": "stop"}
        if is_stuck_on_one_tool(messages):
            user_notify("检测到工具循环，停止运行。")
            return {"action": "stop"}

        # 最后兜底：停
        user_notify("已多次兜底，停止。")
        return {"action": "stop"}

    return handler


transport = SdkTransport(
    on_event=handle_event,
    confirm_tool=confirm,
    on_max_iterations=make_smart_bailout(user_notify=log.info),
)
```

## 选择合适的 `max_iterations` 默认值

也别忘了上游——可以在构造 Agent 时调整初始上限：

```python
reply = agent.chat("大任务", max_iterations=200)  # 特定 chat() 放宽
```

经验参考：

| 场景 | 建议值 |
|------|-------|
| 单次简单问答 | 20–30 |
| 长流程工具链（数据分析、代码生成） | 80–150 |
| 批处理 / 研究型任务 | 200–500 |

**不要盲目设成极大值**——`max_iterations` 是**你能得到 on_max_iterations 通知的唯一机制**，设成无穷就永远不会触发兜底。

## 与事件流的结合

`TURN_START` 事件每轮都会发。你可以在事件层独立做"进度条"和"接近上限警告"：

```python
iteration_count = 0

def on_event(ev):
    global iteration_count
    if ev.type == EventType.TURN_START:
        iteration_count += 1
        ui.set_progress(iteration_count, MAX)
        if iteration_count == MAX - 20:
            ui.warn("Approaching iteration limit...")
```

兜底策略 + 前端进度感知 = 好的用户体验。

---

**第 4 部分到此完成。** 你现在掌握了从 Agent 事件到用户 UI 的完整桥接路径。下一部分讲如何让 Agent **理解你的业务**——通过自定义工具、技能、MCP、权限。

→ [第 5 部分 · 扩展点](/zh/part-5/)（撰写中）
