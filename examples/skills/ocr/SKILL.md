---
name: ocr
description: OCR image files using the Qwen VL model via the bundled scripts/ocr.py (lives in this skill's directory, NOT your cwd). Use when the user asks to extract text from an image, perform OCR on a photo or screenshot, or recognize characters in an image file (.jpg, .jpeg, .png, .gif, .webp). Requires QWEN_API_KEY and QWEN_BASE_URL (env vars, or a .env in the skill directory).
---

# OCR Skill

Extract text from images using `scripts/ocr.py` (Qwen VL OCR model).

**Path note**: this skill's files live in the directory containing this
SKILL.md — written as `<skill-dir>` below. It is shown on the
`Skill directory:` line when the skill is activated (the activation message
also lists the script's absolute path). The script is at
`<skill-dir>/scripts/ocr.py`, NOT in your current working directory.

## Prerequisites

`QWEN_API_KEY` and `QWEN_BASE_URL`, read in this order (first found wins):

1. process environment variables
2. `.env` in your current working directory
3. `<skill-dir>/.env` (recommended place to keep them)
4. `~/.env` (user-wide fallback)

```
QWEN_API_KEY=your_key
QWEN_BASE_URL=https://your-base-url
```

No dependency setup needed: the script carries inline metadata (PEP 723), so
`uv run` resolves `openai` / `python-dotenv` automatically in any directory.
Do NOT run `uv add` — your cwd is usually not a Python project.

## Usage

```bash
uv run "<skill-dir>/scripts/ocr.py" <image_file>
```

Substitute `<skill-dir>` with the absolute skill directory before running —
it is NOT a shell variable. Output is printed to stdout. If credentials are missing, the script exits
with an error telling you where to put them.

## Workflow

1. Confirm the image file path with the user if not provided
2. Run the script with the image path
3. Present the extracted text to the user
4. If the user wants to save the output, write it to a `.txt` file

## Notes

- Blurry or overexposed single characters are replaced with `?`
- Supported formats: `.jpg` `.jpeg` `.png` `.gif` `.webp`
