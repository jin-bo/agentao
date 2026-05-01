# 第六部分 · 安全与生产化部署

**把 Agent 装进用户能用的产品**比让它在开发者本机跑难 10 倍。这部分合并了"安全防护"和"生产部署"两个主题——它们本来就分不开。

::: info 本部分关键词
- **多层防御（Defense-in-Depth）** — 每一层都假定上一层会失守；安全不在单点 · [§6.1](/zh/part-6/1-defense-model)、[G.5](/zh/appendix/g-glossary#g-5-安全术语)
- **SSRF 黑名单** — 默认封禁 `127.0.0.1`、`169.254.169.254`、链路本地、RFC1918；**只能扩，不能关** · [§6.3](/zh/part-6/3-network-ssrf)、[G.5](/zh/appendix/g-glossary#g-5-安全术语)
- **Working-directory 黄金规则** — 一个租户 = 一个 CWD；绝不共享 —— 文件工具都在这里寻址 · [§6.4](/zh/part-6/4-multi-tenant-fs)
- **会话池** — 以 `(tenant_id, session_id)` 为键，TTL + LRU 淘汰；生产侧生命周期模式 · [§6.7](/zh/part-6/7-resource-concurrency)
- **Sticky session** — `StatefulSet` + PVC + `sessionAffinity`；同会话落到同 Pod 的方式 · [§6.8](/zh/part-6/8-deployment)
:::

## 本部分覆盖

- [**6.1 多层防御模型**](./1-defense-model) — 7 层防御栈、5 类威胁模型、最小 vs 理想
- [**6.2 Shell 沙箱与命令控制**](./2-shell-sandbox) — macOS sandbox-exec、3 个内置 profile、Linux 替代方案
- [**6.3 网络与 SSRF 防护**](./3-network-ssrf) — 域名分层、httpx 重定向、MCP 网络隔离
- [**6.4 多租户隔离与文件系统**](./4-multi-tenant-fs) — working_directory 黄金规则、DB 隔离、/tmp 污染
- [**6.5 密钥管理与 Prompt 注入防御**](./5-secrets-injection) — 五条戒律、注入攻击面、红队测试清单
- [**6.6 可观测性与审计**](./6-observability) — 4 个观测维度、内建 replay、合规日志
- [**6.7 资源治理与并发**](./7-resource-concurrency) — 会话池、TTL、token 预算、内存估算
- [**6.8 容器化、灰度与回滚**](./8-deployment) — Dockerfile、K8s StatefulSet、可灰度的维度

## 按角色的阅读路径

| 角色 | 推荐章节 |
|------|---------|
| DevOps / SRE | 6.6 → 6.7 → 6.8 |
| 安全审计 | 6.1 → 6.4 → 6.5 |
| 平台工程 | 6.1 → 6.2 → 6.3 → 6.4 |
| 产品经理（理解风险） | 6.1 → 6.5 关键风险段 |

## 心智模型

> 安全是层叠而非单点；生产是治理而非运气。
> 每一层都假定上一层会失守——这样你就永远有兜底。

→ [6.1 多层防御模型 →](./1-defense-model)
