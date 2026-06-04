# 多轮工具调用修复

## 问题描述

原来的 `agent.py` 实现只能处理**单轮**工具调用：

1. 用户输入 → LLM 返回工具调用
2. 执行工具 → 再次调用 LLM
3. **直接返回**（不检查第二次响应是否又包含工具调用）

当 LLM 需要多轮工具调用时（例如：先列出目录 → 读取文件 → 写入新文件），程序会在第一轮后停止，导致任务无法完成。

### 典型场景

用户请求："帮我写一个总结会议，写会议纪要的 Skill"

LLM 的执行流程：
1. 第一轮：调用 `list_directory` 和 `codebase_investigator` 查看项目结构
2. 第二轮：调用 `list_directory` (recursive)、`read_file` 查看现有 Skill 示例
3. 第三轮：调用 `write_file` 创建新的 Skill 文件
4. 第四轮：返回完成消息

**原来的实现会在第二轮后停止**，导致任务未完成。

## 解决方案

修改 `Agentao.chat()` 方法，添加循环处理多轮工具调用：

```python
def chat(self, user_message: str, max_iterations: int = 10) -> str:
    # ... 准备消息 ...

    iteration = 0
    while iteration < max_iterations:
        iteration += 1

        # 调用 LLM
        response = self.llm.chat(messages=messages_with_system, tools=tools)
        assistant_message = response.choices[0].message

        # 检查是否有工具调用
        if assistant_message.tool_calls:
            # 执行所有工具调用
            # ... 执行工具 ...

            # 更新消息历史
            # 继续循环
        else:
            # 没有工具调用了，返回最终响应
            return assistant_message.content

    # 达到最大迭代次数
    return "Maximum tool call iterations reached."
```

### 关键改进

1. **循环处理**：持续调用 LLM 直到不再需要工具调用
2. **安全限制**：`max_iterations=10` 防止无限循环
3. **日志追踪**：记录每轮迭代和工具调用数量
4. **异常处理**：保持原有的工具执行错误处理

## 测试

运行测试文件验证修复：

```bash
uv run python test_multi_turn.py
```

或者直接使用 CLI：

```bash
./run.sh
```

然后输入需要多轮工具调用的请求，例如：
- "帮我写一个总结会议，写会议纪要的 Skill"
- "查看 skills 目录，读取一个示例 skill，然后创建一个类似的新 skill"

## 日志示例

修复后，日志会显示多轮迭代：

```
INFO - LLM iteration 1/10
INFO - Processing 2 tool call(s) in iteration 1
INFO - LLM iteration 2/10
INFO - Processing 3 tool call(s) in iteration 2
INFO - LLM iteration 3/10
INFO - Processing 1 tool call(s) in iteration 3
INFO - LLM iteration 4/10
INFO - Reached final response in iteration 4
```

## 影响范围

- **文件**：`agentao/agent.py`
- **方法**：`Agentao.chat()`
- **向后兼容**：完全兼容，只是添加了可选的 `max_iterations` 参数
- **性能影响**：无负面影响，只有在需要多轮工具调用时才会多次调用 LLM

## 注意事项

1. **API 成本**：多轮工具调用会增加 API 调用次数，但这是正确完成任务所必需的
2. **超时**：如果任务非常复杂，可能需要调整 `max_iterations` 参数
3. **无限循环保护**：如果 LLM 陷入循环（不断调用相同工具），会在 10 次迭代后停止

## 后续优化建议

1. **自适应限制**：根据任务复杂度动态调整 `max_iterations`
2. **工具调用去重**：检测重复的工具调用，提前终止
3. **进度显示**：在 CLI 中显示工具执行进度
4. **统计信息**：记录平均需要多少轮迭代完成任务

---

**修复日期**：2026-02-09
**修复版本**：0.1.1
**状态**：✅ 已测试并部署
