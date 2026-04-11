# 设计方案：按 Marketplace 组织插件目录

## 1. 背景

Claude Code 将插件按 marketplace 组织（如 `~/.claude/plugins/cache/openai-codex/codex/1.0.2/`）。Agentao 当前使用扁平结构。引入 marketplace 目录层级，为未来支持多个插件市场奠定基础。

---

## 2. 目录结构

```
~/.agentao/plugins/
├── {marketplace-id}/                    # marketplace 目录（autodiscovery）
│   ├── {plugin-name}/
│   │   └── {version}/                  # 插件根目录
│   │       ├── plugin.json             # 可选
│   │       ├── skills/
│   │       ├── commands/
│   │       ├── agents/
│   │       └── hooks/
│   └── ...
├── local/                               # 本地手动插件
│   ├── {plugin-name}/                  # 插件根目录（无版本层）
│   │   ├── plugin.json
│   │   └── ...
│   └── ...
└── plugins_config.json                 # 禁用规则（已有）
```

项目级结构相同：`<project>/.agentao/plugins/{marketplace-id|local}/...`

### 2.1 两层扫描

| 层 | 路径模式 | marketplace 值 |
|----|----------|----------------|
| marketplace | `{mp-id}/{plugin}/{version}/` | `"{mp-id}"` |
| local | `local/{plugin}/` | `"local"` |

`local` 为保留字，不可用作 marketplace ID。

### 2.2 版本选择

`{marketplace}/{plugin}/` 下可能存在多个版本目录。按目录名降序排序取第一个（最新版本）。

---

## 3. 数据模型变更

`PluginCandidate` 和 `LoadedPlugin` 各新增两个可选字段：

```python
marketplace: str | None = None       # "openai-codex" / "local" / None(inline)
qualified_name: str | None = None    # "name@marketplace" 或 None
```

`qualified_name` 格式：`"{name}@{marketplace}"`（marketplace 非 None 时）。

不新增其他模型类，不引入 `installed_plugins.json` 或 `known_marketplaces.json`。

---

## 4. `manager.py` 变更

### 4.1 `_scan_dir()` 重写

```python
def _scan_dir(self, plugins_dir: Path, source: str) -> list[PluginCandidate]:
    if not plugins_dir.is_dir():
        return []
    candidates: list[PluginCandidate] = []
    for child in sorted(plugins_dir.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if child.name == "local":
            candidates.extend(self._scan_local_dir(child, source))
        else:
            candidates.extend(self._scan_marketplace_dir(child, source))
    return candidates

def _scan_local_dir(self, local_dir: Path, source: str) -> list[PluginCandidate]:
    candidates = []
    for child in sorted(local_dir.iterdir()):
        if child.is_dir() and not child.name.startswith("."):
            candidate = self._try_parse(child, source, marketplace="local")
            if candidate is not None:
                candidates.append(candidate)
    return candidates

def _scan_marketplace_dir(self, mp_dir: Path, source: str) -> list[PluginCandidate]:
    marketplace_id = mp_dir.name
    candidates = []
    for plugin_dir in sorted(mp_dir.iterdir()):
        if not plugin_dir.is_dir() or plugin_dir.name.startswith("."):
            continue
        version_dirs = sorted(
            [d for d in plugin_dir.iterdir() if d.is_dir() and not d.name.startswith(".")],
            key=lambda d: d.name,
        )
        if not version_dirs:
            continue
        latest = version_dirs[-1]
        candidate = self._try_parse(latest, source, marketplace=marketplace_id)
        if candidate is not None:
            candidates.append(candidate)
    return candidates
```

### 4.2 `_try_parse()` 加 marketplace 参数

```python
def _try_parse(self, plugin_root, source, *, marketplace=None):
    ...
    qualified = f"{manifest.name}@{marketplace}" if marketplace else None
    return PluginCandidate(..., marketplace=marketplace, qualified_name=qualified, ...)
```

### 4.3 `load_plugin()` 传递字段

```python
return LoadedPlugin(..., marketplace=candidate.marketplace, qualified_name=candidate.qualified_name, ...)
```

### 4.4 `resolve_precedence()` 用 `qualified_name` 分组

不同 marketplace 的同名插件各自独立，不互相覆盖：

```python
key = c.qualified_name or c.name
by_name.setdefault(key, []).append(c)
```

### 4.5 `filter_disabled()` 同时匹配两种名称

```python
if c.name in disabled or (c.qualified_name and c.qualified_name in disabled):
```

---

## 5. 诊断与 CLI

`diagnostics.py` — `format_report()` 显示 marketplace 标签：

```
  - codex v1.0.2 [openai-codex] (global: ~/.agentao/plugins/openai-codex/codex/1.0.2)
  - my-tool [local] (global: ~/.agentao/plugins/local/my-tool)
```

`cli.py` — `_plugin_list_cli()` JSON 输出增加 `marketplace`、`qualified_name` 字段。

---

## 6. 改动文件清单

| 文件 | 变更 |
|------|------|
| `agentao/plugins/models.py` | `PluginCandidate`、`LoadedPlugin` 加 `marketplace`、`qualified_name` |
| `agentao/plugins/manager.py` | `_scan_dir()` 重写；`_try_parse()` 加参数；`load_plugin()`/`resolve_precedence()`/`filter_disabled()` 适配 |
| `agentao/plugins/diagnostics.py` | `format_report()` 显示 marketplace |
| `agentao/cli.py` | JSON 输出加字段 |
| `tests/test_plugin_loader.py` | 新增 `TestMarketplaceDiscovery` |

**不改动：** `manifest.py`、`skills.py`、`agents.py`、`hooks.py`、`mcp.py`

---

## 7. 测试计划

```python
class TestMarketplaceDiscovery:
    def test_discovers_marketplace_plugins(self): ...
    def test_discovers_local_plugins(self): ...
    def test_picks_latest_version(self): ...
    def test_same_name_different_marketplace_coexist(self): ...
    def test_qualified_name_format(self): ...
    def test_disable_by_qualified_name(self): ...
    def test_diagnostics_shows_marketplace(self): ...
```
