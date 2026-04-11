# Agentao Documentation

This directory contains documentation for the Agentao project.

## Structure

This directory mixes stable user-facing guides with implementation notes and historical records. For release-facing documentation, prioritize the files listed under **Core Documentation** and the current release notes in `docs/releases/`.

### Core Documentation

User-facing guides and references:

- [LOGGING.md](LOGGING.md) - Complete logging system documentation
- [MODEL_SWITCHING.md](MODEL_SWITCHING.md) - Guide to switching between LLM models
- [SKILLS_GUIDE.md](SKILLS_GUIDE.md) - Skills system guide and how to create skills
- [QUICKSTART.md](QUICKSTART.md) - Quick start guide (2-minute setup)
- [QUICK_REFERENCE.md](QUICK_REFERENCE.md) - Quick reference for common tasks
- [DEMO.md](DEMO.md) - Interactive demo walkthrough

### `/features` - Feature Documentation
Detailed documentation for major features, including design decisions and implementation details:

- [AGENTAO_MD_FEATURE.md](features/AGENTAO_MD_FEATURE.md) - AGENTAO.md auto-loading for project-specific instructions
- [TOOL_CONFIRMATION_FEATURE.md](features/TOOL_CONFIRMATION_FEATURE.md) - User confirmation system for Shell & Web tools
- [DATE_CONTEXT_FEATURE.md](features/DATE_CONTEXT_FEATURE.md) - Current date/time injection in system prompt

### `/updates` - Historical Update Logs
Historical change notes captured during development. These are useful for archaeology, but they are not the canonical source for current behavior:

- [SKILLS_UPDATE.md](updates/SKILLS_UPDATE.md) - Skills system enhancements
- [LOGGING_UPDATE.md](updates/LOGGING_UPDATE.md) - Logging improvements
- [COMMANDS_UPDATE.md](updates/COMMANDS_UPDATE.md) - CLI commands updates
- [MODEL_COMMAND_UPDATE.md](updates/MODEL_COMMAND_UPDATE.md) - Model switching command
- [SKILLS_PROMPT_UPDATE.md](updates/SKILLS_PROMPT_UPDATE.md) - Skills prompt integration
- [MENU_CONFIRMATION_UPDATE.md](updates/MENU_CONFIRMATION_UPDATE.md) - Menu-based confirmation system

### `/implementation` - Internal Implementation Notes
Design drafts, implementation plans, and technical deep dives for contributors. Some files describe superseded designs and should not be treated as current user-facing documentation:

- [READCHAR_IMPLEMENTATION.md](implementation/READCHAR_IMPLEMENTATION.md) - Single-key input with readchar library
- [CLEAR_RESETS_CONFIRMATION.md](implementation/CLEAR_RESETS_CONFIRMATION.md) - /clear command confirmation reset
- [TOOL_CONFIRMATION.md](implementation/TOOL_CONFIRMATION.md) - Tool confirmation mechanism details

### `/dev-notes` - Archived Development Notes
Archived summaries from earlier development phases. These are historical records, not release-facing docs:

- [FIXES_SUMMARY.md](dev-notes/FIXES_SUMMARY.md) - Summary of fixes
- [MULTI_TURN_FIX.md](dev-notes/MULTI_TURN_FIX.md) - Multi-turn conversation fix details
- [SESSION_SUMMARY.md](dev-notes/SESSION_SUMMARY.md) - Session improvement summary
- [PROJECT_SUMMARY.md](dev-notes/PROJECT_SUMMARY.md) - Project summary
- [STRUCTURE.md](dev-notes/STRUCTURE.md) - Project structure notes

## Main Documentation

For general project information, see:
- [README.md](../README.md) - Project overview and quick start
- [CLAUDE.md](../CLAUDE.md) - Guidance for Claude Code when working with this codebase

## Release Guidance

For a release or external handoff, use this documentation surface first:

1. `../README.md`
2. `../README.zh.md`
3. `ACP.md`, `QUICKSTART.md`, `QUICK_REFERENCE.md`, `LOGGING.md`, `MODEL_SWITCHING.md`
4. `features/` guides that match the shipped feature
5. `releases/` notes for the exact version being published

Treat `updates/`, `implementation/`, and `dev-notes/` as internal context unless a specific file is intentionally published as reference material.

## Contributing

When adding new features or making significant changes:
1. Document the feature in `/features` if it's a major addition
2. Add update notes in `/updates` for specific changes
3. Update the main README.md with user-facing information
