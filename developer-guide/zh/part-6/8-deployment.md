# 6.8 容器化、灰度与回滚

Agent 的部署与普通 Web 服务有三点不同：**每个容器长寿**（会话状态）、**依赖重**（Python + openai + mcp）、**可灰度的维度多**（模型、技能、规则都可以独立版本化）。

## Dockerfile 模板

```dockerfile
# ────────────────────────────────
# Stage 1: builder
FROM python:3.12-slim AS builder

RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# ────────────────────────────────
# Stage 2: runtime
FROM python:3.12-slim

# 非 root 用户
RUN useradd -m -u 10001 agent
WORKDIR /app
COPY --from=builder --chown=agent:agent /app/.venv /app/.venv
COPY --chown=agent:agent your_app/ ./your_app/
COPY --chown=agent:agent skills/ ./skills/
COPY --chown=agent:agent AGENTAO.md ./

# 只读根，可写的只有这些
RUN mkdir -p /data /tmp/agent && chown agent:agent /data /tmp/agent
USER agent

# 工作区
WORKDIR /data
ENV PYTHONPATH=/app \
    PATH="/app/.venv/bin:$PATH" \
    TMPDIR=/tmp/agent \
    HOME=/tmp/agent

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "from agentao import Agentao; print('ok')" || exit 1

# 入口——根据你的封装
CMD ["python", "-m", "your_app.server"]
```

### 关键点

- **多阶段构建**：最终镜像不含 `uv` 和构建工具
- **非 root**：USER 10001
- **只读根**：`--read-only` 启动 + tmpfs `/tmp`
- **HOME 改到 /tmp/agent**：避免记忆 DB 写到 /root/
- **PYTHONUNBUFFERED**：日志实时刷（如果你不用 JSON logger 可以加 `-u`）

## docker-compose 部署样板

```yaml
version: "3.9"

services:
  agent:
    image: your-agent:${TAG:-latest}
    read_only: true
    tmpfs:
      - /tmp:size=512M
    volumes:
      - ./data:/data               # 会话持久化
      - ./logs:/data/logs          # 日志
    environment:
      OPENAI_API_KEY: ${OPENAI_API_KEY}
      OPENAI_MODEL: gpt-5.4
      # 给每容器一个独立租户
      TENANT_ID: ${TENANT_ID}
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    mem_limit: 2g
    cpus: 1.0
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c", "from agentao import Agentao"]
      interval: 30s
      timeout: 5s
```

## Kubernetes 注意事项

```yaml
apiVersion: apps/v1
kind: StatefulSet       # 因为有会话状态，StatefulSet 优于 Deployment
metadata:
  name: agent
spec:
  serviceName: agent
  replicas: 3
  template:
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        fsGroup: 10001
      containers:
      - name: agent
        image: your-agent:v0.2.13
        resources:
          requests: {cpu: "500m", memory: "1Gi"}
          limits: {cpu: "2", memory: "4Gi"}
        securityContext:
          readOnlyRootFilesystem: true
          allowPrivilegeEscalation: false
          capabilities:
            drop: ["ALL"]
        env:
        - name: OPENAI_API_KEY
          valueFrom:
            secretKeyRef: {name: agent-secrets, key: openai-key}
        volumeMounts:
        - name: data
          mountPath: /data
        - name: tmp
          mountPath: /tmp
        livenessProbe:
          exec:
            command: ["python", "-c", "from agentao import Agentao"]
          periodSeconds: 30
      volumes:
      - name: tmp
        emptyDir: {medium: Memory, sizeLimit: 512Mi}
  volumeClaimTemplates:
  - metadata: {name: data}
    spec:
      accessModes: ["ReadWriteOnce"]
      resources: {requests: {storage: 10Gi}}
```

**StatefulSet 而非 Deployment**：会话/记忆持久在 PVC 里，pod 重启后能拿回同一会话。

**粘性路由**：Service 要配 `sessionAffinity: ClientIP`（或走 Ingress 的 sticky），让同一 user 的请求总落到同一 pod。否则会话路由乱了。

## 可灰度的维度

Agent 产品**可以独立灰度**的维度比普通服务多得多：

| 维度 | 做法 | 风险 |
|------|-----|-----|
| **模型版本** | 小流量切新模型观察 | 中——行为可能变 |
| **代码版本** | 常规蓝绿/金丝雀 | 中——同普通服务 |
| **技能** | `skills/` 目录按租户分叉 | 低 |
| **权限规则** | `.agentao/permissions.json` 渐进收紧 | 高——规则松变紧可能挡掉合法工具 |
| **沙箱 profile** | `.agentao/sandbox.json` 改 default | 高——错配全停 |
| **AGENTAO.md** | 项目级改动 | 中——影响所有指令遵从性 |

**建议节奏**：模型和技能可**周级**灰度；规则/沙箱/AGENTAO.md 改动必须**全面测试 + 灰度 + 回滚预案**。

## 模型切换策略

Agentao 支持运行时切换（[2.3 节](/zh/part-2/3-lifecycle)）：

```python
# 某些用户用新模型观察
if user.id in beta_users:
    agent.set_model("gpt-5")
else:
    agent.set_model("gpt-5.4")
```

**注意**：切换不会清历史——如果新旧模型上下文格式不兼容（极少见），需要同时 `clear_history()`。

## 回滚预案

Agent 的改动容易**用户看不到问题但积累中**（比如行为漂移、成本上涨）。回滚要求：

| 改动类型 | 回滚方式 | 回滚时间目标 |
|---------|---------|-----------|
| 代码 bug | 蓝绿切换 | < 1 min |
| 权限规则误封 | 备份 JSON 回滚 | < 5 min |
| 沙箱 profile 错 | 备份 + `/sandbox off` | < 5 min |
| 模型表现变差 | 切回旧模型 ID | < 1 min |
| 技能写错 | git revert + 重启 | < 10 min |

**实现**：关键配置进 git（`.agentao/*.json`、`skills/`、`AGENTAO.md`），任何改动有 commit，能立即 revert。

## 会话迁移（优雅 pod 下线）

Kubernetes rolling update 时，pod 要关闭前的流程：

```python
# SIGTERM handler
def on_sigterm(signum, frame):
    stop_accepting_new_sessions()
    # 持久化每个活跃会话（如果需要）
    for sid, (agent, _, _) in pool._pool.items():
        persist_session(sid, agent.messages)
        agent.close()
    # 正在跑的 chat() 用 cancellation token 停
    for agent in active_agents:
        if agent._current_token:
            agent._current_token.cancel("shutdown")
    sys.exit(0)
```

用户侧通过 `session/load`（ACP）或你的 `add_message()` 循环（SDK）重建会话到新 pod。

## CI/CD 要跑的测试

| 阶段 | 测试 |
|------|-----|
| 构建 | 单元测试（工具、技能 YAML 格式） |
| 打包前 | 系统提示长度、AGENTAO.md 无密钥扫描 |
| 部署前 | 红队 prompt 注入测试（[6.5](./5-secrets-injection)） |
| 预发 | 完整用户旅程 smoke test |
| 生产 | 持续的 canary session（每小时固定 prompt 对比输出） |

## 成本监控告警

```
• 日均 tokens > 上周 * 1.5  → P2 告警（成本异常）
• 单会话 tokens > 50k         → P3 告警（可能卡死）
• 工具失败率 > 10%            → P2（配置可能错）
• LLM 5xx 率 > 5%             → P1（厂商有问题）
```

---

**第 6 部分到此完成。** 你现在有了从沙箱到部署的完整生产化知识栈。最后一部分讲把所有这些编织成**典型集成蓝图**——客户真实场景的手把手示例。

→ [第 7 部分 · 典型集成蓝图](/zh/part-7/)（撰写中）
