"""Deterministic tests for candidate-draft instruction injection (O3-α).

Structural coverage per test-contract.md S3:
1. burst-0 injection present for research/live_compare/general
2. burst-1 absence (active task has already ingested facts/artifacts)
3. pool-already-exists absence (candidate_pool in global_facts suppresses re-injection)
4. non-research/live_compare no-op
5. pool persistence round-trip via record_candidate_pool + subsequent suppression
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from rocky.core.run_flow import RunFlowManager


# Regex that must match when the candidate-draft instruction is present.
# Pattern mirrors the exact wording injected in task_instruction():
#   "enumerate 15 to 25 specific candidate names"
CANDIDATE_DRAFT_RE = re.compile(
    r"(?i)(enumerate|list).{0,80}(1[5-9]|2[0-5]).{0,20}candid"
)


def _make_manager(tmp_path: Path, task_signature: str = "research/live_compare/general") -> RunFlowManager:
    return RunFlowManager(
        tmp_path / ".rocky" / "runs",
        prompt="best wireless earphones between $200 and $300",
        task_signature=task_signature,
        task_class="research",
        execution_cwd=".",
    )


class TestBurstZeroInjectionPresent:
    """test 1: burst-0 injection is present for research/live_compare/general when no pool exists."""

    def test_candidate_draft_regex_matches_burst_zero_prompt(self, tmp_path: Path) -> None:
        manager = _make_manager(tmp_path)
        # Fresh manager: discover task, no facts, no artifacts, no candidate_pool.
        task = manager.run.active_task()
        assert task.kind == "discover", "precondition: first task must be discover"
        assert not task.facts, "precondition: no facts yet"
        assert not task.artifacts, "precondition: no artifacts yet"
        assert not any(f.startswith("candidate_pool:") for f in manager.run.global_facts), (
            "precondition: no candidate_pool in global_facts"
        )

        prompt = manager.user_prompt_for_burst()
        assert CANDIDATE_DRAFT_RE.search(prompt), (
            f"enumerate clause not found in burst-0 prompt.\nPrompt was:\n{prompt}"
        )


class TestBurstOneCandidateDraftAbsent:
    """test 2: after tool events are ingested (simulating burst-1 state), injection is absent."""

    def test_candidate_draft_absent_after_tool_events(self, tmp_path: Path) -> None:
        manager = _make_manager(tmp_path)
        task = manager.run.active_task()

        # Simulate a tool event being ingested, as would happen after burst-0 runs.
        manager.ingest_tool_event(
            {
                "type": "tool_result",
                "name": "search_web",
                "success": True,
                "summary_text": "Search returned results for wireless earphones",
                "text": "Search returned results.",
                "facts": [
                    {"kind": "search_result", "text": "Some earphone review page.", "url": "https://example.com/review"}
                ],
                "artifacts": [],
            }
        )

        # task.facts is now non-empty — burst-0 guard should be False.
        assert task.facts, "precondition: facts must be populated after tool event"

        prompt = manager.user_prompt_for_burst()
        assert not CANDIDATE_DRAFT_RE.search(prompt), (
            f"candidate-draft clause must be absent after burst-0 tool events.\nPrompt was:\n{prompt}"
        )


class TestPoolAlreadyExistsSuppressesInjection:
    """test 3: when candidate_pool is already in global_facts, injection is suppressed."""

    def test_candidate_draft_absent_when_pool_exists(self, tmp_path: Path) -> None:
        manager = _make_manager(tmp_path)

        # Pre-populate candidate_pool in global_facts (as if burst-0 already ran).
        manager.run.global_facts.append(
            "candidate_pool: Sony WF-1000XM5, Bose QC Ultra Earbuds, Apple AirPods Pro 2"
        )

        prompt = manager.user_prompt_for_burst()
        assert not CANDIDATE_DRAFT_RE.search(prompt), (
            f"candidate-draft clause must be absent when candidate_pool already exists.\nPrompt was:\n{prompt}"
        )


class TestNonResearchFlowNoOp:
    """test 4: injection is absent for task signatures outside research-flavored
    flows.  Gate was widened 2026-04-17 from `research/live_compare/*` to any
    signature in `{research/*, site/*}` because gemma4:26b routes recommendation
    tasks into `site/understanding/*` as often as into `research/live_compare/*`.
    """

    @pytest.mark.parametrize("sig", [
        "conversation/general",
        "repo/shell_execution",
        "data/extract",
        "automation/general",
    ])
    def test_candidate_draft_absent_for_non_research_flow(self, tmp_path: Path, sig: str) -> None:
        manager = _make_manager(tmp_path, task_signature=sig)
        prompt = manager.user_prompt_for_burst()
        assert not CANDIDATE_DRAFT_RE.search(prompt), (
            f"candidate-draft clause must not appear for task_signature={sig!r}.\nPrompt was:\n{prompt}"
        )

    @pytest.mark.parametrize("sig", [
        "research/live_compare/general",
        "research/general",
        "site/understanding/general",
        "site/product",
    ])
    def test_candidate_draft_present_for_research_flow(self, tmp_path: Path, sig: str) -> None:
        manager = _make_manager(tmp_path, task_signature=sig)
        prompt = manager.user_prompt_for_burst()
        assert CANDIDATE_DRAFT_RE.search(prompt), (
            f"candidate-draft clause must appear for research-flavored task_signature={sig!r}."
        )


class TestPoolPersistenceRoundTrip:
    """test 5: record_candidate_pool persists the pool and subsequent burst call is suppressed."""

    def test_record_candidate_pool_visible_in_global_facts(self, tmp_path: Path) -> None:
        manager = _make_manager(tmp_path)
        names = [f"Product {i}" for i in range(15)]

        manager.record_candidate_pool(names)

        # Pool must be visible in global_facts.
        pool_entries = [f for f in manager.run.global_facts if f.startswith("candidate_pool:")]
        assert len(pool_entries) == 1, f"Expected exactly 1 candidate_pool entry, got: {pool_entries}"
        for name in names[:15]:
            assert name in pool_entries[0], f"Name {name!r} not found in pool entry: {pool_entries[0]!r}"

    def test_record_candidate_pool_suppresses_subsequent_injection(self, tmp_path: Path) -> None:
        manager = _make_manager(tmp_path)
        names = [f"Item {i}" for i in range(20)]
        manager.record_candidate_pool(names)

        # Now user_prompt_for_burst should not inject the candidate-draft block.
        prompt = manager.user_prompt_for_burst()
        assert not CANDIDATE_DRAFT_RE.search(prompt), (
            f"candidate-draft must be suppressed after record_candidate_pool.\nPrompt was:\n{prompt}"
        )

    def test_record_candidate_pool_is_idempotent(self, tmp_path: Path) -> None:
        manager = _make_manager(tmp_path)
        names_a = [f"Alpha {i}" for i in range(15)]
        names_b = [f"Beta {i}" for i in range(15)]

        manager.record_candidate_pool(names_a)
        manager.record_candidate_pool(names_b)  # second call must be no-op

        pool_entries = [f for f in manager.run.global_facts if f.startswith("candidate_pool:")]
        assert len(pool_entries) == 1, "record_candidate_pool must be idempotent — only one pool entry"
        # First call's names must be present, not second call's names.
        assert "Alpha 0" in pool_entries[0]
        assert "Beta 0" not in pool_entries[0]
