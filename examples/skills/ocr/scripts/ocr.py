import sys
import base64
import os
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

API_KEY = os.getenv("QWEN_API_KEY")
BASE_URL = os.getenv("QWEN_BASE_URL")
MODEL = "qwen-vl-ocr-latest"
PROMPT = "准确无误的提取图像中的文字信息、不要遗漏和捏造虚假信息，模糊或者强光遮挡的单个文字可以用英文问号?代替。以Markdown格式输出，表格用Markdown表格语法表示，标题用#标注，普通文本直接输出。"


def encode_image(image_path: str) -> tuple[str, str]:
    suffix = Path(image_path).suffix.lower()
    mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}
    mime_type = mime_map.get(suffix, "image/jpeg")
    with open(image_path, "rb") as f:
        data = base64.b64encode(f.read()).decode("utf-8")
    return data, mime_type


def ocr(image_path: str) -> str:
    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    image_data, mime_type = encode_image(image_path)
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_data}"}},
                    {"type": "text", "text": PROMPT},
                ],
            }
        ],
    )
    return response.choices[0].message.content


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <image_file>", file=sys.stderr)
        sys.exit(1)

    image_path = sys.argv[1]
    if not Path(image_path).exists():
        print(f"Error: file not found: {image_path}", file=sys.stderr)
        sys.exit(1)

    result = ocr(image_path)
    print(result)


if __name__ == "__main__":
    main()
