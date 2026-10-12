# macOS sandbox-exec 集成

Moved to **[6.2 · Shell Sandbox & Command Control](https://agentao.cn/en/part-6/2-shell-sandbox)** ([中文](https://agentao.cn/zh/part-6/2-shell-sandbox)), with the `/sandbox` command in [CLI 3](https://agentao.cn/en/cli/3-permissions-modes) ([中文](https://agentao.cn/zh/cli/3-permissions-modes)). The headings below are kept so that old links still land on the right section. 本文已并入开发者指南；下面保留原标题，旧链接仍能落到对应小节。

## 概述

Moved to [English](https://agentao.cn/en/part-6/2-shell-sandbox#macos-sandbox-exec) · [中文](https://agentao.cn/zh/part-6/2-shell-sandbox#macos-sandbox-exec)

## 设计原则

Moved to [English](https://agentao.cn/en/part-6/2-shell-sandbox#design-principles) · [中文](https://agentao.cn/zh/part-6/2-shell-sandbox#%E8%AE%BE%E8%AE%A1%E5%8E%9F%E5%88%99)

## 架构

Moved to [English](https://agentao.cn/en/part-6/2-shell-sandbox#how-a-command-is-wrapped) · [中文](https://agentao.cn/zh/part-6/2-shell-sandbox#%E5%91%BD%E4%BB%A4%E6%98%AF%E6%80%8E%E4%B9%88%E8%A2%AB%E5%8C%85%E8%B5%B7%E6%9D%A5%E7%9A%84)

### 关键模块

Moved to [English](https://agentao.cn/en/part-6/2-shell-sandbox#how-a-command-is-wrapped) · [中文](https://agentao.cn/zh/part-6/2-shell-sandbox#%E5%91%BD%E4%BB%A4%E6%98%AF%E6%80%8E%E4%B9%88%E8%A2%AB%E5%8C%85%E8%B5%B7%E6%9D%A5%E7%9A%84)

## 配置

Moved to [English](https://agentao.cn/en/part-6/2-shell-sandbox#config-fields) · [中文](https://agentao.cn/zh/part-6/2-shell-sandbox#%E9%85%8D%E7%BD%AE%E5%AD%97%E6%AE%B5)

### 文件位置（遵循 `.agentao/` 约定）

Moved to [English](https://agentao.cn/en/part-6/2-shell-sandbox#config-fields) · [中文](https://agentao.cn/zh/part-6/2-shell-sandbox#%E9%85%8D%E7%BD%AE%E5%AD%97%E6%AE%B5)

### Schema

Moved to [English](https://agentao.cn/en/part-6/2-shell-sandbox#config-fields) · [中文](https://agentao.cn/zh/part-6/2-shell-sandbox#%E9%85%8D%E7%BD%AE%E5%AD%97%E6%AE%B5)

## 内置 Profile

Moved to [English](https://agentao.cn/en/part-6/2-shell-sandbox#three-built-in-profiles) · [中文](https://agentao.cn/zh/part-6/2-shell-sandbox#_3-%E4%B8%AA%E5%86%85%E7%BD%AE-profile)

### `readonly.sb`

Moved to [English](https://agentao.cn/en/part-6/2-shell-sandbox#three-built-in-profiles) · [中文](https://agentao.cn/zh/part-6/2-shell-sandbox#_3-%E4%B8%AA%E5%86%85%E7%BD%AE-profile)

### `workspace-write.sb`

Moved to [English](https://agentao.cn/en/part-6/2-shell-sandbox#three-built-in-profiles) · [中文](https://agentao.cn/zh/part-6/2-shell-sandbox#_3-%E4%B8%AA%E5%86%85%E7%BD%AE-profile)

### `workspace-write-no-network.sb`

Moved to [English](https://agentao.cn/en/part-6/2-shell-sandbox#three-built-in-profiles) · [中文](https://agentao.cn/zh/part-6/2-shell-sandbox#_3-%E4%B8%AA%E5%86%85%E7%BD%AE-profile)

### Profile 通用骨架（参考）

Moved to [English](https://agentao.cn/en/part-6/2-shell-sandbox#custom-profiles) · [中文](https://agentao.cn/zh/part-6/2-shell-sandbox#%E8%87%AA%E5%AE%9A%E4%B9%89-profile)

## CLI 命令

Moved to [English](https://agentao.cn/en/cli/3-permissions-modes#sandbox-%E2%80%94-macos-sandbox-exec-macos-only) · [中文](https://agentao.cn/zh/cli/3-permissions-modes#sandbox-%E2%80%94-macos-sandbox-exec-%E4%BB%85-macos)

## 失败语义

Moved to [English](https://agentao.cn/en/part-6/2-shell-sandbox#when-the-sandbox-denies-a-command) · [中文](https://agentao.cn/zh/part-6/2-shell-sandbox#%E6%B2%99%E7%AE%B1%E6%8B%92%E7%BB%9D%E5%91%BD%E4%BB%A4%E6%97%B6)

## Gotchas

Moved to [English](https://agentao.cn/en/part-6/2-shell-sandbox#limits-of-sandbox-exec) · [中文](https://agentao.cn/zh/part-6/2-shell-sandbox#sandbox-exec-%E7%9A%84%E5%B1%80%E9%99%90)

## 与现有机制的关系

Moved to [English](https://agentao.cn/en/part-6/2-shell-sandbox) · [中文](https://agentao.cn/zh/part-6/2-shell-sandbox)

## 验证

Moved to [English](https://agentao.cn/en/part-6/2-shell-sandbox#verifying-it-by-hand) · [中文](https://agentao.cn/zh/part-6/2-shell-sandbox#%E6%89%8B%E5%8A%A8%E9%AA%8C%E8%AF%81)

### 单元测试

Removed: a contributor test command (`tests/test_sandbox_policy.py`).

### 手动 end-to-end（macOS）

Moved to [English](https://agentao.cn/en/part-6/2-shell-sandbox#verifying-it-by-hand) · [中文](https://agentao.cn/zh/part-6/2-shell-sandbox#%E6%89%8B%E5%8A%A8%E9%AA%8C%E8%AF%81)

### 跨平台回归

Moved to [English](https://agentao.cn/en/part-6/2-shell-sandbox#verifying-it-by-hand) · [中文](https://agentao.cn/zh/part-6/2-shell-sandbox#%E6%89%8B%E5%8A%A8%E9%AA%8C%E8%AF%81)

## 后续（本方案 out-of-scope）

Removed: a roadmap of unshipped work.
