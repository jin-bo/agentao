# 09 Documentation And Operator Notes

Parent doc: [ACP Client And Project-Local Servers](../../ACP_CLIENT_PROJECT_SERVERS.md)

Issue index: [ACP Client Project-Local Servers Issues](README.md)

## Goal

补齐用户文档、配置示例和运行边界说明，确保该功能可被正确使用和维护。

## Scope

- README 或 docs 中的用户说明
- `.agentao/acp.json` 示例
- `/acp` 命令说明
- 边界与安全说明
- 故障排查说明

## Deliverables

- `README.md` 或 `docs/` 更新
- 配置示例文档
- 故障排查文档

## Dependencies

- 06
- 07
- 08

## Design Notes

- 需要明确说明：
  - 仅项目级配置
  - 默认不自动向 ACP server 发消息
  - ACP 返回默认不进入 Agentao 当前上下文
- 应提供至少一个最小配置样例
- 应记录 `/acp logs`、`/acp status` 的调试建议

## Tests

- 无强制代码测试
- 文档示例需与实际命令和配置一致

## Acceptance Criteria

1. 新用户能按文档完成最小配置和联通
2. 运行边界和非目标明确
3. 文档结构不再把细节堆回母稿

## Out Of Scope

- 自动化教程生成
- 视频或交互式 demo
