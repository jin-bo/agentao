# 项目级 agent 定义的信任线不一致（待决）

**状态：** 待决的开放问题，**尚未实现任何一侧**。2026-08-21 从 `codex-subagent-v2-vs-agentao.zh.md` 的对照评审里撞见，按复审意见移出该文以免扩散其评审范围。**不是提权，也不是安全缺陷**——只是同一条信任线在两处画得不一致。
**锚点：** agentao `main@c06a143`。英文孪生待写。

## 事实

`.agentao/permissions.json`（项目级）被**明确拒绝加载**，理由写在 `permissions.py` 的类 docstring 里：

> "a checked-in rule could grant the agent capabilities the user never approved"

但 `.agentao/agents/*.md`（项目级）是**无条件扫描加载**的（`agents/manager.py:43`），插件 agent 也会注册（`cli/subcommands.py:321`）。而 agent 定义能指定 `model`、挑 `tools` 子集、注入整段 system prompt（`agents/manager.py:57-75`）。

## 为什么这不是提权

定义的 frontmatter 里**没有任何策略字段**，`tools` 只能**收窄**不能扩张，子 agent 还会重建 `PermissionEngine` 并继承父方模式（`_wrapper.py:541-570`）。所以一个签入仓库的 agent 定义拿不到用户没批准过的能力——这正是 `permissions.json` 被拒载所要防的那件事，在这里不成立。

## 两个方向

- **(a) 接受现状** —— agent 定义是「能力描述」而非「策略」，与 `AGENTAO.md` 同级；把这个理由写进 `agents/manager.py` 的 docstring，让下一个人不必重新推一遍。
- **(b) 与 permissions.json 对齐** —— 项目级 agent 需显式启用。代价是多一个配置面，且会打断「clone 仓库即得其 agent」的现有体验。

倾向 (a)：它把不一致解释掉而不是消灭掉，成本一行注释。但这是维护者的判断，本文不替其决定。
