# 7.5 蓝图 E · 离线批处理 / 定时智能任务

> **运行此例**：[`examples/batch-scheduler/`](https://github.com/jin-bo/agentao/tree/main/examples/batch-scheduler) —— `uv run python -m src.daily_digest`

**场景**：每晚跑一个 cron，汇总昨天的 GitHub 动态、用 RSS 写周报、从昨天的订单里找异常。**没有人在线看**——agent 要自己判断、执行，或者干净地失败、响亮地报警。

## 谁 & 为什么

- **产品形态**：调度 worker（cron / k8s CronJob / Airflow）
- **用户**：看输出的干系人（没人盯着运行过程）
- **痛点**：一堆"每天早上给我 10 分钟我就能做 X"的任务永远做不完

## 无人值守 agent 的设计原则

1. **响亮失败，不要静默** —— 绝不自动静默恢复，LLM 出错，任务非零退出
2. **有界预算** —— `max_iterations` 比交互场景更紧；token 预算硬约束
3. **不用 `requires_confirmation` 工具** —— 无人值守意味着没人能确认。要么严格评审后直接允许，要么不注册
4. **确定性输出契约** —— 最终回复必须是可解析的 schema，方便下游消费
5. **幂等** —— 跑两次结果一样（用日期、tag 等）

## 架构

```
cron / k8s CronJob
       │ 每天 03:00
       ▼
Python 入口
       │
       ├─ Agentao 实例（每次跑都新建，结束干净关闭）
       │    ├─ 技能: "daily-digest"
       │    ├─ 工具: web_fetch（只读，白名单源）、write_file
       │    └─ PermissionEngine: READ_ONLY + 显式写入白名单
       │
       ├─ 产出: /reports/YYYY-MM-DD.md
       │
       └─ 后处理: 邮件 / Slack / S3 上传
```

## 关键代码

### 1 · 最小批处理 runner

```python
# jobs/daily_digest.py
import os, sys, json, traceback
from pathlib import Path
from datetime import date
from agentao import Agentao
from agentao.transport import SdkTransport
from agentao.transport.events import EventType

def run():
    today = date.today().isoformat()
    workdir = Path(f"/var/jobs/digest/{today}")
    workdir.mkdir(parents=True, exist_ok=True)

    tokens_used = 0
    def on_event(ev):
        nonlocal tokens_used
        if ev.type is EventType.LLM_TEXT:
            tokens_used += len(ev.data.get("chunk", "")) // 4

    transport = SdkTransport(on_event=on_event)
    agent = Agentao(
        working_directory=workdir,
        transport=transport,
        max_context_tokens=64_000,
    )
    agent.skill_manager.activate_skill(
        "daily-digest",
        task_description="按技能约定生成今天的摘要。",
    )

    try:
        reply = agent.chat(
            "生成今天的 digest。结尾必须有一行 "
            "`RESULT: {\"path\": \"...\", \"items\": N}`，"
            "供调度器消费。",
            max_iterations=40,
        )
        parsed = parse_result(reply)
        print(json.dumps({
            "status": "ok",
            "date": today,
            "tokens_est": tokens_used,
            **parsed,
        }))
    finally:
        agent.close()

def parse_result(reply: str) -> dict:
    import re
    m = re.search(r"RESULT:\s*(\{.*\})\s*$", reply, re.MULTILINE)
    if not m:
        raise SystemExit(f"agent 未输出 RESULT: 行；最后 500 字符:\n{reply[-500:]}")
    return json.loads(m.group(1))

if __name__ == "__main__":
    try:
        run()
    except Exception:
        traceback.print_exc(file=sys.stderr)
        sys.exit(2)
```

### 2 · 带输出契约的技能

````markdown
<!-- skills/daily-digest/SKILL.md -->
---
name: daily-digest
description: 从筛选过的源生成每日 digest。严格遵守输出契约。
---

# 每日 Digest

## 源
按顺序抓以下 URL，404 跳过：
- https://github.com/jin-bo/agentao/commits/main
- https://news.ycombinator.com/
- (你的 RSS 源)

## 输出文件
写到 `./digest.md`，结构：

```
# 每日 Digest — YYYY-MM-DD

## Agentao 提交
- SHA  简短消息

## 技术要点
- 标题  一行总结  (url)

## 待办事项（如有）
- 简短描述
```

## 输出契约
写完后，最终消息必须以下面这一行结尾（唯一）：

`RESULT: {"path": "digest.md", "items": 总要点条数}`

这一行会被机器解析，之后不得再有任何文字。
````

### 3 · k8s CronJob

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: daily-digest
spec:
  schedule: "0 3 * * *"       # 每天 03:00 UTC
  concurrencyPolicy: Forbid   # 昨天没跑完，今天不要再叠加
  jobTemplate:
    spec:
      backoffLimit: 1         # 响亮失败，不要重试 6 次
      template:
        spec:
          restartPolicy: Never
          containers:
          - name: runner
            image: your-agent:v0.2.13
            command: ["python", "-m", "jobs.daily_digest"]
            env:
            - name: OPENAI_API_KEY
              valueFrom:
                secretKeyRef: {name: agent-secrets, key: openai-key}
            resources:
              requests: {cpu: "200m", memory: "512Mi"}
              limits:   {cpu: "1",    memory: "2Gi"}
```

### 4 · 投递步骤

```python
# jobs/deliver.py  — 在 CronJob pod 里 runner 之后执行
import json, smtplib, subprocess
result = json.loads(subprocess.check_output(["python", "-m", "jobs.daily_digest"]))
if result["status"] != "ok":
    raise SystemExit(1)
send_email(to="team@x.com", path=result["path"])
```

或者把 `digest.md` 发到 Slack webhook、上传 S3 等。

## 用 `ACPManager.prompt_once()`（agent 不是 Python 时）

如果调度任务在 Node 或 Go 里，可以通过 ACP 用一次性 helper 驱动——自己构造 `ACPClient`（见 7.2），发一条 `session/prompt`，收集最终消息，关闭。Python 到 Python 且你要**从任务里调另一个 ACP agent** 的场景，用 `ACPManager.prompt_once()`：

```python
from agentao.acp_client import ACPManager

result = ACPManager().prompt_once(
    name="external-reviewer",
    prompt="审查昨天的 digest，检查是否泄漏 PII。",
    cwd="/var/jobs/digest/2026-04-16",
    timeout=120,
)
print(result.stop_reason)
```

## 陷阱

| 上线第二天的 bug | 根因 | 修法 |
|------------------|------|------|
| 任务跑飞，把明天也堵死 | 没有单次超时 | `concurrencyPolicy: Forbid` + `asyncio.wait_for` 包 `chat()` |
| 静默回归（一周 digest 都空） | 没人看日志，输出契约太松 | `items: 0` 或缺 `RESULT:` 行时告警 |
| 一夜烧掉配额 | token 无上限 | `max_iterations` 上限 + `TokenBudget`（[6.7](/zh/part-6/7-resource-concurrency#token-预算)） |
| 重试后同一份 digest 发两遍 | 不幂等 | 以日期打 tag；`/reports/<today>.md` 存在则拒绝重跑 |
| 失败邮件里泄漏密钥 | traceback 带了 API key | stderr 走 scrub filter（[6.5](/zh/part-6/5-secrets-injection)） |

## 可运行代码

完整项目就在主仓 [`examples/batch-scheduler/`](https://github.com/jin-bo/agentao/tree/main/examples/batch-scheduler)——参考本页顶部的 "运行此例" 链接。

```bash
cd examples/batch-scheduler
uv sync && uv run python -m src.daily_digest
```

---

## Part 7 结束——也是主干内容的终点

到这里你已经拥有：

- 两条嵌入路径（[Part 2](/zh/part-2/) SDK、[Part 3](/zh/part-3/) ACP）
- 事件 + UI 集成（[Part 4](/zh/part-4/)）
- 六个扩展点（[Part 5](/zh/part-5/)）
- 安全 + 生产部署（[Part 6](/zh/part-6/)）
- 五个真实蓝图（本部分）

接下来的附录——完整 API 参考、配置键索引、ACP 消息字段、错误码、框架迁移、FAQ、术语表——是落地过程中常翻的查询手册，紧随其后。

→ 附录（即将推出）
