# 6.4 多租户隔离与文件系统

> **本节你会学到**
> - 跨租户串数据的典型模式（几乎都是配置问题，不是代码 bug）
> - 三层 FS 隔离：`working_directory` → 用户命名空间 → 容器/虚机
> - 上线前确认租户安全的检查清单

多租户 Agent 嵌入最容易出的安全事故：**数据串租户**。根源往往不是代码漏洞，而是**共享了同一个 `working_directory`** 或**共享了进程级资源**。

## 黄金规则：一会话 = 一目录 = 一实例

```
❌ 错误：共享 cwd
┌─────────────────────────────────┐
│   进程                           │
│  ┌───────────┐  ┌───────────┐   │
│  │ agent_A   │  │ agent_B   │   │
│  │  ↓        │  │  ↓        │   │
│  │ Path.cwd()│◄─┤ Path.cwd()│   │
│  └───────────┘  └───────────┘   │
│        └────────────────┘        │
│   读到对方的 .agentao/memory.db  │
└─────────────────────────────────┘

✅ 正确：显式隔离
┌─────────────────────────────────┐
│   进程                           │
│  ┌───────────┐  ┌───────────┐   │
│  │ agent_A   │  │ agent_B   │   │
│  │ cwd=/A    │  │ cwd=/B    │   │
│  └───────────┘  └───────────┘   │
│      ↓              ↓            │
│  /data/tenant-A  /data/tenant-B │
└─────────────────────────────────┘
```

**强制要求**：构造 Agent 时**必须**显式传 `working_directory=Path(...)`。不要省略，不要相信默认。

## 目录布局模板

### 模板 A · 按租户分目录

```
/data/
├── tenant-acme/
│   ├── AGENTAO.md            ← acme 的项目说明
│   ├── .agentao/
│   │   ├── memory.db          ← 记忆
│   │   ├── permissions.json   ← 权限规则
│   │   ├── mcp.json           ← MCP 配置
│   │   └── sandbox.json       ← 沙箱规则
│   ├── skills/                ← 技能
│   └── workspace/             ← Agent 可写临时区
├── tenant-globex/
│   └── ...
```

构造时：

```python
agent = Agentao(working_directory=Path(f"/data/tenant-{tenant.id}"))
```

**好处**：所有配置、权限、记忆、技能自动按租户隔离，**代码里不用写 tenant_id 过滤逻辑**。

### 模板 B · 临时工作区

每会话创建独立临时目录，会话结束清理：

```python
from pathlib import Path
from tempfile import mkdtemp
import shutil

def make_session_workdir(tenant_id: str, user_id: str) -> Path:
    root = Path(mkdtemp(prefix=f"agentao-{tenant_id}-{user_id}-"))
    # 从租户模板目录拷贝配置
    template = Path(f"/data/tenant-{tenant_id}/template")
    (root / "AGENTAO.md").write_text((template / "AGENTAO.md").read_text())
    shutil.copytree(template / "skills", root / "skills")
    return root

def cleanup_session_workdir(workdir: Path):
    shutil.rmtree(workdir, ignore_errors=True)
```

**好处**：会话结束文件全清，不留痕迹。**代价**：每会话都加载配置，略慢。

## 用户级记忆的陷阱

```python
# 工厂默认接进来的形态——注意 user_store 那条。
from agentao.memory import MemoryManager, SQLiteMemoryStore
agent._memory_manager = MemoryManager(
    project_store=SQLiteMemoryStore.open_or_memory(
        working_directory / ".agentao" / "memory.db"
    ),
    user_store=SQLiteMemoryStore.open(
        Path.home() / ".agentao" / "memory.db"   # ← 这是进程级别的！
    ),
)
```

即便 `working_directory` 隔离好了，`~/.agentao/memory.db` 是**进程级共享**的——两个租户的 Agent 会读写同一个用户级记忆库。

**解决方案 A · 禁用用户级**：

```python
agent._memory_manager = MemoryManager(
    project_store=SQLiteMemoryStore.open_or_memory(
        workdir / ".agentao" / "memory.db"
    ),
    # user_store=None — 完全不用用户级
)
```

**解决方案 B · 按租户改 HOME**：

```python
import os
os.environ["HOME"] = f"/data/tenant-{tenant.id}/home"
agent = Agentao(working_directory=...)
```

影响整个进程的 `Path.home()`——只在**每进程一租户**时适用（ACP 子进程模型）。

**解决方案 C · 每租户独立进程**：

用 ACP 模式，每租户起一个 Agentao 子进程。进程级隔离最干净，但成本最高。

## 文件系统写入边界

默认 Agent 可以写任何它通过权限规则允许的路径。**多租户生产**强烈建议：

```json
{
  "rules": [
    {
      "tool": "write_file",
      "args": {"path": "^/data/tenant-${TENANT_ID}/"},
      "action": "allow"
    },
    {"tool": "write_file", "action": "deny"}
  ]
}
```

配合**沙箱**（6.2）双重保险。

### 动态生成规则

rule 文件不支持直接变量展开。要按租户注入 `${TENANT_ID}`，用**程序式权限引擎**：

```python
engine = PermissionEngine(project_root=workdir)
engine.rules.insert(0, {
    "tool": "write_file",
    "args": {"path": f"^{re.escape(str(workdir))}/"},
    "action": "allow",
})
engine.rules.append({"tool": "write_file", "action": "deny"})

agent = Agentao(working_directory=workdir, permission_engine=engine)
```

## MCP 跨租户污染

同一个 MCP 服务器实例**不应被多租户共享**——它可能缓存数据、有连接池、依赖单一凭据。

**正确模式**：每租户独立 MCP 子进程：

```python
agent = Agentao(
    working_directory=workdir,
    extra_mcp_servers={
        "github": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
            "env": {"GITHUB_TOKEN": tenant.github_token},   # 每租户不同
        },
    },
)
```

Agent 关闭时 `agent.close()` 会自动 disconnect MCP 子进程。

## 温度数据：日志与临时文件

### agentao.log

默认写到 `<working_directory>/agentao.log`——天然按租户分离。**不要**改回写到全局路径（比如 `/var/log/agentao.log`）——会造成多租户日志混杂。

### Python 临时文件

LLM 可能让 Agent 跑 `tempfile.mkdtemp()`——默认写到 `/tmp`，**跨租户可见**。生产建议：

- 容器里 mount 隔离的 `/tmp`（比如 `--tmpfs /tmp` 每容器独立）
- 或在 Agent 环境里强制 `TMPDIR=<working_directory>/tmp`

### MCP 子进程的 cwd

MCP 子进程默认继承父进程的 cwd。如果 Agent 的 working_directory 没正确传到 MCP，也会串租户。Agentao 会在 `extra_mcp_servers` 自动合并 session cwd，但**你写自己的 MCP Server 时**要 respect 传入的环境。

## 数据库 / API 调用的租户边界

这不是 Agentao 的问题，但要讲清楚：**你的业务工具**（自定义 Tool 调数据库 / 调 API）**必须**自己带 `tenant_id` 过滤，不能信任 LLM 传的参数。

```python
class GetUserTool(Tool):
    def __init__(self, db, tenant_id):
        super().__init__()
        self.db = db
        self.tenant_id = tenant_id   # 构造时绑定

    def execute(self, user_id: str, **kw) -> str:
        # ✅ 用构造时的 tenant_id，不用 kwargs 里的
        user = self.db.get_user(user_id, tenant_id=self.tenant_id)
        ...
```

把 `tenant_id` **绑定到 Tool 实例**，不暴露给 LLM——这样 prompt injection 也无法越权。

## 自测清单

部署前**必须**能回答"如果两个租户同时用产品，会不会……"：

- [ ] 读到对方的 AGENTAO.md？（看 `working_directory`）
- [ ] 读到对方的记忆？（看 project + global memory DB 路径）
- [ ] 读到对方的权限规则？（看 `PermissionEngine.project_root`）
- [ ] 读到对方的技能？（看 SkillManager 的 3 层）
- [ ] 共享 MCP Server 进程？（看是否 per-session `extra_mcp_servers`）
- [ ] 共享 /tmp？（看容器/隔离）
- [ ] 业务工具能跨租户查？（看 Tool 里有没有 tenant_id guard）
- [ ] 日志混在一起？（看 agentao.log 路径）

## TL;DR

- **一会话 = 一 `working_directory` = 一 `Agentao` 实例**。绝不跨租户复用 agent，哪怕只是一瞬间。
- 三层叠加：每会话 `working_directory`、操作系统用户命名空间、容器/虚机。
- 记忆用户作用域（`~/.agentao/memory.db`）是**进程全局**——多租户场景要禁用，或按 `tenant_id+user_id` 索引。
- 自定义 Tool 在构造时捕获了 `tenant_id` 的，**必须每会话新建**，不能从进程级缓存里复用。

→ [6.5 密钥管理与 Prompt 注入](./5-secrets-injection)
