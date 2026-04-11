# Menu-Based Confirmation Update

## 概述

将工具确认从简单的是/否提示升级为菜单模式，提供更多选项和更好的用户体验。

## 改进内容

### 之前（简单确认）

```
⚠️  Tool Confirmation Required
...
Do you want to execute this tool? (y/n):
```

问题：
- 只有是/否两个选项
- 每次都需要确认，即使用户信任所有工具
- 界面简单，不够友好

### 现在（菜单模式）

```
⚠️  Tool Confirmation Required
Tool: run_shell_command
Description: Execute a shell command and return its output
Arguments:
  • command: ls -la

╭─ Options ─────────────────────────────────────╮
│ 1. Yes                                       │
│ 2. Yes, allow all tools during this session │
│ 3. No                                        │
╰───────────────────────────────────────────────╯

Enter choice (1-3) · Press Ctrl+C to cancel
```

优势：
- ✅ 单键选择（1、2、3）
- ✅ "允许全部"选项，避免重复确认
- ✅ 精美的菜单界面
- ✅ 清晰的操作提示

## 功能详情

### 三个选项

#### 1. Yes - 执行此工具

- 执行当前工具
- 下次遇到工具时仍会提示
- 适合偶尔使用工具的场景

#### 2. Yes, allow all tools during this session - 允许所有工具

- 执行当前工具
- **启用"允许全部"模式**
- 后续工具自动批准，不再提示
- 仅在当前会话有效，重启后重置
- 适合频繁使用工具的工作流

#### 3. No - 取消执行

- 取消工具执行
- LLM 收到取消通知
- LLM 可以尝试其他方法

### 会话管理

#### 允许全部模式

启用后：
```
✓ Auto-approved: run_shell_command (allow all mode)
```

工具自动批准，无需等待。

#### 查看状态

使用 `/status` 命令：
```
Status: Total messages: 5
Current model: gpt-4
Active skills: 0
Tool Confirmation: Allow all mode enabled   ← 显示确认模式
```

#### 重置确认

使用 `/reset-confirm` 命令：
```
Tool confirmation reset. Will prompt for each tool.
```

恢复到每次提示模式。

## 实现细节

### CLI 改动

**文件**: `agentao/cli/app.py`

1. **添加会话状态**
   ```python
   class AgentaoCLI:
       def __init__(self):
           self.allow_all_tools = False  # 跟踪"允许全部"状态
   ```

2. **改进确认方法**
   ```python
   def confirm_tool_execution(self, tool_name, tool_description, tool_args):
       # 如果启用"允许全部"，自动批准
       if self.allow_all_tools:
           return True

       # 显示菜单
       # ...
       choice = Prompt.ask("", choices=["1", "2", "3"])

       if choice == "2":
           self.allow_all_tools = True  # 启用允许全部
   ```

3. **添加状态显示**
   ```python
   def show_status(self):
       # 显示确认模式状态
       if self.allow_all_tools:
           console.print("Tool Confirmation: Allow all mode enabled")
   ```

4. **添加重置命令**
   ```python
   elif command == "reset-confirm":
       self.allow_all_tools = False
       console.print("Tool confirmation reset.")
   ```

### 键盘快捷键

- **1-3**: 选择选项
- **Ctrl+C**: 取消（等同于选项 3）
- **Esc**: 取消（通过 Ctrl+C 实现）

## 测试

### 测试文件

**test_menu_confirmation.py** - 7 个测试用例：

1. ✅ 选项 1 (Yes) 正确执行
2. ✅ 选项 2 (Yes to all) 启用允许全部模式
3. ✅ 选项 3 (No) 取消执行
4. ✅ 允许全部模式绕过确认
5. ✅ Ctrl+C 正确处理
6. ✅ 会话状态正确初始化
7. ✅ 允许全部模式跨调用持久

运行测试：
```bash
uv run python test_menu_confirmation.py
```

结果：
```
✅ All tests passed!
```

## 用户体验改进

### 之前的流程

```
工具 1 → 确认(y/n) → 执行
工具 2 → 确认(y/n) → 执行
工具 3 → 确认(y/n) → 执行
工具 4 → 确认(y/n) → 执行
...重复确认...
```

问题：频繁确认，打断工作流

### 现在的流程

#### 场景 1: 偶尔使用

```
工具 1 → 确认(选1) → 执行
工具 2 → 确认(选1) → 执行
```

每次确认，保持控制

#### 场景 2: 频繁使用

```
工具 1 → 确认(选2) → 执行 + 启用允许全部
工具 2 → 自动批准 → 执行
工具 3 → 自动批准 → 执行
工具 4 → 自动批准 → 执行
...无需再确认...
```

一次确认，后续流畅

## 使用场景

### 场景 1: 探索性任务

用户不确定需要什么工具：
- 使用选项 1，每次确认
- 保持完全控制

### 场景 2: 批量操作

用户需要多次使用工具：
- 第一次选择选项 2
- 后续自动执行
- 完成后用 `/reset-confirm` 恢复

### 场景 3: 混合模式

开始时逐个确认，信任后启用允许全部：
```
工具 1 → 选 1 (确认一次)
工具 2 → 选 1 (再确认一次)
工具 3 → 选 2 (启用允许全部)
工具 4+ → 自动批准
```

## UI 设计

### 菜单框架

使用 Unicode 字符绘制：
```
╭─ Options ─────────────────────────────────────╮
│ 1. Yes                                       │
│ 2. Yes, allow all tools during this session │
│ 3. No                                        │
╰───────────────────────────────────────────────╯
```

- ✅ 视觉上清晰分隔
- ✅ 美观专业
- ✅ 选项对齐
- ✅ 易于阅读

### 颜色方案

- 黄色: 警告图标 ⚠️
- 青色: 工具名称
- 绿色: Yes 选项
- 红色: No 选项
- 灰色: 提示文字

## 向后兼容

- ✅ 所有现有工具继续工作
- ✅ 确认机制保持不变（仅界面改进）
- ✅ 无需修改工具代码
- ✅ 无配置文件更改

## 新增命令

### `/reset-confirm`

**用途**: 重置工具确认模式

**使用**:
```
You: /reset-confirm

Tool confirmation reset. Will prompt for each tool.
```

**何时使用**:
- 启用了"允许全部"但想恢复确认
- 完成批量操作后
- 开始新的谨慎任务

## 更新文件

### 修改的文件

1. **agentao/cli/app.py** (+45 行)
   - 添加 `allow_all_tools` 状态
   - 改进 `confirm_tool_execution()` 方法
   - 更新 `show_status()` 显示
   - 添加 `/reset-confirm` 命令处理
   - 更新帮助文本

2. **README.md** (+20 行)
   - 更新工具确认说明
   - 添加菜单示例
   - 说明新功能

### 新增文件

1. **test_menu_confirmation.py** (180 行)
   - 全面测试菜单功能
   - 所有测试通过 ✅

2. **MENU_CONFIRMATION_UPDATE.md** (本文档)
   - 功能说明
   - 使用指南

## 性能影响

- **启动**: 无影响（仅添加一个布尔变量）
- **确认**: 稍快（单键选择 vs 输入 y/n）
- **批量操作**: 大幅提升（允许全部模式避免重复确认）
- **内存**: +1 字节（allow_all_tools 布尔值）

## 未来改进

可能的增强：
- [ ] 记住用户偏好（保存到配置文件）
- [ ] 按工具类型配置（某些工具总是允许）
- [ ] 超时自动选择（默认 No）
- [ ] 批量确认多个工具
- [ ] 确认历史记录

## 用户反馈

预期用户反馈：
- ✅ 菜单更清晰直观
- ✅ 单键选择更快
- ✅ 允许全部模式提高效率
- ✅ 状态可见性更好
- ✅ 重置功能很有用

---

**更新状态**: ✅ 已完成并测试
**版本**: 0.2.1
**最后更新**: 2026-02-11
