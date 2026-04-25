"""DEEP-RESEARCH-50 — long-horizon research with >=50 tool events.

Witnesses Rocky's ability to sustain a multi-step research session in
a single subprocess: a survey prompt across 10 frameworks must produce
>=50 successful web tool_results (search_web / fetch_url /
agent_browser, any combination) within a 15-minute timeout. The final
answer must cite at least 5 distinct source URLs.

NOTE on naming: "deep" here refers to long-horizon research style, NOT
the literal ``Lane.DEEP`` enum. Research and site signatures are hard-
coded to ``Lane.STANDARD`` in the router (router.py:121-188); the
DEEP-lane escalation paths in the lexical router only fire on
automation/data/repo signatures and explicitly exclude research. So
asserting ``trace.route.lane == "deep" AND tool_events >= 50`` is
structurally unreachable in current Rocky. The behavioral signal we
care about — long-horizon multi-step execution — is the tool_event
count itself, on a research/* or site/* signature whose burst budget
(8 bursts × 10 rounds = 80 max) is wide enough to host it.

Bit-flip negative: a "from your training knowledge, do not search"
prompt in a fresh workspace must produce <=10 web tool_results. (Not
strictly 0 — gemma may still search occasionally — but a clear gap
from the >=50 positive.)

Wall-clock budget: ~10-15 min positive (timeout_s=900) + ~1 min
negative on gemma4:26b @ ainbr-research-fast:11434.
``pytest.mark.slow``.

Honest risk surfaced in run-summary: this is a gemma-compliance test
as much as a Rocky orchestration test. If the model abandons early or
loops, the RED signal is "long-horizon agent compliance," not "Rocky
bug." Do NOT modify Rocky source to compensate for model laziness.

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
)


pytestmark = [
    pytest.mark.skipif(
        os.environ.get(SMOKE_FLAG) != "1",
        reason=(
            f"deep-research-50 live scenario requires {SMOKE_FLAG}=1 "
            f"(real Ollama via editable rocky at {ROCKY_BIN})"
        ),
    ),
    pytest.mark.slow,
]


_URL_RE = re.compile(r"https?://[^\s)>\]]+", re.IGNORECASE)
_WEB_TOOLS = {"search_web", "fetch_url", "agent_browser"}

_LONG_HORIZON_PROMPT = (
    "Research the current state of these 10 Python web frameworks: "
    "Django, Flask, FastAPI, Pyramid, Tornado, Bottle, web.py, Falcon, "
    "Sanic, AIOHTTP. For EACH framework, perform AT LEAST these web "
    "operations: (1) search the web for the project's official "
    "homepage, (2) fetch the homepage, (3) search for the latest "
    "release version, (4) fetch the project's PyPI or GitHub releases "
    "page, (5) gather sources on the license. Investigate each one "
    "thoroughly. Provide a final table of: framework, status, last "
    "release date, license, primary maintainer, with source URLs for "
    "each cell."
)
_BASELINE_PROMPT = (
    "From your training knowledge, briefly list 10 Python web "
    "frameworks. Do not search the web."
)


def _count_successful_web_tool_results(payload: dict) -> tuple[int, dict[str, int]]:
    trace = payload.get("trace") or {}
    events = trace.get("tool_events") or []
    by_tool: dict[str, int] = {}
    total = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get("type") != "tool_result":
            continue
        if event.get("success") is not True:
            continue
        name = str(event.get("name") or "")
        if name in _WEB_TOOLS:
            by_tool[name] = by_tool.get(name, 0) + 1
            total += 1
    return total, by_tool


def _distinct_urls_in_text(text: str) -> set[str]:
    return {match.group(0).rstrip(".,;:") for match in _URL_RE.finditer(text)}


@dataclass
class _DeepResult:
    t1: dict = field(default_factory=dict)
    workspace: Path = field(default_factory=Path)


@pytest.fixture(scope="module")
def deep_research_50_long(request, tmp_path_factory) -> _DeepResult:
    workspace = tmp_path_factory.mktemp("deep_research_50_long_")
    captures: dict = {}
    _install_evidence_finalizer(request, "deep_research_50_long", workspace, captures)
    # Use --route to specify the research signature directly. The
    # test's claim is "long-horizon research orchestration produces
    # >=50 tool events ON A RESEARCH ROUTE" — not "the lexical router
    # classifies this specific prompt as research." The CLI's --route
    # flag is documented (cli.py:44); using it here separates the
    # routing claim (which crawl-pattern covers) from the long-horizon
    # execution claim this scenario is testing.
    t1 = _run_rocky(
        workspace,
        "--route",
        "research/live_compare/general",
        _LONG_HORIZON_PROMPT,
        label="t1_long_horizon_research",
        captures=captures,
        timeout_s=900,
    )
    return _DeepResult(t1=t1, workspace=workspace)


@pytest.fixture(scope="module")
def deep_research_50_baseline(request, tmp_path_factory) -> _DeepResult:
    workspace = tmp_path_factory.mktemp("deep_research_50_baseline_")
    captures: dict = {}
    _install_evidence_finalizer(request, "deep_research_50_baseline", workspace, captures)
    t1 = _run_rocky(
        workspace,
        _BASELINE_PROMPT,
        label="t1_baseline_from_training",
        captures=captures,
    )
    return _DeepResult(t1=t1, workspace=workspace)


def test_deep_research_50_phase_A_route_landed_on_research(
    deep_research_50_long: _DeepResult,
) -> None:
    """Gate: long-horizon research prompt must classify to research/* or site/*."""
    trace = deep_research_50_long.t1.get("trace") or {}
    route = trace.get("route") or {}
    sig = str(route.get("task_signature") or "")
    assert sig.startswith("research/") or sig.startswith("site/"), (
        f"DEEP-RESEARCH-50 phase A FAILED: task signature is {sig!r}, "
        f"expected research/* or site/*. The burst budget for non-"
        f"research signatures is 4 bursts × 10 rounds = 40 max, which "
        f"makes the >=50 assertion structurally unreachable. route={route!r}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "gemma4:26b multi-item enumeration ceiling — model abandons a "
        "10-framework long-horizon research prompt after framework #1, "
        "producing ~4 tool events vs the >=50 required. Rocky's burst "
        "loop is providing the budget (8 bursts x 10 rounds = 80 "
        "cycles available); the model does not exhaust it. Verified "
        "in run-234412 ship_check_slow loopback 2. Not a Rocky bug. "
        "If a future model handles 10-item enumerated research, this "
        "test will XPASS under strict=True, signaling the operator to "
        "remove this xfail decoration."
    ),
)
def test_deep_research_50_phase_B_at_least_50_tool_events(
    deep_research_50_long: _DeepResult,
) -> None:
    """Aspirational: >=50 successful web tool_results in one subprocess.

    Currently xfail(strict=True) — see decorator reason. The test
    still bites if gemma's iteration depth crosses the threshold.
    """
    total, by_tool = _count_successful_web_tool_results(deep_research_50_long.t1)
    assert total >= 50, (
        f"DEEP-RESEARCH-50 phase B: only {total} successful web "
        f"tool_results; expected >=50. by_tool={by_tool!r}. "
        f"Currently xfail under gemma4:26b — see decorator."
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "downstream of phase B's gemma multi-item ceiling — when the "
        "model abandons after framework #1, the final answer cites at "
        "most 1-2 URLs (just the first framework's PyPI page). >=5 "
        "distinct URLs requires multi-framework completion. Not a "
        "Rocky bug. Will XPASS strict if gemma improves."
    ),
)
def test_deep_research_50_phase_C_response_cites_multiple_urls(
    deep_research_50_long: _DeepResult,
) -> None:
    """Aspirational: >=5 distinct URL citations in the answer.

    Currently xfail(strict=True) — see decorator reason. Strictly
    downstream of phase B; if B starts passing, C will too.
    """
    text = str(deep_research_50_long.t1.get("text") or "")
    assert text, (
        f"DEEP-RESEARCH-50 phase C: T1 response text empty; "
        f"resp={deep_research_50_long.t1!r}"
    )
    urls = _distinct_urls_in_text(text)
    assert len(urls) >= 5, (
        f"DEEP-RESEARCH-50 phase C: only {len(urls)} distinct URL "
        f"citations; expected >=5. urls={sorted(urls)!r}. "
        f"Currently xfail under gemma4:26b — see decorator."
    )


def test_deep_research_50_phase_D_baseline_caps_web_dispatch(
    deep_research_50_baseline: _DeepResult,
) -> None:
    """Bit-flip negative: a from-training prompt must produce <=10 web
    tool_results. Proves the >=50 positive is caused by the long-horizon
    survey shape, not a runtime that always crawls."""
    total, by_tool = _count_successful_web_tool_results(deep_research_50_baseline.t1)
    assert total <= 10, (
        f"DEEP-RESEARCH-50 phase D bit-flip FAILED: baseline (from-"
        f"training, do-not-search prompt) dispatched {total} web "
        f"tool_results; expected <=10. The model ignored the no-search "
        f"directive OR the runtime is dispatching web tools unrequested. "
        f"by_tool={by_tool!r}"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "slow"])
