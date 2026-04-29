# Agentao Logging System

## Overview

Agentao 自动记录所有与 LLM 的交互到日志文件，包括完整的请求和响应内容。

## 日志文件

**默认位置**: `agentao.log`（在运行目录）

## 记录内容

### 1. 请求信息 (Request)

每次发送给 LLM 的请求都会记录：

```
================================================================================
[req_1] LLM REQUEST
================================================================================
Model: claude-sonnet-4-5
Temperature: 0.7
Max Tokens: (if specified)

Messages (N total):

  Message 1 [system]:
    Content (XXX chars):
      You are Agentao, a helpful AI assistant...
      (完整的系统提示)

  Message 2 [user]:
    Content (XXX chars):
      用户的完整消息内容...
      (逐行记录，不截断)

  Message 3 [assistant]:
    Content (XXX chars):
      助手的回复...
    Tool Calls: 2
      Tool Call 1:
        Function: read_file
        ID: call_xxx
        Arguments (full):
          {
            "file_path": "example.py"
          }

  Message 4 [tool]:
    Tool: read_file
    Tool Call ID: call_xxx
    Result (XXX chars):
      文件的完整内容...
      (逐行记录，不截断)

Tools (N available):
  - read_file
  - write_file
  - (其他工具...)
```

### 2. 响应信息 (Response)

LLM 返回的响应也会完整记录：

```
================================================================================
[req_1] LLM RESPONSE
================================================================================
Model: claude-sonnet-4-5
Finish Reason: stop

Token Usage:
  Prompt Tokens: 1234
  Completion Tokens: 567
  Total Tokens: 1801

Assistant Response (XXX chars):
  助手的完整回复内容...
  (逐行记录，不截断)

Tool Calls (N):
  Tool: write_file
  ID: call_yyy
  Arguments:
    {
      "file_path": "output.txt",
      "content": "完整的文件内容..."
    }

================================================================================
```

## 日志特性

### ✅ 完整记录

- **不截断内容** - 记录完整的消息、工具参数和结果
- **逐行记录** - 每行独立记录，便于阅读
- **JSON 格式化** - 工具参数自动格式化为易读的 JSON

### ✅ 详细信息

记录的详细信息包括：

1. **请求参数**
   - 模型名称
   - 温度设置
   - 最大 token 数
   - 所有消息（system, user, assistant, tool）
   - 可用工具列表

2. **消息内容**
   - 角色 (role)
   - 内容长度统计
   - 完整文本内容
   - 工具调用详情
   - 工具结果详情

3. **响应数据**
   - 模型版本
   - 完成原因 (finish_reason)
   - Token 使用统计
   - 完整的助手回复
   - 工具调用请求

### ✅ 请求追踪

- 每个请求有唯一 ID (`req_1`, `req_2`, ...)
- 请求和响应通过 ID 关联
- 请求计数器自动递增

## 使用方法

### 自动记录

日志功能默认开启，无需配置：

```bash
# 启动 Agentao
uv run python main.py

# 所有交互会自动记录到 agentao.log
```

### 查看日志

**实时查看**：
```bash
tail -f agentao.log
```

**查看完整日志**：
```bash
cat agentao.log
```

**搜索特定内容**：
```bash
grep "read_file" agentao.log
grep "ERROR" agentao.log
grep "req_5" agentao.log  # 查看特定请求
```

**查看最近 N 行**：
```bash
tail -n 100 agentao.log
```

### 自定义日志位置

在代码中可以指定日志文件位置：

```python
from agentao.llm import LLMClient

client = LLMClient(
    api_key="your-key",
    base_url="https://api.anthropic.com/v1",  # 0.2.16 起必传
    model="claude-sonnet-4-5",
    log_file="custom_location.log",  # 自定义日志文件
)
```

## 日志格式

### 时间戳格式

```
2026-02-09 14:30:45 - agentao.llm - INFO - [req_1] LLM REQUEST
```

格式说明：
- `2026-02-09 14:30:45` - 时间戳
- `agentao.llm` - 日志来源
- `INFO` - 日志级别
- `[req_1]` - 请求 ID
- `LLM REQUEST` - 日志内容

### 分隔线

使用 `=` 字符作为分隔线（80 个字符宽）：

```
================================================================================
[req_1] LLM REQUEST
================================================================================
```

## 日志级别

- **INFO** - 正常的请求/响应记录
- **ERROR** - API 调用失败或异常

## 隐私和安全

### ⚠️ 注意事项

1. **敏感信息** - 日志包含完整的对话内容，可能包含敏感信息
2. **API 密钥** - API 密钥不会记录到日志
3. **文件权限** - 确保日志文件权限设置正确
4. **定期清理** - 日志文件会持续增长，需要定期清理

### 建议

- 在 `.gitignore` 中忽略日志文件（已配置）
- 不要将日志文件提交到版本控制
- 定期归档或删除旧日志
- 在生产环境中考虑日志轮转

## 日志管理

### 清空日志

```bash
# 清空但保留文件
> agentao.log

# 或删除文件
rm agentao.log
```

### 归档日志

```bash
# 按日期归档
mv agentao.log agentao_$(date +%Y%m%d).log

# 压缩归档
gzip agentao_$(date +%Y%m%d).log
```

### 日志轮转

创建 logrotate 配置（Linux/macOS）：

```bash
# /etc/logrotate.d/agentao 或 ~/.logrotate.d/agentao
/path/to/agentao.log {
    daily
    rotate 7
    compress
    missingok
    notifempty
}
```

## 示例日志

### 完整对话示例

```
2026-02-09 14:30:45 - agentao.llm - INFO - LLMClient initialized with model: claude-sonnet-4-5
2026-02-09 14:30:50 - agentao.llm - INFO - ================================================================================
2026-02-09 14:30:50 - agentao.llm - INFO - [req_1] LLM REQUEST
2026-02-09 14:30:50 - agentao.llm - INFO - ================================================================================
2026-02-09 14:30:50 - agentao.llm - INFO - Model: claude-sonnet-4-5
2026-02-09 14:30:50 - agentao.llm - INFO - Temperature: 0.7
2026-02-09 14:30:50 - agentao.llm - INFO -
Messages (2 total):
2026-02-09 14:30:50 - agentao.llm - INFO -
  Message 1 [system]:
2026-02-09 14:30:50 - agentao.llm - INFO -     Content (245 chars):
2026-02-09 14:30:50 - agentao.llm - INFO -       You are Agentao, a helpful AI assistant with access to various tools and skills.
2026-02-09 14:30:50 - agentao.llm - INFO -
2026-02-09 14:30:50 - agentao.llm - INFO -       You can help users with:
2026-02-09 14:30:50 - agentao.llm - INFO -       - Reading, writing, and editing files
2026-02-09 14:30:50 - agentao.llm - INFO -       - Searching for files and text content
2026-02-09 14:30:50 - agentao.llm - INFO -       ...
2026-02-09 14:30:50 - agentao.llm - INFO -
  Message 2 [user]:
2026-02-09 14:30:50 - agentao.llm - INFO -     Content (25 chars):
2026-02-09 14:30:50 - agentao.llm - INFO -       Read the file main.py
2026-02-09 14:30:50 - agentao.llm - INFO -
Tools (13 available):
2026-02-09 14:30:50 - agentao.llm - INFO -   - read_file
2026-02-09 14:30:50 - agentao.llm - INFO -   - write_file
2026-02-09 14:30:50 - agentao.llm - INFO -   ...
2026-02-09 14:30:52 - agentao.llm - INFO - ================================================================================
2026-02-09 14:30:52 - agentao.llm - INFO - [req_1] LLM RESPONSE
2026-02-09 14:30:52 - agentao.llm - INFO - ================================================================================
2026-02-09 14:30:52 - agentao.llm - INFO - Model: claude-sonnet-4-5
2026-02-09 14:30:52 - agentao.llm - INFO - Finish Reason: tool_calls
2026-02-09 14:30:52 - agentao.llm - INFO -
Token Usage:
2026-02-09 14:30:52 - agentao.llm - INFO -   Prompt Tokens: 1234
2026-02-09 14:30:52 - agentao.llm - INFO -   Completion Tokens: 45
2026-02-09 14:30:52 - agentao.llm - INFO -   Total Tokens: 1279
2026-02-09 14:30:52 - agentao.llm - INFO -
Tool Calls (1):
2026-02-09 14:30:52 - agentao.llm - INFO -   Tool: read_file
2026-02-09 14:30:52 - agentao.llm - INFO -   ID: call_abc123
2026-02-09 14:30:52 - agentao.llm - INFO -   Arguments:
2026-02-09 14:30:52 - agentao.llm - INFO -     {
2026-02-09 14:30:52 - agentao.llm - INFO -       "file_path": "main.py"
2026-02-09 14:30:52 - agentao.llm - INFO -     }
2026-02-09 14:30:52 - agentao.llm - INFO - ================================================================================
```

## 调试技巧

### 查找错误

```bash
grep -n "ERROR" agentao.log
```

### 查看特定请求

```bash
grep -A 50 "req_5" agentao.log
```

### 统计 Token 使用

```bash
grep "Total Tokens" agentao.log | awk '{sum += $NF} END {print "Total:", sum}'
```

### 查看工具调用

```bash
grep -B 2 -A 5 "Tool Calls" agentao.log
```

## 配置选项

在初始化 `Agentao` 时，可以传递日志配置：

```python
from pathlib import Path
from agentao.agent import Agentao

agent = Agentao(
    api_key="your-key",
    base_url="https://api.anthropic.com/v1",  # 0.2.16 起必传
    model="claude-sonnet-4-5",
    working_directory=Path.cwd(),
    # log_file 会传递给 LLMClient
)

# 或者走工厂从 .env 自动发现（.env 里设 LLM_PROVIDER + 对应 _API_KEY/_BASE_URL/_MODEL）
from agentao.embedding import build_from_environment
agent = build_from_environment(working_directory=Path.cwd())
```

> 嵌入式 host 的完整接入指南（凭据、能力注入、异步、回放/沙箱/后台等）见
> **[docs/EMBEDDING.md](EMBEDDING.md)**。

## 故障排除

### 日志文件未创建

检查：
1. 当前目录的写入权限
2. 是否有足够的磁盘空间
3. 是否有其他进程占用文件

### 日志内容不完整

可能原因：
1. 程序异常退出
2. 缓冲区未刷新

解决方案：
- 正常退出程序（使用 `exit` 命令）
- Python 日志会自动刷新

### 日志文件过大

定期清理或使用日志轮转：

```bash
# 只保留最近 1000 行
tail -n 1000 agentao.log > agentao_temp.log
mv agentao_temp.log agentao.log
```

## 最佳实践

1. **定期检查** - 定期查看日志了解系统运行状况
2. **定期清理** - 避免日志文件过大占用磁盘
3. **安全存储** - 确保日志文件不被未授权访问
4. **分析 Token** - 通过日志分析 API 使用情况
5. **调试工具调用** - 日志是调试工具问题的最佳资源

---

**更新日期**: 2026-02-09
**功能状态**: ✅ 已实现并测试
**记录方式**: 完整内容，不截断
