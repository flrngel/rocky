from __future__ import annotations

from pathlib import Path

import httpx

from rocky.app import RockyRuntime
from rocky.tools.proxy_support import TOOL_PROXY_ENV_VAR
import rocky.tools.web as web


def _tool_context(tmp_path: Path):
    runtime = RockyRuntime.load_from(tmp_path)
    runtime.permissions.config.mode = "bypass"
    return runtime.tool_registry.context


def _install_mock_client(monkeypatch, handler) -> None:
    transport = httpx.MockTransport(handler)

    def make_client(timeout_s: int = 20, *, verify: bool = True) -> httpx.Client:
        return httpx.Client(
            transport=transport,
            headers=web.DEFAULT_HEADERS,
            timeout=timeout_s,
            verify=verify,
            trust_env=False,
        )

    monkeypatch.setattr(web, "_client", make_client)


def test_search_web_skips_challenge_and_follows_redirected_fallback(tmp_path: Path, monkeypatch) -> None:
    ctx = _tool_context(tmp_path)
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if str(request.url) == "https://html.duckduckgo.com/html/?q=rocky+agent":
            return httpx.Response(
                202,
                request=request,
                headers={"content-type": "text/html; charset=UTF-8"},
                text="""
                <html>
                  <body>
                    <p>Unfortunately, bots use DuckDuckGo too.</p>
                    <p>Please complete the following challenge.</p>
                  </body>
                </html>
                """,
            )
        if str(request.url) == "https://duckduckgo.com/html/?q=rocky+agent":
            return httpx.Response(
                302,
                request=request,
                headers={"location": "https://html.duckduckgo.com/html/?q=rocky+agent&ia=web"},
            )
        if str(request.url) == "https://html.duckduckgo.com/html/?q=rocky+agent&ia=web":
            return httpx.Response(
                200,
                request=request,
                headers={"content-type": "text/html; charset=UTF-8"},
                text="""
                <html>
                  <body>
                    <div class="result">
                      <a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Farticle">
                        Example result
                      </a>
                      <a class="result__snippet">Useful summary</a>
                    </div>
                  </body>
                </html>
                """,
            )
        raise AssertionError(f"Unexpected request URL: {request.url}")

    _install_mock_client(monkeypatch, handler)

    result = web.search_web(ctx, {"query": "rocky agent", "max_results": 3})

    assert result.success is True
    assert result.data == [
        {
            "title": "Example result",
            "url": "https://example.com/article",
            "snippet": "Useful summary",
        }
    ]
    assert result.metadata["attempted_urls"] == [
        "https://html.duckduckgo.com/html/?q=rocky+agent",
        "https://duckduckgo.com/html/?q=rocky+agent",
    ]
    assert result.metadata["redirected"] is True
    assert result.metadata["redirect_chain"] == ["https://html.duckduckgo.com/html/?q=rocky+agent&ia=web"]
    assert requests == [
        "https://html.duckduckgo.com/html/?q=rocky+agent",
        "https://duckduckgo.com/html/?q=rocky+agent",
        "https://html.duckduckgo.com/html/?q=rocky+agent&ia=web",
    ]


def test_search_web_falls_back_to_brave_after_duckduckgo_challenges(tmp_path: Path, monkeypatch) -> None:
    ctx = _tool_context(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host in {"html.duckduckgo.com", "duckduckgo.com", "lite.duckduckgo.com"}:
            return httpx.Response(
                202,
                request=request,
                headers={"content-type": "text/html; charset=UTF-8"},
                text="""
                <html>
                  <body>
                    <p>Unfortunately, bots use DuckDuckGo too.</p>
                    <p>Please complete the following challenge.</p>
                  </body>
                </html>
                """,
            )
        if str(request.url) == "https://search.brave.com/search?q=rocky+agent":
            return httpx.Response(
                200,
                request=request,
                headers={"content-type": "text/html; charset=UTF-8"},
                text="""
                <html>
                  <body>
                    <div class="snippet" data-type="web">
                      <a class="l1" href="https://example.com/brave-docs">
                        <div class="title search-snippet-title">Brave result</div>
                      </a>
                      <div class="generic-snippet">
                        <div class="content">Brave summary</div>
                      </div>
                    </div>
                  </body>
                </html>
                """,
            )
        raise AssertionError(f"Unexpected request URL: {request.url}")

    _install_mock_client(monkeypatch, handler)

    result = web.search_web(ctx, {"query": "rocky agent", "max_results": 3})

    assert result.success is True
    assert result.metadata["engine"] == "brave"
    assert result.metadata["attempted_urls"] == [
        "https://html.duckduckgo.com/html/?q=rocky+agent",
        "https://duckduckgo.com/html/?q=rocky+agent",
        "https://lite.duckduckgo.com/lite/?q=rocky+agent",
        "https://search.brave.com/search?q=rocky+agent",
    ]
    assert result.data == [
        {
            "title": "Brave result",
            "url": "https://example.com/brave-docs",
            "snippet": "Brave summary",
        }
    ]


def test_search_web_returns_empty_results_for_explicit_no_results_page(tmp_path: Path, monkeypatch) -> None:
    ctx = _tool_context(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "text/html; charset=UTF-8"},
            text="<html><body><p>No results found.</p></body></html>",
        )

    _install_mock_client(monkeypatch, handler)

    result = web.search_web(ctx, {"query": "rocky nowhere result", "max_results": 3})

    assert result.success is True
    assert result.data == []
    assert result.summary == "Search returned 0 result(s)"


def test_fetch_url_returns_normalized_http_failure(tmp_path: Path, monkeypatch) -> None:
    ctx = _tool_context(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, request=request, text="missing")

    _install_mock_client(monkeypatch, handler)

    result = web.fetch_url(ctx, {"url": "https://example.com/missing"})

    assert result.success is False
    assert result.data["status_code"] == 404
    assert result.metadata["transient"] is False
    assert "HTTP 404" in result.summary


def test_fetch_url_retries_transient_request_errors(tmp_path: Path, monkeypatch) -> None:
    ctx = _tool_context(tmp_path)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "text/html; charset=UTF-8"},
            text="<html><head><title>Recovered</title></head><body><a href='/ok'>ok</a></body></html>",
        )

    _install_mock_client(monkeypatch, handler)

    result = web.fetch_url(ctx, {"url": "https://example.com/retry"})

    assert result.success is True
    assert result.data["title"] == "Recovered"
    assert calls == 2


def test_fetch_url_rejects_bot_challenge_pages(tmp_path: Path, monkeypatch) -> None:
    ctx = _tool_context(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            202,
            request=request,
            headers={"content-type": "text/html; charset=UTF-8"},
            text="""
            <html>
              <head><title>Challenge</title></head>
              <body>
                <p>Please complete the following challenge.</p>
                <p>Verify you are human.</p>
              </body>
            </html>
            """,
        )

    _install_mock_client(monkeypatch, handler)

    result = web.fetch_url(ctx, {"url": "https://example.com/challenge"})

    assert result.success is False
    assert result.data["error"] == "anti-bot challenge"
    assert result.metadata["blocked_by_challenge"] is True
    assert "anti-bot challenge" in result.summary.lower()


def test_fetch_url_allows_normal_html_that_mentions_captcha_without_challenge_signals(tmp_path: Path, monkeypatch) -> None:
    ctx = _tool_context(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "text/html; charset=UTF-8"},
            text="""
            <html>
              <head><title>Captcha usability notes</title></head>
              <body>
                <article>
                  <p>This plain text article discusses captcha history and how web apps reduce false positives.</p>
                </article>
              </body>
            </html>
            """,
        )

    _install_mock_client(monkeypatch, handler)

    result = web.fetch_url(ctx, {"url": "https://example.com/article"})

    assert result.success is True
    assert result.data["title"] == "Captcha usability notes"
    assert "captcha history" in result.data["text_excerpt"].lower()


def test_fetch_url_falls_back_to_unverified_tls_on_certificate_errors(tmp_path: Path, monkeypatch) -> None:
    ctx = _tool_context(tmp_path)
    requests: list[tuple[bool, str]] = []

    def make_client(timeout_s: int = 20, *, verify: bool = True) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            requests.append((verify, str(request.url)))
            if verify:
                raise httpx.ConnectError(
                    "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed",
                    request=request,
                )
            return httpx.Response(
                200,
                request=request,
                headers={"content-type": "text/html; charset=UTF-8"},
                text="<html><head><title>Recovered TLS</title></head><body><a href='/ok'>ok</a></body></html>",
            )

        return httpx.Client(
            transport=httpx.MockTransport(handler),
            headers=web.DEFAULT_HEADERS,
            timeout=timeout_s,
            verify=verify,
            trust_env=False,
        )

    monkeypatch.setattr(web, "_client", make_client)

    result = web.fetch_url(ctx, {"url": "https://example.com/tls"})

    assert result.success is True
    assert result.data["title"] == "Recovered TLS"
    assert result.metadata["tls_verified"] is False
    assert requests == [
        (True, "https://example.com/tls"),
        (True, "https://example.com/tls"),
        (False, "https://example.com/tls"),
    ]


def test_client_uses_explicit_rocky_tool_proxy_env_var(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_client(**kwargs):
        seen.update(kwargs)
        class _DummyClient:
            pass
        return _DummyClient()

    monkeypatch.setenv(TOOL_PROXY_ENV_VAR, "http://proxy.internal:8080")
    monkeypatch.setattr(web.httpx, "Client", fake_client)

    client = web._client(15)

    assert isinstance(client, object)
    assert seen["timeout"] == 15
    assert seen["proxy"] == "http://proxy.internal:8080"
    assert seen["trust_env"] is False




def test_fetch_url_follows_redirects_and_filters_links(tmp_path: Path, monkeypatch) -> None:
    ctx = _tool_context(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://start.example/page":
            return httpx.Response(
                302,
                request=request,
                headers={"location": "https://end.example/docs"},
            )
        if str(request.url) == "https://end.example/docs":
            return httpx.Response(
                200,
                request=request,
                headers={"content-type": "text/html; charset=UTF-8"},
                text="""
                <html>
                  <head><title>Docs</title></head>
                  <body>
                    <a href="/getting-started">Start</a>
                    <a href="/getting-started">Start again</a>
                    <a href="https://example.com/guide">Guide</a>
                    <a href="mailto:hello@example.com">Email</a>
                  </body>
                </html>
                """,
            )
        raise AssertionError(f"Unexpected request URL: {request.url}")

    _install_mock_client(monkeypatch, handler)

    result = web.fetch_url(ctx, {"url": "https://start.example/page"})

    assert result.success is True
    assert result.data["url"] == "https://end.example/docs"
    assert result.data["title"] == "Docs"
    assert result.data["links"] == [
        "https://end.example/getting-started",
        "https://example.com/guide",
    ]
    assert result.metadata["redirected"] is True
    assert result.metadata["redirect_chain"] == ["https://end.example/docs"]


def test_fetch_url_prioritizes_main_content_links_over_navigation(tmp_path: Path, monkeypatch) -> None:
    ctx = _tool_context(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "text/html; charset=UTF-8"},
            text="""
            <html>
              <head><title>Trending</title></head>
              <body>
                <header>
                  <nav>
                    <a href="/login">Login</a>
                    <a href="/models">Models</a>
                  </nav>
                </header>
                <main>
                  <a href="/Qwen/Qwen3-8B">Qwen3-8B</a>
                  <a href="/meta-llama/Llama-3.2-3B-Instruct">Llama-3.2-3B-Instruct</a>
                </main>
                <footer>
                  <a href="/pricing">Pricing</a>
                </footer>
              </body>
            </html>
            """,
        )

    _install_mock_client(monkeypatch, handler)

    result = web.fetch_url(ctx, {"url": "https://huggingface.co/models?sort=trending"})

    assert result.success is True
    assert result.data["link_items"][:2] == [
        {"text": "Qwen3-8B", "url": "https://huggingface.co/Qwen/Qwen3-8B"},
        {"text": "Llama-3.2-3B-Instruct", "url": "https://huggingface.co/meta-llama/Llama-3.2-3B-Instruct"},
    ]
    assert "https://huggingface.co/login" not in result.data["links"]
    assert "https://huggingface.co/pricing" not in result.data["links"]


def test_extract_links_normalizes_relative_and_duckduckgo_redirect_urls(tmp_path: Path, monkeypatch) -> None:
    ctx = _tool_context(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "text/html; charset=UTF-8"},
            text="""
            <html>
              <body>
                <a href="/local">Local page</a>
                <a href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fstory">Story</a>
                <a href="/local">Duplicate</a>
                <a href="javascript:void(0)">Ignore me</a>
              </body>
            </html>
            """,
        )

    _install_mock_client(monkeypatch, handler)

    result = web.extract_links(ctx, {"url": "https://page.example/search", "max_links": 5})

    assert result.success is True
    assert result.data == [
        {"text": "Local page", "url": "https://page.example/local"},
        {"text": "Story", "url": "https://example.com/story"},
    ]


def test_extract_links_prioritizes_main_content_items_over_navigation(tmp_path: Path, monkeypatch) -> None:
    ctx = _tool_context(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "text/html; charset=UTF-8"},
            text="""
            <html>
              <body>
                <nav>
                  <a href="/docs">Docs</a>
                  <a href="/enterprise">Enterprise</a>
                </nav>
                <main>
                  <a href="/google/gemma-3-4b-it">Gemma-3-4B-It</a>
                  <a href="/Qwen/Qwen2.5-7B-Instruct">Qwen2.5-7B-Instruct</a>
                </main>
              </body>
            </html>
            """,
        )

    _install_mock_client(monkeypatch, handler)

    result = web.extract_links(ctx, {"url": "https://huggingface.co/models", "max_links": 4})

    assert result.success is True
    assert result.data[:2] == [
        {"text": "Gemma-3-4B-It", "url": "https://huggingface.co/google/gemma-3-4b-it"},
        {"text": "Qwen2.5-7B-Instruct", "url": "https://huggingface.co/Qwen/Qwen2.5-7B-Instruct"},
    ]
