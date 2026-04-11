# 02 Process Manager And Runtime State

Parent doc: [ACP Client And Project-Local Servers](../../ACP_CLIENT_PROJECT_SERVERS.md)

Issue index: [ACP Client Project-Local Servers Issues](README.md)

## Goal

建立每个 ACP server 的本地进程管理层和 runtime 状态机。

## Scope

- 子进程启动
- 子进程停止
- 子进程重启
- stderr 消费入口
- per-server runtime 状态模型
- `ACPManager` 基础 registry

## Deliverables

- `agentao/acp_client/process.py`
- `agentao/acp_client/manager.py`
- `agentao/acp_client/models.py` 中 runtime 状态类型
- 基础生命周期单测

## Dependencies

- 01

## Design Notes

- 每个 server 一个独立 `ACPProcessHandle`
- 推荐状态：
  - `configured`
  - `starting`
  - `initializing`
  - `ready`
  - `busy`
  - `stopping`
  - `stopped`
  - `failed`
- `start()` 仅负责拉起进程和建立 stdio 基础设施，不在此 issue 中完成 ACP 握手
- 需要保留 pid、最近错误、最近活动时间
- 应支持 CLI 退出时统一回收

## Tests

- 可启动测试用子进程
- 重复 start 不会创建重复进程
- stop 后状态变为 `stopped`
- 启动失败时状态变为 `failed`
- restart 会替换旧进程句柄

## Acceptance Criteria

1. 每个配置 server 都能有独立 runtime 实例
2. CLI 可查询稳定状态快照
3. 退出流程不遗留子进程

## Out Of Scope

- JSON-RPC request/response
- inbox
- slash commands 细节
