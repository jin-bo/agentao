---
name: ocr
description: OCR image files using the Qwen VL model via scripts/ocr.py. Use when the user asks to extract text from an image, perform OCR on a photo or screenshot, or recognize characters in an image file (.jpg, .jpeg, .png, .gif, .webp). Requires QWEN_API_KEY and QWEN_BASE_URL in a .env file in the project directory.
---

# OCR Skill

Extract text from images using `scripts/ocr.py` (Qwen VL OCR model).

## Prerequisites

`.env` in the project directory:
```
QWEN_API_KEY=your_key
QWEN_BASE_URL=https://your-base-url
```

Dependencies (if not already installed):
```bash
uv add openai python-dotenv
```

## Usage

```bash
uv run scripts/ocr.py <image_file>
```

Output is printed to stdout.

## Workflow

1. Confirm the image file path with the user if not provided
2. Run the script with the image path
3. Present the extracted text to the user
4. If the user wants to save the output, write it to a `.txt` file

## Notes

- Blurry or overexposed single characters are replaced with `?`
- Supported formats: `.jpg` `.jpeg` `.png` `.gif` `.webp`
