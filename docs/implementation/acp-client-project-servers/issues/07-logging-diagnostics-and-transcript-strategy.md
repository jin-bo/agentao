# 07 Logging Diagnostics And Transcript Strategy

Parent doc: [ACP Client And Project-Local Servers](../../ACP_CLIENT_PROJECT_SERVERS.md)

Issue index: [ACP Client Project-Local Servers Issues](README.md)

## Goal

提供最小可调试性，包括 stderr 观察、最近错误摘要和可选 transcript 策略。

## Scope

- stderr ring buffer
- 最近错误摘要
- 最近活动时间
- `/acp logs <name>`
- transcript 持久化策略定义

## Deliverables

- `agentao/acp_client/process.py`
- `agentao/acp_client/manager.py`
- `agentao/cli.py`
- 诊断相关测试

## Dependencies

- 02
- 03
- 06

## Design Notes

- v1 至少要有 stderr ring buffer
- transcript 持久化可定义目录和格式，但不一定要求首版完全实现
- 建议目录：
  - `.agentao/acp/`
- `status` 快照应能读取最近错误摘要

## Tests

- stderr 被正确采集
- `/acp logs` 可读取尾部内容
- 最近错误在状态快照中可见
- 大量 stderr 不会无限增长

## Acceptance Criteria

1. server 启动失败或运行失败时可定位
2. 用户无需附加调试器即可看到基本问题
3. 诊断信息不会污染正常消息通道

## Out Of Scope

- 完整 observability dashboard
- 远程日志聚合
