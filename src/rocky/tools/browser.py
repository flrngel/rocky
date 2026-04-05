from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from rocky.tools.base import Tool, ToolContext, ToolResult
from rocky.tools.proxy_support import tool_proxy_url
from rocky.util.text import truncate


def _browser_launch_options() -> dict[str, Any]:
    options: dict[str, Any] = {'headless': True}
    if proxy := tool_proxy_url():
        options['proxy'] = {'server': proxy}
    return options


async def _render(url: str, timeout_ms: int) -> dict[str, Any]:
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:  # pragma: no cover
        raise RuntimeError('Playwright is not available. Install the package and run `playwright install`.') from exc
    async with async_playwright() as p:  # pragma: no cover - depends on browser runtime
        browser = await p.chromium.launch(**_browser_launch_options())
        page = await browser.new_page()
        await page.goto(url, wait_until='networkidle', timeout=timeout_ms)
        title = await page.title()
        text = await page.locator('body').inner_text(timeout=5000)
        final_url = page.url
        html = await page.content()
        await browser.close()
    return {'title': title, 'final_url': final_url, 'text': text, 'html': html}


async def _screenshot(url: str, output_path: Path, timeout_ms: int, full_page: bool) -> dict[str, Any]:
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:  # pragma: no cover
        raise RuntimeError('Playwright is not available. Install the package and run `playwright install`.') from exc
    async with async_playwright() as p:  # pragma: no cover
        browser = await p.chromium.launch(**_browser_launch_options())
        page = await browser.new_page(viewport={'width': 1440, 'height': 900})
        await page.goto(url, wait_until='networkidle', timeout=timeout_ms)
        await page.screenshot(path=str(output_path), full_page=full_page)
        final_url = page.url
        title = await page.title()
        await browser.close()
    return {'title': title, 'final_url': final_url, 'path': str(output_path)}


def browser_render_page(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    url = str(args['url'])
    timeout_ms = int(args.get('timeout_ms', 20000))
    ctx.require('browser', 'render page', url, risky=True)
    try:
        data = asyncio.run(_render(url, timeout_ms))
        return ToolResult(True, {
            'title': data['title'],
            'final_url': data['final_url'],
            'text_excerpt': truncate(data['text'], 5000),
            'html_excerpt': truncate(data['html'], 3000),
        }, f'Rendered {data['final_url']}')
    except Exception as exc:
        return ToolResult(False, {}, str(exc))


def browser_screenshot(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    url = str(args['url'])
    timeout_ms = int(args.get('timeout_ms', 20000))
    full_page = bool(args.get('full_page', True))
    output_dir = ctx.artifacts_dir / 'browser'
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / (args.get('filename') or 'screenshot.png')
    ctx.require('browser', 'screenshot page', url, writes=True, risky=True)
    try:
        data = asyncio.run(_screenshot(url, output_path, timeout_ms, full_page))
        return ToolResult(True, data, f'Saved screenshot to {output_path.name}')
    except Exception as exc:
        return ToolResult(False, {}, str(exc))


def tools() -> list[Tool]:
    return [
        Tool('browser_render_page', 'Render a page in a headless browser and extract text', {'type': 'object', 'properties': {'url': {'type': 'string'}, 'timeout_ms': {'type': 'integer'}}, 'required': ['url']}, 'browser', browser_render_page),
        Tool('browser_screenshot', 'Take a browser screenshot of a page', {'type': 'object', 'properties': {'url': {'type': 'string'}, 'filename': {'type': 'string'}, 'timeout_ms': {'type': 'integer'}, 'full_page': {'type': 'boolean'}}, 'required': ['url']}, 'browser', browser_screenshot),
    ]
