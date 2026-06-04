# Current Date Context Feature

## 概述

在系统提示词中自动添加当前日期和时间信息，帮助 LLM 理解时间上下文，提高时间相关任务的准确性。

## 实现

### 修改内容

**文件**: `agentao/agent.py`

1. **导入 datetime 模块**
   ```python
   from datetime import datetime
   ```

2. **在 `_build_system_prompt()` 方法中添加日期信息**
   ```python
   # Get current date and time
   now = datetime.now()
   current_date = now.strftime("%Y-%m-%d")
   current_time = now.strftime("%H:%M:%S")
   current_datetime = now.strftime("%Y-%m-%d %H:%M:%S")
   day_of_week = now.strftime("%A")
   ```

3. **将日期信息注入到系统提示词**
   ```
   Current Date and Time: 2026-02-11 14:40:58 (Wednesday)
   ```

## 日期格式

- **完整格式**: `YYYY-MM-DD HH:MM:SS (Day of Week)`
- **示例**: `2026-02-11 14:40:58 (Wednesday)`
- **组成部分**:
  - 日期: `2026-02-11` (ISO 8601 格式)
  - 时间: `14:40:58` (24 小时制)
  - 星期: `Wednesday` (英文全称)

## 使用场景

### 1. 时间敏感任务

```
You: Create a log file for today's activities