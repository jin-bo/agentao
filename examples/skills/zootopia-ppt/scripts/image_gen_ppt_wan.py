#!/usr/bin/env python3
"""从 Markdown 文件批量生成 PPT 页面图像 (阿里 Wan2.7-image 模型版本)。

使用 dashscope SDK 调用 wan2.7-image 模型。

Markdown 格式：
  ---
  name: my presentation
  resolution: "16:9"
  style: 整体风格描述（注入到每页提示词最前面）
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
import json
import time
import argparse
import logging
import requests
from pathlib import Path

# 强制行缓冲
if not sys.stdout.isatty():
    sys.stdout.reconfigure(line_buffering=True)

try:
    import yaml
except ImportError:
    print("缺少依赖：pyyaml。请执行 pip install pyyaml")
    sys.exit(1)

try:
    import dashscope
    from dashscope.aigc.image_generation import ImageGeneration
    from dashscope.api_entities.dashscope_response import Message
except ImportError:
    print("缺少依赖：dashscope。请执行 pip install dashscope")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Wan2.7 API Settings
MODEL_NAME = "wan2.7-image"
dashscope.base_http_api_url = 'https://dashscope.aliyuncs.com/api/v1'

DEFAULT_IMAGES_DIR = Path("images")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def get_api_key() -> str:
    """从环境变量获取 QWEN_API_KEY (支持通过 .env 文件加载)"""
    key = os.environ.get("QWEN_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "未找到 QWEN_API_KEY。请在 .env 文件中设置，或导出为环境变量。"
        )
    return key


def download_image(url: str, save_path: Path):
    """从 URL 下载图像并保存"""
    try:
        response = requests.get(url, timeout=60)
        response.raise_for_status()
        save_path.write_bytes(response.content)
        return True
    except Exception as e:
        logger.error(f"下载图像失败: {e}")
        return False


def generate_image_wan(prompt: str, resolution: str, api_key: str, output_dir: Path, filename: str) -> list[str]:
    """使用 DashScope SDK 生成图像 (Wan2.7-image)"""
    
    # 映射分辨率到 Wan2.7 支持的尺寸字符串 "width*height"
    # 常用映射 (以 1024/720 为基础)
    size_map = {
        "1:1": "1024*1024",
        "16:9": "1696*960",
        "9:16": "960*1696",
        "3:4": "896*1152",
        "4:3": "1152*896",
    }
    
    selected_size = "1024*1024" # 默认
    for ratio, size_str in size_map.items():
        if ratio in resolution:
            selected_size = size_str
            break

    logger.info(f"🎨 正在请求 DashScope ({MODEL_NAME})...")
    logger.info(f"📝 提示词: {prompt[:120]}{'...' if len(prompt) > 120 else ''}")
    logger.info(f"📐 尺寸: {selected_size}")

    message = Message(
        role="user",
        content=[{"text": prompt}]
    )

    try:
        rsp = ImageGeneration.call(
            model=MODEL_NAME,
            api_key=api_key,
            messages=[message],
            enable_sequential=False, # 默认单图生成
            n=1,
            size=selected_size
        )
    except Exception as e:
        raise RuntimeError(f"API 请求失败: {e}") from e

    if rsp.status_code != 200:
        raise RuntimeError(f"API 返回错误: {rsp.code} - {rsp.message}")

    saved_paths = []
    # 按照参考文档解析响应
    if hasattr(rsp, 'output') and rsp.output.get('choices'):
        for i, choice in enumerate(rsp.output['choices']):
            if choice.get('message') and choice['message'].get('content'):
                for content_item in choice['message']['content']:
                    if content_item.get('type') == 'image' and content_item.get('image'):
                        img_url = content_item['image']
                        final_path = output_dir / f"{filename}_{i}.png"
                        if download_image(img_url, final_path):
                            saved_paths.append(str(final_path))
                            logger.info(f"📥 已保存: {final_path}")
    
    if not saved_paths:
        raise RuntimeError(f"API 未返回有效的图像链接。原始响应: {rsp}")

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
) -> tuple[bool, str, list[str]]:
    prefix_parts = [p for p in (style, characters_prefix) if p]
    prefix = "\n\n".join(prefix_parts)
    full_prompt = f"{prefix}\n\n{prompt}".strip() if prefix else prompt
    
    fname = safe_filename(title)
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            saved_files = generate_image_wan(full_prompt, resolution, api_key, output_dir, fname)
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
            for p in range(start, end + 1): indices.add(p)
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
        description="从 Markdown 文件批量生成 PPT 页面图像 (阿里 Wan2.7-image)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("markdown", help="Markdown 文件路径")
    parser.add_argument("--output-dir", "-o", default=None, help="图像保存目录")
    parser.add_argument("--resolution", "-r", default=None, help="比例 (如 16:9)")
    parser.add_argument("--api-key", help="QWEN_API_KEY")
    parser.add_argument("--pages", "-p", default=None, help="指定生成页面")
    parser.add_argument("--dry-run", action="store_true", help="仅显示生图计划")
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
    resolution = args.resolution or str(frontmatter.get("resolution", "16:9"))

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

    logger.info(f"📄 Markdown : {md_path}")
    logger.info(f"📂 输出目录 : {output_dir}")
    logger.info(f"📐 比例设定 : {resolution}")
    logger.info(f"📑 总页面数 : {len(sections)}，本次生成 : {len(selected)} 页")

    if args.dry_run:
        print("=== 生图计划 (Wan2.7 dry-run) ===")
        if characters_prefix:
            print(f"--- [角色设定] ---\n{characters_prefix}\n")
        for idx, (title, prompt) in selected:
            prefix_parts = [p for p in (style, characters_prefix) if p]
            prefix = "\n\n".join(prefix_parts)
            full_prompt = f"{prefix}\n\n{prompt}".strip() if prefix else prompt
            print(f"--- [Page {idx + 1}/{len(sections)}] {title} ---")
            print(f"文件名 : {safe_filename(title)}.png")
            print(f"比例   : {resolution}")
            print(f"提示词 :\n{full_prompt}")
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
