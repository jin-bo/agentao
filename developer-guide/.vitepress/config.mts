import { defineConfig } from 'vitepress'
import { withMermaid } from 'vitepress-plugin-mermaid'

// Bilingual (ZH/EN) developer guide for Agentao harness embedding.
// Route layout:
//   /            → language picker (index.md)
//   /zh/...      → 简体中文
//   /en/...      → English

const zhSidebar = [
  {
    text: 'Recipes · 高频任务直链',
    collapsed: false,
    items: [
      { text: '一键到答案', link: '/zh/recipes/' },
    ],
  },
  {
    text: '第一部分 · 起步与心智模型',
    collapsed: false,
    items: [
      { text: '1.1 Agentao 是什么', link: '/zh/part-1/1-what-is-agentao' },
      { text: '1.2 核心概念', link: '/zh/part-1/2-core-concepts' },
      { text: '1.3 两种集成模式', link: '/zh/part-1/3-integration-modes' },
      { text: '1.4 5 分钟 Hello Agentao', link: '/zh/part-1/4-hello-agentao' },
      { text: '1.5 运行环境要求', link: '/zh/part-1/5-requirements' },
    ],
  },
  {
    text: '第二部分 · Python 进程内嵌入',
    collapsed: false,
    items: [
      { text: '概述', link: '/zh/part-2/' },
      { text: '2.1 安装与包导入', link: '/zh/part-2/1-install-import' },
      { text: '2.2 构造器完整参数表', link: '/zh/part-2/2-constructor-reference' },
      { text: '2.3 生命周期管理', link: '/zh/part-2/3-lifecycle' },
      { text: '2.4 会话状态', link: '/zh/part-2/4-session-state' },
      { text: '2.5 运行时切换 LLM', link: '/zh/part-2/5-runtime-llm-switch' },
      { text: '2.6 取消与超时', link: '/zh/part-2/6-cancellation-timeouts' },
      { text: '2.7 FastAPI / Flask 嵌入', link: '/zh/part-2/7-fastapi-flask-embed' },
    ],
  },
  {
    text: '第三部分 · ACP 协议嵌入',
    collapsed: false,
    items: [
      { text: '概述', link: '/zh/part-3/' },
      { text: '3.1 ACP 协议速览', link: '/zh/part-3/1-acp-tour' },
      { text: '3.2 Agentao 作为 ACP Server', link: '/zh/part-3/2-agentao-as-server' },
      { text: '3.3 宿主作为 ACP 客户端架构', link: '/zh/part-3/3-host-client-architecture' },
      { text: '3.4 反向调用外部 ACP Agent', link: '/zh/part-3/4-reverse-acp-call' },
      { text: '3.5 Zed / IDE 集成', link: '/zh/part-3/5-zed-ide-integration' },
    ],
  },
  {
    text: '第四部分 · 事件层与 UI 集成',
    collapsed: true,
    items: [
      { text: '概述', link: '/zh/part-4/' },
      { text: '4.1 Transport Protocol', link: '/zh/part-4/1-transport-protocol' },
      { text: '4.2 AgentEvent 事件清单', link: '/zh/part-4/2-agent-events' },
      { text: '4.3 SdkTransport 快速桥接', link: '/zh/part-4/3-sdk-transport' },
      { text: '4.4 构建流式 UI', link: '/zh/part-4/4-streaming-ui' },
      { text: '4.5 工具确认 UI', link: '/zh/part-4/5-tool-confirmation-ui' },
      { text: '4.6 最大迭代数兜底', link: '/zh/part-4/6-max-iterations' },
      { text: '4.7 嵌入式 Host 合约', link: '/zh/part-4/7-host-contract' },
    ],
  },
  {
    text: '第五部分 · 扩展点',
    collapsed: true,
    items: [
      { text: '概述', link: '/zh/part-5/' },
      { text: '5.1 自定义工具', link: '/zh/part-5/1-custom-tools' },
      { text: '5.2 技能（Skills）', link: '/zh/part-5/2-skills' },
      { text: '5.3 MCP 服务器接入', link: '/zh/part-5/3-mcp' },
      { text: '5.4 权限引擎', link: '/zh/part-5/4-permissions' },
      { text: '5.5 记忆系统', link: '/zh/part-5/5-memory' },
      { text: '5.6 系统提示定制', link: '/zh/part-5/6-system-prompt' },
    ],
  },
  {
    text: '第六部分 · 安全与生产化部署',
    collapsed: true,
    items: [
      { text: '概述', link: '/zh/part-6/' },
      { text: '6.1 多层防御模型', link: '/zh/part-6/1-defense-model' },
      { text: '6.2 Shell 沙箱', link: '/zh/part-6/2-shell-sandbox' },
      { text: '6.3 网络与 SSRF', link: '/zh/part-6/3-network-ssrf' },
      { text: '6.4 多租户隔离', link: '/zh/part-6/4-multi-tenant-fs' },
      { text: '6.5 密钥与 Prompt 注入', link: '/zh/part-6/5-secrets-injection' },
      { text: '6.6 可观测性与审计', link: '/zh/part-6/6-observability' },
      { text: '6.7 资源治理与并发', link: '/zh/part-6/7-resource-concurrency' },
      { text: '6.8 容器化与部署', link: '/zh/part-6/8-deployment' },
    ],
  },
  {
    text: '第七部分 · 典型集成蓝图',
    collapsed: true,
    items: [
      { text: '概述', link: '/zh/part-7/' },
      { text: '7.1 SaaS 内置助手', link: '/zh/part-7/1-saas-assistant' },
      { text: '7.2 IDE 插件', link: '/zh/part-7/2-ide-plugin' },
      { text: '7.3 工单自动化', link: '/zh/part-7/3-ticket-automation' },
      { text: '7.4 数据分析工作台', link: '/zh/part-7/4-data-workbench' },
      { text: '7.5 批处理与定时任务', link: '/zh/part-7/5-batch-scheduler' },
    ],
  },
  {
    text: '附录',
    collapsed: false,
    items: [
      { text: '概述', link: '/zh/appendix/' },
      { text: 'A · API 参考', link: '/zh/appendix/a-api-reference' },
      { text: 'B · 配置键索引', link: '/zh/appendix/b-config-keys' },
      { text: 'C · ACP 消息字段', link: '/zh/appendix/c-acp-messages' },
      { text: 'D · 错误码参考', link: '/zh/appendix/d-error-codes' },
      { text: 'E · 框架迁移', link: '/zh/appendix/e-migration' },
      { text: 'F · FAQ 与排错', link: '/zh/appendix/f-faq' },
      { text: 'G · 双语术语表', link: '/zh/appendix/g-glossary' },
    ],
  },
]

const enSidebar = [
  {
    text: 'Recipes · Common tasks',
    collapsed: false,
    items: [
      { text: '1-click to the answer', link: '/en/recipes/' },
    ],
  },
  {
    text: 'Part 1 · Getting Started',
    collapsed: false,
    items: [
      { text: '1.1 What is Agentao', link: '/en/part-1/1-what-is-agentao' },
      { text: '1.2 Core Concepts', link: '/en/part-1/2-core-concepts' },
      { text: '1.3 Integration Modes', link: '/en/part-1/3-integration-modes' },
      { text: '1.4 Hello Agentao in 5 min', link: '/en/part-1/4-hello-agentao' },
      { text: '1.5 Requirements', link: '/en/part-1/5-requirements' },
    ],
  },
  {
    text: 'Part 2 · In-Process SDK',
    collapsed: false,
    items: [
      { text: 'Overview', link: '/en/part-2/' },
      { text: '2.1 Install & Import', link: '/en/part-2/1-install-import' },
      { text: '2.2 Constructor Reference', link: '/en/part-2/2-constructor-reference' },
      { text: '2.3 Lifecycle', link: '/en/part-2/3-lifecycle' },
      { text: '2.4 Session State', link: '/en/part-2/4-session-state' },
      { text: '2.5 Runtime LLM Switch', link: '/en/part-2/5-runtime-llm-switch' },
      { text: '2.6 Cancellation & Timeouts', link: '/en/part-2/6-cancellation-timeouts' },
      { text: '2.7 FastAPI / Flask Embedding', link: '/en/part-2/7-fastapi-flask-embed' },
    ],
  },
  {
    text: 'Part 3 · ACP Protocol',
    collapsed: false,
    items: [
      { text: 'Overview', link: '/en/part-3/' },
      { text: '3.1 ACP Protocol Tour', link: '/en/part-3/1-acp-tour' },
      { text: '3.2 Agentao as an ACP Server', link: '/en/part-3/2-agentao-as-server' },
      { text: '3.3 Host as ACP Client', link: '/en/part-3/3-host-client-architecture' },
      { text: '3.4 Reverse ACP Call', link: '/en/part-3/4-reverse-acp-call' },
      { text: '3.5 Zed / IDE Integration', link: '/en/part-3/5-zed-ide-integration' },
    ],
  },
  {
    text: 'Part 4 · Event Layer & UI',
    collapsed: true,
    items: [
      { text: 'Overview', link: '/en/part-4/' },
      { text: '4.1 Transport Protocol', link: '/en/part-4/1-transport-protocol' },
      { text: '4.2 AgentEvent Reference', link: '/en/part-4/2-agent-events' },
      { text: '4.3 SdkTransport Bridging', link: '/en/part-4/3-sdk-transport' },
      { text: '4.4 Streaming UI', link: '/en/part-4/4-streaming-ui' },
      { text: '4.5 Tool Confirmation UI', link: '/en/part-4/5-tool-confirmation-ui' },
      { text: '4.6 Max-Iterations Fallback', link: '/en/part-4/6-max-iterations' },
      { text: '4.7 Embedded Host Contract', link: '/en/part-4/7-host-contract' },
    ],
  },
  {
    text: 'Part 5 · Extensibility',
    collapsed: true,
    items: [
      { text: 'Overview', link: '/en/part-5/' },
      { text: '5.1 Custom Tools', link: '/en/part-5/1-custom-tools' },
      { text: '5.2 Skills', link: '/en/part-5/2-skills' },
      { text: '5.3 MCP Integration', link: '/en/part-5/3-mcp' },
      { text: '5.4 Permission Engine', link: '/en/part-5/4-permissions' },
      { text: '5.5 Memory System', link: '/en/part-5/5-memory' },
      { text: '5.6 System Prompt', link: '/en/part-5/6-system-prompt' },
    ],
  },
  {
    text: 'Part 6 · Security & Production',
    collapsed: true,
    items: [
      { text: 'Overview', link: '/en/part-6/' },
      { text: '6.1 Defense-in-Depth', link: '/en/part-6/1-defense-model' },
      { text: '6.2 Shell Sandbox', link: '/en/part-6/2-shell-sandbox' },
      { text: '6.3 Network & SSRF', link: '/en/part-6/3-network-ssrf' },
      { text: '6.4 Multi-Tenant & FS', link: '/en/part-6/4-multi-tenant-fs' },
      { text: '6.5 Secrets & Injection', link: '/en/part-6/5-secrets-injection' },
      { text: '6.6 Observability', link: '/en/part-6/6-observability' },
      { text: '6.7 Resource Governance', link: '/en/part-6/7-resource-concurrency' },
      { text: '6.8 Deployment', link: '/en/part-6/8-deployment' },
    ],
  },
  {
    text: 'Part 7 · Integration Blueprints',
    collapsed: true,
    items: [
      { text: 'Overview', link: '/en/part-7/' },
      { text: '7.1 SaaS Assistant', link: '/en/part-7/1-saas-assistant' },
      { text: '7.2 IDE Plugin', link: '/en/part-7/2-ide-plugin' },
      { text: '7.3 Ticket Automation', link: '/en/part-7/3-ticket-automation' },
      { text: '7.4 Data Workbench', link: '/en/part-7/4-data-workbench' },
      { text: '7.5 Batch & Scheduler', link: '/en/part-7/5-batch-scheduler' },
    ],
  },
  {
    text: 'Appendix',
    collapsed: false,
    items: [
      { text: 'Overview', link: '/en/appendix/' },
      { text: 'A · API reference', link: '/en/appendix/a-api-reference' },
      { text: 'B · Config keys', link: '/en/appendix/b-config-keys' },
      { text: 'C · ACP messages', link: '/en/appendix/c-acp-messages' },
      { text: 'D · Error codes', link: '/en/appendix/d-error-codes' },
      { text: 'E · Migration', link: '/en/appendix/e-migration' },
      { text: 'F · FAQ', link: '/en/appendix/f-faq' },
      { text: 'G · Glossary', link: '/en/appendix/g-glossary' },
    ],
  },
]

export default withMermaid(defineConfig({
  title: 'Agentao Developer Guide',
  description: 'Embed the Agentao harness into your application',
  // Served from custom domain agentao.cn at the root. Override with DOCS_BASE only
  // if deploying to a subpath (e.g. a GitHub Pages project site under /agentao/).
  base: process.env.DOCS_BASE || '/',
  cleanUrls: true,
  lastUpdated: true,
  ignoreDeadLinks: true,
  srcExclude: ['README.md'],

  head: [
    ['meta', { name: 'theme-color', content: '#3c8772' }],
  ],

  themeConfig: {
    search: { provider: 'local' },
    socialLinks: [
      { icon: 'github', link: 'https://github.com/jin-bo/agentao' },
    ],
  },

  locales: {
    root: {
      label: 'Language · 语言',
      lang: 'en',
    },
    zh: {
      label: '简体中文',
      lang: 'zh-CN',
      link: '/zh/',
      title: 'Agentao 应用开发者指南',
      description: '把 Agentao Harness 嵌入你的应用',
      themeConfig: {
        nav: [
          { text: '首页', link: '/zh/' },
          { text: '第一部分', link: '/zh/part-1/1-what-is-agentao' },
          { text: 'Recipes', link: '/zh/recipes/' },
          {
            text: '按角色阅读',
            items: [
              { text: '后端工程师 · Python 嵌入', link: '/zh/part-2/' },
              { text: 'IDE 插件作者 · ACP', link: '/zh/part-3/' },
              { text: 'DevOps · 生产部署', link: '/zh/part-6/' },
              { text: '安全审计 · 沙箱与权限', link: '/zh/part-6/1-defense-model' },
            ],
          },
          { text: '附录', link: '/zh/appendix/' },
          { text: 'GitHub', link: 'https://github.com/jin-bo/agentao' },
        ],
        sidebar: { '/zh/': zhSidebar },
        outline: { label: '本页大纲', level: [2, 3] },
        docFooter: { prev: '上一节', next: '下一节' },
        lastUpdated: { text: '最后更新' },
        returnToTopLabel: '回到顶部',
        darkModeSwitchLabel: '主题',
        sidebarMenuLabel: '菜单',
        langMenuLabel: '语言',
      },
    },
    en: {
      label: 'English',
      lang: 'en-US',
      link: '/en/',
      title: 'Agentao Developer Guide',
      description: 'Embed the Agentao harness into your application',
      themeConfig: {
        nav: [
          { text: 'Home', link: '/en/' },
          { text: 'Part 1', link: '/en/part-1/1-what-is-agentao' },
          { text: 'Recipes', link: '/en/recipes/' },
          {
            text: 'By role',
            items: [
              { text: 'Backend engineer · Python embed', link: '/en/part-2/' },
              { text: 'IDE plugin author · ACP', link: '/en/part-3/' },
              { text: 'DevOps · Production', link: '/en/part-6/' },
              { text: 'Security review · sandbox & perms', link: '/en/part-6/1-defense-model' },
            ],
          },
          { text: 'Appendix', link: '/en/appendix/' },
          { text: 'GitHub', link: 'https://github.com/jin-bo/agentao' },
        ],
        sidebar: { '/en/': enSidebar },
        outline: { label: 'On this page', level: [2, 3] },
      },
    },
  },
}))
