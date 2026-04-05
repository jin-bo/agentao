"""Web-related tools."""

import asyncio
import logging
from typing import Any, Dict
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from .base import Tool

logger = logging.getLogger("agentao.tools.web")

try:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
    _CRAWL4AI_AVAILABLE = True
except ImportError:
    _CRAWL4AI_AVAILABLE = False

# SPA/JS-framework fingerprints that indicate server-side content is absent
_JS_MARKERS = [
    "__NEXT_DATA__",        # Next.js
    "__nuxt__",             # Nuxt.js
    "data-reactroot",       # React
    "data-react-helmet",    # React Helmet
    "ng-version",           # Angular
    "__vue__",              # Vue devtools hook
    "data-server-rendered", # Vue SSR (but still needs hydration)
    "ember-application",    # Ember
    "svelte-",              # Svelte
    "__remix_manifest",     # Remix
]

# Ratio of visible text to raw HTML below this threshold → likely JS-rendered
_TEXT_RATIO_THRESHOLD = 0.05
# Absolute visible text shorter than this (chars) → likely JS-rendered
_MIN_TEXT_LENGTH = 200


def _needs_js_rendering(html: str, soup: BeautifulSoup) -> bool:
    """Return True if the page likely requires JavaScript to show its content."""
    # Check framework fingerprints
    for marker in _JS_MARKERS:
        if marker in html:
            return True

    # Check text/HTML ratio
    text = soup.get_text(separator=" ", strip=True)
    text_len = len(text)
    html_len = len(html)

    if html_len > 0 and text_len / html_len < _TEXT_RATIO_THRESHOLD:
        return True
    if text_len < _MIN_TEXT_LENGTH and html_len > 5000:
        return True

    return False


def _extract_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "nav", "footer"]):
        tag.decompose()
    text = soup.get_text()
    lines = (line.strip() for line in text.splitlines())
    chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
    return "\n".join(chunk for chunk in chunks if chunk)


async def _fetch_with_crawl4ai(url: str) -> str:
    config = BrowserConfig(enable_stealth=True, headless=True, verbose=False)
    run_config = CrawlerRunConfig(verbose=False)
    async with AsyncWebCrawler(config=config) as crawler:
        result = await crawler.arun(url=url, config=run_config)
        return result.markdown or ""


def _run_async(coro):
    try:
        return asyncio.run(coro)
    except RuntimeError:
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(coro)


class WebFetchTool(Tool):
    """Tool for fetching web content."""

    @property
    def is_read_only(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return "web_fetch"

    @property
    def description(self) -> str:
        return (
            "Fetch content from a URL. Uses a fast HTTP fetch first; "
            "automatically falls back to Crawl4AI (headless browser) if the "
            "page requires JavaScript rendering."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL to fetch",
                },
                "extract_text": {
                    "type": "boolean",
                    "description": "Whether to extract only text content (default: True)",
                    "default": True,
                },
            },
            "required": ["url"],
        }

    @property
    def requires_confirmation(self) -> bool:
        return True

    def execute(self, url: str, extract_text: bool = True) -> str:
        """Fetch web content, auto-detecting JS-heavy pages."""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        # --- Fast path: plain HTTP fetch ---
        try:
            with httpx.Client(follow_redirects=True, timeout=30.0) as client:
                response = client.get(url, headers=headers)
                response.raise_for_status()

            html = response.text
            soup = BeautifulSoup(html, "html.parser")

            if _CRAWL4AI_AVAILABLE and _needs_js_rendering(html, soup):
                logger.info("JS rendering detected for %s, using crawl4ai", url)
                return self._crawl4ai_fetch(url)

            # Static page — extract directly
            if extract_text:
                text = _extract_text(soup)
                if len(text) > 10000:
                    text = text[:10000] + "\n\n[Content truncated...]"
                return f"URL: {url}\nStatus: {response.status_code}\n\n{text}"
            else:
                content = html
                if len(content) > 10000:
                    content = content[:10000] + "\n\n[Content truncated...]"
                return f"URL: {url}\nStatus: {response.status_code}\n\n{content}"

        except httpx.TimeoutException:
            return f"Error: Request timed out for {url}"
        except httpx.HTTPError as e:
            # Network/HTTP error — try crawl4ai as last resort
            if _CRAWL4AI_AVAILABLE:
                logger.warning("httpx failed for %s (%s), trying crawl4ai", url, e)
                return self._crawl4ai_fetch(url)
            return f"Error fetching URL: {e}"
        except Exception as e:
            return f"Error: {e}"

    def _crawl4ai_fetch(self, url: str) -> str:
        try:
            markdown = _run_async(_fetch_with_crawl4ai(url))
            if not markdown:
                markdown = "[No content extracted]"
            if len(markdown) > 20000:
                markdown = markdown[:20000] + "\n\n[Content truncated...]"
            return f"URL: {url}\n\n{markdown}"
        except Exception as e:
            logger.warning("crawl4ai failed for %s: %s", url, e)
            return f"Error: crawl4ai failed — {e}"


class GoogleSearchTool(Tool):
    """Tool for performing Google searches."""

    @property
    def is_read_only(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return "google_web_search"

    @property
    def description(self) -> str:
        return "Search the web using Google. Returns search results with titles, URLs, and snippets."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query",
                },
                "num_results": {
                    "type": "integer",
                    "description": "Number of results to return (default: 5)",
                    "default": 5,
                },
            },
            "required": ["query"],
        }

    @property
    def requires_confirmation(self) -> bool:
        return True

    def execute(self, query: str, num_results: int = 5) -> str:
        """Perform Google search."""
        try:
            encoded_query = quote_plus(query)
            url = f"https://html.duckduckgo.com/html/?q={encoded_query}"

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }

            with httpx.Client(timeout=30.0) as client:
                response = client.get(url, headers=headers)
                response.raise_for_status()

                soup = BeautifulSoup(response.text, "html.parser")
                results = []

                for result_div in soup.find_all("div", class_="result", limit=num_results):
                    title_elem = result_div.find("a", class_="result__a")
                    snippet_elem = result_div.find("a", class_="result__snippet")

                    if title_elem:
                        title = title_elem.get_text(strip=True)
                        link = title_elem.get("href", "")
                        snippet = snippet_elem.get_text(strip=True) if snippet_elem else ""

                        results.append({
                            "title": title,
                            "url": link,
                            "snippet": snippet,
                        })

                if not results:
                    return f"No search results found for: {query}"

                output = f"Search results for: {query}\n\n"
                for i, result in enumerate(results, 1):
                    output += f"{i}. {result['title']}\n"
                    output += f"   URL: {result['url']}\n"
                    if result['snippet']:
                        output += f"   {result['snippet']}\n"
                    output += "\n"

                return output

        except httpx.HTTPError as e:
            return f"Error performing search: {str(e)}"
        except Exception as e:
            return f"Error: {str(e)}"
