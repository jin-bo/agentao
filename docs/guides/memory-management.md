# Memory Management

Moved to **[5.5 · Memory](https://agentao.cn/en/part-5/5-memory)** ([中文](https://agentao.cn/zh/part-5/5-memory)) and **[CLI 6 · Memory](https://agentao.cn/en/cli/6-memory)** ([中文](https://agentao.cn/zh/cli/6-memory)). This guide moved into the developer guide. The headings below are kept so that old links still land on the right section. 本文已并入开发者指南；下面保留原标题，旧链接仍能落到对应小节。

## 存储架构

Moved to [English](https://agentao.cn/en/part-5/5-memory#two-sqlite-databases) · [中文](https://agentao.cn/zh/part-5/5-memory#%E4%B8%A4%E4%B8%AA-sqlite-%E6%95%B0%E6%8D%AE%E5%BA%93)

### 数据库文件

Moved to [English](https://agentao.cn/en/part-5/5-memory#two-sqlite-databases) · [中文](https://agentao.cn/zh/part-5/5-memory#%E4%B8%A4%E4%B8%AA-sqlite-%E6%95%B0%E6%8D%AE%E5%BA%93)

### 子 agent 的记忆边界

Moved to [English](https://agentao.cn/en/part-5/5-memory#sub-agent-memory-boundary) · [中文](https://agentao.cn/zh/part-5/5-memory#%E5%AD%90-agent-%E7%9A%84%E8%AE%B0%E5%BF%86%E8%BE%B9%E7%95%8C)

### SQLite 表结构

Moved to [English](https://agentao.cn/en/part-5/5-memory#tables-and-change-tracking) · [中文](https://agentao.cn/zh/part-5/5-memory#%E8%A1%A8%E7%BB%93%E6%9E%84%E4%B8%8E%E5%8F%98%E6%9B%B4%E8%AE%A1%E6%95%B0)

## 四类数据

Moved to [English](https://agentao.cn/en/part-5/5-memory#four-kinds-of-data) · [中文](https://agentao.cn/zh/part-5/5-memory#%E8%AE%B0%E5%BF%86%E7%9A%84%E5%9B%9B%E7%A7%8D%E6%95%B0%E6%8D%AE)

### 1. Persistent Memories（持久记忆）

Moved to [English](https://agentao.cn/en/part-5/5-memory#persistent-memory-records) · [中文](https://agentao.cn/zh/part-5/5-memory#%E6%8C%81%E4%B9%85%E5%8C%96%E8%AE%B0%E5%BF%86%E7%9A%84%E5%AD%97%E6%AE%B5)

### 2. Session Summaries（会话摘要）

Moved to [English](https://agentao.cn/en/part-5/5-memory#session-summaries) · [中文](https://agentao.cn/zh/part-5/5-memory#%E4%BC%9A%E8%AF%9D%E6%91%98%E8%A6%81)

### 3. Recall Candidates（召回候选）

Moved to [English](https://agentao.cn/en/part-5/5-memory#memory-context-%E2%80%94-dynamic-recall) · [中文](https://agentao.cn/zh/part-5/5-memory#memory-context-%E2%80%94-%E5%8A%A8%E6%80%81%E5%8F%AC%E5%9B%9E%E5%9D%97)

### 4. Review Items（待审条目）

Moved to [English](https://agentao.cn/en/part-5/5-memory#four-kinds-of-data) · [中文](https://agentao.cn/zh/part-5/5-memory#%E8%AE%B0%E5%BF%86%E7%9A%84%E5%9B%9B%E7%A7%8D%E6%95%B0%E6%8D%AE)

## 提示词注入

Moved to [English](https://agentao.cn/en/part-5/5-memory#the-two-prompt-blocks) · [中文](https://agentao.cn/zh/part-5/5-memory#%E6%8F%90%E7%A4%BA%E8%AF%8D%E9%87%8C%E7%9A%84%E4%B8%A4%E4%B8%AA%E8%AE%B0%E5%BF%86%E5%9D%97)

### `<memory-stable>` 块

Moved to [English](https://agentao.cn/en/part-5/5-memory#memory-stable-%E2%80%94-stable-block) · [中文](https://agentao.cn/zh/part-5/5-memory#memory-stable-%E2%80%94-%E7%A8%B3%E5%AE%9A%E5%9D%97)

### `<memory-context>` 块

Moved to [English](https://agentao.cn/en/part-5/5-memory#memory-context-%E2%80%94-dynamic-recall) · [中文](https://agentao.cn/zh/part-5/5-memory#memory-context-%E2%80%94-%E5%8A%A8%E6%80%81%E5%8F%AC%E5%9B%9E%E5%9D%97)

## LLM 工具（仅写入）

Moved to [English](https://agentao.cn/en/cli/6-memory#what-the-agent-can-do-with-memory) · [中文](https://agentao.cn/zh/cli/6-memory#agent-%E8%83%BD%E6%8B%BF%E8%AE%B0%E5%BF%86%E5%81%9A%E4%BB%80%E4%B9%88)

## CLI 命令（用户专用，不暴露给 LLM）

Moved to [English](https://agentao.cn/en/cli/6-memory#commands-and-their-memorymanager-methods) · [中文](https://agentao.cn/zh/cli/6-memory#%E5%91%BD%E4%BB%A4%E4%B8%8E-memorymanager-%E6%96%B9%E6%B3%95%E7%9A%84%E5%AF%B9%E5%BA%94)

## 最佳实践

Moved to [English](https://agentao.cn/en/cli/6-memory#pitfalls) · [中文](https://agentao.cn/zh/cli/6-memory#%E5%AE%B9%E6%98%93%E8%B8%A9%E7%9A%84%E5%9D%91)

### 使用描述性的 key

Removed: generic advice (use descriptive keys).

### 用标签驱动 scope 和分类

Moved to [English](https://agentao.cn/en/cli/6-memory#what-the-agent-can-do-with-memory) · [中文](https://agentao.cn/zh/cli/6-memory#agent-%E8%83%BD%E6%8B%BF%E8%AE%B0%E5%BF%86%E5%81%9A%E4%BB%80%E4%B9%88)

### 更新记忆（相同 key 自动 upsert）

Moved to [English](https://agentao.cn/en/cli/6-memory#what-the-agent-can-do-with-memory) · [中文](https://agentao.cn/zh/cli/6-memory#agent-%E8%83%BD%E6%8B%BF%E8%AE%B0%E5%BF%86%E5%81%9A%E4%BB%80%E4%B9%88)

## 故障排除

Moved to [English](https://agentao.cn/en/cli/6-memory#troubleshooting) · [中文](https://agentao.cn/zh/cli/6-memory#%E6%95%85%E9%9A%9C%E6%8E%92%E6%9F%A5)

### 记忆未出现在提示词中

Moved to [English](https://agentao.cn/en/cli/6-memory#memory-doesn-t-reach-the-prompt) · [中文](https://agentao.cn/zh/cli/6-memory#%E8%AE%B0%E5%BF%86%E6%B2%A1%E8%BF%9B-prompt)

### `/memory delete` 没有删除条目

Moved to [English](https://agentao.cn/en/cli/6-memory#memory-delete-finds-nothing) · [中文](https://agentao.cn/zh/cli/6-memory#memory-delete-%E6%89%BE%E4%B8%8D%E5%88%B0%E6%9D%A1%E7%9B%AE)

### 会话摘要丢失

Moved to [English](https://agentao.cn/en/cli/6-memory#session-summaries-are-missing) · [中文](https://agentao.cn/zh/cli/6-memory#%E4%BC%9A%E8%AF%9D%E6%91%98%E8%A6%81%E4%B8%8D%E8%A7%81%E4%BA%86)
