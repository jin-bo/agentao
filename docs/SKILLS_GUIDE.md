# Skills System Guide

## Overview

Agentao dynamically loads skills from the `skills` directory. Each skill is defined in its own subdirectory with a `SKILL.md` file.

## How Skills Are Loaded

1. **Skills Directory**: `<project-root>/skills/`
2. **Scan Subdirectories**: Each subdirectory represents one skill
3. **Read SKILL.md**: Each subdirectory must contain a `SKILL.md` file
4. **Parse Metadata**: Extract name and description from YAML frontmatter
5. **Make Available**: Skills are automatically available in the CLI

## Current Skills

✅ **17 skills loaded successfully:**

- algorithmic-art
- brand-guidelines
- canvas-design
- doc-coauthoring
- docx
- frontend-design
- internal-comms
- mcp-builder
- pdf
- pptx
- research-wbs-review
- skill-creator
- slack-gif-creator
- theme-factory
- web-artifacts-builder
- webapp-testing
- xlsx

## SKILL.md Format

Each `SKILL.md` file should follow this structure:

```markdown
---
name: skill-name
description: Brief description of what this skill does and when to use it
license: Optional license information
---

# Skill Title

Main documentation content goes here...

## Sections

- Overview
- Usage examples
- API reference
- etc.
```

### Required Fields

- **name**: Unique identifier for the skill (lowercase with hyphens)
- **description**: Detailed description of when to trigger and use this skill

### Optional Fields

- **license**: License information
- Any other metadata you need

## Adding a New Skill

### Method 1: Create from Scratch

1. Create a new directory in `skills/`:
   ```bash
   mkdir skills/my-new-skill
   ```

2. Create `SKILL.md` file:
   ```bash
   touch skills/my-new-skill/SKILL.md
   ```

3. Add content following the format above:
   ```markdown
   ---
   name: my-new-skill
   description: Use this skill when...
   ---

   # My New Skill

   ## Overview
   This skill provides...
   ```

4. Reload (restart Agentao or use reload feature)

### Method 2: Copy from Template

1. Copy an existing skill directory:
   ```bash
   cp -r skills/pdf skills/my-new-skill
   ```

2. Edit `SKILL.md` to match your new skill

3. Update the name and description in the frontmatter

## Using Skills in Agentao

### List Available Skills

```
You: /skills
```

This shows all loaded skills with their descriptions.

### Activate a Skill

```
You: Activate the pdf skill to help me work with PDFs
```

The agent will use the `activate_skill` tool automatically.

Or explicitly:

```
You: /skill pdf "I need to merge multiple PDF files"
```

### Check Active Skills

```
You: /status
```

Shows conversation status including active skills.

## Skill Structure Best Practices

### File Organization

```
skills/
  my-skill/
    ├── SKILL.md          # Main documentation (required)
    ├── examples/         # Usage examples (optional)
    ├── templates/        # Templates or samples (optional)
    └── scripts/          # Helper scripts (optional)
```

### SKILL.md Content

1. **Clear Trigger Description**: Explain when to use this skill
2. **Concrete Examples**: Provide real-world usage examples
3. **API Reference**: Document functions, commands, or tools
4. **Best Practices**: Share tips and common patterns
5. **Troubleshooting**: Address common issues

### Example Structure

```markdown
---
name: my-skill
description: Detailed trigger description explaining when to activate this skill...
---

# Skill Title

## Overview
Brief introduction to what this skill does.

## Quick Start
```
# Quick example
command or code here
```

## Common Tasks

### Task 1
Step-by-step guide...

### Task 2
Another common use case...

## API Reference
Detailed reference...

## Best Practices
- Tip 1
- Tip 2

## Examples
Real-world examples...

## Troubleshooting
Common issues and solutions...
```

## Programmatic Access

### In Python Code

```python
from agentao.skills import SkillManager

# Initialize with default location
manager = SkillManager()

# Or specify custom location
manager = SkillManager(skills_dir="/path/to/skills")

# List skills
skills = manager.list_available_skills()

# Get skill info
info = manager.get_skill_info("pdf")
print(info['title'])
print(info['description'])
print(info['path'])

# Read full content
content = manager.get_skill_content("pdf")

# Activate skill
result = manager.activate_skill("pdf", "Merge PDF files")

# Reload skills (after adding new ones)
manager.reload_skills()
```

## Skill Discovery

Skills are discovered by:

1. Scanning all directories in the skills folder
2. Looking for `SKILL.md` files
3. Parsing YAML frontmatter
4. Extracting metadata

**Note**: Hidden directories (starting with `.`) are ignored.

## Testing Skills

Test your skills with the provided test script:

```bash
uv run python test_skills.py
```

This will:
- List all loaded skills
- Show metadata for each skill
- Test skill activation
- Verify content retrieval

## Troubleshooting

### Skill Not Loading

1. **Check directory structure**: Ensure `SKILL.md` exists
2. **Verify frontmatter**: Check YAML syntax (name and description)
3. **Check file encoding**: Use UTF-8 encoding
4. **Restart Agentao**: Skills are loaded at startup

### Skill Not Activating

1. **Check name**: Use exact name from `skills` command
2. **Case sensitivity**: Skill names are case-sensitive
3. **Spelling**: Verify correct spelling

### Invalid Frontmatter

If frontmatter is invalid:
- Ensure it starts and ends with `---`
- Check YAML syntax (key: value)
- Remove quotes from strings unless needed
- Use proper indentation

## Advanced Features

### Multiple Skill Versions

You can have different versions:

```
skills/
  pdf-v1/
    SKILL.md
  pdf-v2/
    SKILL.md
```

Name them differently in frontmatter.

### Skill Dependencies

Reference other skills in your documentation:

```markdown
## Related Skills

This skill works well with:
- [xlsx](#) for data processing
- [docx](#) for reports
```

### Dynamic Content

Skills can include:
- Code examples with syntax highlighting
- Links to external resources
- Images and diagrams (in subdirectories)
- Sample files and templates

## CLI Commands

All commands start with `/`:

| Command | Description |
|---------|-------------|
| `/skills` | List all available and active skills |
| `/status` | Show conversation and skill status |
| `/skill <name> "<task>"` | Activate a skill explicitly |
| `/help` | Show general help including skills info |
| `/memory` | Show saved memories |
| `/clear` | Clear conversation history |
| `/exit` or `/quit` | Exit the program |

## API Integration

Skills integrate with the Agentao through:

1. **activate_skill tool**: LLM can call this tool
2. **Skills context**: Active skills are added to system prompt
3. **Documentation access**: LLM can read full SKILL.md content

## Best Practices for Skill Development

1. **Clear triggers**: Make it obvious when to use the skill
2. **Comprehensive docs**: Include examples and references
3. **Test thoroughly**: Use test_skills.py before deployment
4. **Version control**: Track changes to skills
5. **Keep updated**: Maintain docs as skills evolve

## Examples

See existing skills for reference:
- `pdf/` - Comprehensive PDF processing
- `xlsx/` - Excel and spreadsheet operations
- `docx/` - Word document handling
- `pptx/` - PowerPoint presentations

---

**Last Updated**: 2026-02-09
**Skills Count**: 17
**Status**: ✅ Production Ready
