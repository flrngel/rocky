Status: DONE
"""Tests for O6: semantic_research_v1 verifier.

Covers:
  1. Unsupported claim -> needs_review
  2. Grounded claims -> status stays pass
  3. default_v1 preserved in details
  4. CF-4 control: non-research route skips semantic
  5. Threshold tuning (0.1 < 0.5 -> pass; 0.6 > 0.5 -> needs_review)
  6. Config gate: semantic_enabled=False skips semantic
"""
import pytest

from rocky.config.models import AppConfig, VerifierConfig
from rocky.core.router import RouteDecision, Lane, TaskClass
from rocky.core.verifiers import VerifierRegistry


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _research_route(sig: str = "research/general") -> RouteDecision:
    return RouteDecision(
        lane=Lane.STANDARD,
        task_class=TaskClass.RESEARCH,
        risk="low",
        reasoning="research task",
        tool_families=["web"],
        task_signature=sig,
    )


def _repo_route() -> RouteDecision:
    return RouteDecision(
        lane=Lane.STANDARD,
        task_class=TaskClass.REPO,
        risk="low",
        reasoning="repo task",
        tool_families=["shell"],
        task_signature="repo/shell_execution",
    )


def _fetch_event(content: str) -> dict:
    """Simulate a successful fetch_url tool result."""
    return {
        "type": "tool_result",
        "name": "fetch_url",
        "success": True,
        "output": content,
        "content": content,
    }


def _with_citation(text: str) -> str:
    """Append a source URL so citation_hint_v1 does not short-circuit."""
    return text + " Sources: https://example.com/source"


def _make_config(semantic_enabled: bool = True, threshold: float = 0.5) -> AppConfig:
    cfg = AppConfig.default()
    cfg.verifier.semantic_enabled = semantic_enabled
    cfg.verifier.semantic_threshold = threshold
    return cfg


# ---------------------------------------------------------------------------
# Test 1: unsupported claim -> needs_review
# ---------------------------------------------------------------------------

def test_unsupported_claim_triggers_needs_review() -> None:
    """Answer mentions 'Widget Ltd' which is absent from fetched payloads."""
    verifier = VerifierRegistry()
    route = _research_route()
    tool_events = [_fetch_event("Acme Corp provides cloud services to enterprises.")]

    result = verifier.verify(
        prompt="tell me about the market leader in quantum networking",
        route=route,
        task_class=route.task_class,
        output=_with_citation("Widget Ltd is the market leader in quantum networking."),
        tool_events=tool_events,
        config=_make_config(),
    )

    assert result.status == "needs_review", f"Expected needs_review, got {result.status!r}: {result.message}"
    assert any("Widget Ltd" in c for c in result.unsupported_claim_ids), (
        f"'Widget Ltd' not in unsupported_claim_ids: {result.unsupported_claim_ids}"
    )


# ---------------------------------------------------------------------------
# Test 2: grounded claims -> pass
# ---------------------------------------------------------------------------

def test_grounded_claims_stay_pass() -> None:
    """Answer is directly supported by fetch payload; status must remain pass."""
    verifier = VerifierRegistry()
    route = _research_route()
    tool_events = [_fetch_event("Acme Corp is based in Palo Alto and leads cloud infrastructure.")]

    result = verifier.verify(
        prompt="where is Acme Corp based",
        route=route,
        task_class=route.task_class,
        output=_with_citation("Acme Corp is based in Palo Alto."),
        tool_events=tool_events,
        config=_make_config(),
    )

    assert result.status == "pass", f"Expected pass, got {result.status!r}: {result.message}"
    assert result.unsupported_claim_ids == [], (
        f"Expected no unsupported claims, got: {result.unsupported_claim_ids}"
    )


# ---------------------------------------------------------------------------
# Test 3: default_v1 preserved in details
# ---------------------------------------------------------------------------

def test_default_v1_preserved_in_details() -> None:
    """Merged result must carry default_v1's record inside details['default_v1']."""
    verifier = VerifierRegistry()
    route = _research_route()
    tool_events = [_fetch_event("Acme Corp provides cloud services.")]

    result = verifier.verify(
        prompt="tell me about Widget Ltd in quantum networking",
        route=route,
        task_class=route.task_class,
        output=_with_citation("Widget Ltd is the market leader in quantum networking."),
        tool_events=tool_events,
        config=_make_config(),
    )

    assert "default_v1" in result.details, (
        f"details does not contain 'default_v1'. keys={list(result.details.keys())}"
    )
    dv1 = result.details["default_v1"]
    assert "name" in dv1, f"default_v1 record missing 'name': {dv1}"
    assert "status" in dv1, f"default_v1 record missing 'status': {dv1}"
    assert "message" in dv1, f"default_v1 record missing 'message': {dv1}"


# ---------------------------------------------------------------------------
# Test 4: CF-4 control — non-research route skips semantic
# ---------------------------------------------------------------------------

def test_non_research_route_skips_semantic() -> None:
    """repo/shell_execution route with unsupported claims must not trigger needs_review."""
    verifier = VerifierRegistry()
    route = _repo_route()
    tool_events = [
        {
            "type": "tool_result",
            "name": "run_shell_command",
            "success": True,
            "output": "done",
            "content": "done",
        }
    ]

    result = verifier.verify(
        prompt="run echo hello",
        route=route,
        task_class=route.task_class,
        output="Widget Ltd is the leader in quantum networking and drives innovation.",
        tool_events=tool_events,
        config=_make_config(),
    )

    assert result.status != "needs_review", (
        "Semantic verifier must not run for non-research routes"
    )
    assert result.name != "semantic_research_v1", (
        f"semantic_research_v1 must not activate for repo route, got name={result.name!r}"
    )


# ---------------------------------------------------------------------------
# Test 5a: threshold — low unsupported fraction stays pass
# ---------------------------------------------------------------------------

def test_threshold_low_fraction_stays_pass() -> None:
    """1 unsupported out of 10 claims -> fraction=0.1 < 0.5 -> pass."""
    verifier = VerifierRegistry()
    route = _research_route()

    grounded_phrases = [
        "Alpha Beta technologies",
        "Delta Gamma systems",
        "Epsilon Zeta networks",
        "Eta Theta solutions",
        "Iota Kappa group",
        "Lambda Mu partners",
        "Nu Xi ventures",
        "Omicron Pi labs",
        "Rho Sigma capital",
    ]
    payload = " ".join(f"{p} is a leading firm." for p in grounded_phrases)
    tool_events = [_fetch_event(payload)]

    answer = _with_citation(
        "Alpha Beta technologies leads. "
        "Delta Gamma systems follows. "
        "Epsilon Zeta networks is third. "
        "Eta Theta solutions is fourth. "
        "Iota Kappa group is fifth. "
        "Lambda Mu partners is sixth. "
        "Nu Xi ventures is seventh. "
        "Omicron Pi labs is eighth. "
        "Rho Sigma capital is ninth. "
        "Widget Ltd is tenth."
    )

    result = verifier.verify(
        prompt="tell me about the top firms",
        route=route,
        task_class=route.task_class,
        output=answer,
        tool_events=tool_events,
        config=_make_config(threshold=0.5),
    )

    assert result.status == "pass", (
        f"Fraction ~0.1 should be below threshold 0.5, got status={result.status!r} "
        f"unsupported={result.unsupported_claim_ids}"
    )


# ---------------------------------------------------------------------------
# Test 5b: threshold — high unsupported fraction -> needs_review
# ---------------------------------------------------------------------------

def test_threshold_high_fraction_triggers_needs_review() -> None:
    """6 unsupported out of 10 claims -> fraction=0.6 > 0.5 -> needs_review."""
    verifier = VerifierRegistry()
    route = _research_route()

    payload = (
        "Alpha Beta technologies is a leading firm. "
        "Delta Gamma systems is well known. "
        "Epsilon Zeta networks has grown. "
        "Eta Theta solutions expanded."
    )
    tool_events = [_fetch_event(payload)]

    answer = _with_citation(
        "Alpha Beta technologies leads. "
        "Delta Gamma systems follows. "
        "Epsilon Zeta networks is third. "
        "Eta Theta solutions is fourth. "
        "Widget Ltd is fifth. "
        "Foo Bar corp is sixth. "
        "Baz Qux industries is seventh. "
        "Quux Corge ltd is eighth. "
        "Grault Garply inc is ninth. "
        "Waldo Fred company is tenth."
    )

    result = verifier.verify(
        prompt="tell me about the top firms",
        route=route,
        task_class=route.task_class,
        output=answer,
        tool_events=tool_events,
        config=_make_config(threshold=0.5),
    )

    assert result.status == "needs_review", (
        f"Fraction 0.6 should exceed threshold 0.5, got status={result.status!r} "
        f"unsupported={result.unsupported_claim_ids}"
    )


# ---------------------------------------------------------------------------
# Test 6: config gate — semantic_enabled=False skips semantic
# ---------------------------------------------------------------------------

def test_config_gate_disabled_skips_semantic() -> None:
    """When semantic_enabled=False, unsupported claims must not trigger needs_review."""
    verifier = VerifierRegistry()
    route = _research_route()
    tool_events = [_fetch_event("Acme Corp provides cloud services.")]

    result = verifier.verify(
        prompt="tell me about Widget Ltd",
        route=route,
        task_class=route.task_class,
        output=_with_citation("Widget Ltd is the market leader in quantum networking."),
        tool_events=tool_events,
        config=_make_config(semantic_enabled=False),
    )

    assert result.status != "needs_review", (
        f"Semantic must be skipped when disabled; got status={result.status!r}"
    )
    assert result.name != "semantic_research_v1", (
        f"semantic_research_v1 must not activate when disabled, got name={result.name!r}"
    )
