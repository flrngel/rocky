# status: DONE
"""Regression tests — driving-policy carry invariant (set-site L538 and inject-site L3188).

Background
----------
When a *policy* (not a skill) drives a route upgrade in
``AgentCore._maybe_upgrade_route_from_project_context``, the upgraded task_signature
may differ from the policy's declared ``task_signatures``.  ContextBuilder retrieves
``learned_policies`` against the *upgraded* signature, which can drop the very policy
that triggered the upgrade (the "diverging-signature" scenario).

To keep the route-upgrade and context-retrieval lanes consistent, AgentCore maintains a
carry field ``_route_upgrade_driving_policy``:

- **Set-site (agent.py:L538)**: assigned inside
  ``_maybe_upgrade_route_from_project_context`` whenever a policy-driven upgrade
  succeeds.
- **Inject-site (agent.py:L3188)**: inside ``run_prompt``, after
  ``context_builder.build`` returns, the driving policy is inserted at position 0 of
  ``context.learned_policies`` *if* it is not already present (guard at L3186).

These two tests independently witness each site so that a sensitivity revert of either
line causes an ``AssertionError``.

Constraint references
---------------------
CF-L10  : Both set-site (L538) and inject-site (L3188) must be independently witnessed.
CF-L13  : No concurrent AgentCore instances — fresh instance per test.
CF-fixtures-match-prod-paths : Do not pre-assign _route_upgrade_driving_policy or mock
           the carry field; exercise the real production path.
CF-sensitivity-witness : Revert targets documented in docstrings (exact file:line,
           expected failure, restore instruction).
CF-assert-output-not-proxy : Test 2 asserts structural policy membership, not
           response text.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from rocky.app import RockyRuntime
from rocky.core.router import RouteDecision, Lane
from rocky.providers.base import ProviderResponse


# ---------------------------------------------------------------------------
# Shared fixture: minimal POLICY.md that reliably triggers the upgrade
# ---------------------------------------------------------------------------

_POLICY_ID = "driving-policy-upgrade-test"

# Frontmatter and body for the synthesized policy.
# Design constraints:
#   - task_signatures: exactly ONE entry (repo/shell_execution) so the
#     teach-over-tagging guard at agent.py:L474 does not suppress it
#     (guard fires only when current route IS in declared AND len > 1).
#   - scope: project  -> +4 in guidance_score
#   - origin: learned -> +1 in guidance_score (matched by "learned" in the set)
#   - kind: policy    -> +2 in guidance_score (guidance_kind == "policy")
#   - trigger "deploy" in retrieval.triggers -> trigger_match=True when prompt
#     contains "deploy" -> +6 in guidance_score.
#   - Total base score = 4+1+2+6 = 13.
#   - _TASK_SIGNATURE_BIAS["repo/shell_execution"] = +4 -> final score = 17 >= 9.
#   - Shellish marker "run_shell_command" in body -> shellish_guidance=True, so
#     the -2 penalty for repo/shell_execution is NOT applied.
_POLICY_FRONTMATTER = """\
---
policy_id: driving-policy-upgrade-test
name: driving-policy-upgrade-test
description: Deploy workspace tasks should use shell execution.
scope: project
task_signatures:
- repo/shell_execution
task_family: repo
generation: 1
origin:
  type: learned
  episode_ids:
  - ep_test_upgrade_001
promotion_state: promoted
retrieval:
  triggers:
  - deploy
  keywords:
  - deploy
  - shell
  - workspace
---
"""

_POLICY_BODY = (
    "When handling deploy tasks, prefer run_shell_command to execute the deployment "
    "steps. Verify the result after each step.\n"
)

# Matches _looks_like_atomic_workspace_task: 2 words <= 8, no /, no newline, <= 80 chars.
_PROMPT = "deploy changes"


def _build_runtime_with_policy(tmp_path: Path, monkeypatch) -> tuple[RockyRuntime, Path]:
    """Wire a fresh RockyRuntime against a tmp workspace containing the synthesized policy.

    Returns (runtime, policy_md_path) so callers can use the on-disk path for
    membership assertions without constructing LearnedPolicy manually.

    Follows the scaffolding pattern in tests/test_route_intersection.py:41-66.
    """
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    policy_dir = workspace / ".rocky" / "policies" / "learned" / _POLICY_ID
    policy_dir.mkdir(parents=True, exist_ok=True)

    policy_md = policy_dir / "POLICY.md"
    policy_md.write_text(
        _POLICY_FRONTMATTER + _POLICY_BODY,
        encoding="utf-8",
    )
    (policy_dir / "POLICY.meta.json").write_text(
        f'{{"policy_id": "{_POLICY_ID}", "promotion_state": "promoted"}}\n',
        encoding="utf-8",
    )

    runtime = RockyRuntime.load_from(workspace)
    return runtime, policy_md


# ---------------------------------------------------------------------------
# Test 1 — set-site witness (agent.py:L538)
# ---------------------------------------------------------------------------

def test_set_site_stores_driving_policy(tmp_path: Path, monkeypatch) -> None:
    """Witness agent.py:L538: _route_upgrade_driving_policy is populated by the upgrade.

    Calls _maybe_upgrade_route_from_project_context directly with a
    conversation/general route (tool_families=[]) and a prompt that matches
    _looks_like_atomic_workspace_task.  Asserts both:

    1. The returned route is upgraded (task_signature changed by the policy).
    2. agent._route_upgrade_driving_policy is not None -- the carry field is
       populated exactly by the assignment at L538.

    No live LLM call is made (complete/run_with_tools are never reached here).

    SENSITIVITY-REVERT: comment out src/rocky/core/agent.py:L538
      (``self._route_upgrade_driving_policy = best_candidate[4]``)
    Expected failure: AssertionError -- agent._route_upgrade_driving_policy is None.
    Restore: uncomment the line -- test must pass again.

    CF-fixtures-match-prod-paths: _route_upgrade_driving_policy is NOT pre-assigned
      in test setup; it is set solely by the production code path at L538.
    CF-L13: fresh AgentCore per test -- do not reuse across test functions.
    """
    runtime, _ = _build_runtime_with_policy(tmp_path, monkeypatch)
    agent = runtime.agent

    # Confirm the carry field starts at None (fresh instance).
    assert agent._route_upgrade_driving_policy is None, (
        "Fresh AgentCore instance should have _route_upgrade_driving_policy=None "
        "before any upgrade."
    )

    # Build a conversation/general route with empty tool_families so the gate
    # at agent.py:L442 opens.
    route = agent.router.decision_for_task_signature(
        "conversation/general",
        reasoning="initial conversation route for test",
        confidence=0.9,
        source="lexical",
    )
    assert route is not None
    route.tool_families = []  # ensure the guard at L442 opens

    # Call the upgrade method directly -- no LLM involved.
    upgraded = agent._maybe_upgrade_route_from_project_context(_PROMPT, route)

    # Assert 1: route was upgraded by the policy.
    assert upgraded.task_signature == "repo/shell_execution", (
        f"Expected upgrade to repo/shell_execution via policy trigger 'deploy'. "
        f"Got {upgraded.task_signature!r} (source={upgraded.source!r})."
    )
    assert upgraded.source == "project_context", (
        f"Upgraded route should carry source='project_context'. "
        f"Got source={upgraded.source!r}."
    )

    # Assert 2: the carry field was populated at L538.
    assert agent._route_upgrade_driving_policy is not None, (
        "agent._route_upgrade_driving_policy must be set to the driving policy after "
        "a policy-driven upgrade (set-site at agent.py:L538). "
        "SENSITIVITY-REVERT: comment out L538 to observe this assertion fail."
    )
    assert agent._route_upgrade_driving_policy.policy_id == _POLICY_ID, (
        f"Carry field holds wrong policy. "
        f"Expected policy_id={_POLICY_ID!r}, "
        f"got {agent._route_upgrade_driving_policy.policy_id!r}."
    )


# ---------------------------------------------------------------------------
# Test 2 -- inject-site witness (agent.py:L3188)
# ---------------------------------------------------------------------------

class _StubProvider:
    """Minimal stub provider that short-circuits all LLM calls.

    Returns a valid ProviderResponse with text='stub-done' and empty tool_events.
    The flow loop may call run_with_tools up to ~6 times (3 tasks x up to 2 calls
    each for repo/shell_execution route); each call returns the same stub response.
    """

    def complete(
        self,
        system_prompt: str,
        messages: Any,
        stream: bool = False,
        event_handler: Any = None,
    ) -> ProviderResponse:
        return ProviderResponse(text="stub-done", usage={}, raw={}, tool_events=[])

    def run_with_tools(
        self,
        system_prompt: str,
        messages: Any,
        tools: Any,
        execute_tool: Any,
        max_rounds: int = 8,
        event_handler: Any = None,
    ) -> ProviderResponse:
        return ProviderResponse(text="stub-done", usage={}, raw={}, tool_events=[])


def test_inject_site_populates_learned_policies(tmp_path: Path, monkeypatch) -> None:
    """Witness agent.py:L3188: the driving policy is injected into learned_policies.

    This is a *structural* assertion (policy membership in context.learned_policies),
    NOT a behavioral assertion on response text -- per CF-assert-output-not-proxy.

    Setup
    -----
    1. Build a fresh runtime with the synthesized policy on disk (CF-L13 -- separate
       instance from test_set_site_stores_driving_policy).
    2. Monkey-patch context_builder.build to:
       a. Call the original build (which may naturally retrieve the policy).
       b. Remove the policy from the returned context.learned_policies to simulate
          the diverging-signature drop scenario -- the policy declares
          task_signatures: [repo/shell_execution] while the upgraded route is also
          repo/shell_execution, but we strip it to ensure the inject at L3188 is
          the ONLY path that puts it back. Without this strip, L3186's guard
          (``if driver_name and driver_name not in existing_names``) would see the
          policy already present and skip the insert at L3188, making the revert
          at L3188 invisible to the test.
       c. Capture the ContextPackage reference (the mutable object); the inject at
          L3188 mutates the same list via context.learned_policies.insert(...).
    3. Stub provider_registry.provider_for_task so no live LLM is called.
    4. Call runtime.run_prompt(_PROMPT) -- upgrade fires at agent.py:L3110,
       carry is set at L538, inject runs at L3188.
    5. Assert: the policy appears in captured_context.learned_policies by path.

    Rollback gate (agent.py:L3181-L3183): the policy file must exist on disk and
    must not be in a rolled-back lineage. The synthesized workspace is fresh so
    _is_artifact_rolled_back returns False.

    SENSITIVITY-REVERT: comment out src/rocky/core/agent.py:L3188
      (the ``context.learned_policies.insert(0, {...})`` line inside the
      driving-policy inject block)
    Expected failure: AssertionError -- policy absent from
      captured_context.learned_policies (the carry was set at L538 but the insert
      at L3188 never ran, so the policy is missing).
    Restore: uncomment the line -- test must pass again.

    CF-assert-output-not-proxy: this is a structural assertion (policy membership),
      not a behavioral assertion on response text.
    CF-fixtures-match-prod-paths: the provider's complete/run_with_tools methods are
      stubbed; _route_upgrade_driving_policy is NOT pre-assigned in test setup.
    CF-L13: fresh AgentCore per test -- do not reuse the instance from
      test_set_site_stores_driving_policy.
    """
    runtime, policy_md_path = _build_runtime_with_policy(tmp_path, monkeypatch)
    agent = runtime.agent

    # --- Capture the ContextPackage produced during run_prompt ---
    captured_contexts: list[Any] = []
    original_build = runtime.agent.context_builder.build

    def _capturing_build(*args: Any, **kwargs: Any) -> Any:
        ctx = original_build(*args, **kwargs)
        # Remove the test policy from natural retrieval results so the inject
        # at agent.py:L3188 is the ONLY path that puts it into learned_policies.
        # L3186 guard: ``if driver_name and driver_name not in existing_names``
        # only fires the insert when the policy is absent; stripping it here
        # ensures a revert of L3188 causes a real assertion failure.
        ctx.learned_policies = [
            p for p in ctx.learned_policies
            if p.get("name") != _POLICY_ID
        ]
        captured_contexts.append(ctx)
        return ctx

    runtime.agent.context_builder.build = _capturing_build

    # --- Stub the provider so no live LLM is called ---
    stub_provider = _StubProvider()
    runtime.agent.provider_registry.provider_for_task = lambda **_kw: stub_provider

    # --- Run prompt -- upgrade, carry, inject all fire inside run_prompt ---
    # The router returns conversation/general for "deploy changes" (short, no
    # shell signals).  _maybe_upgrade_route_from_project_context at L3110 fires
    # the upgrade to repo/shell_execution and sets _route_upgrade_driving_policy
    # at L538.  context_builder.build is called, our capturing wrapper strips the
    # policy, then the inject at L3188 fires (policy absent -> L3186 guard passes)
    # and inserts the policy dict at position 0 of ctx.learned_policies.
    try:
        runtime.run_prompt(_PROMPT)
    except Exception:
        # The stub provider may cause the flow loop to exhaust retries or raise.
        # The capturing wrapper holds a reference to the live ContextPackage;
        # the inject at L3188 mutates its learned_policies list before any
        # post-build exception could fire, so captured_contexts[0].learned_policies
        # reflects the injection regardless of whether run_prompt raises.
        pass

    # --- Assert: exactly one ContextPackage was captured ---
    assert len(captured_contexts) >= 1, (
        "context_builder.build must be called at least once during run_prompt."
    )
    captured_context = captured_contexts[0]

    # --- Assert: the driving policy was injected by L3188 ---
    policy_path_str = str(policy_md_path)
    found = any(
        p.get("path") == policy_path_str
        for p in captured_context.learned_policies
    )
    assert found, (
        f"Policy {_POLICY_ID!r} (path={policy_path_str!r}) must appear in "
        f"captured_context.learned_policies after the inject at agent.py:L3188. "
        f"Current learned_policies names: "
        f"{[p.get('name') for p in captured_context.learned_policies]!r}.\n"
        "SENSITIVITY-REVERT: comment out agent.py:L3188 to observe this assertion fail."
    )
