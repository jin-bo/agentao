# AGENTAO.md Auto-Loading Feature

## Overview

Agentao now automatically loads project-specific instructions from a `AGENTAO.md` file at startup, similar to how Claude Code reads `CLAUDE.md`. This allows you to define project conventions, workflows, and best practices that the agent will follow throughout the session.

## How It Works

1. **Automatic Detection**: When Agentao starts, it looks for `AGENTAO.md` in the current working directory
2. **Content Injection**: If found, the entire file content is injected into the system prompt
3. **Session-Wide Scope**: Instructions apply to all interactions in that session
4. **Graceful Fallback**: If the file doesn't exist, the agent works normally with default instructions

## File Location

```
your-project/
├── AGENTAO.md       # Place in the root of your project
├── .env
├── main.py
└── ...
```

## What to Include

The `AGENTAO.md` file can contain:

- **Code Style**: Naming conventions, formatting rules, type hint requirements
- **Project Structure**: Module organization, file locations, import patterns
- **Development Workflows**: How to add tools, skills, tests
- **Common Patterns**: Error handling, file operations, security best practices
- **Testing Approaches**: Test file conventions, testing tools
- **Documentation Standards**: Comment styles, README updates
- **Package Management**: Use of uv, dependency management
- **Common Commands**: Development, testing, debugging commands

## Example AGENTAO.md Structure

```markdown
# Project Name Instructions

## Code Style & Conventions
- Use type hints for all functions
- Max line length: 100 characters
- Tool names in snake_case

## Project Structure
[Explain directory organization]

## Development Workflows
### Adding a New Tool
1. Create tool class...
2. Register in agent.py...
3. Test...

## Common Patterns
[Error handling examples, etc.]

## Common Commands
```bash
uv sync                    # Install dependencies
uv run python main.py     # Run agent
```
```

## Benefits

1. **Consistency**: All sessions follow the same project conventions
2. **Reduced Repetition**: No need to explain project structure every time
3. **Onboarding**: New contributors understand the project faster
4. **Context Preservation**: Important patterns and workflows are always available
5. **Customization**: Each project can have its own instructions

## Implementation Details

### Code Changes

**File**: `agentao/agent.py`

1. Added `_load_project_instructions()` method to read `AGENTAO.md`
2. Modified `__init__()` to load instructions at startup
3. Updated `_build_system_prompt()` to include instructions in the system prompt

### System Prompt Structure

When `AGENTAO.md` exists:
```
=== Project Instructions ===
[Full content of AGENTAO.md]

=== Agent Instructions ===
[Default Agentao instructions]

=== Available Skills ===
[List of skills]
```

When `AGENTAO.md` doesn't exist:
```
[Default Agentao instructions]

=== Available Skills ===
[List of skills]
```

## Testing

Created `test_agentao_md.py` with two test cases:

1. **test_agentao_md_loading**: Verifies that the file is loaded and included in system prompt
2. **test_agentao_md_missing**: Verifies graceful handling when file doesn't exist

Run tests:
```bash
uv run python test_agentao_md.py
```

## Usage Examples

### Basic Usage

1. Create `AGENTAO.md` in your project root
2. Add your project-specific instructions
3. Start Agentao
4. The agent will automatically follow your instructions

### Multiple Projects

Different projects can have different `AGENTAO.md` files:

```
project-a/
├── AGENTAO.md    # Instructions for project A
└── ...

project-b/
├── AGENTAO.md    # Different instructions for project B
└── ...
```

The agent loads the instructions from the current working directory.

## Best Practices

1. **Keep It Focused**: Include essential project information only
2. **Update Regularly**: Keep instructions current as project evolves
3. **Be Specific**: Provide concrete examples and patterns
4. **Organize Clearly**: Use headers and sections for easy reference
5. **Include Examples**: Show code examples for common patterns

## Logging

When `AGENTAO.md` is loaded, you'll see a log entry:
```
INFO: Loaded project instructions from /path/to/AGENTAO.md
```

Check `agentao.log` to verify the file was loaded successfully.

## Compatibility

- **No Breaking Changes**: Existing setups without `AGENTAO.md` continue to work
- **Optional Feature**: File is completely optional
- **No Configuration Needed**: Works automatically when file exists

## Comparison with CLAUDE.md

| Feature | CLAUDE.md | AGENTAO.md |
|---------|-----------|--------------|
| Purpose | General instructions for any codebase | Project-specific Agentao instructions |
| Read by | Claude Code (external tool) | Agentao itself |
| Scope | Affects all Claude Code interactions | Affects Agentao sessions only |
| Use case | General coding conventions | Agentao-specific workflows |

Both files can coexist:
- `CLAUDE.md` - For general project instructions (read by Claude Code)
- `AGENTAO.md` - For Agentao-specific workflows and patterns

## Future Enhancements

Potential improvements:
- Support for `.agentao/` directory with multiple instruction files
- Template system for common project types
- Per-user override files (`.agentao.local.md`)
- Validation and linting for instruction files
- Web UI for editing instructions

## Troubleshooting

### Instructions Not Loading

1. **Check file location**: Must be in current working directory
2. **Check file name**: Must be exactly `AGENTAO.md` (case-sensitive)
3. **Check logs**: Look for "Loaded project instructions" in `agentao.log`
4. **Check permissions**: Ensure file is readable

### File Found But Not Applied

1. **Verify system prompt**: Check that instructions appear in system prompt
2. **Check encoding**: File should be UTF-8 encoded
3. **Check content**: Ensure file isn't empty

### Debugging

View the system prompt to verify instructions are loaded:
```python
from pathlib import Path
from agentao.agent import Agentao
agent = Agentao(working_directory=Path.cwd())
print(agent._build_system_prompt())
```

## Contributing

If you have suggestions for improving this feature:
1. Test the change with `test_agentao_md.py`
2. Update documentation
3. Submit a pull request

## Related Files

- `AGENTAO.md` - Example project instructions for this codebase
- `test_agentao_md.py` - Test suite for this feature
- `agentao/agent.py` - Implementation
- `README.md` - Updated with feature documentation

---

**Feature Status**: ✅ Implemented and Tested
**Version**: Added in 0.1.0
**Last Updated**: 2026-02-11
