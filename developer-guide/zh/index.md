---
layout: home
hero:
  name: Agentao
  text: 把 AI Agent 嵌入你自己的应用
  tagline: 3 行 Python 起步，或用 stdio JSON-RPC 让任意语言驱动。工具、权限、记忆、多租户都已内置。
  actions:
    - theme: brand
      text: 5 分钟跑通 Hello →
      link: /zh/part-1/4-hello-agentao
    - theme: alt
      text: Recipes（一键到答案）
      link: /zh/recipes/
features:
  - icon: ⚡
    title: 我先想试一下
    details: 3 行 Python 拿到流式回复。无需 Web 服务器、无需额外组件。
    link: /zh/part-1/4-hello-agentao
    linkText: 5 分钟 Hello →
  - icon: 🧰
    title: 我要让 Agent 调我的业务 API
    details: 把 HTTP / DB / 业务函数包成一个 Tool，强类型、可审计、可二次确认。
    link: /zh/part-5/1-custom-tools
    linkText: 自定义工具 →
  - icon: 🔌
    title: 我的宿主不是 Python
    details: 让 Agentao 作为子进程运行，Node / Go / Rust / IDE 通过 ACP（stdio + JSON-RPC）驱动它。
    link: /zh/part-3/1-acp-tour
    linkText: ACP 协议速览 →
  - icon: 🛡️
    title: 我要上生产
    details: 权限、沙箱、SSRF 防护、多租户隔离、可观测性、部署模板。
    link: /zh/part-6/1-defense-model
    linkText: 多层防御 →
  - icon: 🌐
    title: 我做的是 Web 后端
    details: FastAPI / Flask 模板，含 SSE 流式输出、会话池、取消、鉴权。
    link: /zh/part-2/7-fastapi-flask-embed
    linkText: Web 后端嵌入 →
  - icon: 📐
    title: 我要让宿主代码跨版本不断
    details: "`agentao.host` 是稳定的、附 schema 快照的宿主 API。审计 / 可观测 / 计费流水线就用它。"
    link: /zh/part-4/7-host-contract
    linkText: Harness 合约 →
  - icon: 🆘
    title: 出问题了
    details: 按症状索引的 FAQ、错误码参考、版本迁移指南。
    link: /zh/appendix/f-faq
    linkText: 问题排查 →
---

## 一句话介绍

Agentao 是一个**可嵌入的 Python Agent 运行时**——`from agentao import Agentao` 之后，你的应用就拥有一个有状态、能调用工具的助手。同一份运行时还可以通过 **ACP** stdio 协议被非 Python 宿主（IDE 插件、Node、Go、Rust）驱动，不需要重新造轮子。

开箱内容：内置工具（文件 / Shell / Web / 搜索）、MCP 客户端、权限引擎、多租户工作目录、对话压缩、SQLite 持久化记忆、运行时切换 LLM。

## 按你的角色选起点

| 你是… | 推荐顺序 |
|------|---------|
| 只想先试试 | [1.4 Hello](/zh/part-1/4-hello-agentao) → [1.2 核心概念](/zh/part-1/2-core-concepts) |
| Python 后端工程师，要上线一个功能 | [1.4](/zh/part-1/4-hello-agentao) → [第 2 部分](/zh/part-2/) → [5.1 自定义工具](/zh/part-5/1-custom-tools) → [第 6 部分](/zh/part-6/) |
| 做 IDE / 编辑器插件 | [1.3 集成模式](/zh/part-1/3-integration-modes) → [第 3 部分 · ACP](/zh/part-3/) → [第 4 部分 · 事件](/zh/part-4/) |
| DevOps / SRE | [1.5 运行环境](/zh/part-1/5-requirements) → [第 6 部分](/zh/part-6/) |
| 安全审计 | [第 6 部分 · 防御](/zh/part-6/) + [附录 D · 错误码](/zh/appendix/d-error-codes) |

Prefer English? → [English version](/en/)

> 假定你对 LLM 和函数调用（function/tool calling）有基本概念。只在走 ACP 路径时才需要了解 JSON-RPC。
