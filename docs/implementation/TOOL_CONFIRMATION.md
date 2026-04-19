# Tool Confirmation Feature

## 概述

为了提高安全性，Agentao 现在在执行 Shell 命令和 Web 工具之前会要求用户确认。这可以防止意外或潜在危险的操作。

## 需要确认的工具

以下工具在执行前需要用户确认：

### 🔧 Shell 工具
- **`run_shell_command`** - 执行 Shell 命令
  - 可能执行危险命令（如 `rm -rf`）
  - 可能修改系统状态
  - 需要用户明确同意

### 🌐 Web 工具
- **`web_fetch`** - 获取网页内容
  - 可能访问敏感网站
  - 可能产生网络流量
  - 需要用户知晓访问的 URL

- **`web_search`** - 网页搜索
  - 可能暴露搜索意图
  - 可能产生网络请求
  - 需要用户确认搜索内容

## 工作原理

### 确认流程

```
LLM 决定使用工具
    ↓
检查工具是否需要确认
    ↓
是 → 显示确认提示 → 等待用户输入
    ↓               ↓
    确认          拒绝
    ↓               ↓
执行工具      取消执行并通知 LLM
```

### 确认提示示例

当工具需要确认时，CLI 会显示：

```
⚠️  Tool Confirmation Required
Tool: run_shell_command
Description: Execute a shell command and return its output. Use with caution as it can execute any command.
Arguments:
  • command: ls -la
  • working_directory: .
  • timeout: 30

Do you want to execute this tool? (y/n):
```

## 使用示例

### 示例 1: 执行 Shell 命令

```
You: List all files in the current directory using ls