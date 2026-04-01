from __future__ import annotations

from typing import Any
from urllib.parse import urlencode, urljoin

import httpx
from bs4 import BeautifulSoup

from rocky.tools.base import Tool, ToolContext, ToolResult
from rocky.util.text import truncate


USER_AGENT = 'Rocky/0.1 (+https://local.rocky)'


def _client(timeout_s: int = 20) -> httpx.Client:
    return httpx.Client(timeout=timeout_s, headers={'User-Agent': USER_AGENT})


def fetch_url(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    url = str(args['url'])
    ctx.require('web', 'fetch url', url, risky=True)
    with _client(int(args.get('timeout_s', 20))) as client:
        response = client.get(url, follow_redirects=True)
        response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    title = soup.title.string.strip() if soup.title and soup.title.string else ''
    text = ' '.join(soup.get_text(' ', strip=True).split())
    links = [a.get('href') for a in soup.find_all('a', href=True)[:20]]
    return ToolResult(True, {
        'url': str(response.url),
        'status_code': response.status_code,
        'title': title,
        'text_excerpt': truncate(text, 4000),
        'links': links,
    }, f'Fetched {response.url}')


def extract_links(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    url = str(args['url'])
    ctx.require('web', 'extract links', url, risky=True)
    with _client(int(args.get('timeout_s', 20))) as client:
        response = client.get(url, follow_redirects=True)
        response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    links: list[dict[str, Any]] = []
    for a in soup.find_all('a', href=True)[: int(args.get('max_links', 50))]:
        links.append({'text': a.get_text(' ', strip=True)[:120], 'url': urljoin(str(response.url), a['href'])})
    return ToolResult(True, links, f'Extracted {len(links)} link(s)')


def search_web(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    query = str(args['query'])
    ctx.require('web', 'search web', query, risky=True)
    params = {'q': query}
    url = f'https://duckduckgo.com/html/?{urlencode(params)}'
    with _client(int(args.get('timeout_s', 20))) as client:
        response = client.get(url)
        response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    results: list[dict[str, Any]] = []
    for result in soup.select('.result')[: int(args.get('max_results', 8))]:
        title_el = result.select_one('.result__title')
        link_el = result.select_one('.result__url') or result.select_one('.result__title a')
        snippet_el = result.select_one('.result__snippet')
        href = None
        if result.select_one('.result__title a'):
            href = result.select_one('.result__title a').get('href')
        elif link_el:
            href = link_el.get_text(' ', strip=True)
        results.append({
            'title': title_el.get_text(' ', strip=True) if title_el else '',
            'url': href,
            'snippet': snippet_el.get_text(' ', strip=True) if snippet_el else '',
        })
    return ToolResult(True, results, f'Search returned {len(results)} result(s)')


def tools() -> list[Tool]:
    return [
        Tool('fetch_url', 'Fetch a URL and extract title/text', {'type': 'object', 'properties': {'url': {'type': 'string'}, 'timeout_s': {'type': 'integer'}}, 'required': ['url']}, 'web', fetch_url),
        Tool('search_web', 'Search the web with a lightweight HTML fallback', {'type': 'object', 'properties': {'query': {'type': 'string'}, 'max_results': {'type': 'integer'}, 'timeout_s': {'type': 'integer'}}, 'required': ['query']}, 'web', search_web),
        Tool('extract_links', 'Extract links from a web page', {'type': 'object', 'properties': {'url': {'type': 'string'}, 'max_links': {'type': 'integer'}, 'timeout_s': {'type': 'integer'}}, 'required': ['url']}, 'web', extract_links),
    ]
