# 日期时间上下文注入

## 概述

每个 turn 都会把当前日期和时间告诉 LLM，帮助它理解时间上下文，提高时间相关任务的准确性。

日期**不在系统提示词里**——它作为 `<system-reminder>` 前置到**用户消息**上。这个区分是刻意的，见下文「为什么不放进系统提示词」。

## 实现

**文件**: `agentao/runtime/chat_loop/_runner.py::ChatLoopRunner.run()`

每个 turn 开始时构造 reminder，前置到该 turn 的用户消息：

```python
now = datetime.now()
system_reminder = (
    f"<system-reminder>\n"
    f"Current Date/Time: {now.strftime('%Y-%m-%d %H:%M:%S')} ({now.strftime('%A')})\n"
    f"</system-reminder>\n"
)
```

模型看到的用户消息形如：

```
<system-reminder>
Current Date/Time: 2026-02-11 14:40:58 (Wednesday)
</system-reminder>
今天的活动记一个日志文件
```

## 为什么不放进系统提示词

系统提示词的**稳定前缀**（identity、operational guidelines、`<memory-stable>` 等）被刻意维持成逐 turn **逐字节相同**，好让 provider 的 prompt cache 能复用它（见 `agentao/prompts/builder.py::_build_sections()`）。

时间每秒都在变。把它放进前缀，等于每个 turn 都让缓存失效——为了一行字，付掉整个前缀的缓存收益。放在用户消息里，前缀保持稳定，日期照样每 turn 刷新。

这也是为什么 `tests/test_date_in_prompt.py` 同时断言两边：日期**必须**出现在用户消息里，且**必须不**出现在系统提示词里。任何把它挪回前缀的改动都会让测试变红。

## 日期格式

- **完整格式**: `YYYY-MM-DD HH:MM:SS (Day of Week)`
- **示例**: `2026-02-11 14:40:58 (Wednesday)`
- **组成部分**:
  - 日期: `2026-02-11`（ISO 8601）
  - 时间: `14:40:58`（24 小时制）
  - 星期: `Wednesday`（英文全称）

取的是**本地时间**（`datetime.now()`，不带时区），即宿主进程所在时区。

## 使用场景

时间敏感任务——「今天的日志」「这周的报告」「三天前那次提交」——模型不必猜今天几号，也不必调工具去问。

## 相关

- `agentao/runtime/chat_loop/_runner.py` —— 注入点
- `agentao/prompts/builder.py` —— 系统提示词分段与缓存边界
- `tests/test_date_in_prompt.py` —— 双向断言
