"""ROUTE-UPGRADE-PERF — live witness for the carry-field on a NEW family.

Extends the deterministic in-process witness at
``tests/test_route_upgrade_driving_policy.py`` (which covers
``repo/shell_execution``) into the LIVE end-to-end pipeline against a
DIFFERENT task family (``automation/general``). This proves the
``AgentCore._route_upgrade_driving_policy`` carry-field (set at
agent.py:538, reset at :3078, injected at :3188) survives the real
ContextBuilder retrieval + system-prompt assembly + LLM round-trip on
a family beyond the single one already covered.

Setup:
    A production-shaped ``POLICY.md`` is hand-written into
    ``<workspace>/.rocky/policies/learned/route-upgrade-auto-test/``
    BEFORE the live call. We bypass ``/teach`` deliberately to control
    scoring variables — same isolation pattern as the deterministic
    test, applied to a live invocation. The frontmatter shape is
    copied verbatim from the deterministic test; only ``task_signatures``
    and ``retrieval.triggers`` differ.

Bit-flip negative:
    A separate workspace runs the same prompt WITHOUT the seeded
    policy. The carry-field cannot fire; ``selected_policies`` should
    not contain the policy id, OR ``learned_policies[0].name`` should
    not be the policy.

Gated by ``ROCKY_LLM_SMOKE=1``. Helpers come from
``tests/agent/_helpers.py`` ``__all__`` only.
"""

from __future__ import annotations

import os
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


pytestmark = pytest.mark.skipif(
    os.environ.get(SMOKE_FLAG) != "1",
    reason=(
        f"route-upgrade-perf live scenario requires {SMOKE_FLAG}=1 "
        f"(real Ollama via editable rocky at {ROCKY_BIN})"
    ),
)


_POLICY_ID = "route-upgrade-auto-test"

# Frontmatter shape mirrors tests/test_route_upgrade_driving_policy.py
# verbatim. Only task_signatures and retrieval.triggers differ.
# Score budget on automation/general:
#   guidance_score: scope:project (+4) + origin:learned (+1) +
#                   kind:policy (+2) + trigger "schedule" match (+6) = 13
#   _TASK_SIGNATURE_BIAS["automation/general"] = +3 → final 16 (>= 9 threshold).
_POLICY_FRONTMATTER = """\
---
policy_id: route-upgrade-auto-test
name: route-upgrade-auto-test
description: Scheduled-automation tasks should follow the project automation runbook.
scope: project
task_signatures:
- automation/general
task_family: automation
generation: 1
origin:
  type: learned
  episode_ids:
  - ep_test_route_upgrade_auto_001
promotion_state: promoted
retrieval:
  triggers:
  - schedule
  keywords:
  - schedule
  - nightly
  - automation
  - backup
---
"""

_POLICY_BODY = (
    "When handling scheduled-automation tasks (database backups, periodic "
    "jobs, nightly maintenance), follow the project's standard automation "
    "runbook. Document the schedule and the verification step. Prefer "
    "structured cron-style descriptors over free-form scheduling text.\n"
)

_T1_PROMPT = "schedule a nightly database backup"


def _seed_policy(workspace: Path) -> str:
    policy_dir = workspace / ".rocky" / "policies" / "learned" / _POLICY_ID
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / "POLICY.md").write_text(
        _POLICY_FRONTMATTER + _POLICY_BODY,
        encoding="utf-8",
    )
    (policy_dir / "POLICY.meta.json").write_text(
        f'{{"policy_id": "{_POLICY_ID}", "promotion_state": "promoted"}}\n',
        encoding="utf-8",
    )
    return _POLICY_ID


@dataclass
class _RouteUpgradeResult:
    t1: dict = field(default_factory=dict)
    policy_id: str = ""
    workspace: Path = field(default_factory=Path)


@dataclass
class _RouteUpgradeBaseline:
    t1: dict = field(default_factory=dict)
    workspace: Path = field(default_factory=Path)


@pytest.fixture(scope="module")
def route_upgrade_with_policy(request, tmp_path_factory) -> _RouteUpgradeResult:
    workspace = tmp_path_factory.mktemp("route_upgrade_perf_taught_")
    captures: dict = {}
    _install_evidence_finalizer(request, "route_upgrade_perf_taught", workspace, captures)
    policy_id = _seed_policy(workspace)
    captures["policy_id"] = policy_id

    def _carry_field_fired(payload: dict) -> bool:
        trace = payload.get("trace") or {}
        context = trace.get("context") or {}
        learned = context.get("learned_policies") or []
        if not learned:
            return False
        first = learned[0] if isinstance(learned[0], dict) else {}
        first_name = str(first.get("name") or first.get("policy_id") or "")
        return first_name == policy_id

    t1 = _run_rocky_until(
        workspace,
        _T1_PROMPT,
        label="t1_upgrade_carry_field",
        captures=captures,
        predicate=_carry_field_fired,
        predicate_reason=(
            "the seeded policy must reach context at position 0 "
            "(witnesses agent.py:3188 inject-site); fewer than 3 attempts "
            "passing means the carry-field is silently dropping the policy"
        ),
    )
    return _RouteUpgradeResult(t1=t1, policy_id=policy_id, workspace=workspace)


@pytest.fixture(scope="module")
def route_upgrade_baseline(request, tmp_path_factory) -> _RouteUpgradeBaseline:
    workspace = tmp_path_factory.mktemp("route_upgrade_perf_baseline_")
    captures: dict = {}
    _install_evidence_finalizer(request, "route_upgrade_perf_baseline", workspace, captures)
    t1 = _run_rocky(
        workspace,
        _T1_PROMPT,
        label="t1_baseline_no_seeded_policy",
        captures=captures,
    )
    return _RouteUpgradeBaseline(t1=t1, workspace=workspace)


def test_route_upgrade_perf_phase_A_route_landed_on_automation(
    route_upgrade_with_policy: _RouteUpgradeResult,
) -> None:
    """T1's final task_signature must be automation/general (the policy's family)."""
    trace = route_upgrade_with_policy.t1.get("trace") or {}
    route = trace.get("route") or {}
    task_signature = str(route.get("task_signature") or "")
    assert task_signature == "automation/general", (
        f"ROUTE-UPGRADE-PERF phase A FAILED: T1 route is {task_signature!r}, "
        f"expected 'automation/general'. Either the upgrade did not fire "
        f"or a downstream pass downgraded the route. route={route!r}"
    )


def test_route_upgrade_perf_phase_B_carry_field_at_position_zero(
    route_upgrade_with_policy: _RouteUpgradeResult,
) -> None:
    """The carry-field's load-bearing claim — the seeded policy is at
    ``context.learned_policies[0]``. This is the inject-site witness
    (agent.py:3188). A revert of that line drops the policy out of
    position 0 and this assertion fires."""
    trace = route_upgrade_with_policy.t1.get("trace") or {}
    context = trace.get("context") or {}
    learned = context.get("learned_policies") or []
    assert learned, (
        f"ROUTE-UPGRADE-PERF phase B FAILED: context.learned_policies is "
        f"empty — the policy never reached the system prompt. "
        f"context={context!r}"
    )
    first = learned[0] if isinstance(learned[0], dict) else {}
    first_name = str(first.get("name") or first.get("policy_id") or "")
    assert first_name == route_upgrade_with_policy.policy_id, (
        f"ROUTE-UPGRADE-PERF phase B FAILED: learned_policies[0] is "
        f"{first_name!r}, expected {route_upgrade_with_policy.policy_id!r}. "
        f"The carry-field did not bump the upgrade-driving policy to "
        f"position 0. learned_policies (names)={[str(p.get('name') or p.get('policy_id') or '') for p in learned]!r}"
    )


def test_route_upgrade_perf_phase_C_policy_in_selected_policies(
    route_upgrade_with_policy: _RouteUpgradeResult,
) -> None:
    """Independent witness: the trace's selected_policies should include
    the seeded policy. Catches regressions where the policy reaches
    context but is dropped before retrieval-list emission."""
    trace = route_upgrade_with_policy.t1.get("trace") or {}
    selected = trace.get("selected_policies") or []
    assert route_upgrade_with_policy.policy_id in selected, (
        f"ROUTE-UPGRADE-PERF phase C FAILED: selected_policies missing "
        f"{route_upgrade_with_policy.policy_id!r}; selected={selected!r}"
    )


def test_route_upgrade_perf_phase_D_baseline_does_not_select_seeded_policy(
    route_upgrade_baseline: _RouteUpgradeBaseline,
) -> None:
    """Bit-flip negative: with no seeded policy on disk, the policy id
    must not appear in selected_policies. Proves the positive tests
    measure the seeded policy's effect, not a coincident retrieval."""
    trace = route_upgrade_baseline.t1.get("trace") or {}
    selected = trace.get("selected_policies") or []
    context = trace.get("context") or {}
    learned = context.get("learned_policies") or []
    learned_names = [str(p.get("name") or p.get("policy_id") or "") for p in learned]
    assert _POLICY_ID not in selected, (
        f"ROUTE-UPGRADE-PERF bit-flip FAILED: baseline (no seeded policy) "
        f"selected_policies still contains {_POLICY_ID!r}; selected={selected!r}"
    )
    assert _POLICY_ID not in learned_names, (
        f"ROUTE-UPGRADE-PERF bit-flip FAILED: baseline learned_policies "
        f"contains {_POLICY_ID!r}; names={learned_names!r}"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
