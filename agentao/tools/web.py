"""Web-related tools."""

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Any, Dict
from urllib.parse import quote_plus

# `httpx`, `bs4`, and `crawl4ai` are deferred (P0.5): the web tools may
# never be registered in an embedded host that doesn't expose web
# capabilities, so importing this module should not pay for the parsing /
# HTTP / headless-browser stack until a tool actually runs.
if TYPE_CHECKING:
    from bs4 import BeautifulSoup as _BeautifulSoup_t

from .base import Tool

logger = logging.getLogger("agentao.tools.web")

# Fallback when the fast httpx path doesn't yield usable content (JS-rendered
# page or HTTP error). Default is "none" — the tool never silently proxies
# the user-supplied URL through a third party. Hosts opt in explicitly.
_FALLBACK_NONE = "none"
_FALLBACK_JINA = "jina"
_FALLBACK_CRAWL4AI = "crawl4ai"
_VALID_FALLBACKS = {_FALLBACK_NONE, _FALLBACK_JINA, _FALLBACK_CRAWL4AI}


def _read_fallback_setting() -> str:
    raw = (os.getenv("AGENTAO_WEB_FETCH_FALLBACK") or "").strip().lower()
    if raw in _VALID_FALLBACKS:
        return raw
    if raw:
        logger.warning(
            "Ignoring invalid AGENTAO_WEB_FETCH_FALLBACK=%r; expected one of %s",
            raw, sorted(_VALID_FALLBACKS),
        )
    return _FALLBACK_NONE

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

_TEXT_RATIO_THRESHOLD = 0.05
_MIN_TEXT_LENGTH = 200


def _needs_js_rendering(html: str, soup: "_BeautifulSoup_t") -> bool:
    for marker in _JS_MARKERS:
        if marker in html:
            return True

    text = soup.get_text(separator=" ", strip=True)
    text_len = len(text)
    html_len = len(html)

    if html_len > 0 and text_len / html_len < _TEXT_RATIO_THRESHOLD:
        return True
    if text_len < _MIN_TEXT_LENGTH and html_len > 5000:
        return True

    return False


def _extract_text(soup: "_BeautifulSoup_t") -> str:
    for tag in soup(["script", "style", "nav", "footer"]):
        tag.decompose()
    text = soup.get_text()
    lines = (line.strip() for line in text.splitlines())
    chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
    return "\n".join(chunk for chunk in chunks if chunk)


def _truncate(text: str, limit: int) -> str:
    if len(text) > limit:
        return text[:limit] + "\n\n[Content truncated...]"
    return text


def _format_fallback_body(url: str, label: str, body: str) -> str:
    body = body or "[No content extracted]"
    return f"URL: {url}\nFallback: {label}\n\n{_truncate(body, 20000)}"


async def _fetch_with_crawl4ai(url: str) -> str:
    # Deferred import: crawl4ai pulls in Playwright + Chromium; only
    # imported when this fallback is actually selected and invoked.
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig

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


def _jina_proxy_url(url: str) -> str:
    return f"https://r.jina.ai/{url}"


def _fetch_via_jina(url: str) -> str:
    import httpx

    proxy_url = _jina_proxy_url(url)
    headers = {"Accept": "text/markdown, text/plain;q=0.9"}
    api_key = os.getenv("JINA_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    with httpx.Client(follow_redirects=True, timeout=30.0) as client:
        response = client.get(proxy_url, headers=headers)
        response.raise_for_status()
    return response.text or ""


class WebFetchTool(Tool):
    def __init__(self) -> None:
        # Read env once at construction (matches WebSearchTool pattern). The
        # fallback target is part of the audit surface — the description below
        # tells the LLM and the host operator exactly where outbound traffic
        # can go before the user is ever asked to confirm a fetch.
        self._fallback = _read_fallback_setting()

    @property
    def is_read_only(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return "web_fetch"

    @property
    def description(self) -> str:
        base = "Fetch content from a URL via HTTP (GET) and extract readable text."
        if self._fallback == _FALLBACK_JINA:
            return (
                base
                + " If the page requires JavaScript rendering or the direct"
                + " fetch fails, falls back to Jina Reader — the URL is sent"
                + " to https://r.jina.ai and rendered server-side."
            )
        if self._fallback == _FALLBACK_CRAWL4AI:
            return (
                base
                + " If the page requires JavaScript rendering or the direct"
                + " fetch fails, falls back to a local headless browser"
                + " (crawl4ai)."
            )
        return base + " No JS-rendering fallback is configured."

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
        import httpx
        from bs4 import BeautifulSoup

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        try:
            with httpx.Client(follow_redirects=True, timeout=30.0) as client:
                response = client.get(url, headers=headers)
                response.raise_for_status()

            html = response.text
            soup = BeautifulSoup(html, "html.parser")

            js_detected = _needs_js_rendering(html, soup)
            if js_detected:
                fallback_result = self._run_fallback(
                    url, reason="JS rendering detected"
                )
                if fallback_result is not None:
                    return fallback_result

            js_note = (
                "Note: page appears to require JS rendering; only the static"
                " shell was captured. Set AGENTAO_WEB_FETCH_FALLBACK=jina"
                " (sends URL to r.jina.ai) or =crawl4ai (local headless"
                " browser) to enable a fallback.\n"
                if js_detected
                else ""
            )
            body = _extract_text(soup) if extract_text else html
            return (
                f"URL: {url}\nStatus: {response.status_code}\n{js_note}\n"
                f"{_truncate(body, 10000)}"
            )

        except httpx.TimeoutException:
            fallback_result = self._run_fallback(url, reason="httpx timeout")
            if fallback_result is not None:
                return fallback_result
            return f"Error: Request timed out for {url}"
        except httpx.HTTPError as e:
            fallback_result = self._run_fallback(url, reason=f"httpx error: {e}")
            if fallback_result is not None:
                return fallback_result
            return f"Error fetching URL: {e}"
        except Exception as e:
            return f"Error: {e}"

    def _run_fallback(self, url: str, *, reason: str) -> str | None:
        """Returns None when fallback is ``none``, signalling the caller to
        keep the primary result."""
        if self._fallback == _FALLBACK_NONE:
            return None
        if self._fallback == _FALLBACK_JINA:
            logger.info("web_fetch fallback=jina for %s (%s)", url, reason)
            return self._jina_fetch(url)
        logger.info("web_fetch fallback=crawl4ai for %s (%s)", url, reason)
        return self._crawl4ai_fetch(url)

    def _jina_fetch(self, url: str) -> str:
        label = f"jina reader ({_jina_proxy_url(url)})"
        try:
            markdown = _fetch_via_jina(url)
        except Exception as e:
            logger.warning("jina reader failed for %s: %s", url, e)
            return f"URL: {url}\nFallback: {label}\nError: {e}"
        return _format_fallback_body(url, label, markdown)

    def _crawl4ai_fetch(self, url: str) -> str:
        label = "crawl4ai (headless browser)"
        try:
            markdown = _run_async(_fetch_with_crawl4ai(url))
        except ImportError as e:
            return (
                f"URL: {url}\nFallback: {label}\nError: crawl4ai not"
                f" installed — install with `pip install agentao[crawl4ai]`"
                f" and run `playwright install chromium`. ({e})"
            )
        except Exception as e:
            logger.warning("crawl4ai failed for %s: %s", url, e)
            return f"URL: {url}\nFallback: {label}\nError: {e}"
        return _format_fallback_body(url, label, markdown)


class WebSearchTool(Tool):
    def __init__(self) -> None:
        self._bocha_api_key = os.getenv("BOCHA_API_KEY")
        self._provider = "bocha" if self._bocha_api_key else "duckduckgo"

    @property
    def is_read_only(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return (
            "Search the web. Returns search results with titles, URLs, and snippets. "
            f"Backend: {self._provider}."
        )

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
        if self._provider == "bocha":
            return self._search_bocha(query, num_results)
        return self._search_duckduckgo(query, num_results)

    def _search_bocha(self, query: str, num_results: int) -> str:
        import httpx

        try:
            payload = {"query": query, "count": num_results, "summary": True}
            headers = {
                "Authorization": f"Bearer {self._bocha_api_key}",
                "Content-Type": "application/json",
            }
            with httpx.Client(timeout=30.0) as client:
                response = client.post(
                    "https://api.bochaai.com/v1/web-search",
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()

            raw_results = []
            web_pages = data.get("data", {}).get("webPages", {}) if isinstance(data.get("data"), dict) else {}
            if web_pages and isinstance(web_pages.get("value"), list):
                raw_results = web_pages["value"]
            elif isinstance(data.get("data"), list):
                raw_results = data["data"]
            elif isinstance(data.get("results"), list):
                raw_results = data["results"]

            if not raw_results:
                return f"No search results found for: {query}"

            output = f"Search results for: {query}\n\n"
            for i, item in enumerate(raw_results[:num_results], 1):
                title = item.get("name") or item.get("title") or "(no title)"
                url = item.get("url") or ""
                snippet = item.get("snippet") or item.get("summary") or ""
                output += f"{i}. {title}\n"
                output += f"   URL: {url}\n"
                if snippet:
                    output += f"   {snippet}\n"
                output += "\n"
            return output

        except httpx.HTTPError as e:
            return f"Error performing Bocha search: {str(e)}"
        except Exception as e:
            return f"Error: {str(e)}"

    def _search_duckduckgo(self, query: str, num_results: int) -> str:
        import httpx
        from bs4 import BeautifulSoup

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
