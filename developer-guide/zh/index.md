---
layout: home
hero:
  name: Agentao 开发者指南
  text: 把 Harness 嵌入你的应用
  tagline: 两条稳定的嵌入路径 · 六大业务扩展点 · 面向生产的安全与可观测
  actions:
    - theme: brand
      text: 起步 · 第一部分
      link: /zh/part-1/1-what-is-agentao
    - theme: alt
      text: 5 分钟上手
      link: /zh/part-1/4-hello-agentao
features:
  - icon: 🐍
    title: Python 进程内 SDK
    details: 直接 new 出 Agentao，配 SdkTransport 和回调，适合 Python 后端最短路径集成。
  - icon: 🔌
    title: ACP 跨语言协议
    details: stdio + NDJSON JSON-RPC 2.0，让 Node / Go / Rust / IDE 都能驱动 Agentao。
  - icon: 🧩
    title: 六大扩展点
    details: 自定义 Tool / Skill / MCP / 权限引擎 / 记忆系统 / 沙箱，业务化 Agent 能力。
---

## 本指南的读者

你正在做一款**你自己的产品**（SaaS 后端、IDE 插件、工单系统、数据工作台……），想在其中嵌入一个"能用你的业务语言做事的智能体"。本指南告诉你：

- 怎样把 Agentao 当作库/服务嵌入你的进程
- 怎样把你的业务 API 暴露为 Agent 工具
- 怎样把合规、安全、审计收进可控的边界内

## 如何阅读

| 你的角色 | 建议路径 |
|---------|---------|
| Python 后端工程师 | 第 1 → 2 → 4 → 5 → 6 → 8 部分 |
| IDE / 编辑器插件作者 | 第 1 → 3 → 4 → 8.2 部分 |
| DevOps / SRE | 第 1 → 6 → 7 部分 |
| 安全审计 | 第 6 部分 + 附录 D |

> 本指南假定你已经对 LLM、函数调用（function/tool calling）、JSON-RPC 有基本了解。
