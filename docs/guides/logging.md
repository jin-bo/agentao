# Agentao Logging System

Moved to **[6.6 · Observability](https://agentao.cn/en/part-6/6-observability)** ([中文](https://agentao.cn/zh/part-6/6-observability)), with log scrubbing in [6.5](https://agentao.cn/en/part-6/5-secrets-injection) ([中文](https://agentao.cn/zh/part-6/5-secrets-injection)). The headings below are kept so that old links still land on the right section. 本文已并入开发者指南；下面保留原标题，旧链接仍能落到对应小节。

## Overview

Moved to [English](https://agentao.cn/en/part-6/6-observability#the-built-in-agentao-log) · [中文](https://agentao.cn/zh/part-6/6-observability#agentao-%E8%87%AA%E5%B8%A6%E7%9A%84-agentao-log)

## 日志文件

Moved to [English](https://agentao.cn/en/part-6/6-observability#the-built-in-agentao-log) · [中文](https://agentao.cn/zh/part-6/6-observability#agentao-%E8%87%AA%E5%B8%A6%E7%9A%84-agentao-log)

## 记录内容

Moved to [English](https://agentao.cn/en/part-6/6-observability#reading-a-request-and-its-response) · [中文](https://agentao.cn/zh/part-6/6-observability#%E8%AF%BB%E6%87%82%E4%B8%80%E6%AC%A1%E8%AF%B7%E6%B1%82%E5%92%8C%E5%93%8D%E5%BA%94)

### 1. 请求信息 (Request)

Moved to [English](https://agentao.cn/en/part-6/6-observability#reading-a-request-and-its-response) · [中文](https://agentao.cn/zh/part-6/6-observability#%E8%AF%BB%E6%87%82%E4%B8%80%E6%AC%A1%E8%AF%B7%E6%B1%82%E5%92%8C%E5%93%8D%E5%BA%94)

### 2. 响应信息 (Response)

Moved to [English](https://agentao.cn/en/part-6/6-observability#reading-a-request-and-its-response) · [中文](https://agentao.cn/zh/part-6/6-observability#%E8%AF%BB%E6%87%82%E4%B8%80%E6%AC%A1%E8%AF%B7%E6%B1%82%E5%92%8C%E5%93%8D%E5%BA%94)

## 日志特性

Moved to [English](https://agentao.cn/en/part-6/6-observability#the-built-in-agentao-log) · [中文](https://agentao.cn/zh/part-6/6-observability#agentao-%E8%87%AA%E5%B8%A6%E7%9A%84-agentao-log)

### ✅ 完整记录

Moved to [English](https://agentao.cn/en/part-6/6-observability#the-built-in-agentao-log) · [中文](https://agentao.cn/zh/part-6/6-observability#agentao-%E8%87%AA%E5%B8%A6%E7%9A%84-agentao-log)

### ✅ 详细信息

Moved to [English](https://agentao.cn/en/part-6/6-observability#reading-a-request-and-its-response) · [中文](https://agentao.cn/zh/part-6/6-observability#%E8%AF%BB%E6%87%82%E4%B8%80%E6%AC%A1%E8%AF%B7%E6%B1%82%E5%92%8C%E5%93%8D%E5%BA%94)

### ✅ 请求追踪

Moved to [English](https://agentao.cn/en/part-6/6-observability#reading-a-request-and-its-response) · [中文](https://agentao.cn/zh/part-6/6-observability#%E8%AF%BB%E6%87%82%E4%B8%80%E6%AC%A1%E8%AF%B7%E6%B1%82%E5%92%8C%E5%93%8D%E5%BA%94)

### 🔒 凭据脱敏（两层）

Moved to [English](https://agentao.cn/en/part-6/5-secrets-injection#four-log-scrubbing) · [中文](https://agentao.cn/zh/part-6/5-secrets-injection#%E5%9B%9B-%E6%97%A5%E5%BF%97%E8%84%B1%E6%95%8F)

#### 第 1 层：`extra_body` 递归按键脱敏

Moved to [English](https://agentao.cn/en/part-6/5-secrets-injection#four-log-scrubbing) · [中文](https://agentao.cn/zh/part-6/5-secrets-injection#%E5%9B%9B-%E6%97%A5%E5%BF%97%E8%84%B1%E6%95%8F)

#### 第 2 层：日志文件全局按模式脱敏（默认开启）

Moved to [English](https://agentao.cn/en/part-6/5-secrets-injection#four-log-scrubbing) · [中文](https://agentao.cn/zh/part-6/5-secrets-injection#%E5%9B%9B-%E6%97%A5%E5%BF%97%E8%84%B1%E6%95%8F)

## 使用方法

Moved to [English](https://agentao.cn/en/part-6/6-observability#the-built-in-agentao-log) · [中文](https://agentao.cn/zh/part-6/6-observability#agentao-%E8%87%AA%E5%B8%A6%E7%9A%84-agentao-log)

### 自动记录

Moved to [English](https://agentao.cn/en/part-6/6-observability#the-built-in-agentao-log) · [中文](https://agentao.cn/zh/part-6/6-observability#agentao-%E8%87%AA%E5%B8%A6%E7%9A%84-agentao-log)

### 查看日志

Moved to [English](https://agentao.cn/en/part-6/6-observability#debugging-with-grep) · [中文](https://agentao.cn/zh/part-6/6-observability#%E7%94%A8-grep-%E6%8E%92%E6%9F%A5)

### 自定义日志位置

Moved to [English](https://agentao.cn/en/part-6/6-observability#take-over-agentao-s-logger) · [中文](https://agentao.cn/zh/part-6/6-observability#%E6%8E%A5%E7%AE%A1-agentao-%E7%9A%84-logger)

### 关闭日志文件 / 让 host 完全接管日志栈（嵌入场景）

Moved to [English](https://agentao.cn/en/part-6/6-observability#take-over-agentao-s-logger) · [中文](https://agentao.cn/zh/part-6/6-observability#%E6%8E%A5%E7%AE%A1-agentao-%E7%9A%84-logger)

## 日志格式

Moved to [English](https://agentao.cn/en/part-6/6-observability#reading-a-request-and-its-response) · [中文](https://agentao.cn/zh/part-6/6-observability#%E8%AF%BB%E6%87%82%E4%B8%80%E6%AC%A1%E8%AF%B7%E6%B1%82%E5%92%8C%E5%93%8D%E5%BA%94)

### 时间戳格式

Moved to [English](https://agentao.cn/en/part-6/6-observability#the-built-in-agentao-log) · [中文](https://agentao.cn/zh/part-6/6-observability#agentao-%E8%87%AA%E5%B8%A6%E7%9A%84-agentao-log)

### 分隔线

Moved to [English](https://agentao.cn/en/part-6/6-observability#reading-a-request-and-its-response) · [中文](https://agentao.cn/zh/part-6/6-observability#%E8%AF%BB%E6%87%82%E4%B8%80%E6%AC%A1%E8%AF%B7%E6%B1%82%E5%92%8C%E5%93%8D%E5%BA%94)

## 日志级别

Moved to [English](https://agentao.cn/en/part-6/6-observability#debugging-with-grep) · [中文](https://agentao.cn/zh/part-6/6-observability#%E7%94%A8-grep-%E6%8E%92%E6%9F%A5)

## 隐私和安全

Moved to [English](https://agentao.cn/en/part-6/5-secrets-injection#four-log-scrubbing) · [中文](https://agentao.cn/zh/part-6/5-secrets-injection#%E5%9B%9B-%E6%97%A5%E5%BF%97%E8%84%B1%E6%95%8F)

### ⚠️ 注意事项

Moved to [English](https://agentao.cn/en/part-6/5-secrets-injection#four-log-scrubbing) · [中文](https://agentao.cn/zh/part-6/5-secrets-injection#%E5%9B%9B-%E6%97%A5%E5%BF%97%E8%84%B1%E6%95%8F)

### 建议

Moved to [English](https://agentao.cn/en/part-6/6-observability#the-built-in-agentao-log) · [中文](https://agentao.cn/zh/part-6/6-observability#agentao-%E8%87%AA%E5%B8%A6%E7%9A%84-agentao-log)

## 日志管理

Moved to [English](https://agentao.cn/en/part-6/6-observability#the-built-in-agentao-log) · [中文](https://agentao.cn/zh/part-6/6-observability#agentao-%E8%87%AA%E5%B8%A6%E7%9A%84-agentao-log)

### 清空日志

Removed: plain shell commands, nothing specific to Agentao.

### 归档日志

Removed: the built-in rotation keeps the backups.

### 日志轮转

Moved to [English](https://agentao.cn/en/part-6/6-observability#the-built-in-agentao-log) · [中文](https://agentao.cn/zh/part-6/6-observability#agentao-%E8%87%AA%E5%B8%A6%E7%9A%84-agentao-log)

## 示例日志

Moved to [English](https://agentao.cn/en/part-6/6-observability#reading-a-request-and-its-response) · [中文](https://agentao.cn/zh/part-6/6-observability#%E8%AF%BB%E6%87%82%E4%B8%80%E6%AC%A1%E8%AF%B7%E6%B1%82%E5%92%8C%E5%93%8D%E5%BA%94)

### 完整对话示例

Moved to [English](https://agentao.cn/en/part-6/6-observability#reading-a-request-and-its-response) · [中文](https://agentao.cn/zh/part-6/6-observability#%E8%AF%BB%E6%87%82%E4%B8%80%E6%AC%A1%E8%AF%B7%E6%B1%82%E5%92%8C%E5%93%8D%E5%BA%94)

## 调试技巧

Moved to [English](https://agentao.cn/en/part-6/6-observability#debugging-with-grep) · [中文](https://agentao.cn/zh/part-6/6-observability#%E7%94%A8-grep-%E6%8E%92%E6%9F%A5)

### 查找错误

Moved to [English](https://agentao.cn/en/part-6/6-observability#debugging-with-grep) · [中文](https://agentao.cn/zh/part-6/6-observability#%E7%94%A8-grep-%E6%8E%92%E6%9F%A5)

### 查看特定请求

Moved to [English](https://agentao.cn/en/part-6/6-observability#debugging-with-grep) · [中文](https://agentao.cn/zh/part-6/6-observability#%E7%94%A8-grep-%E6%8E%92%E6%9F%A5)

### 统计 Token 使用

Moved to [English](https://agentao.cn/en/part-6/6-observability#debugging-with-grep) · [中文](https://agentao.cn/zh/part-6/6-observability#%E7%94%A8-grep-%E6%8E%92%E6%9F%A5)

### 查看工具调用

Moved to [English](https://agentao.cn/en/part-6/6-observability#debugging-with-grep) · [中文](https://agentao.cn/zh/part-6/6-observability#%E7%94%A8-grep-%E6%8E%92%E6%9F%A5)

## 配置选项

Moved to [English](https://agentao.cn/en/part-6/6-observability#take-over-agentao-s-logger) · [中文](https://agentao.cn/zh/part-6/6-observability#%E6%8E%A5%E7%AE%A1-agentao-%E7%9A%84-logger)

## 故障排除

Moved to [English](https://agentao.cn/en/part-6/6-observability#when-the-log-is-missing-or-incomplete) · [中文](https://agentao.cn/zh/part-6/6-observability#%E6%97%A5%E5%BF%97%E7%BC%BA%E5%A4%B1%E6%88%96%E4%B8%8D%E5%AE%8C%E6%95%B4%E6%97%B6)

### 日志文件未创建

Moved to [English](https://agentao.cn/en/part-6/6-observability#when-the-log-is-missing-or-incomplete) · [中文](https://agentao.cn/zh/part-6/6-observability#%E6%97%A5%E5%BF%97%E7%BC%BA%E5%A4%B1%E6%88%96%E4%B8%8D%E5%AE%8C%E6%95%B4%E6%97%B6)

### 日志内容不完整

Moved to [English](https://agentao.cn/en/part-6/6-observability#when-the-log-is-missing-or-incomplete) · [中文](https://agentao.cn/zh/part-6/6-observability#%E6%97%A5%E5%BF%97%E7%BC%BA%E5%A4%B1%E6%88%96%E4%B8%8D%E5%AE%8C%E6%95%B4%E6%97%B6)

### 日志文件过大

Moved to [English](https://agentao.cn/en/part-6/6-observability#when-the-log-is-missing-or-incomplete) · [中文](https://agentao.cn/zh/part-6/6-observability#%E6%97%A5%E5%BF%97%E7%BC%BA%E5%A4%B1%E6%88%96%E4%B8%8D%E5%AE%8C%E6%95%B4%E6%97%B6)

## 最佳实践

Moved to [English](https://agentao.cn/en/part-6/6-observability#the-built-in-agentao-log) · [中文](https://agentao.cn/zh/part-6/6-observability#agentao-%E8%87%AA%E5%B8%A6%E7%9A%84-agentao-log)
