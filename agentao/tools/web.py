"""Web-related tools."""

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Any, Dict, Optional
from urllib.parse import quote_plus

# `httpx`, `bs4`, and `crawl4ai` are deferred (P0.5): the web tools may
# never be registered in an embedded host that doesn't expose web
# capabilities, so importing this module should not pay for the parsing /
# HTTP / headless-browser stack until a tool actually runs.
if TYPE_CHECKING:
    from bs4 import BeautifulSoup as _BeautifulSoup_t

from .base import Tool
from ..security.url_policy import (
    UrlPolicyError,
    guarded_get,
    read_allow_cidrs_setting,
)

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
        # Opt-in SSRF allowlist (AGENTAO_WEB_FETCH_ALLOW_CIDRS): CIDRs that are
        # not globally routable but the operator trusts — e.g. a fake-IP proxy
        # range (Clash/V2Ray → 198.18.0.0/15) or an internal service. Empty =
        # fully strict. Reading it here (not per-call) keeps it on the same
        # audit surface as the fallback and emits its WARNING once.
        self._allow_cidrs = read_allow_cidrs_setting()

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
            base += (
                " If the page requires JavaScript rendering or the direct"
                " fetch fails, falls back to Jina Reader — the URL is sent"
                " to https://r.jina.ai and rendered server-side."
            )
        elif self._fallback == _FALLBACK_CRAWL4AI:
            base += (
                " If the page requires JavaScript rendering or the direct"
                " fetch fails, falls back to a local headless browser"
                " (crawl4ai)."
            )
        else:
            base += " No JS-rendering fallback is configured."
        if self._allow_cidrs:
            base += (
                " SSRF allowlist active (AGENTAO_WEB_FETCH_ALLOW_CIDRS): also"
                " permits otherwise-blocked non-public target(s) in "
                + ", ".join(str(n) for n in self._allow_cidrs)
                + "."
            )
        return base

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
            # SSRF guard: follow_redirects=False so guarded_get owns the
            # redirect chase and re-validates the target on every hop. The
            # static PermissionEngine blocklist already ran at the plan-phase
            # gate on the original URL; this catches what a string check
            # can't — names that *resolve* to private/loopback addresses and
            # redirects into the internal network.
            with httpx.Client(follow_redirects=False, timeout=30.0) as client:
                response = guarded_get(
                    client, url, headers=headers, allow_networks=self._allow_cidrs
                )
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

        except UrlPolicyError as e:
            # Blocked target — do NOT fall through to the jina/crawl4ai
            # fallbacks: those would exfiltrate the internal URL through a
            # third party or fetch it via a local headless browser.
            return f"Error: blocked outbound request — {e}"
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
        keep the primary result.

        SSRF note: ``url`` reaching here has already passed
        ``validate_outbound_url`` on the primary path (a blocked target raises
        ``UrlPolicyError``, which the caller returns without falling back). But
        the fallbacks themselves are NOT per-hop guarded — Jina fetches the
        target server-side and the crawl4ai headless browser follows
        redirects / JS navigation on its own. Both are opt-in
        (``AGENTAO_WEB_FETCH_FALLBACK``, default ``none``) and surfaced in the
        tool description; closing the in-browser redirect path would require
        intercepting Chromium's network, which is out of scope here.
        """
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


#: Per-snippet cap so one verbose result can't dominate the tool output.
_SNIPPET_LIMIT = 300


def _format_search_results(query: str, items: list[dict]) -> str:
    """Render a normalized result list to the shared ``web_search`` text block.

    Each backend (bocha / jina / duckduckgo) maps its own response shape to a
    list of ``{"title", "url", "snippet"}`` dicts and calls this — the single
    source for the heading, numbering, the "No search results" sentinel, and
    snippet capping. Values are coerced to ``str`` so a non-string field in a
    backend payload can never raise (the f-strings stringify ``title``/``url``;
    ``snippet`` is coerced explicitly because it is sliced).
    """
    if not items:
        return f"No search results found for: {query}"
    out = f"Search results for: {query}\n\n"
    for i, item in enumerate(items, 1):
        snippet = item.get("snippet") or ""
        if not isinstance(snippet, str):
            snippet = str(snippet)
        if len(snippet) > _SNIPPET_LIMIT:
            snippet = snippet[:_SNIPPET_LIMIT] + "…"
        out += f"{i}. {item.get('title') or '(no title)'}\n"
        out += f"   URL: {item.get('url') or ''}\n"
        if snippet:
            out += f"   {snippet}\n"
        out += "\n"
    return out


class WebSearchTool(Tool):
    #: Known search backends. ``duckduckgo`` is the only keyless one, so it is
    #: the universal last-resort fallback — but keyless is not the same as
    #: reliable: see ``_search_duckduckgo`` for the bot-challenge guard.
    #: ``jina`` and ``bocha`` both need a key. (``s.jina.ai`` *was* keyless when
    #: this backend landed; measured 2026-07-22 it answers 401 without one.)
    _KNOWN_BACKENDS = ("jina", "bocha", "duckduckgo")

    def __init__(
        self,
        *,
        backend: Optional[str] = None,
        api_key: Optional[str] = None,
        jina_api_key: Optional[str] = None,
    ) -> None:
        """Web-search tool.

        Args:
            backend: Search provider override (``"jina"`` / ``"bocha"`` /
                ``"duckduckgo"``). When ``None``, the tool runs in **auto mode**
                and builds a fallback chain from key availability (see below).
            api_key: Bocha API key. When ``None``, falls back to the
                ``BOCHA_API_KEY`` process env var.
            jina_api_key: Jina API key. When ``None``, falls back to the
                ``JINA_API_KEY`` process env var. Required in practice —
                ``s.jina.ai`` answers 401 without one (see ``_search_jina``) —
                but deliberately *not* enforced at construction the way
                ``bocha`` is: the endpoint used to be keyless, so a keyless pin
                stays constructible and surfaces the 401 as a search error.

        Backend resolution:

        * **Explicit ``backend=``** → exactly that one backend, *no* automatic
          fallback. A host that deliberately pins a provider is honored (it is
          never silently re-routed through another third party).
        * **Auto mode (``backend=None``)** → an ordered fallback chain that, on a
          backend *error* (not an empty result), retreats to the next backend
          and ends at the keyless ``duckduckgo``: ``jina`` (if a Jina key
          resolves) → ``bocha`` (if a Bocha key resolves) → ``duckduckgo``.

        Explicit args take precedence over the env var so two Agentao instances
        in the same process can use different search backends — the env read is
        only a fallback, not a process-global override. Pass a configured
        instance via ``Agentao(extra_tools=[...])``.

        Raises:
            ValueError: if ``backend`` is not a known provider, or if
                ``backend="bocha"`` is requested without a resolvable key.
                Fails loudly at construction rather than emitting a
                ``Bearer None`` request (401) on the first query.
        """
        # ``is not None`` (not ``or``) so an explicit api_key="" — a host
        # deliberately forcing "no key" — is honored and does NOT silently
        # fall back to the process-global env var.
        self._bocha_api_key = (
            api_key if api_key is not None else os.getenv("BOCHA_API_KEY")
        )
        self._jina_api_key = (
            jina_api_key if jina_api_key is not None else os.getenv("JINA_API_KEY")
        )
        if backend is not None and backend not in self._KNOWN_BACKENDS:
            raise ValueError(
                f"WebSearchTool(backend=): unknown backend {backend!r}; "
                f"expected one of {', '.join(repr(b) for b in self._KNOWN_BACKENDS)}."
            )
        if backend == "bocha" and not self._bocha_api_key:
            raise ValueError(
                "WebSearchTool: backend 'bocha' requires an API key — pass "
                "api_key= or set BOCHA_API_KEY."
            )

        if backend is not None:
            # Explicit pin: single backend, no auto-fallback (literal contract).
            self._chain = [backend]
        else:
            # Auto mode: jina (keyed) → bocha (keyed) → duckduckgo (keyless).
            chain = []
            if self._jina_api_key:
                chain.append("jina")
            if self._bocha_api_key:
                chain.append("bocha")
            chain.append("duckduckgo")
            self._chain = chain
        # ``_provider`` = the primary (first attempted) backend. Kept for
        # back-compat (tests + description) now that a chain exists behind it.
        self._provider = self._chain[0]

    @property
    def is_read_only(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        base = "Search the web. Returns search results with titles, URLs, and snippets. "
        if len(self._chain) > 1:
            return base + f"Backend order (falls back on failure): {' → '.join(self._chain)}."
        return base + f"Backend: {self._provider}."

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
        """Run the query down the backend chain, falling back on *error*.

        A backend that raises (HTTP / timeout / parse failure) is logged and the
        next backend in ``self._chain`` is tried; a backend that succeeds —
        *including* a valid "no results" answer — terminates the chain. Every
        fallback is surfaced: the served result is prefixed with a note naming
        the backends that failed. If the whole chain fails, the combined error
        is returned.
        """
        # num_results is LLM-supplied: coerce + clamp so a 0/negative value can't
        # silently truncate (slice ``[:-1]``) or yield a header-only "success"
        # that suppresses the fallback chain. Backends differ on degenerate
        # counts; normalize before dispatch.
        try:
            num_results = int(num_results)
        except (TypeError, ValueError):
            num_results = 5
        num_results = max(1, num_results)

        errors: list[tuple[str, str]] = []
        for idx, backend in enumerate(self._chain):
            try:
                result = self._dispatch(backend, query, num_results)
            except Exception as e:  # noqa: BLE001 — any backend failure → fall back
                logger.warning("web_search backend %r failed: %s", backend, e)
                errors.append((backend, str(e)))
                continue
            if idx > 0:
                tried = ", ".join(b for b, _ in errors)
                result = (
                    f"[Note: backend(s) {tried} failed; these results are from the "
                    f"fallback backend '{backend}'.]\n\n{result}"
                )
            return result

        # A pinned single backend reports its own failure plainly; the chain case
        # lists every attempt.
        if len(errors) == 1:
            backend, msg = errors[0]
            return f"Error performing {backend} search: {msg}"
        detail = "; ".join(f"{b}: {msg}" for b, msg in errors)
        return f"Error: all web_search backends failed ({detail})"

    def _dispatch(self, backend: str, query: str, num_results: int) -> str:
        if backend == "bocha":
            return self._search_bocha(query, num_results)
        if backend == "jina":
            return self._search_jina(query, num_results)
        return self._search_duckduckgo(query, num_results)

    def _search_bocha(self, query: str, num_results: int) -> str:
        """Query Bocha. Raises on HTTP / parse failure so ``execute`` can fall back."""
        import httpx

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

        # Guard every level before ``.get`` — a malformed HTTP-200 body (root or
        # ``webPages`` not an object) is a parse failure that should raise (→ the
        # chain falls back), not crash with AttributeError.
        if not isinstance(data, dict):
            raise ValueError("bocha: unexpected response shape (root is not an object)")
        inner = data.get("data")
        web_pages = inner.get("webPages") if isinstance(inner, dict) else None
        raw_results: list = []
        if isinstance(web_pages, dict) and isinstance(web_pages.get("value"), list):
            raw_results = web_pages["value"]
        elif isinstance(inner, list):
            raw_results = inner
        elif isinstance(data.get("results"), list):
            raw_results = data["results"]

        items = [
            {
                "title": item.get("name") or item.get("title"),
                "url": item.get("url"),
                "snippet": item.get("snippet") or item.get("summary") or "",
            }
            for item in raw_results[:num_results]
            if isinstance(item, dict)
        ]
        return _format_search_results(query, items)

    def _search_jina(self, query: str, num_results: int) -> str:
        """Query Jina search (``s.jina.ai``). Needs a ``JINA_API_KEY``: measured
        2026-07-22 the endpoint answers 401 ``AuthenticationRequiredError``
        without one. It was keyless when this backend landed, which is why the
        constructor still accepts a keyless pin. Raises on HTTP / parse failure
        so ``execute`` falls back."""
        import httpx

        headers = {
            "Accept": "application/json",
            # Return only the hit list (title/url/description), not each page's
            # scraped content — faster and all a search listing needs.
            "X-Respond-With": "no-content",
        }
        if self._jina_api_key:
            headers["Authorization"] = f"Bearer {self._jina_api_key}"

        with httpx.Client(timeout=30.0) as client:
            response = client.get(
                "https://s.jina.ai/", params={"q": query}, headers=headers
            )
            response.raise_for_status()
            data = response.json()

        # ``data`` is the hit list. A missing / non-list ``data`` is a malformed
        # (soft-error) 200 — raise so the chain falls back, rather than reporting
        # a misleading "no results". An *empty* list is a genuine no-results
        # answer and terminates the chain.
        raw_results = data.get("data") if isinstance(data, dict) else None
        if not isinstance(raw_results, list):
            raise ValueError("jina: unexpected response shape (no 'data' list)")

        items = [
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "snippet": item.get("description") or item.get("content") or "",
            }
            for item in raw_results[:num_results]
            if isinstance(item, dict)
        ]
        return _format_search_results(query, items)

    def _search_duckduckgo(self, query: str, num_results: int) -> str:
        """Query DuckDuckGo HTML. Raises on HTTP / parse / bot-challenge failure
        so ``execute`` can fall back (or report an error)."""
        import httpx
        from bs4 import BeautifulSoup

        encoded_query = quote_plus(query)
        url = f"https://html.duckduckgo.com/html/?q={encoded_query}"

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        with httpx.Client(timeout=30.0) as client:
            response = client.get(url, headers=headers)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")

            # A real SERP — *including* a genuine zero-hit one — always renders
            # the ``#links`` results container (a zero-hit page puts a
            # ``.no-results`` notice inside it). The bot-challenge page renders
            # none of that and answers 202 rather than 200, so ``raise_for_status``
            # waves it through. Either signal means the body is not a result set.
            #
            # Raise rather than return an empty list: ``find_all("div", "result")``
            # yields 0 on a challenge page, which would otherwise be formatted
            # into a confident "No search results found" — indistinguishable
            # from a real empty answer. ``duckduckgo`` is last in every auto
            # chain (and the *only* entry when no key resolves), so a silent
            # empty here is the entire search surface failing quietly.
            # Measured 2026-07-22: keyless html.duckduckgo.com answers 202 with
            # a captcha page for every query.
            if response.status_code == 202 or soup.select_one("#links, .results") is None:
                raise ValueError(
                    "duckduckgo: bot-challenge page, not a result set "
                    f"(HTTP {response.status_code}, no '#links' results container)"
                )

            items = []

            for result_div in soup.find_all("div", class_="result", limit=num_results):
                title_elem = result_div.find("a", class_="result__a")
                snippet_elem = result_div.find("a", class_="result__snippet")

                if title_elem:
                    items.append({
                        "title": title_elem.get_text(strip=True),
                        "url": title_elem.get("href", ""),
                        "snippet": snippet_elem.get_text(strip=True) if snippet_elem else "",
                    })

            return _format_search_results(query, items)
