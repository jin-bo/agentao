#!/usr/bin/env python3
"""从 Markdown 文件批量生成 PPT 页面图像 (OpenRouter openai/gpt-5.4-image-2 版本)。

使用 openai SDK 通过 OpenRouter 调用 openai/gpt-5.4-image-2 模型。

Markdown 格式：
  ---
  name: my presentation
  resolution: "16:9"
  style: 整体风格描述（注入到每页提示词最前面）
  characters:
    - name: 角色名
      species: 物种
      appearance: 外貌
      outfit: 服装
  ---

  # 标题

  ## 页面一

  图像提示词内容

  ## 页面二

  图像提示词内容
"""

import os
import sys
import re
import base64
import time
import argparse
import logging
from pathlib import Path

# 强制行缓冲
if not sys.stdout.isatty():
    sys.stdout.reconfigure(line_buffering=True)

import yaml
from openai import OpenAI
from dotenv import load_dotenv

# Load environment variables from .env if present
load_dotenv()

MODEL_NAME = "openai/gpt-5.4-image-2"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# OpenRouter openai/gpt-5.4-image-2 supported aspect_ratio values
ASPECT_RATIOS = ("1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9")
DEFAULT_ASPECT_RATIO = "16:9"

# Supported image_size values per OpenRouter docs
IMAGE_SIZE_OPTIONS = ("1K", "2K", "4K")
DEFAULT_IMAGE_SIZE = "2K"

DEFAULT_IMAGES_DIR = Path("images")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def get_api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "未找到 OPENROUTER_API_KEY。请在 .env 文件中设置，或导出为环境变量。"
        )
    return key


def resolution_to_aspect_ratio(resolution: str) -> str:
    """Extract a supported aspect_ratio string (e.g. '16:9') from user input."""
    if not resolution:
        return DEFAULT_ASPECT_RATIO
    s = resolution.strip()
    for ar in ASPECT_RATIOS:
        if ar in s:
            return ar
    return DEFAULT_ASPECT_RATIO


def aspect_ratio_to_floats(ar: str) -> tuple[int, int]:
    a, _, b = ar.partition(":")
    return int(a), int(b)


_MIME_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}


def _center_crop_to_aspect(image_bytes: bytes, target_w: int, target_h: int, ext: str | None) -> tuple[bytes, str | None]:
    """If the image's aspect ratio differs from target by >2%, center-crop to target ratio.

    Returns (possibly-rewritten bytes, possibly-updated ext). If Pillow is missing or
    the ratios already match, returns the input unchanged.
    """
    try:
        from PIL import Image  # type: ignore
        import io
    except ImportError:
        logger.warning("⚠️ 未安装 Pillow，跳过比例裁剪。`uv add pillow` 可启用本地裁剪兜底。")
        return image_bytes, ext

    target_ratio = target_w / target_h
    img = Image.open(io.BytesIO(image_bytes))
    actual_w, actual_h = img.size
    actual_ratio = actual_w / actual_h

    if abs(actual_ratio - target_ratio) / target_ratio < 0.02:
        logger.info(f"   实际尺寸 {actual_w}×{actual_h}，比例已匹配目标，无需裁剪。")
        return image_bytes, ext

    logger.warning(
        f"   实际尺寸 {actual_w}×{actual_h} (ratio {actual_ratio:.3f}) 与目标 "
        f"{target_w}×{target_h} (ratio {target_ratio:.3f}) 不符，居中裁剪。"
    )

    if actual_ratio > target_ratio:
        # too wide — crop sides
        new_w = int(round(actual_h * target_ratio))
        new_h = actual_h
        left = (actual_w - new_w) // 2
        top = 0
    else:
        # too tall — crop top/bottom
        new_w = actual_w
        new_h = int(round(actual_w / target_ratio))
        left = 0
        top = (actual_h - new_h) // 2

    cropped = img.crop((left, top, left + new_w, top + new_h))

    out_fmt_pil = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "webp": "WEBP"}.get((ext or "png").lower(), "PNG")
    if out_fmt_pil == "JPEG" and cropped.mode in ("RGBA", "P"):
        cropped = cropped.convert("RGB")
    buf = io.BytesIO()
    cropped.save(buf, format=out_fmt_pil)
    new_ext = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}[out_fmt_pil]
    logger.info(f"   裁剪完成 → {new_w}×{new_h} ({new_ext})")
    return buf.getvalue(), new_ext


def _decode_image_url(url: str) -> tuple[bytes, str | None]:
    """Decode a data: URL or fetch a remote URL. Returns (bytes, ext-or-None)."""
    if url.startswith("data:"):
        header, _, b64 = url.partition(",")
        mime = header[5:].split(";", 1)[0].strip().lower()  # strip "data:"
        return base64.b64decode(b64), _MIME_EXT.get(mime)
    import urllib.request
    with urllib.request.urlopen(url) as resp:  # nosec - controlled provider URL
        ctype = (resp.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        return resp.read(), _MIME_EXT.get(ctype)


def _extract_images_from_message(message) -> list[tuple[bytes, str | None]]:
    """Pull (bytes, ext) tuples out of an OpenRouter chat-completion message.

    OpenRouter returns image-generating model output in a few shapes; try each.
    """
    out: list[tuple[bytes, str | None]] = []

    # Shape 1: message.images = [{type, image_url: {url}}]
    images = getattr(message, "images", None)
    if images:
        for img in images:
            d = img if isinstance(img, dict) else (img.model_dump() if hasattr(img, "model_dump") else {})
            url = (d.get("image_url") or {}).get("url") or d.get("url")
            if url:
                out.append(_decode_image_url(url))

    if out:
        return out

    # Shape 2: message.content as a list of multimodal parts
    content = getattr(message, "content", None)
    if isinstance(content, list):
        for part in content:
            d = part if isinstance(part, dict) else (part.model_dump() if hasattr(part, "model_dump") else {})
            ptype = d.get("type")
            if ptype in ("image_url", "output_image", "image"):
                url = (d.get("image_url") or {}).get("url") or d.get("url") or d.get("image")
                if url:
                    out.append(_decode_image_url(url))
            elif ptype == "image_b64" and d.get("b64_json"):
                out.append((base64.b64decode(d["b64_json"]), None))

    return out


def generate_image_openai(
    prompt: str,
    resolution: str,
    api_key: str,
    output_dir: Path,
    filename: str,
    image_size: str = DEFAULT_IMAGE_SIZE,
    output_format: str = "png",
) -> list[str]:
    """通过 OpenRouter 的 chat.completions 接口调用 openai/gpt-5.4-image-2 并保存结果。

    依据官方文档：
      - modalities 必须为 ["image", "text"]
      - 通过 image_config={aspect_ratio, image_size} 控制画幅与分辨率
      - 响应图像以 data:image/png;base64,... 形式返回
    """
    aspect_ratio = resolution_to_aspect_ratio(resolution)
    client = OpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL)

    image_config = {
        "aspect_ratio": aspect_ratio,
        "image_size": image_size,
    }

    logger.info(f"🎨 正在请求 OpenRouter ({MODEL_NAME})...")
    logger.info(f"📝 提示词: {prompt[:120]}{'...' if len(prompt) > 120 else ''}")
    logger.info(f"📐 image_config: {image_config}")

    logger.info("⏳ 已发送请求，等待 OpenRouter 响应（图像生成通常需要 30–120 秒）...")
    t0 = time.monotonic()
    try:
        completion = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            modalities=["image", "text"],
            extra_body={"image_config": image_config},
            timeout=180.0,
        )
    except Exception as e:
        elapsed = time.monotonic() - t0
        raise RuntimeError(f"API 请求失败 (耗时 {elapsed:.1f}s): {e}") from e
    logger.info(f"✅ 收到响应，耗时 {time.monotonic() - t0:.1f}s")

    # 记录 token 用量
    usage = getattr(completion, "usage", None)
    if usage:
        prompt_t = getattr(usage, "prompt_tokens", None)
        completion_t = getattr(usage, "completion_tokens", None)
        total_t = getattr(usage, "total_tokens", None)
        logger.info(f"📊 Token 用量: 输入 {prompt_t}, 输出 {completion_t}, 合计 {total_t}")

    if not completion.choices:
        logger.error(f"⚠️ 响应无 choices: {completion!r}")
        raise RuntimeError("API 响应中未包含 choices。")

    message = completion.choices[0].message
    images = _extract_images_from_message(message)

    if not images:
        try:
            dump = completion.model_dump() if hasattr(completion, "model_dump") else completion
        except Exception:
            dump = completion
        logger.error(f"⚠️ 未在响应中找到图像。原始响应: {dump!r}")
        raise RuntimeError("API 响应中未包含图像数据。")

    # Crop fallback: derive target ratio from aspect_ratio string.
    try:
        ar_w, ar_h = aspect_ratio_to_floats(aspect_ratio)
    except Exception:
        ar_w, ar_h = 0, 0

    saved_paths = []
    for i, (image_bytes, ext) in enumerate(images):
        ext = ext or output_format
        if ar_w and ar_h:
            image_bytes, ext = _center_crop_to_aspect(image_bytes, ar_w, ar_h, ext)
        path = output_dir / f"{filename}_{i}.{ext}"
        path.write_bytes(image_bytes)
        saved_paths.append(str(path))
        logger.info(f"📥 已保存: {path} ({len(image_bytes)} bytes, ext={ext})")

    return saved_paths


def parse_markdown(md_path: Path) -> tuple[dict, list[tuple[str, str]]]:
    content = md_path.read_text(encoding="utf-8")
    frontmatter = {}
    body = content
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if fm_match:
        try:
            frontmatter = yaml.safe_load(fm_match.group(1)) or {}
        except yaml.YAMLError as e:
            logger.warning(f"解析 frontmatter 失败: {e}")
        body = content[fm_match.end():]

    sections = []
    pattern = re.compile(r"^##\s+(.+)$", re.MULTILINE)
    parts = pattern.split(body)
    for i in range(1, len(parts), 2):
        title = parts[i].strip()
        prompt = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if prompt:
            sections.append((title, prompt))
        else:
            logger.warning(f"跳过空内容页面: [{title}]")
    return frontmatter, sections


def build_characters_prefix(characters: list[dict]) -> str:
    if not characters:
        return ""
    lines = ["【角色设定】以下角色在画面中出场时，必须严格遵循对应的外貌与服装描述："]
    for ch in characters:
        name = ch.get("name", "未命名")
        parts = []
        if ch.get("species"): parts.append(f"物种：{ch['species']}")
        if ch.get("appearance"): parts.append(f"外貌：{ch['appearance']}")
        if ch.get("outfit"): parts.append(f"服装：{ch['outfit']}")
        if ch.get("personality"): parts.append(f"气质：{ch['personality']}")
        if ch.get("accessories"): parts.append(f"装备：{ch['accessories']}")
        lines.append(f"- {name}：{'；'.join(parts)}")
    return "\n".join(lines)


def safe_filename(title: str) -> str:
    return re.sub(r'[\/*?:"<>|]', "_", title).strip()


def generate_page_with_retry(
    title: str,
    prompt: str,
    style: str,
    characters_prefix: str,
    resolution: str,
    api_key: str,
    output_dir: Path,
    image_size: str = DEFAULT_IMAGE_SIZE,
    output_format: str = "png",
) -> tuple[bool, str, list[str]]:
    prefix_parts = [p for p in (style, characters_prefix) if p]
    prefix = "\n\n".join(prefix_parts)
    full_prompt = f"{prefix}\n\n{prompt}".strip() if prefix else prompt

    fname = safe_filename(title)
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            saved_files = generate_image_openai(
                full_prompt, resolution, api_key, output_dir, fname,
                image_size=image_size, output_format=output_format,
            )
            if not saved_files:
                return False, "API 未返回任何图像", []
            return True, "", saved_files
        except Exception as e:
            error_msg = str(e)
            remaining = max_retries - attempt
            logger.warning(f"⚠️ 第 {attempt} 次尝试失败: {error_msg}")
            if remaining > 0:
                logger.info(f"🔄 将在 5 秒后重试（剩余 {remaining} 次）...")
                time.sleep(5)
            else:
                return False, f"生成失败（已重试 {max_retries} 次）: {error_msg}", []
    return False, "未知错误", []


def parse_pages(pages_str: str, total: int) -> list[int]:
    indices = set()
    for part in pages_str.split(","):
        part = part.strip()
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            start, end = int(start_s.strip()), int(end_s.strip())
            for p in range(start, end + 1):
                indices.add(p)
        else:
            indices.add(int(part))
    result = []
    for p in sorted(indices):
        if p < 1 or p > total:
            logger.warning(f"⚠️ 页码 {p} 超出范围（共 {total} 页），已跳过")
        else:
            result.append(p - 1)
    return result


def main():
    parser = argparse.ArgumentParser(
        description="从 Markdown 文件批量生成 PPT 页面图像 (OpenRouter openai/gpt-5.4-image-2)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("markdown", help="Markdown 文件路径")
    parser.add_argument("--output-dir", "-o", default=None, help="图像保存目录")
    parser.add_argument("--resolution", "-r", default=None, help=f"画幅比例 (支持: {', '.join(ASPECT_RATIOS)})")
    parser.add_argument("--image-size", "-s", default=None, choices=list(IMAGE_SIZE_OPTIONS), help=f"图像分辨率档位 ({'/'.join(IMAGE_SIZE_OPTIONS)}，默认 {DEFAULT_IMAGE_SIZE})")
    parser.add_argument("--output-format", "-f", default=None, help="保存扩展名（默认按返回 mime 嗅探，回退 png）")
    parser.add_argument("--api-key", help="OpenRouter API Key")
    parser.add_argument("--pages", "-p", default=None, help="指定生成页面 (如 1, 2-5, 1,3,5)")
    parser.add_argument("--dry-run", action="store_true", help="仅显示生图计划，不调用 API")
    parser.add_argument("--debug", action="store_true", help="启用调试日志")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    md_path = Path(args.markdown)
    if not md_path.exists():
        logger.error(f"文件不存在: {md_path}")
        sys.exit(1)

    frontmatter, sections = parse_markdown(md_path)
    if not sections:
        logger.error("未找到任何 ## 二级标题页面")
        sys.exit(1)

    name = str(frontmatter.get("name", "output")).strip()
    style = str(frontmatter.get("style", "")).strip()
    characters_prefix = build_characters_prefix(frontmatter.get("characters", []) or [])
    resolution = args.resolution or str(frontmatter.get("resolution", DEFAULT_ASPECT_RATIO))
    image_size = args.image_size or str(frontmatter.get("image_size", DEFAULT_IMAGE_SIZE))
    output_format = args.output_format or str(frontmatter.get("output_format", "png"))

    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        output_dir = DEFAULT_IMAGES_DIR / name

    if args.pages:
        page_indices = parse_pages(args.pages, len(sections))
        if not page_indices:
            logger.error("⚠️ --pages 指定的页面均无效")
            sys.exit(1)
        selected = [(idx, sections[idx]) for idx in page_indices]
    else:
        selected = list(enumerate(sections))

    aspect_ratio = resolution_to_aspect_ratio(resolution)

    logger.info(f"📄 Markdown : {md_path}")
    logger.info(f"📂 输出目录 : {output_dir}")
    logger.info(f"📐 画幅比例 : {resolution} → aspect_ratio={aspect_ratio}")
    logger.info(f"🌟 分辨率档 : image_size={image_size}")
    logger.info(f"🖼️  保存扩展名兜底: {output_format}（默认按返回 mime 嗅探）")
    logger.info(f"📑 总页面数 : {len(sections)}，本次生成 : {len(selected)} 页")

    if args.dry_run:
        print(f"=== 生图计划 (openai/gpt-5.4-image-2 via OpenRouter dry-run) ===")
        if characters_prefix:
            print(f"--- [角色设定] ---\n{characters_prefix}\n")
        for idx, (title, prompt) in selected:
            prefix_parts = [p for p in (style, characters_prefix) if p]
            prefix = "\n\n".join(prefix_parts)
            full_prompt = f"{prefix}\n\n{prompt}".strip() if prefix else prompt
            print(f"--- [Page {idx + 1}/{len(sections)}] {title} ---")
            print(f"文件名      : {safe_filename(title)}_0.<ext>")
            print(f"aspect_ratio: {aspect_ratio}")
            print(f"image_size  : {image_size}")
            print(f"提示词      :\n{full_prompt}")
            print("-" * 40)
            print()
        return

    try:
        api_key = args.api_key or get_api_key()
    except RuntimeError as e:
        logger.error(f"❌ {e}")
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)
    results = []

    for step, (idx, (title, prompt)) in enumerate(selected, 1):
        logger.info(f"\n{'='*50}")
        logger.info(f"[{step}/{len(selected)}] 正在生成 Page {idx + 1}: {title}")

        ok, err_msg, saved_files = generate_page_with_retry(
            title=title,
            prompt=prompt,
            style=style,
            characters_prefix=characters_prefix,
            resolution=resolution,
            api_key=api_key,
            output_dir=output_dir,
            image_size=image_size,
            output_format=output_format,
        )

        if ok:
            for fpath in saved_files:
                results.append((title, fpath))
        else:
            logger.error(f"❌ Page {idx + 1} 生成失败: {err_msg}")
            results.append((title, f"ERROR: {err_msg}"))
            sys.exit(1)

    print(f"\n🎉 批量生成完成！保存至: {output_dir}/")


if __name__ == "__main__":
    main()
