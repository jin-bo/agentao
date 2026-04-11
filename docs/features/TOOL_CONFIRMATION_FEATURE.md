# Tool Confirmation Feature

## 概述

为 Agentao 添加了工具确认功能，在执行 Shell 和 Web 工具前需要用户明确确认，提高安全性。

## 改动内容

### 修改的文件

1. **agentao/tools/base.py**
   - 在 `Tool` 基类添加 `requires_confirmation` 属性（默认 False）

2. **agentao/tools/shell.py**
   - `ShellTool` 设置 `requires_confirmation = True`

3. **agentao/tools/web.py**
   - `WebFetchTool` 设置 `requires_confirmation = True`
   - `GoogleSearchTool` 设置 `requires_confirmation = True`

4. **agentao/agent.py**
   - 添加 `confirmation_callback` 参数
   - 在工具执行前检查 `requires_confirmation`
   - 如需确认则调用回调函数
   - 记录确认结果到日志

5. **agentao/cli.py**
   - 添加 `confirm_tool_execution()` 方法
   - 使用 rich.Confirm 显示确认提示
   - 将确认回调传递给 agent

6. **README.md**
   - 添加工具确认功能说明
   - 更新特性列表

### 新增文件

1. **test_tool_confirmation.py**
   - 测试 `requires_confirmation` 属性
   - 测试确认回调机制
   - 测试无回调时的行为
   - 所有测试通过 ✅

2. **TOOL_CONFIRMATION.md**
   - 功能文档（中文）
   - 使用示例和说明

## 工作流程

```
1. LLM 决定使用工具
   ↓
2. Agent 检查工具的 requires_confirmation 属性
   ↓
3. 如果为 True 且有回调函数：
   - 暂停执行
   - 调用 confirmation_callback
   - CLI 显示确认提示
   - 等待用户输入 (y/n)
   ↓
4a. 用户确认 (y)         4b. 用户拒绝 (n)
    ↓                        ↓
    执行工具                 取消执行
    ↓                        ↓
    返回结果                 通知 LLM 已取消
```

## 需要确认的工具

| 工具 | 原因 |
|------|------|
| `run_shell_command` | 可能执行危险命令、修改系统 |
| `web_fetch` | 可能访问敏感网站、产生网络流量 |
| `google_web_search` | 可能暴露搜索意图、产生网络请求 |

## 确认提示示例

```
⚠️  Tool Confirmation Required
Tool: run_shell_command
Description: Execute a shell command and return its output. Use with caution as it can execute any command.
Arguments:
  • command: ls -la
  • working_directory: .
  • timeout: 30

Do you want to execute this tool? (y/n):
```

## 使用方法

### 用户使用

无需配置，自动生效：

```bash
uv run python main.py

You: List all files in current directory

# 系统会显示确认提示
⚠️  Tool Confirmation Required
Tool: run_shell_command
...
Do you want to execute this tool? (y/n): y

# 确认后执行
```

### 开发者添加新工具

```python
class MyTool(Tool):
    @property
    def requires_confirmation(self) -> bool:
        """Set to True for tools that perform risky operations."""
        return True  # 需要确认

    def execute(self, **kwargs) -> str:
        # 工具实现
        pass
```

## 安全优势

1. **防止意外执行** - 用户可以在执行前检查命令
2. **透明度** - 显示完整的工具参数
3. **可控性** - 用户决定是否执行
4. **审计** - 所有确认决定记录在日志中

## 向后兼容性

- ✅ 不影响现有工具（默认不需要确认）
- ✅ 无需修改配置
- ✅ 可选功能（如果没有回调函数，工具正常执行）

## 测试

运行测试：

```bash
uv run python test_tool_confirmation.py
```

所有测试通过：
- ✅ Shell & Web 工具需要确认
- ✅ 文件操作工具不需要确认
- ✅ Agent 接受确认回调
- ✅ 确认回调签名正确
- ✅ 无回调时正常工作

## 域名分级权限 (Domain-Based Tiered Permissions)

**版本 0.2.8 新增**：`web_fetch` 工具现在支持基于域名的分级权限控制。

### 工作原理

权限引擎在评估 `web_fetch` 请求时，会解析 URL 的域名并按以下优先级判断：

1. **白名单 (allowlist)** — 自动允许，无需确认
2. **黑名单 (blocklist)** — 自动拒绝，防止 SSRF 攻击
3. **默认 (default)** — 需要用户确认

### 默认规则

| 类别 | 域名 | 行为 |
|------|------|------|
| 白名单 | `.github.com`, `.docs.python.org`, `.wikipedia.org`, `r.jina.ai`, `.pypi.org`, `.readthedocs.io` | 自动允许 |
| 黑名单 | `localhost`, `127.0.0.1`, `0.0.0.0`, `169.254.169.254`, `.internal`, `.local`, `::1` | 自动拒绝 |
| 其他 | 所有不在上述列表中的域名 | 需要确认 |

### 域名匹配规则

- **前缀点号** (如 `.github.com`)：后缀匹配 — 匹配 `github.com` 和 `api.github.com`，但不匹配 `notgithub.com`
- **无前缀点号** (如 `r.jina.ai`)：精确匹配 — 只匹配 `r.jina.ai`
- 匹配不区分大小写，端口号会被自动忽略

### 自定义配置

在 `.agentao/permissions.json` 中添加自定义域名规则：

```json
{
  "rules": [
    {
      "tool": "web_fetch",
      "domain": {"allowlist": [".mycompany.com", ".confluence.io"]},
      "action": "allow"
    },
    {
      "tool": "web_fetch",
      "domain": {"blocklist": [".sketchy.io", "evil.com"]},
      "action": "deny"
    }
  ]
}
```

自定义规则在 `workspace-write` 模式下优先于内置预设规则评估（先匹配先生效）。

### 安全防护

- **SSRF 防护**：默认黑名单覆盖回环地址、链路本地地址、云元数据端点
- **URL 解析安全**：使用 `urlparse.hostname` 获取规范化主机名（小写、去端口、忽略 userinfo）
- **无后缀误匹配**：IP 地址使用精确匹配，不会把 `1.127.0.0.1` 误匹配为 `127.0.0.1`

### 示例

```
# 自动允许（白名单）
web_fetch https://docs.python.org/3/library/os.html  → ✓ ALLOW

# 自动拒绝（黑名单）
web_fetch http://169.254.169.254/latest/meta-data    → ✗ DENY

# 需要确认（其他）
web_fetch https://example.com/api                    → ? ASK
```

## 未来改进

可能的增强：
- 重定向目标域名检查（当前只检查初始 URL）
- RFC 1918 私有 IP 段匹配（10.x、172.16.x、192.168.x）
- `google_web_search` 的类似分级权限

---

**特性状态**: ✅ 已实现并测试
**版本**: 0.2.8
**最后更新**: 2026-04-11
