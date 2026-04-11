# Plugin System MVP Issues

这组 issue 文件把 staged plan 进一步拆成可执行实施单元。

设计入口：

- `docs/implementation/plugin-system-mvp/README.md`

阶段文档：

- `PHASE_1_MANIFEST_AND_LOADER.md`
- `PHASE_2_SKILLS_AND_COMMANDS.md`
- `PHASE_3_AGENTS.md`
- `PHASE_4_MCP_INTEGRATION.md`
- `PHASE_5_USER_PROMPT_SUBMIT_AND_HOOK_CORE.md`
- `PHASE_6_SESSION_TOOL_HOOKS_AND_CLI.md`

## Issue Index

1. [01 Plugin Models And Diagnostics Types](./01-plugin-models-and-diagnostics-types.md)
2. [02 Plugin Manifest Parser](./02-plugin-manifest-parser.md)
3. [03 Manifest Path Safety Validation](./03-manifest-path-safety-validation.md)
4. [04 Plugin Discovery Disable Rules And Precedence](./04-plugin-discovery-disable-rules-and-precedence.md)
5. [05 LoadedPlugin Assembly And Diagnostics Snapshot](./05-loadedplugin-assembly-and-diagnostics-snapshot.md)
6. [06 Plugin Skills Registration Seam](./06-plugin-skills-registration-seam.md)
7. [07 Plugin Commands Mapping](./07-plugin-commands-mapping.md)
8. [08 Plugin Agents Registration](./08-plugin-agents-registration.md)
9. [09 Plugin MCP Loading And Merge](./09-plugin-mcp-loading-and-merge.md)
10. [10 Claude Hooks Parser And Runtime Validation](./10-claude-hooks-parser-and-runtime-validation.md)
11. [11 Tool Alias Resolver And Hook Payload Adapters](./11-tool-alias-resolver-and-hook-payload-adapters.md)
12. [12 UserPromptSubmit Command Hooks](./12-userpromptsubmit-command-hooks.md)
13. [13 UserPromptSubmit Prompt Hooks](./13-userpromptsubmit-prompt-hooks.md)
14. [14 Prepare User Turn And Attachment Normalization](./14-prepare-user-turn-and-attachment-normalization.md)
15. [15 Session And Tool Lifecycle Hooks](./15-session-and-tool-lifecycle-hooks.md)
16. [16 CLI Plugin Discovery And Diagnostics](./16-cli-plugin-discovery-and-diagnostics.md)
17. [17 Plugin Fixture Library](./17-plugin-fixture-library.md)
18. [18 Diagnostics Renderer And Compatibility Notes](./18-diagnostics-renderer-and-compatibility-notes.md)

## Recommended Order

1. 01
2. 02
3. 03
4. 04
5. 05
6. 17
7. 06
8. 07
9. 08
10. 09
11. 10
12. 11
13. 12
14. 13
15. 14
16. 15
17. 16
18. 18
