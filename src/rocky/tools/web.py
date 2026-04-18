from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from rocky.tools.base import Tool, ToolContext, ToolResult, _tool_cap
from rocky.tools.proxy_support import tool_proxy_url
from rocky.util.text import truncate


CHALLENGE_EXCERPT_CHARS = 2000


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
GENERIC_LINK_TEXTS = {
    "",
    "home",
    "login",
    "log in",
    "sign in",
    "sign up",
    "join",
    "pricing",
    "docs",
    "documentation",
    "enterprise",
    "about",
    "blog",
    "contact",
    "terms",
    "privacy",
    "models",
    "datasets",
    "spaces",
    "storage",
}
GENERIC_PATH_SEGMENTS = {
    "",
    "about",
    "app",
    "apps",
    "blog",
    "collection",
    "collections",
    "contact",
    "dataset",
    "datasets",
    "doc",
    "docs",
    "documentation",
    "enterprise",
    "explore",
    "join",
    "library",
    "libraries",
    "login",
    "logout",
    "model",
    "models",
    "news",
    "pricing",
    "privacy",
    "search",
    "settings",
    "signin",
    "signup",
    "space",
    "spaces",
    "tag",
    "tags",
    "terms",
}


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
    raw_html_lower = response.text.lower()
    soup = BeautifulSoup(response.text, "html.parser")
    # Strip inert-infrastructure tags before HARD-marker check so CF/Turnstile script
    # URLs in <script src> / <link> / <meta> don't false-flag clean 200 responses
    # (O4b-β). soup.decode() preserves attributes on surviving elements (e.g.
    # data-sitekey on a visible <div class="cf-turnstile">) so the positive case
    # at test line 538 still triggers correctly.
    for tag in soup.find_all(["script", "link", "noscript", "meta"]):
        tag.decompose()
    stripped_html = soup.decode().lower()
    if any(marker in stripped_html for marker in BOT_CHALLENGE_HARD_MARKERS):
        return True
    title = soup.title.string.strip().lower() if soup.title and soup.title.string else ""
    phrase_matches = sum(1 for marker in BOT_CHALLENGE_MARKERS if marker in raw_html_lower)
    if phrase_matches >= 2:
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


def _link_text(anchor) -> str:
    return " ".join(anchor.get_text(" ", strip=True).split())[:120]


def _preferred_link_items(html: str, base_url: str, *, max_links: int = 20) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    base_host = urlparse(base_url).netloc.lower()
    scored: dict[str, tuple[int, int, dict[str, str]]] = {}
    order = 0
    roots: list[tuple[Any, int]] = []
    for candidate in (
        soup.find("main"),
        soup.find(attrs={"role": "main"}),
        soup.find("article"),
        soup.body,
        soup,
    ):
        if candidate is not None:
            bonus = 6 if getattr(candidate, "name", "") in {"main", "article"} else 0
            roots.append((candidate, bonus))
    seen_roots: set[int] = set()
    deduped_roots: list[tuple[Any, int]] = []
    for root, bonus in roots:
        marker = id(root)
        if marker in seen_roots:
            continue
        seen_roots.add(marker)
        deduped_roots.append((root, bonus))
    for root, root_bonus in deduped_roots:
        for anchor in root.find_all("a", href=True):
            normalized = _normalize_page_link(anchor.get("href"), base_url)
            if not normalized:
                continue
            text = _link_text(anchor)
            parsed = urlparse(normalized)
            host = parsed.netloc.lower()
            parts = [part for part in parsed.path.split("/") if part]
            lowered_parts = [part.lower() for part in parts]
            score = root_bonus
            if host == base_host:
                score += 3
            if parts:
                score += min(len(parts), 3)
            else:
                score -= 3
            if (
                len(parts) >= 2
                and not parsed.query
                and all(part not in GENERIC_PATH_SEGMENTS for part in lowered_parts[:2])
            ):
                score += 5
            if parsed.query:
                score -= 2
                if len(parts) <= 1 or (lowered_parts and lowered_parts[0] in GENERIC_PATH_SEGMENTS):
                    score -= 5
            if lowered_parts and lowered_parts[0] in GENERIC_PATH_SEGMENTS:
                score -= 3
            if "search" in lowered_parts[:2]:
                score -= 5
            if anchor.find_parent(["nav", "header", "footer", "aside"]) is not None:
                score -= 6
            lowered_text = text.lower()
            if lowered_text in GENERIC_LINK_TEXTS:
                score -= 4
            elif text:
                score += 2
                if len(text.split()) <= 12:
                    score += 1
                if "/" in text and len(text.split()) <= 10:
                    score += 2
            else:
                score -= 1
            lowered_url = normalized.lower()
            if any(marker in lowered_url for marker in ("/login", "/join", "/pricing", "/docs", "/enterprise")):
                score -= 4
            candidate = {"text": text, "url": normalized}
            existing = scored.get(normalized)
            if existing is None or score > existing[0]:
                scored[normalized] = (score, order, candidate)
            order += 1
    ranked = sorted(scored.values(), key=lambda item: (-item[0], item[1]))
    items = [item for score, _order, item in ranked if score >= 1]
    if items:
        return items[:max_links]
    fallback: list[dict[str, str]] = []
    for _score, _order, item in ranked[:max_links]:
        fallback.append(item)
    return fallback


def _challenge_result(response: httpx.Response, *, requested_url: str, attempts: int) -> ToolResult:
    return _error_result(
        f"Encountered anti-bot challenge while fetching {requested_url}",
        url=str(response.url),
        status_code=response.status_code,
        error="anti-bot challenge",
        details={"text_excerpt": _response_text_excerpt(response, CHALLENGE_EXCERPT_CHARS)},
        metadata={
            "attempts": attempts,
            "blocked_by_challenge": True,
            "browser_fallback_hint": True,
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


def _response_text_excerpt(response: httpx.Response, limit: int) -> str:
    content_type = response.headers.get("content-type", "").lower()
    if "html" in content_type:
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup.find_all(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()
        candidate = None
        for selector in [soup.find("article"), soup.find(attrs={"role": "main"}), soup.find("main")]:
            if selector is not None:
                candidate = selector
                break
        if candidate is not None:
            text = " ".join(candidate.get_text(" ", strip=True).split())
            if len(text) >= 200:
                return truncate(text, limit)
        body = soup.find("body") or soup
        text = " ".join(body.get_text(" ", strip=True).split())
        return truncate(text, limit)
    return truncate(response.text.strip(), limit)


def fetch_url(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    url = str(args["url"])
    ctx.require("web", "fetch url", url, risky=True)
    response, error = _request(url, timeout_s=int(args.get("timeout_s", 20)), follow_redirects=True)
    if error is not None:
        return error
    assert response is not None
    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    link_items = _preferred_link_items(response.text, str(response.url), max_links=20)
    links = [item["url"] for item in link_items]
    return ToolResult(
        True,
        {
            "url": str(response.url),
            "status_code": response.status_code,
            "title": title,
            "text_excerpt": _response_text_excerpt(response, _tool_cap(ctx.config.tools, "fetch_url")),
            "link_items": link_items,
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
    max_links = int(args.get("max_links", 50))
    links = _preferred_link_items(response.text, str(response.url), max_links=max_links)
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


_SITE_RE = re.compile(r"\bsite:\S+\s*", re.I)
_QUOTED_RE = re.compile(r'"([^"]+)"')
MAX_BROADENING_ROUNDS = 2


def _broaden_query(query: str) -> list[str]:
    variants: list[str] = []
    seen: set[str] = {query.strip().lower()}
    without_site = _SITE_RE.sub("", query).strip()
    if without_site and without_site.lower() not in seen:
        variants.append(without_site)
        seen.add(without_site.lower())
    base = without_site or query
    without_quotes = _QUOTED_RE.sub(r"\1", base).strip()
    if without_quotes and without_quotes.lower() not in seen:
        variants.append(without_quotes)
        seen.add(without_quotes.lower())
    base2 = without_quotes or base
    tokens = base2.split()
    if len(tokens) >= 4:
        shorter = " ".join(tokens[:-1])
        if shorter.lower() not in seen:
            variants.append(shorter)
            seen.add(shorter.lower())
    return variants


def _search_engines(
    query: str,
    *,
    max_results: int,
    timeout_s: int,
    steps: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    encoded_query = urlencode({"q": query})
    attempted_urls: list[str] = []
    errors: list[str] = []
    for engine, template in SEARCH_SOURCES:
        url = template.format(query=encoded_query)
        attempted_urls.append(url)
        response, error = _request(url, timeout_s=timeout_s, follow_redirects=True)
        if error is not None:
            errors.append(error.summary)
            outcome = "challenge" if error.metadata.get("blocked_by_challenge") else "request_error"
            steps.append({"engine": engine, "url": url, "outcome": outcome, "result_count": 0})
            continue
        assert response is not None
        results = _extract_search_results(response.text, str(response.url), max_results)
        if results:
            steps.append({"engine": engine, "url": url, "outcome": "success", "result_count": len(results)})
            return results, {
                "engine": engine,
                "url": str(response.url),
                "attempted_urls": attempted_urls,
                "redirected": bool(response.history),
                "redirect_chain": _response_links(response),
                "tls_verified": _tls_verified(response),
            }
        if _looks_like_no_results(response.text):
            steps.append({"engine": engine, "url": url, "outcome": "no_results", "result_count": 0})
            return [], {
                "engine": engine,
                "url": str(response.url),
                "attempted_urls": attempted_urls,
                "tls_verified": _tls_verified(response),
            }
        steps.append({"engine": engine, "url": url, "outcome": "parse_failed", "result_count": 0})
        errors.append(f"No parsable results from {response.url}")
    return [], None


def search_web(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    query = str(args["query"])
    max_results = int(args.get("max_results", 8))
    ctx.require("web", "search web", query, risky=True)
    timeout_s = int(args.get("timeout_s", 20))
    steps: list[dict[str, Any]] = []

    results, meta = _search_engines(query, max_results=max_results, timeout_s=timeout_s, steps=steps)
    if results:
        assert meta is not None
        meta["steps"] = steps
        return ToolResult(True, results, f"Search returned {len(results)} result(s)", meta)
    if meta is not None and not results:
        meta["steps"] = steps
        return ToolResult(True, [], "Search returned 0 result(s)", meta)

    variants = _broaden_query(query)
    for round_num, variant in enumerate(variants[:MAX_BROADENING_ROUNDS], 1):
        steps.append({"stage": "broadening", "round": round_num, "variant": variant, "outcome": "attempting"})
        variant_results, variant_meta = _search_engines(
            variant, max_results=max_results, timeout_s=timeout_s, steps=steps,
        )
        if variant_results:
            assert variant_meta is not None
            variant_meta["steps"] = steps
            variant_meta["broadened"] = True
            variant_meta["original_query"] = query
            variant_meta["query_used"] = variant
            return ToolResult(
                True, variant_results, f"Search returned {len(variant_results)} result(s) (broadened query)", variant_meta,
            )

    return _error_result(
        "Search failed or returned no parsable results",
        query=query,
        error=f"Tried {len(steps)} steps including broadened variants",
        metadata={"steps": steps},
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
    ]
