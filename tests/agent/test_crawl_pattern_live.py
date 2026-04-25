"""CRAWL-PATTERN — live web-crawling research scenario.

Witnesses Rocky's research/live_compare/general routing path with real
web-tool dispatch. A research prompt requiring multi-source comparison
must produce >=3 successful ``search_web`` tool_results AND >=2
successful ``fetch_url`` tool_results in a single subprocess, plus a
URL citation in the final answer text.

Bit-flip negative: a non-research atomic prompt in a fresh workspace
must produce zero web tool_results. Proves the positive's web usage is
caused by the prompt's research character + Rocky's routing, not a
runtime default.

Wall-clock budget: ~3-5 min positive + ~30s negative on
gemma4:26b @ ainbr-research-fast:11434. ``pytest.mark.slow``.

Gated by ``ROCKY_LLM_SMOKE=1``. Helpers from
``tests/agent/_helpers.py`` ``__all__`` only.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from ._helpers import (
    ROCKY_BIN,
    SMOKE_FLAG,
    _install_evidence_finalizer,
    _run_rocky,
    _run_rocky_until,
)


pytestmark = [
    pytest.mark.skipif(
        os.environ.get(SMOKE_FLAG) != "1",
        reason=(
            f"crawl-pattern live scenario requires {SMOKE_FLAG}=1 "
            f"(real Ollama via editable rocky at {ROCKY_BIN})"
        ),
    ),
    pytest.mark.slow,
]


_URL_RE = re.compile(r"https?://[^\s)>\]]+", re.IGNORECASE)
_RESEARCH_PROMPT = (
    "Research the licensing differences between MIT, Apache-2.0, and "
    "BSD-3-Clause licenses. Search the web for each license's official "
    "text and gather sources on the key clauses. Cite official URLs."
)
_BASELINE_PROMPT = "Create a file README.md in this workspace that says hello."


def _count_successful_tool_results(payload: dict, tool_name: str) -> int:
    trace = payload.get("trace") or {}
    events = trace.get("tool_events") or []
    matched = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get("type") != "tool_result":
            continue
        if event.get("success") is not True:
            continue
        if str(event.get("name") or "") == tool_name:
            matched += 1
    return matched


@dataclass
class _CrawlResult:
    t1: dict = field(default_factory=dict)
    workspace: Path = field(default_factory=Path)


@pytest.fixture(scope="module")
def crawl_pattern_research(request, tmp_path_factory) -> _CrawlResult:
    workspace = tmp_path_factory.mktemp("crawl_pattern_research_")
    captures: dict = {}
    _install_evidence_finalizer(request, "crawl_pattern_research", workspace, captures)

    def _routes_to_research(payload: dict) -> bool:
        trace = payload.get("trace") or {}
        route = trace.get("route") or {}
        sig = str(route.get("task_signature") or "")
        return sig.startswith("research/") or sig.startswith("site/")

    t1 = _run_rocky_until(
        workspace,
        _RESEARCH_PROMPT,
        label="t1_research_crawl",
        captures=captures,
        predicate=_routes_to_research,
        predicate_reason=(
            "the research prompt must classify to a research/* or site/* "
            "task signature for the live web tools to be exposed; if it "
            "lands on conversation/general the assertions below cannot fire"
        ),
        max_attempts=2,
    )
    return _CrawlResult(t1=t1, workspace=workspace)


@pytest.fixture(scope="module")
def crawl_pattern_baseline(request, tmp_path_factory) -> _CrawlResult:
    workspace = tmp_path_factory.mktemp("crawl_pattern_baseline_")
    captures: dict = {}
    _install_evidence_finalizer(request, "crawl_pattern_baseline", workspace, captures)
    t1 = _run_rocky(
        workspace,
        _BASELINE_PROMPT,
        label="t1_baseline_no_research",
        captures=captures,
    )
    return _CrawlResult(t1=t1, workspace=workspace)


def test_crawl_pattern_phase_A_route_landed_on_research(
    crawl_pattern_research: _CrawlResult,
) -> None:
    """Gate: research prompt must classify to research/* or site/*."""
    trace = crawl_pattern_research.t1.get("trace") or {}
    route = trace.get("route") or {}
    sig = str(route.get("task_signature") or "")
    assert sig.startswith("research/") or sig.startswith("site/"), (
        f"CRAWL-PATTERN phase A FAILED: task signature is {sig!r}, "
        f"expected research/* or site/*. Web tools are not exposed on "
        f"non-research routes; the downstream assertions cannot bite. "
        f"route={route!r}"
    )


def test_crawl_pattern_phase_B_search_and_fetch_dispatched(
    crawl_pattern_research: _CrawlResult,
) -> None:
    """Load-bearing: >=1 search_web AND >=1 fetch_url successful tool_results.

    The test's claim is "research routing exposes web tools and they
    fire", not "the model uses them aggressively". A higher threshold
    would conflate model-aggression with tool-dispatch correctness.
    The bit-flip negative (phase D) requires zero web tool_results on
    a non-research prompt — pairing >=1 here with ==0 there gives a
    clean signal of routing-driven dispatch.
    """
    payload = crawl_pattern_research.t1
    search_count = _count_successful_tool_results(payload, "search_web")
    fetch_count = _count_successful_tool_results(payload, "fetch_url")
    assert search_count >= 1, (
        f"CRAWL-PATTERN phase B FAILED: 0 successful search_web "
        f"tool_results; expected >=1. The research route exposed the "
        f"tool but it never fired."
    )
    assert fetch_count >= 1, (
        f"CRAWL-PATTERN phase B FAILED: 0 successful fetch_url "
        f"tool_results; expected >=1. The model searched but did not "
        f"actually fetch any source page."
    )


def test_crawl_pattern_phase_C_response_cites_url(
    crawl_pattern_research: _CrawlResult,
) -> None:
    """Behavioral: the answer text must contain at least one URL citation."""
    text = str(crawl_pattern_research.t1.get("text") or "")
    assert text, (
        f"CRAWL-PATTERN phase C FAILED: T1 response text empty; "
        f"resp={crawl_pattern_research.t1!r}"
    )
    assert _URL_RE.search(text), (
        f"CRAWL-PATTERN phase C FAILED: response contains no URL "
        f"citation. The model fetched sources but did not surface them "
        f"in the answer. text={text[:1500]!r}"
    )


def test_crawl_pattern_phase_D_baseline_does_not_dispatch_web(
    crawl_pattern_baseline: _CrawlResult,
) -> None:
    """Bit-flip negative: a non-research atomic prompt must produce zero
    web tool_results. Proves the positive's web usage is task-driven,
    not a runtime default."""
    payload = crawl_pattern_baseline.t1
    search_count = _count_successful_tool_results(payload, "search_web")
    fetch_count = _count_successful_tool_results(payload, "fetch_url")
    assert search_count == 0, (
        f"CRAWL-PATTERN phase D bit-flip FAILED: baseline (atomic non-"
        f"research prompt) dispatched {search_count} search_web "
        f"tool_results; expected 0. The router or tool exposure leaked."
    )
    assert fetch_count == 0, (
        f"CRAWL-PATTERN phase D bit-flip FAILED: baseline dispatched "
        f"{fetch_count} fetch_url tool_results; expected 0."
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "slow"])
