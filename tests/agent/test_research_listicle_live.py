"""Live proof that fetch_url excerpts can exceed the legacy 4000-char cap.

Generic end-to-end counterpart to the deterministic proofs in
``tests/test_web_tools.py`` (`test_fetch_url_excerpt_respects_configured_output_cap`
et al.). This test runs the real ``rocky`` CLI against a live LLM and
a test-owned local HTTP server, then asserts a behavioral property:
at least one ``fetch_url`` tool result in the run carries
``text_excerpt`` content longer than the legacy 4000-char ceiling.

The proof is structural — no assertion about topic, URL, or answer
quality. If the fix (`_response_text_excerpt` honoring
``ctx.config.tools.max_tool_output_chars``) ever regresses to the
hard-coded 4000 cap, every excerpt stays at or below it and this test
bites.

Why a local server, not a real site: public sites (Wikipedia, review
blogs) rate-limit or 403 Rocky's User-Agent variably — that's noise
for this assertion. Owning the content makes the test deterministic
in its INPUT while still exercising the full CLI + real LLM path.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from ._helpers import SMOKE_FLAG, _run_rocky

pytestmark = pytest.mark.skipif(
    not bool(int(os.environ.get(SMOKE_FLAG, "0") or "0")),
    reason="live-LLM smoke; set ROCKY_LLM_SMOKE=1 to enable.",
)


LEGACY_CAP = 4000
# Actual old-cap output length can slightly exceed LEGACY_CAP because
# ``util.text.truncate`` appends a ``[rocky-truncated: N chars omitted]``
# suffix (~40 chars) after cutting to ``limit - 32``. Use a generous
# sensitivity threshold that sits clearly above that inflated old ceiling
# and clearly below the new-cap output for the test fixture's article.
LEGACY_CAP_WITH_SUFFIX_HEADROOM = 5000


def _build_long_article_html() -> bytes:
    body_paragraphs = "\n".join(
        f"<p>Paragraph {i:03d}: this is generic article filler about research "
        f"topics, long-form writeups, and the kind of listicle content readers "
        f"want to see past the first screen of any review page. Filler "
        f"filler filler filler filler filler filler filler filler filler.</p>"
        for i in range(80)
    )
    return (
        "<!doctype html><html><head><title>Long article</title></head>"
        "<body><main><article>"
        f"{body_paragraphs}"
        "<p>END_MARKER_SENTINEL</p>"
        "</article></main></body></html>"
    ).encode("utf-8")


class _ArticleHandler(BaseHTTPRequestHandler):
    _payload: bytes = b""

    def do_GET(self) -> None:  # noqa: N802 — http.server API
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(self._payload)))
        self.end_headers()
        self.wfile.write(self._payload)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return  # silence stderr during pytest


@pytest.fixture
def local_long_article_url():
    payload = _build_long_article_html()

    class _Handler(_ArticleHandler):
        pass

    _Handler._payload = payload
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}/article"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


_EXCERPT_MARKER = '"text_excerpt": "'


def _extract_text_excerpt(raw: str) -> str | None:
    """Pull ``data.text_excerpt`` out of a tool-output string.

    Tries strict JSON first, then falls back to a defensive scan for the
    ``"text_excerpt": "..."`` region. The fallback exists because
    ``ToolResult.as_text(limit=...)`` in ``src/rocky/tools/base.py`` naively
    truncates the serialized payload at ``limit`` characters and appends
    ``"\\n... [truncated]"`` — which can cut the excerpt string mid-value
    and yield invalid JSON for any tool with a large output. That is a
    separate, pre-existing correctness issue (tracked as residual risk for
    this run); the assertion we care about here is that the excerpt length
    exceeded the legacy 4000-char cap, which is observable regardless.
    """
    raw = raw or ""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        pass
    else:
        data = payload.get("data") or {}
        excerpt = data.get("text_excerpt")
        return excerpt if isinstance(excerpt, str) else None
    idx = raw.find(_EXCERPT_MARKER)
    if idx < 0:
        return None
    start = idx + len(_EXCERPT_MARKER)
    cursor = start
    n = len(raw)
    while cursor < n:
        ch = raw[cursor]
        if ch == "\\":
            cursor += 2
            continue
        if ch == '"':
            break
        cursor += 1
    return raw[start:cursor]


def _fetch_excerpt_lengths(payload: dict) -> list[int]:
    tool_events = (payload.get("trace") or {}).get("tool_events") or []
    lengths: list[int] = []
    for event in tool_events:
        if not isinstance(event, dict):
            continue
        if event.get("type") != "tool_result":
            continue
        if str(event.get("name") or "") != "fetch_url":
            continue
        raw_ref = event.get("raw_ref") or ""
        if raw_ref and Path(raw_ref).exists():
            try:
                raw = Path(raw_ref).read_text(encoding="utf-8")
            except OSError:
                raw = ""
        else:
            raw = event.get("raw_text") or ""
        excerpt = _extract_text_excerpt(raw)
        if isinstance(excerpt, str):
            lengths.append(len(excerpt))
    return lengths


def test_listicle_fetch_excerpt_exceeds_legacy_cap(
    tmp_path: Path, local_long_article_url: str
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    captures: dict = {}
    payload = _run_rocky(
        workspace,
        f"Use fetch_url to fetch {local_long_article_url} and summarize "
        "what the article contains.",
        label="listicle_fetch",
        captures=captures,
    )
    lengths = _fetch_excerpt_lengths(payload)
    if not lengths:
        tool_events = (payload.get("trace") or {}).get("tool_events") or []
        diag = []
        for i, ev in enumerate(tool_events):
            if not isinstance(ev, dict):
                continue
            if ev.get("name") != "fetch_url" or ev.get("type") != "tool_result":
                continue
            raw_ref = ev.get("raw_ref") or ""
            diag.append({
                "i": i,
                "keys": sorted(ev.keys()),
                "raw_text_len": len(ev.get("raw_text") or ""),
                "raw_ref": raw_ref,
                "raw_ref_exists": bool(raw_ref) and Path(raw_ref).exists(),
                "fact_kinds": [f.get("kind") for f in (ev.get("facts") or []) if isinstance(f, dict)],
            })
        pytest.fail(
            "expected at least one fetch_url tool_result with a parseable "
            f"text_excerpt; tool_events_total={len(tool_events)}; "
            f"fetch_url_result_diag={diag}"
        )
    assert max(lengths) > LEGACY_CAP_WITH_SUFFIX_HEADROOM, (
        f"every fetch_url excerpt stayed at or below the legacy "
        f"{LEGACY_CAP}-char cap + truncation suffix "
        f"(lengths={lengths}; threshold={LEGACY_CAP_WITH_SUFFIX_HEADROOM}). "
        "The fix makes content past the old ceiling visible — "
        "regression suspected."
    )
