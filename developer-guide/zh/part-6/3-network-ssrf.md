# 6.3 网络与 SSRF 防护

Agent 能访问网络的三个工具：`web_fetch`、`google_web_search`、以及通过 MCP 的各种服务。本节讲如何把它们的访问面缩到**最小必要**。

## 三层网络防线

```
   LLM → web_fetch / google_web_search / MCP
                │
                ▼
  ┌──────────────────────────────┐
  │ 层 1: PermissionEngine 域名  │  .github.com 允 / 127.0.0.1 拒
  │        allowlist / blocklist  │
  └──────────────────────────────┘
                │
                ▼
  ┌──────────────────────────────┐
  │ 层 2: HTTP 客户端（httpx）    │  TLS、超时、重定向策略
  └──────────────────────────────┘
                │
                ▼
  ┌──────────────────────────────┐
  │ 层 3: 网络边界                │  VPC / egress rules / firewall
  │        (基础设施级)           │
  └──────────────────────────────┘
```

## 层 1 · 域名规则（权限引擎）

WORKSPACE_WRITE 预设自带的 SSRF 黑名单值得每个项目都**保留+扩展**：

```json
{
  "tool": "web_fetch",
  "domain": {
    "blocklist": [
      "localhost",
      "127.0.0.1",
      "0.0.0.0",
      "169.254.169.254",   // AWS/GCP 元数据服务
      ".internal",
      ".local",
      "::1"                 // IPv6 localhost
    ]
  },
  "action": "deny"
}
```

**生产环境建议扩展**：

```json
{
  "tool": "web_fetch",
  "domain": {
    "blocklist": [
      "localhost", "127.0.0.1", "0.0.0.0", "169.254.169.254",
      "::1", ".internal", ".local",
      // 你自己的内网网段（字面量，因为 IP 没法做后缀匹配）
      "10.", "172.16.", "192.168.",   // 注意：这些只会匹配 URL 里的 literal，IP 匹配不完整
      // 你的 SaaS 公司内部域名
      ".corp.your-company.com",
      ".internal.your-company.com",
      // 云厂商 metadata
      "metadata.google.internal",
      "metadata.azure.com"
    ]
  },
  "action": "deny"
}
```

⚠️ **局限**：`_extract_domain` 从 URL 提取 hostname，纯字符串前缀匹配。攻击者可以用**十进制 IP**（如 `http://2130706433`，等于 `127.0.0.1`）、**IPv6 形式**或**DNS rebinding** 绕过。**生产环境必须加层 3**（基础设施级网络隔离）兜底。

### 限制到 allowlist 的保守模式

更安全的做法是**默认拒绝**，只显式允许你需要的域：

```json
{
  "rules": [
    {"tool": "web_fetch", "domain": {"allowlist": [".your-docs-site.com", ".github.com"]}, "action": "allow"},
    {"tool": "web_fetch", "action": "deny"}
  ]
}
```

客户端产品（Agent 帮用户做事）一般要 blocklist；内部工具（Agent 做研究）可用更开放的 allowlist。

## 层 2 · HTTP 客户端行为

Agentao 的 `web_fetch` 使用 `httpx`，默认：

- 10 秒超时
- 跟随 3 次重定向
- TLS 验证开启
- User-Agent 可定制

**安全注意**：允许重定向 = 允许 302 跳转到内网地址绕过 hostname 检查。生产上建议**禁止重定向**或**每次重定向重新跑域名规则**。目前 Agentao 未做"重定向后重检"——这是已知限制。

你可以**自定义 web_fetch**（替代内置）来加严：

```python
from agentao.tools.base import Tool
import httpx

class StrictWebFetchTool(Tool):
    @property
    def name(self) -> str:
        return "web_fetch"

    def execute(self, url: str, **kw) -> str:
        with httpx.Client(follow_redirects=False, timeout=5.0) as client:
            resp = client.get(url)
            if resp.status_code // 100 == 3:
                return "Redirects are disabled for security. URL: " + url
            return resp.text[:50000]   # 限长
    # ...省略其他方法
```

注册它会**覆盖**内置 `web_fetch`：

```python
agent = Agentao(...)
agent.tools.register(StrictWebFetchTool())   # warning: overwriting
```

## 层 3 · 基础设施级隔离

这是兜底层——**哪怕前面所有层都失效，Agent 也够不到危险的东西**。

### 容器的 network 选项

```bash
# 完全无网：Agent 只能靠 MCP stdio 之类的本地服务
docker run --network=none agent-image

# 自定义网络：只允许出站到白名单
docker run --network=custom-egress-only agent-image
```

### VPC egress 白名单

云上给 Agent 容器绑定一个 egress security group，只允许出站到：

- LLM API（OpenAI / Anthropic 官方 IP 段）
- 必要的 MCP SSE 端点
- 白名单文档站点（`.github.com`, `.pypi.org` 等）

禁止一切其他出站。这样哪怕规则引擎被绕过，LLM 请求也到不了内网。

### DNS 层过滤

用公司 DNS 做**内网域名黑名单**——Agent 的 hostname 解析请求被 DNS 拒绝，直接连不上。

## MCP 服务器的网络

MCP 服务器通常**比 web_fetch 风险更高**——它们有自己的凭据、自己的访问面：

```json
{
  "mcpServers": {
    "database": {
      "command": "...",
      "env": {"DB_URL": "postgres://..."}   // 数据库访问
    }
  }
}
```

**控制策略**：

1. **每租户独立 MCP 实例** —— 凭据按租户隔离（参见 [5.3](/zh/part-5/3-mcp)）
2. **MCP 子进程跑在独立网络命名空间** —— Linux 上用 `unshare -n` 或容器
3. **把 MCP 工具也纳入权限规则**：

```json
{
  "rules": [
    {"tool": "mcp_database_query", "args": {"sql": "^SELECT "}, "action": "allow"},
    {"tool": "mcp_database_*", "action": "deny"}
  ]
}
```

## ACP 模式的网络考量

Agentao 作为 ACP Server 时**不监听端口**——只用 stdio。这是好消息：

- 宿主不需要为 Agent 开 inbound 端口
- 网络攻击面缩到**出站**方向

但 Agent 的 LLM 调用、web_fetch、MCP SSE 还是会出站。同样适用上面层 1-3 的策略。

## 审计日志

每次网络访问都应进日志：

```python
# 在 on_event 里监听 TOOL_COMPLETE
def on_event(ev):
    if ev.type == EventType.TOOL_COMPLETE and ev.data["tool"] in {"web_fetch", "google_web_search"}:
        audit_log.info("network_call", extra={
            "tool": ev.data["tool"],
            "status": ev.data["status"],
            "duration_ms": ev.data["duration_ms"],
            "call_id": ev.data["call_id"],
            # 从别处查到 URL（比如 TOOL_START 时存一下）
        })
```

`agentao.log` 默认已经记录工具调用的完整参数——日志脱敏请看 [6.5 密钥管理](./5-secrets-injection)。

## 常见陷阱

### ❌ 只有 allowlist 没有 blocklist

```json
{"tool": "web_fetch", "domain": {"allowlist": [".github.com"]}, "action": "allow"}
// 缺 blocklist → 其他 URL 走到了默认 ASK → 用户可能点同意访问内网
```

补上明确的 blocklist + 默认 deny 才稳。

### ❌ 相信 LLM 不会去访问内网

Prompt injection 可以**骗**LLM 访问任何 URL。不要依赖 LLM 的"常识"，依赖规则和基础设施。

### ❌ 重定向未受保护

`web_fetch https://good.com` → 302 → `http://169.254.169.254/` 会被内置 `httpx` 跟随。生产上考虑用自定义 `web_fetch` 禁重定向。

→ [6.4 多租户隔离与文件系统](./4-multi-tenant-fs)
