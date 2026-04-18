# Agentao Developer Guide — Site Source

Bilingual VitePress site for third-party developers embedding Agentao's harness.

## Local Development

```bash
cd developer-guide
npm install
npm run docs:dev     # start dev server at http://localhost:5173
npm run docs:build   # production build → .vitepress/dist
npm run docs:preview # preview the built site
```

## Structure

```
developer-guide/
├── .vitepress/config.mts   # i18n + sidebar + nav
├── index.md                # language picker (root /)
├── zh/                     # 简体中文 (/zh/)
│   ├── index.md
│   └── part-1/             # Part 1: Introduction
└── en/                     # English (/en/)
    ├── index.md
    └── part-1/
```

## Editing Conventions

- **Keep ZH and EN parallel**: every page that exists in one locale should exist in the other with the same filename.
- **Code examples must be runnable**: prefer copy-paste-friendly blocks; verify against `agentao/` source before merging.
- **File-based routing**: `/zh/part-1/hello.md` → `/zh/part-1/hello.html`.
- **Sidebar**: update `.vitepress/config.mts` when adding new pages.
