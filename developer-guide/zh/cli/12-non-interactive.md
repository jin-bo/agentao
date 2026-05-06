# 12. 非交互入口

`agentao` 不只是一套 REPL。顶层命令还支持初始化、一次性 prompt、会话恢复、ACP server 模式，以及 skill / plugin 管理。

## `agentao init` — 写 `.env`

第一次在项目里使用时，可以让向导生成 `.env`：

```bash
agentao init
```

向导会询问 provider、API key、base URL、model，然后写入：

```bash
LLM_PROVIDER=OPENAI
OPENAI_API_KEY=...
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-5.4
```

已有 `.env` 时会先询问是否覆盖。

## `agentao -p` / `--print` — 一次性运行

打印模式发送一个 prompt，输出回答，然后退出。

```bash
agentao -p "总结 README"
cat issue.md | agentao --print "根据下面内容生成修复计划"
```

退出码：

| 退出码 | 含义 |
|---|---|
| `0` | 正常完成 |
| `1` | 运行出错 |
| `2` | 达到最大工具迭代数，回答可能不完整 |

## `--resume` — 启动即恢复会话

```bash
agentao --resume
agentao --resume a1b2c3
```

不带 id 恢复最近会话；带 id 时按前缀匹配。REPL 内同等命令是 `/sessions resume <id>`。

## `--acp --stdio` — 作为 ACP Server

```bash
agentao --acp --stdio
```

这会把 Agentao 作为 ACP stdio JSON-RPC server 启动，供 IDE、宿主进程或其他 agent 调用。`--stdio` 当前只在 `--acp` 下有效。

## `--plugin-dir` — 临时加载插件

```bash
agentao --plugin-dir ./my-plugin
agentao plugin --plugin-dir ./my-plugin list
```

`--plugin-dir` 可重复。适合本地开发插件时不安装包，直接指向目录。

## `agentao skill ...`

顶层 `skill` 子命令用于管理受管安装的 skills：

```bash
agentao skill install owner/repo[:path][@ref]
agentao skill list
agentao skill list --installed
agentao skill remove <name>
agentao skill update <name>
agentao skill update --all
```

REPL 内的 `/skills` 管“当前会话看见什么、激活什么”；顶层 `agentao skill ...` 管“磁盘上安装了什么”。

## `agentao plugin list`

```bash
agentao plugin list
agentao plugin list --json
```

这会加载插件并输出诊断，适合 CI 或发布前检查。REPL 内的 `/plugins` 是同一类诊断的交互版。

---

::: tip 真相源头
顶层参数 parser 在 [`agentao/cli/entrypoints.py:_build_parser`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/entrypoints.py)。非交互 print 模式在同文件的 `run_print_mode`。Skill / plugin 子命令在 [`agentao/cli/subcommands.py`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/subcommands.py)。
:::
