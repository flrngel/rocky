from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from rocky.tools.base import Tool, ToolContext, ToolResult
from rocky.tools.proxy_support import tool_proxy_url
from rocky.util.text import truncate


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36 Rocky/0.3"
)
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
}
SEARCH_SOURCES = (
    ("duckduckgo", "https://html.duckduckgo.com/html/?{query}"),
    ("duckduckgo", "https://duckduckgo.com/html/?{query}"),
    ("duckduckgo", "https://lite.duckduckgo.com/lite/?{query}"),
    ("brave", "https://search.brave.com/search?{query}"),
)
DEFAULT_REQUEST_ATTEMPTS = 2
TRANSIENT_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})
BOT_CHALLENGE_MARKERS = (
    "human verification",
    "verify you are human",
    "verify you're human",
    "please complete the following challenge",
    "complete the following challenge",
    "prove you're human",
    "unusual traffic",
    "automated requests",
    "bots use duckduckgo too",
)
BOT_CHALLENGE_HARD_MARKERS = (
    "g-recaptcha",
    "hcaptcha",
    "cf-turnstile",
    "cf-chl",
    "__cf_chl_",
    "/cdn-cgi/challenge-platform/",
    "data-sitekey",
    "challenge-form",
)
BOT_CHALLENGE_TITLE_MARKERS = (
    "challenge",
    "just a moment",
    "attention required",
    "verify you are human",
    "verify you're human",
)
NO_RESULTS_MARKERS = (
    "no results found",
    "no results.",
    "search returned 0 result",
    "did not match any documents",
)


def _client(timeout_s: int = 20, *, verify: bool = True) -> httpx.Client:
    return httpx.Client(
        timeout=timeout_s,
        headers=DEFAULT_HEADERS,
        verify=verify,
        trust_env=False,
        proxy=tool_proxy_url(),
    )


def _error_result(
    summary: str,
    *,
    url: str | None = None,
    query: str | None = None,
    status_code: int | None = None,
    error: str | None = None,
    attempted_urls: list[str] | None = None,
    details: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> ToolResult:
    data: dict[str, Any] = {}
    if url:
        data["url"] = url
    if query:
        data["query"] = query
    if status_code is not None:
        data["status_code"] = status_code
    if error:
        data["error"] = error
    if attempted_urls:
        data["attempted_urls"] = attempted_urls
    if details:
        data.update(details)
    return ToolResult(False, data, summary, metadata or {})


def _response_links(response: httpx.Response) -> list[str]:
    return [str(item.headers.get("location", "")) for item in response.history if item.headers.get("location")]


def _is_transient_request_error(exc: httpx.HTTPError) -> bool:
    return isinstance(exc, (httpx.TimeoutException, httpx.TransportError))


def _is_tls_verification_error(exc: httpx.HTTPError) -> bool:
    return isinstance(exc, httpx.ConnectError) and "certificate verify failed" in str(exc).lower()


def _tls_verified(response: httpx.Response) -> bool:
    return bool(response.extensions.get("tls_verified", True))


def _looks_like_bot_challenge(response: httpx.Response) -> bool:
    content_type = response.headers.get("content-type", "").lower()
    if "html" not in content_type:
        return False
    html = response.text.lower()
    if any(marker in html for marker in BOT_CHALLENGE_HARD_MARKERS):
        return True
    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.title.string.strip().lower() if soup.title and soup.title.string else ""
    phrase_matches = sum(1 for marker in BOT_CHALLENGE_MARKERS if marker in html)
    title_matches = sum(1 for marker in BOT_CHALLENGE_TITLE_MARKERS if marker in title)
    if phrase_matches >= 2:
        return True
    if phrase_matches >= 1 and title_matches >= 1:
        return True
    if response.status_code in {202, 403, 429, 503} and phrase_matches >= 1:
        return True
    return False


def _looks_like_no_results(html: str) -> bool:
    lowered = html.lower()
    return any(marker in lowered for marker in NO_RESULTS_MARKERS)


def _normalize_page_link(href: str | None, base_url: str) -> str | None:
    if not href:
        return None
    normalized = _normalize_result_url(href, base_url)
    if normalized:
        return normalized
    absolute = urljoin(base_url, href)
    parsed = urlparse(absolute)
    if parsed.scheme in {"http", "https"}:
        return absolute
    return None


def _challenge_result(response: httpx.Response, *, requested_url: str, attempts: int) -> ToolResult:
    return _error_result(
        f"Encountered anti-bot challenge while fetching {requested_url}",
        url=str(response.url),
        status_code=response.status_code,
        error="anti-bot challenge",
        details={"text_excerpt": _response_text_excerpt(response)},
        metadata={
            "attempts": attempts,
            "blocked_by_challenge": True,
            "redirected": bool(response.history),
            "redirect_chain": _response_links(response),
            "tls_verified": _tls_verified(response),
        },
    )


def _get(
    client: httpx.Client,
    url: str,
    *,
    follow_redirects: bool = True,
    attempts: int = DEFAULT_REQUEST_ATTEMPTS,
) -> tuple[httpx.Response | None, ToolResult | None]:
    total_attempts = max(1, attempts)
    for attempt in range(1, total_attempts + 1):
        try:
            response = client.get(url, follow_redirects=follow_redirects)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            response = exc.response
            transient = response.status_code in TRANSIENT_STATUS_CODES
            if transient and attempt < total_attempts:
                continue
            return None, _error_result(
                f"HTTP {response.status_code} while fetching {url}",
                url=str(response.url),
                status_code=response.status_code,
                error=str(exc),
                metadata={
                    "attempts": attempt,
                    "transient": transient,
                    "redirect_chain": _response_links(response),
                },
            )
        except httpx.HTTPError as exc:
            transient = _is_transient_request_error(exc)
            if transient and attempt < total_attempts:
                continue
            return None, _error_result(
                f"Request failed for {url}",
                url=url,
                error=f"{exc.__class__.__name__}: {exc}",
                metadata={
                    "attempts": attempt,
                    "transient": transient,
                    "tls_verification_failed": _is_tls_verification_error(exc),
                },
            )
        if _looks_like_bot_challenge(response):
            return None, _challenge_result(response, requested_url=url, attempts=attempt)
        return response, None
    return None, _error_result(f"Request failed for {url}", url=url, error="unknown request failure")


def _request(
    url: str,
    *,
    timeout_s: int,
    follow_redirects: bool = True,
) -> tuple[httpx.Response | None, ToolResult | None]:
    with _client(timeout_s) as client:
        response, error = _get(client, url, follow_redirects=follow_redirects)
    if response is not None:
        response.extensions["tls_verified"] = True
        return response, None
    if error is None or not error.metadata.get("tls_verification_failed") or urlparse(url).scheme != "https":
        return None, error

    with _client(timeout_s, verify=False) as insecure_client:
        insecure_response, insecure_error = _get(insecure_client, url, follow_redirects=follow_redirects, attempts=1)
    if insecure_response is not None:
        insecure_response.extensions["tls_verified"] = False
        return insecure_response, None
    if insecure_error is not None:
        insecure_error.metadata["tls_verified"] = False
    return None, insecure_error


def _normalize_result_url(href: str | None, base_url: str) -> str | None:
    if not href:
        return None
    absolute = urljoin(base_url, href)
    parsed = urlparse(absolute)
    query = parse_qs(parsed.query)
    for key in ("uddg", "u"):
        values = query.get(key) or []
        if values:
            return unquote(values[0])
    if parsed.scheme in {"http", "https"}:
        return absolute
    return None


def _extract_duckduckgo_results(html: str, base_url: str, max_results: int) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for anchor in soup.select("a.result__a, .result__title a, a.result-link"):
        title = anchor.get_text(" ", strip=True)
        url = _normalize_result_url(anchor.get("href"), base_url)
        if not title or not url or url in seen_urls:
            continue
        container = anchor.find_parent(class_="result") or anchor.find_parent("tr") or anchor.parent
        snippet_el = None
        if container is not None:
            snippet_el = container.select_one(".result__snippet, .result-snippet, td.result-snippet")
        snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""
        results.append({"title": title, "url": url, "snippet": snippet})
        seen_urls.add(url)
        if len(results) >= max_results:
            return results

    for anchor in soup.find_all("a", href=True):
        title = anchor.get_text(" ", strip=True)
        url = _normalize_result_url(anchor.get("href"), base_url)
        if not title or len(title) < 8 or not url or url in seen_urls:
            continue
        if "duckduckgo.com" in urlparse(url).netloc and "uddg=" not in (anchor.get("href") or ""):
            continue
        results.append({"title": title, "url": url, "snippet": ""})
        seen_urls.add(url)
        if len(results) >= max_results:
            break
    return results


def _extract_brave_results(html: str, base_url: str, max_results: int) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for container in soup.select("div.snippet[data-type='web']"):
        link = container.select_one("a.l1[href], a[href]")
        if link is None:
            continue
        url = _normalize_page_link(link.get("href"), base_url)
        if not url or url in seen_urls:
            continue
        title_el = container.select_one(".search-snippet-title, .title")
        snippet_el = container.select_one(".generic-snippet .content, .description")
        title = title_el.get_text(" ", strip=True) if title_el else link.get_text(" ", strip=True)
        snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""
        if not title:
            continue
        results.append({"title": title, "url": url, "snippet": snippet})
        seen_urls.add(url)
        if len(results) >= max_results:
            break
    return results


def _extract_search_results(html: str, base_url: str, max_results: int) -> list[dict[str, Any]]:
    host = urlparse(base_url).netloc.lower()
    if host == "search.brave.com":
        return _extract_brave_results(html, base_url, max_results)
    return _extract_duckduckgo_results(html, base_url, max_results)


def _response_text_excerpt(response: httpx.Response) -> str:
    content_type = response.headers.get("content-type", "").lower()
    if "html" in content_type:
        soup = BeautifulSoup(response.text, "html.parser")
        text = " ".join(soup.get_text(" ", strip=True).split())
        return truncate(text, 4000)
    return truncate(response.text.strip(), 4000)


def fetch_url(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    url = str(args["url"])
    ctx.require("web", "fetch url", url, risky=True)
    response, error = _request(url, timeout_s=int(args.get("timeout_s", 20)), follow_redirects=True)
    if error is not None:
        return error
    assert response is not None
    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    links: list[str] = []
    seen_links: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        normalized = _normalize_page_link(anchor.get("href"), str(response.url))
        if not normalized or normalized in seen_links:
            continue
        links.append(normalized)
        seen_links.add(normalized)
        if len(links) >= 20:
            break
    return ToolResult(
        True,
        {
            "url": str(response.url),
            "status_code": response.status_code,
            "title": title,
            "text_excerpt": _response_text_excerpt(response),
            "links": links,
            "content_type": response.headers.get("content-type", ""),
        },
        f"Fetched {response.url}",
        {
            "redirected": bool(response.history),
            "redirect_chain": _response_links(response),
            "tls_verified": _tls_verified(response),
        },
    )


def extract_links(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    url = str(args["url"])
    ctx.require("web", "extract links", url, risky=True)
    response, error = _request(url, timeout_s=int(args.get("timeout_s", 20)), follow_redirects=True)
    if error is not None:
        return error
    assert response is not None
    soup = BeautifulSoup(response.text, "html.parser")
    links: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    max_links = int(args.get("max_links", 50))
    for anchor in soup.find_all("a", href=True):
        normalized = _normalize_page_link(anchor.get("href"), str(response.url))
        if not normalized or normalized in seen_urls:
            continue
        links.append(
            {
                "text": anchor.get_text(" ", strip=True)[:120],
                "url": normalized,
            }
        )
        seen_urls.add(normalized)
        if len(links) >= max_links:
            break
    return ToolResult(
        True,
        links,
        f"Extracted {len(links)} link(s)",
        {
            "url": str(response.url),
            "redirected": bool(response.history),
            "redirect_chain": _response_links(response),
            "tls_verified": _tls_verified(response),
        },
    )


def search_web(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    query = str(args["query"])
    max_results = int(args.get("max_results", 8))
    ctx.require("web", "search web", query, risky=True)
    encoded_query = urlencode({"q": query})
    attempted_urls: list[str] = []
    errors: list[str] = []
    timeout_s = int(args.get("timeout_s", 20))

    engines_tried: list[str] = []
    for engine, template in SEARCH_SOURCES:
        url = template.format(query=encoded_query)
        attempted_urls.append(url)
        if engine not in engines_tried:
            engines_tried.append(engine)
        response, error = _request(url, timeout_s=timeout_s, follow_redirects=True)
        if error is not None:
            errors.append(error.summary)
            continue
        assert response is not None
        results = _extract_search_results(response.text, str(response.url), max_results)
        if results:
            return ToolResult(
                True,
                results,
                f"Search returned {len(results)} result(s)",
                {
                    "engine": engine,
                    "url": str(response.url),
                    "attempted_urls": attempted_urls,
                    "redirected": bool(response.history),
                    "redirect_chain": _response_links(response),
                    "tls_verified": _tls_verified(response),
                },
            )
        if _looks_like_no_results(response.text):
            return ToolResult(
                True,
                [],
                "Search returned 0 result(s)",
                {
                    "engine": engine,
                    "url": str(response.url),
                    "attempted_urls": attempted_urls,
                    "tls_verified": _tls_verified(response),
                },
            )
        errors.append(f"No parsable results from {response.url}")

    return _error_result(
        "Search failed or returned no parsable results",
        query=query,
        attempted_urls=attempted_urls,
        error="; ".join(errors[:4]),
        metadata={"engines_tried": engines_tried},
    )


def tools() -> list[Tool]:
    return [
        Tool(
            "fetch_url",
            "Fetch a URL and extract title/text",
            {"type": "object", "properties": {"url": {"type": "string"}, "timeout_s": {"type": "integer"}}, "required": ["url"]},
            "web",
            fetch_url,
        ),
        Tool(
            "search_web",
            "Search the web with a lightweight HTML fallback",
            {"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}, "timeout_s": {"type": "integer"}}, "required": ["query"]},
            "web",
            search_web,
        ),
        Tool(
            "extract_links",
            "Extract links from a web page",
            {"type": "object", "properties": {"url": {"type": "string"}, "max_links": {"type": "integer"}, "timeout_s": {"type": "integer"}}, "required": ["url"]},
            "web",
            extract_links,
        ),
    ]
