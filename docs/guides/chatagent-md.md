# AGENTAO.md Auto-Loading Feature

This guide moved into the developer guide: **[5.6 · System Prompt, §1 `AGENTAO.md`](https://agentao.cn/en/part-5/6-system-prompt)** ([中文](https://agentao.cn/zh/part-5/6-system-prompt)). The headings below are kept so that old links still land on the right section.

## Overview

Moved to [English](https://agentao.cn/en/part-5/6-system-prompt#_1-agentao-md-%E2%80%94-project-instructions) · [中文](https://agentao.cn/zh/part-5/6-system-prompt#_1-agentao-md-%E2%80%94-%E9%A1%B9%E7%9B%AE%E7%BA%A7%E6%8C%87%E4%BB%A4)

## How It Works

Moved to [English](https://agentao.cn/en/part-5/6-system-prompt#_1-agentao-md-%E2%80%94-project-instructions) · [中文](https://agentao.cn/zh/part-5/6-system-prompt#_1-agentao-md-%E2%80%94-%E9%A1%B9%E7%9B%AE%E7%BA%A7%E6%8C%87%E4%BB%A4)

## File Location

Moved to [English](https://agentao.cn/en/part-5/6-system-prompt#how-the-file-is-found-and-placed) · [中文](https://agentao.cn/zh/part-5/6-system-prompt#%E6%96%87%E4%BB%B6%E6%80%8E%E4%B9%88%E6%89%BE%E3%80%81%E6%94%BE%E5%9C%A8%E5%93%AA)

### `AGENTS.md` fallback

Moved to [English](https://agentao.cn/en/part-5/6-system-prompt#how-the-file-is-found-and-placed) · [中文](https://agentao.cn/zh/part-5/6-system-prompt#%E6%96%87%E4%BB%B6%E6%80%8E%E4%B9%88%E6%89%BE%E3%80%81%E6%94%BE%E5%9C%A8%E5%93%AA)

## What to Include

Moved to [English](https://agentao.cn/en/part-5/6-system-prompt#_1-agentao-md-%E2%80%94-project-instructions) · [中文](https://agentao.cn/zh/part-5/6-system-prompt#_1-agentao-md-%E2%80%94-%E9%A1%B9%E7%9B%AE%E7%BA%A7%E6%8C%87%E4%BB%A4)

## Example AGENTAO.md Structure

Moved to [English](https://agentao.cn/en/part-5/6-system-prompt#_1-agentao-md-%E2%80%94-project-instructions) · [中文](https://agentao.cn/zh/part-5/6-system-prompt#_1-agentao-md-%E2%80%94-%E9%A1%B9%E7%9B%AE%E7%BA%A7%E6%8C%87%E4%BB%A4)

## Benefits

Removed: a list of benefits with no facts beyond §1.

## Implementation Details

Removed: the code moved to `agentao/prompts/helpers.py` and `builder.py`; source links are in §1.

### Code Changes

Removed: see the line above.

### System Prompt Structure

Moved to [English](https://agentao.cn/en/part-5/6-system-prompt#how-the-file-is-found-and-placed) · [中文](https://agentao.cn/zh/part-5/6-system-prompt#%E6%96%87%E4%BB%B6%E6%80%8E%E4%B9%88%E6%89%BE%E3%80%81%E6%94%BE%E5%9C%A8%E5%93%AA)

## Testing

Removed: the test is `tests/test_agentao_md.py`, linked from §1.

## Usage Examples

Moved to [English](https://agentao.cn/en/part-5/6-system-prompt#_1-agentao-md-%E2%80%94-project-instructions) · [中文](https://agentao.cn/zh/part-5/6-system-prompt#_1-agentao-md-%E2%80%94-%E9%A1%B9%E7%9B%AE%E7%BA%A7%E6%8C%87%E4%BB%A4)

### Basic Usage

Moved to [English](https://agentao.cn/en/part-5/6-system-prompt#_1-agentao-md-%E2%80%94-project-instructions) · [中文](https://agentao.cn/zh/part-5/6-system-prompt#_1-agentao-md-%E2%80%94-%E9%A1%B9%E7%9B%AE%E7%BA%A7%E6%8C%87%E4%BB%A4)

### Multiple Projects

Moved to [English](https://agentao.cn/en/part-5/6-system-prompt#different-agentao-md-per-business-line) · [中文](https://agentao.cn/zh/part-5/6-system-prompt#%E4%B8%BA%E4%B8%8D%E5%90%8C%E4%B8%9A%E5%8A%A1%E9%85%8D%E7%BD%AE%E4%B8%8D%E5%90%8C-agentao-md)

## Best Practices

Moved to [English](https://agentao.cn/en/part-5/6-system-prompt#_1-agentao-md-%E2%80%94-project-instructions) · [中文](https://agentao.cn/zh/part-5/6-system-prompt#_1-agentao-md-%E2%80%94-%E9%A1%B9%E7%9B%AE%E7%BA%A7%E6%8C%87%E4%BB%A4)

## Logging

Moved to [English](https://agentao.cn/en/part-5/6-system-prompt#when-the-instructions-do-not-take-effect) · [中文](https://agentao.cn/zh/part-5/6-system-prompt#%E6%8C%87%E4%BB%A4%E6%B2%A1%E6%9C%89%E7%94%9F%E6%95%88%E6%97%B6)

## Compatibility

Removed: it only said the file is optional, which §1 states.

## Comparison with CLAUDE.md

Removed: Agentao does not read `CLAUDE.md`.

## Future Enhancements

Removed: no planned work is tracked here.

## Troubleshooting

Moved to [English](https://agentao.cn/en/part-5/6-system-prompt#when-the-instructions-do-not-take-effect) · [中文](https://agentao.cn/zh/part-5/6-system-prompt#%E6%8C%87%E4%BB%A4%E6%B2%A1%E6%9C%89%E7%94%9F%E6%95%88%E6%97%B6)

### Instructions Not Loading

Moved to [English](https://agentao.cn/en/part-5/6-system-prompt#when-the-instructions-do-not-take-effect) · [中文](https://agentao.cn/zh/part-5/6-system-prompt#%E6%8C%87%E4%BB%A4%E6%B2%A1%E6%9C%89%E7%94%9F%E6%95%88%E6%97%B6)

### File Found But Not Applied

Moved to [English](https://agentao.cn/en/part-5/6-system-prompt#when-the-instructions-do-not-take-effect) · [中文](https://agentao.cn/zh/part-5/6-system-prompt#%E6%8C%87%E4%BB%A4%E6%B2%A1%E6%9C%89%E7%94%9F%E6%95%88%E6%97%B6)

### Debugging

Moved to [English](https://agentao.cn/en/part-5/6-system-prompt#inspect-the-system-prompt) · [中文](https://agentao.cn/zh/part-5/6-system-prompt#%E9%AA%8C%E8%AF%81%E7%B3%BB%E7%BB%9F%E6%8F%90%E7%A4%BA)

## Contributing

Removed: contribution notes are in the repository README.

## Related Files

Removed: source links are in §1.
