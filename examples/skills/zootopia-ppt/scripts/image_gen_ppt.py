#!/usr/bin/env python3
"""从 Markdown 文件批量生成 PPT 页面图像。

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
import mimetypes
import argparse
import logging
from pathlib import Path
from urllib.parse import urlparse

# 强制行缓冲：当作为子进程运行时 Python 默认块缓冲 stdout，
# 导致 print() 输出堆积到脚本结束才显示。行缓冲让每行立即刷出。
if not sys.stdout.isatty():
    sys.stdout.reconfigure(line_buffering=True)

try:
    import yaml
except ImportError:
    print("缺少依赖：pyyaml。请执行 pip install pyyaml")
    sys.exit(1)

try:
    import requests
except ImportError:
    print("缺少依赖：requests。请执行 pip install requests")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

API_BASE_URL = "https://api.tensorslab.com"
ENDPOINT_GENERATE = f"{API_BASE_URL}/v1/images/seedreamv5"
ENDPOINT_STATUS = f"{API_BASE_URL}/v1/images/infobytaskid"

IMAGE_STATUS = {1: "排队中", 2: "生成中", 3: "已完成", 4: "失败"}

DEFAULT_IMAGES_DIR = Path("images")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_SESSION = requests.Session()
_SESSION.proxies = {"http": "", "https": ""}


def get_api_key() -> str:
    key = os.environ.get("TENSORLAB_API_KEY", "").strip()
    if not key:
        env_file = Path(".env")
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("TENSORLAB_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"\'')
                    break
    if not key:
        raise RuntimeError(
            "未找到 API Key。请在 .env 文件中设置 TENSORLAB_API_KEY=your_key"
        )
    return key


def generate_image(prompt: str, resolution: str, api_key: str) -> str:
    headers = {"Authorization": f"Bearer {api_key}"}
    files = [
        ("prompt", (None, prompt)),
        ("resolution", (None, resolution)),
        ("category", (None, "seedreamv5")),
    ]
    logger.info("🎨 正在提交生成任务（seedreamv5）...")
    logger.info(f"📝 提示词: {prompt[:120]}{'...' if len(prompt) > 120 else ''}")
    try:
        resp = _SESSION.post(ENDPOINT_GENERATE, headers=headers, files=files, timeout=60)
        resp.raise_for_status()
        result = resp.json()
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"网络请求失败: {e}") from e
    except ValueError:
        raise RuntimeError(f"API 返回非 JSON 响应 (HTTP {resp.status_code}): {resp.text}")

    if result.get("code") == 1000:
        task_id = result["data"]["taskid"]
        logger.info(f"✅ 任务提交成功，Task ID: {task_id}")
        return task_id
    elif result.get("code") == 9000:
        raise RuntimeError("账户积分不足，请前往 https://tensorai.tensorslab.com/ 充值")
    else:
        raise RuntimeError(f"API 错误: {result.get('msg', '未知错误')} (code={result.get('code')})")


def _download_image(url: str, output_path: Path) -> Path | None:
    try:
        resp = _SESSION.get(url, timeout=60)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "").split(";")[0].strip()
        ext = mimetypes.guess_extension(content_type)
        if ext == ".jpe":
            ext = ".jpg"
        if not ext:
            ext = Path(urlparse(url).path).suffix
        if not ext or len(ext) > 6:
            ext = ".png"
        final_path = output_path.with_suffix(ext)
        final_path.write_bytes(resp.content)
        return final_path
    except Exception as e:
        logger.warning(f"下载图像失败 ({url}): {e}")
        return None


def wait_and_download(
    task_id: str,
    api_key: str,
    output_dir: Path,
    poll_interval: int = 3,
    timeout: int = 300,
) -> list[str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    start = time.time()
    logger.info("⏳ 等待图像生成完成...")

    while time.time() - start < timeout:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        try:
            resp = _SESSION.post(ENDPOINT_STATUS, headers=headers, json={"taskid": task_id}, timeout=30)
            result = resp.json()
            data = result.get("data", {}) if result.get("code") == 1000 else None
        except Exception as e:
            logger.error(f"查询任务状态出错: {e}")
            data = None

        if not data:
            time.sleep(poll_interval)
            continue

        status = data.get("image_status")
        elapsed = int(time.time() - start)
        logger.info(f"🔄 状态: {IMAGE_STATUS.get(status, '未知')} (已等待 {elapsed}s)")

        if status == 3:
            urls = data.get("url", [])
            if not urls:
                logger.warning("⚠️ 任务完成但未返回图像 URL")
                return []
            downloaded = []
            for i, url in enumerate(urls):
                out_path = output_dir / f"{task_id}_{i}"
                path = _download_image(url, out_path)
                if path:
                    downloaded.append(str(path))
                    logger.info(f"📥 已保存: {path}")
            return downloaded
        elif status == 4:
            raise RuntimeError(f"生成任务失败: {data.get('error_message', '未知原因')}")

        time.sleep(poll_interval)

    raise RuntimeError(f"等待超时（已等待 {timeout}s）")


def parse_markdown(md_path: Path) -> tuple[dict, list[tuple[str, str]]]:
    """解析 Markdown 文件，返回 (frontmatter, [(title, prompt), ...])"""
    content = md_path.read_text(encoding="utf-8")

    # 解析 YAML frontmatter
    frontmatter = {}
    body = content
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if fm_match:
        try:
            frontmatter = yaml.safe_load(fm_match.group(1)) or {}
        except yaml.YAMLError as e:
            logger.warning(f"解析 frontmatter 失败: {e}")
        body = content[fm_match.end():]

    # 按 ## 二级标题切分页面
    sections = []
    pattern = re.compile(r"^##\s+(.+)$", re.MULTILINE)
    parts = pattern.split(body)
    # parts 结构: [前置内容, 标题1, 内容1, 标题2, 内容2, ...]
    for i in range(1, len(parts), 2):
        title = parts[i].strip()
        prompt = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if prompt:
            sections.append((title, prompt))
        else:
            logger.warning(f"跳过空内容页面: [{title}]")

    return frontmatter, sections


def build_characters_prefix(characters: list[dict]) -> str:
    """将 frontmatter 中的 characters 列表转换为注入提示词的角色描述文本。"""
    if not characters:
        return ""
    lines = ["【角色设定】以下角色在画面中出场时，必须严格遵循对应的外貌与服装描述："]
    for ch in characters:
        name = ch.get("name", "未命名")
        parts = []
        if ch.get("species"):
            parts.append(f"物种：{ch['species']}")
        if ch.get("appearance"):
            parts.append(f"外貌：{ch['appearance']}")
        if ch.get("outfit"):
            parts.append(f"服装：{ch['outfit']}")
        if ch.get("personality"):
            parts.append(f"气质：{ch['personality']}")
        if ch.get("accessories"):
            parts.append(f"装备：{ch['accessories']}")
        lines.append(f"- {name}：{'；'.join(parts)}")
    return "\n".join(lines)


def safe_filename(title: str) -> str:
    """将标题转换为合法文件名"""
    return re.sub(r'[\\/*?:"<>|]', "_", title).strip()


# ---------------------------------------------------------------------------
# 带重试的单页生成（仅对网络/临时错误重试，不修改提示词）
# ---------------------------------------------------------------------------
MAX_RETRIES = 3


def generate_page_with_retry(
    title: str,
    prompt: str,
    style: str,
    characters_prefix: str,
    resolution: str,
    api_key: str,
    output_dir: Path,
    poll_interval: int,
    timeout: int,
) -> tuple[bool, str, list[str]]:
    """生成单页图像，带重试逻辑。

    仅对网络超时等临时错误进行重试，不会自动修改提示词。
    内容审核 / 敏感词等错误直接失败并返回，由调用方（LLM）分析并调整提示词。

    Returns:
        (success, error_message, file_paths)
        - success=True 时 file_paths 包含已保存文件路径
        - success=False 时 error_message 包含错误详情
    """
    # 组装完整提示词：风格 + 角色设定 + 页面内容
    prefix_parts = [p for p in (style, characters_prefix) if p]
    prefix = "\n\n".join(prefix_parts)
    full_prompt = f"{prefix}\n\n{prompt}".strip() if prefix else prompt

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            task_id = generate_image(full_prompt, resolution, api_key)
            files = wait_and_download(
                task_id=task_id,
                api_key=api_key,
                output_dir=output_dir,
                poll_interval=poll_interval,
                timeout=timeout,
            )
            # 成功
            fname = safe_filename(title)
            saved = []
            for j, fpath in enumerate(files):
                src = Path(fpath)
                dest_name = f"{fname}{src.suffix}" if j == 0 else f"{fname}_{j}{src.suffix}"
                dest = output_dir / dest_name
                src.rename(dest)
                saved.append(str(dest))
                logger.info(f"✅ 已保存: {dest}")
            return True, "", saved

        except RuntimeError as e:
            error_msg = str(e)
            remaining = MAX_RETRIES - attempt

            logger.warning(f"⚠️ 第 {attempt} 次尝试失败: {error_msg}")
            if remaining > 0:
                logger.info(f"🔄 将在 3 秒后重试（剩余 {remaining} 次）...")
                time.sleep(3)
            else:
                return False, f"生成失败（已重试 {MAX_RETRIES} 次）: {error_msg}", []

    return False, "未知错误", []


# ---------------------------------------------------------------------------
# 页面范围解析
# ---------------------------------------------------------------------------
def parse_pages(pages_str: str, total: int) -> list[int]:
    """解析 --pages 参数，返回 0-based 页面索引列表。

    支持格式:
        "3"     → 第 3 页（返回 [2]）
        "3-7"   → 第 3 到第 7 页（返回 [2,3,4,5,6]）
        "3,5,8" → 第 3、5、8 页（返回 [2,4,7]）
    """
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

    # 校验范围
    result = []
    for p in sorted(indices):
        if p < 1 or p > total:
            logger.warning(f"⚠️ 页码 {p} 超出范围（共 {total} 页），已跳过")
        else:
            result.append(p - 1)  # 转为 0-based

    return result


def main():
    parser = argparse.ArgumentParser(
        description="从 Markdown 文件批量生成 PPT 页面图像（seedreamv5）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python image_gen_ppt.py slides.md
  python image_gen_ppt.py slides.md --output-dir ./images/my_ppt
  python image_gen_ppt.py slides.md --pages 3        # 仅生成第 3 页
  python image_gen_ppt.py slides.md --pages 3-7      # 生成第 3 到第 7 页
  python image_gen_ppt.py slides.md --pages 1,3,5    # 生成第 1、3、5 页
  python image_gen_ppt.py slides.md --dry-run
        """,
    )
    parser.add_argument("markdown", help="Markdown 文件路径")
    parser.add_argument(
        "--output-dir", "-o", default=None,
        help="图像保存目录（默认: images/{name}，由 frontmatter 的 name 字段决定）",
    )
    parser.add_argument(
        "--resolution", "-r", default=None,
        help="分辨率，覆盖 frontmatter 设置（默认: 3840x2160）",
    )
    parser.add_argument("--poll-interval", type=int, default=6,
                        help="轮询间隔秒数（默认: 6）")
    parser.add_argument("--timeout", type=int, default=300,
                        help="每张图像最长等待秒数（默认: 300）")
    parser.add_argument("--api-key", help="TensorsLab API Key（优先于 .env 的 TENSORLAB_API_KEY）")
    parser.add_argument(
        "--pages", "-p", default=None,
        help="指定生成页面，如 '3' (单页)、'3-7' (范围)、'1,3,5' (多页)。默认生成全部",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="仅显示生图计划，不实际调用 API")
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

    # 从 frontmatter 和命令行参数确定配置
    name = str(frontmatter.get("name", "output")).strip()
    style = str(frontmatter.get("style", "")).strip()
    characters_prefix = build_characters_prefix(frontmatter.get("characters", []) or [])
    resolution = args.resolution or str(frontmatter.get("resolution", "3840x2160"))

    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        output_dir = DEFAULT_IMAGES_DIR / name

    # 过滤页面范围
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
    logger.info(f"🖼️  分辨率   : {resolution}")
    logger.info(f"📑 总页面数 : {len(sections)}，本次生成 : {len(selected)} 页")
    if args.pages:
        page_nums = [idx + 1 for idx, _ in selected]
        logger.info(f"📌 指定页面 : {page_nums}")
    if style:
        logger.info(f"🎨 风格前缀 : {style[:100]}{'...' if len(style) > 100 else ''}")
    if characters_prefix:
        logger.info(f"👤 角色设定 : {len(frontmatter.get('characters', []))} 个角色已加载")
    print()

    if args.dry_run:
        print("=== 生图计划（dry-run）===")
        if characters_prefix:
            print(f"[角色设定]\n{characters_prefix}\n")
        for idx, (title, prompt) in selected:
            prefix_parts = [p for p in (style, characters_prefix) if p]
            prefix = "\n\n".join(prefix_parts)
            full_prompt = f"{prefix}\n\n{prompt}".strip() if prefix else prompt
            print(f"--- [Page {idx + 1}/{len(sections)}] {title} ---")
            print(f"文件名 : {safe_filename(title)}.png")
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
    results: list[tuple[str, str]] = []  # [(title, filepath_or_error)]

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
            poll_interval=args.poll_interval,
            timeout=args.timeout,
        )

        if ok:
            for fpath in saved_files:
                results.append((title, fpath))
        else:
            # 生成失败 → 立即中止，输出结构化错误信息供 LLM 分析
            results.append((title, f"ERROR: {err_msg}"))

            error_report = {
                "status": "FAILED",
                "failed_page": idx + 1,
                "failed_title": title,
                "error": err_msg,
                "completed_pages": [t for t, p in results if not p.startswith("ERROR")],
                "remaining_pages": [
                    sections[ri][0] for ri, _ in selected[step:]
                ],
                "prompt_used": prompt[:300],
            }

            print(f"\n{'='*50}")
            print("❌ 生图任务中止！以下为错误分析信息：")
            print(json.dumps(error_report, ensure_ascii=False, indent=2))
            sys.exit(1)

    # 汇总报告
    success = [(t, p) for t, p in results if not p.startswith("ERROR")]
    print(f"\n{'='*50}")
    print(f"🎉 批量生成完成！成功 {len(success)}/{len(selected)} 张，保存至: {output_dir}/")
    for title, path in success:
        print(f"   ✅ [{title}] -> {Path(path).name}")


if __name__ == "__main__":
    main()
