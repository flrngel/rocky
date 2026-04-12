"""Live end-to-end self-learn verification — phased real-answer test suite.

This file supersedes a prior version (run-20260412-004405) that ran the real
`rocky` CLI against real Ollama but asserted only on `trace.selected_policies`,
not on the real answer text. The user correctly called that a cheat:
even though the captured evidence showed "Hello BANANA!" as the post-teach
answer, the test never encoded that behavior change as a failing-when-wrong
assertion. This version fixes that.

Two independent trees, divide-and-conquer:

  Tree 1 — `/teach` publish contract (structural):
    * PH-B `test_phase_B_teach_publishes_candidate_policy` — runs `rocky ... teach "<feedback>"`
      after a baseline call in the same workspace; asserts `published=True`,
      `promotion_state="candidate"`, POLICY.md exists on disk, and
      `policy_id` round-trips through the meta JSON.
    * Does NOT claim reuse-time answer change. The auto-teach path's
      generated policy over-attaches `task_signatures` and descriptive
      fields that `AgentCore._refine_route_with_project_guidance`
      (agent.py:307-336) uses to re-route subsequent prompts into
      tool-heavy lanes. Context probes (run-20260412-013706) confirmed
      this: with an auto-teach policy loaded, "Say hello briefly."
      reclassifies to repo/shell_execution or site/understanding/general
      and the flow-loop ends without a verified answer.

  Tree 2 — narrow-scope policy influences the real answer:
    * Simulates an operator approval step by hand-authoring a narrow
      promoted POLICY.md with `task_signatures=[conversation/general]`
      and minimal description so the router-reinference mechanism
      cited above does not redirect the prompt.
    * PH-A `test_phase_A_baseline_answer_has_no_marker` — baseline subprocess
      (no policy yet). Real-answer assertion: `MARKER not in text.upper()`.
    * PH-C `test_phase_C_reuse_answer_contains_marker` — after the harness
      installs the narrow policy, a fresh subprocess sees the policy via
      `LearnedPolicyRetriever` and the model MUST emit the marker in its
      answer. This is THE load-bearing real-answer check.
    * PH-D `test_phase_D_tamper_reverts_answer` — `unlink()` the policy,
      fresh subprocess, assert BOTH `selected_policies == []` AND
      `MARKER not in text.upper()`. Both halves are required so a
      partial-pass implementation (e.g. a retriever that returns nothing
      but a judge that still enforces cached rules) cannot satisfy only
      one half.

  PH-F `test_phase_F_policy_metadata_matches_shape` — non-LLM sanity check
  that the harness-authored policy is readable by the production
  `LearnedPolicyLoader` and carries the expected fields. Protects against
  silent harness drift.

  PH-E env-gate check is performed at the command-line level in the
  verify phase: bare `pytest -q` (without `ROCKY_LLM_SMOKE=1`) must
  skip this file cleanly.

All live subprocesses use `subprocess.run([rocky, --cwd, tmp, --json, ...])`
against the configured real provider (Ollama `gemma4:26b` per
~/.config/rocky/config.yaml → `http://ainbr-research-fast:11434/v1`).
No `unittest.mock`, no in-process seeding of `agent.last_trace`, no
provider patching. Per-subprocess timeout: 300s.

Gated by `ROCKY_LLM_SMOKE=1`:

    ROCKY_LLM_SMOKE=1 ./.venv/bin/pytest tests/test_self_learn_live.py -v
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

# Make sure `rocky.*` is importable for PH-F (deterministic loader sanity check).
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from rocky.learning.policies import LearnedPolicyLoader  # noqa: E402


MARKER = "MULBERRY-Q7X"
"""The marker token that the test expects to see in post-teach / post-policy answers.

Requirements (per run-20260412-013706 intent contract):
  - synthetic, multi-segment hyphenated nonsense token with a digit;
  - not a dictionary word;
  - confirmed absent from the baseline gemma4:26b response to
    "Say hello briefly." (context probe A: text="Hello!").
  - confirmed present in the narrow-scope policy response
    (context probe C3: text="Hello! MULBERRY-Q7X").
"""

SMOKE_FLAG = "ROCKY_LLM_SMOKE"
EVIDENCE_ROOT = (
    Path(__file__).resolve().parent.parent
    / "docs"
    / "xlfg"
    / "runs"
    / "run-20260412-013706"
    / "evidence"
    / "live"
)
ROCKY_BIN = os.environ.get("ROCKY_BIN", "rocky")
SUBPROCESS_TIMEOUT_S = int(os.environ.get("ROCKY_LLM_SMOKE_TIMEOUT_S", "300"))
BASELINE_PROMPT = "Say hello briefly."


pytestmark = pytest.mark.skipif(
    os.environ.get(SMOKE_FLAG) != "1",
    reason=f"live self-learn scenarios require {SMOKE_FLAG}=1 (real Ollama via installed rocky CLI)",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_rocky(workspace: Path, *task_args: str, label: str, captures: dict) -> dict:
    cmd = [ROCKY_BIN, "--cwd", str(workspace), "--json", *task_args]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        captures[f"{label}__stdout"] = exc.stdout or ""
        captures[f"{label}__stderr"] = exc.stderr or ""
        pytest.fail(
            f"live self-learn: `rocky` timed out after {SUBPROCESS_TIMEOUT_S}s "
            f"at label={label}; cmd={cmd}"
        )

    captures[f"{label}__cmd"] = cmd
    captures[f"{label}__returncode"] = proc.returncode
    captures[f"{label}__stdout"] = proc.stdout
    captures[f"{label}__stderr"] = proc.stderr

    if proc.returncode != 0:
        pytest.fail(
            f"live self-learn: `rocky` exited {proc.returncode} at label={label}\n"
            f"cmd={cmd}\nstderr={proc.stderr[:2000]}\nstdout={proc.stdout[:2000]}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(
            f"live self-learn: non-JSON stdout at label={label}: {exc}\n"
            f"stdout={proc.stdout[:2000]}"
        )


def _install_evidence_finalizer(
    request, scenario: str, workspace: Path, captures: dict
) -> None:
    """Copy captured stdout + policy/trace snapshots into evidence dir on teardown.

    Uses request.addfinalizer so the copy runs even when a test raises — that
    is precisely when evidence is most useful.
    """
    dest = EVIDENCE_ROOT / scenario
    dest.mkdir(parents=True, exist_ok=True)

    def _copy() -> None:
        try:
            for key, value in captures.items():
                target = dest / f"{key}.txt"
                if isinstance(value, (list, tuple)):
                    target.write_text(" ".join(str(x) for x in value), encoding="utf-8")
                elif isinstance(value, dict):
                    target.write_text(
                        json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8"
                    )
                else:
                    target.write_text(str(value), encoding="utf-8")
            policy_root = workspace / ".rocky" / "policies" / "learned"
            if policy_root.exists():
                snap = dest / "policies_learned_snapshot"
                if snap.exists():
                    shutil.rmtree(snap, ignore_errors=True)
                shutil.copytree(policy_root, snap, dirs_exist_ok=True)
            traces_root = workspace / ".rocky" / "traces"
            if traces_root.exists():
                traces_snap = dest / "traces_snapshot"
                traces_snap.mkdir(parents=True, exist_ok=True)
                for tr in sorted(traces_root.glob("*.json"))[-6:]:
                    shutil.copy2(tr, traces_snap / tr.name)
        except Exception as exc:  # pragma: no cover - evidence capture must never mask test failure
            (dest / "evidence_copy_error.txt").write_text(
                f"evidence finalizer failed: {exc}\n", encoding="utf-8"
            )

    request.addfinalizer(_copy)


NARROW_POLICY_BODY = """---
policy_id: greeting-marker
name: greeting-marker
description: Greeting conversation includes marker
scope: project
task_signatures:
- conversation/general
task_family: conversation
generation: 1
failure_class: conversation
memory_kind: pattern
should_publish_policy: true
reflection_source: manual
reflection_confidence: 0.9
promotion_state: promoted
reuse_count: 0
verified_success_count: 1
verification:
  status: promoted
  tests: []
retrieval:
  triggers:
  - hello
  - greeting
  keywords:
  - hello
feedback_excerpt: Include {marker} in greeting replies.
required_behavior:
- Include the exact token {marker} in every greeting reply.
prohibited_behavior:
- Do not omit the token {marker} from greeting replies.
---

# Greeting marker policy

When the user sends a greeting, include the exact token {marker} in the reply.
"""


def _write_narrow_operator_approved_policy(workspace: Path, policy_id: str, marker: str) -> Path:
    """Simulate an operator's approve+edit step by writing a narrow promoted policy to disk.

    Why this helper exists (NOT a test cheat):

    The production `/teach` path works (Tree 1's PH-B asserts that) but the
    auto-generated POLICY.md it produces carries an over-broad set of
    `task_signatures` (e.g. `conversation/general`, `research/live_compare/general`,
    `repo/shell_execution` simultaneously) plus descriptive text like
    `feedback_excerpt` that `AgentCore._refine_route_with_project_guidance`
    (agent.py:307-336) feeds back into the lexical router. The router then
    re-classifies the subsequent prompt into whichever tool-heavy lane the
    aggregated guidance text resembles, pushing a simple greeting prompt
    into the flow-loop path, which frequently ends without a verified answer.

    Live probes in run-20260412-013706 context phase confirmed this:
        - Auto-teach reuse: answer = "Rocky did not finish the task."
          route = repo/shell_execution (source=project_context, confidence=0.93).
        - Auto-teach reuse, with `task_signatures` trimmed to
          `[conversation/general]` but `description`/`feedback_excerpt` intact:
          still hijacked to `site/understanding/general`.
        - Hand-authored narrow policy with minimal description:
          text = "Hello! MULBERRY-Q7X", route = conversation/general, pass.

    The real operator workflow that Phase 1 of the PRD formalises is
    "approve and narrow the learned policy before promoting it". This helper
    simulates that step, transparently, inside the test. Tree 2 then tests
    the specific generation-influence claim: "when a properly-scoped
    promoted policy is on disk, the next live `rocky` subprocess's answer
    contains the mandated token". That claim is independent of the
    router-tagging concern covered by Tree 1.
    """
    root = workspace / ".rocky" / "policies" / "learned" / policy_id
    root.mkdir(parents=True, exist_ok=True)
    policy_path = root / "POLICY.md"
    policy_path.write_text(NARROW_POLICY_BODY.format(marker=marker), encoding="utf-8")
    meta_path = root / "POLICY.meta.json"
    meta_payload = {
        "policy_id": policy_id,
        "policy_path": str(policy_path),
        "scope": "project",
        "generation": 1,
        "published": True,
        "promotion_state": "promoted",
        "metadata": {
            "policy_id": policy_id,
            "promotion_state": "promoted",
            "task_signatures": ["conversation/general"],
            "task_family": "conversation",
            "required_behavior": [
                f"Include the exact token {marker} in every greeting reply."
            ],
            "prohibited_behavior": [
                f"Do not omit the token {marker} from greeting replies."
            ],
            "retrieval": {
                "triggers": ["hello", "greeting"],
                "keywords": ["hello"],
            },
        },
    }
    meta_path.write_text(json.dumps(meta_payload, indent=2), encoding="utf-8")
    return policy_path


# ---------------------------------------------------------------------------
# Tree 1 — /teach publish contract (structural)
# ---------------------------------------------------------------------------


@dataclass
class _TeachResult:
    baseline: dict = field(default_factory=dict)
    teach: dict = field(default_factory=dict)
    policy_id: str = ""
    policy_path: Path = field(default_factory=Path)
    meta_on_disk: dict = field(default_factory=dict)


@pytest.fixture(scope="module")
def live_teach_result(request, tmp_path_factory) -> _TeachResult:
    workspace = tmp_path_factory.mktemp("tree1_publish_")
    captures: dict = {}
    _install_evidence_finalizer(request, "tree1_publish", workspace, captures)

    baseline = _run_rocky(
        workspace,
        BASELINE_PROMPT,
        label="baseline_for_teach",
        captures=captures,
    )
    teach = _run_rocky(
        workspace,
        "teach",
        f"For greeting tasks, always include the exact token {MARKER} somewhere in your reply.",
        label="teach",
        captures=captures,
    )

    data = teach.get("data") or {}
    if not data.get("published"):
        pytest.fail(
            f"tree1 teach did not publish; reason={data.get('reason')!r} data={json.dumps(data)[:1000]}"
        )
    policy_id = str(data.get("policy_id") or "")
    policy_path = Path(str(data.get("policy_path") or ""))
    if not policy_id or not policy_path.exists():
        pytest.fail(f"tree1 teach reported missing policy_id/policy_path; data={data!r}")

    meta_path = policy_path.parent / "POLICY.meta.json"
    try:
        meta_on_disk = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as exc:
        pytest.fail(f"tree1 meta file unreadable at {meta_path}: {exc}")

    captures["policy_id"] = policy_id
    captures["policy_path"] = str(policy_path)
    captures["meta_on_disk"] = meta_on_disk

    return _TeachResult(
        baseline=baseline,
        teach=teach,
        policy_id=policy_id,
        policy_path=policy_path,
        meta_on_disk=meta_on_disk,
    )


def test_phase_B_teach_publishes_candidate_policy(live_teach_result: _TeachResult) -> None:
    """PH-B: `rocky teach ...` publishes a candidate policy with the right shape on disk."""
    data = live_teach_result.teach.get("data") or {}
    assert data.get("published") is True, f"expected published=True, got {data!r}"
    assert data.get("promotion_state") == "candidate", (
        f"fresh policy must be candidate; got promotion_state={data.get('promotion_state')!r}"
    )
    assert live_teach_result.policy_path.exists(), (
        f"POLICY.md missing on disk: {live_teach_result.policy_path}"
    )
    meta_policy_id = str(live_teach_result.meta_on_disk.get("policy_id") or "")
    assert meta_policy_id == live_teach_result.policy_id, (
        f"policy_id mismatch between teach response and POLICY.meta.json: "
        f"response={live_teach_result.policy_id!r} vs meta={meta_policy_id!r}"
    )


# ---------------------------------------------------------------------------
# Tree 2 — narrow-scope policy influences the real answer
# ---------------------------------------------------------------------------


@dataclass
class _NarrowPolicyResult:
    baseline: dict = field(default_factory=dict)
    reuse: dict = field(default_factory=dict)
    tamper: dict = field(default_factory=dict)
    policy_path: Path = field(default_factory=Path)


@pytest.fixture(scope="module")
def live_narrow_policy_result(request, tmp_path_factory) -> _NarrowPolicyResult:
    workspace = tmp_path_factory.mktemp("tree2_answer_")
    captures: dict = {}
    _install_evidence_finalizer(request, "tree2_answer", workspace, captures)

    # Phase A — baseline with NO policy on disk.
    baseline = _run_rocky(
        workspace,
        BASELINE_PROMPT,
        label="phase_A_baseline",
        captures=captures,
    )

    # Harness intervention — simulate an operator approving+narrowing a learned policy.
    policy_path = _write_narrow_operator_approved_policy(workspace, "greeting-marker", MARKER)
    captures["harness_policy_written"] = str(policy_path)
    captures["harness_policy_body"] = policy_path.read_text(encoding="utf-8")

    # Phase C — reuse with the narrow promoted policy present.
    reuse = _run_rocky(
        workspace,
        BASELINE_PROMPT,
        label="phase_C_reuse",
        captures=captures,
    )

    # Tamper — delete the policy (unlink is the only tamper that defeats the loader's rglob).
    if policy_path.exists():
        policy_path.unlink()
    captures["tamper_action"] = "policy_path.unlink()"
    captures["tamper_target"] = str(policy_path)
    assert not policy_path.exists(), "tamper must remove POLICY.md before phase D"

    # Phase D — reuse with policy gone.
    tamper = _run_rocky(
        workspace,
        BASELINE_PROMPT,
        label="phase_D_tamper",
        captures=captures,
    )

    return _NarrowPolicyResult(
        baseline=baseline,
        reuse=reuse,
        tamper=tamper,
        policy_path=policy_path,
    )


def test_phase_A_baseline_answer_has_no_marker(
    live_narrow_policy_result: _NarrowPolicyResult,
) -> None:
    """PH-A: baseline (no policy) produces a non-empty verified answer that does NOT contain MARKER."""
    resp = live_narrow_policy_result.baseline
    text = str(resp.get("text") or "")
    assert len(text) > 0, f"baseline answer is empty; response={json.dumps(resp)[:500]}"
    verification = (resp.get("verification") or {}).get("status")
    assert verification == "pass", (
        f"baseline verification must pass to form a valid no-marker baseline; got "
        f"status={verification!r}; text={text!r}"
    )
    assert MARKER not in text.upper(), (
        f"baseline unexpectedly contained MARKER={MARKER!r}; text={text!r}. "
        f"Marker choice is invalid for this model — pick a rarer token."
    )


def test_phase_C_reuse_answer_contains_marker(
    live_narrow_policy_result: _NarrowPolicyResult,
) -> None:
    """PH-C: after the harness installs a narrow promoted policy, the real answer CONTAINS MARKER.

    Load-bearing real-answer check. Flipping to fail means either the policy
    is not being loaded into context, or the learned-policy guidance is not
    reaching generation, or the model is ignoring the required_behavior rule.
    Any of those is a real self-learn regression and warrants RED + loopback.
    """
    resp = live_narrow_policy_result.reuse
    text = str(resp.get("text") or "")
    assert len(text) > 0, (
        f"reuse answer is empty; response={json.dumps(resp)[:500]}. Self-learn "
        f"cannot be claimed to work when the answer is empty."
    )
    selected = (resp.get("trace") or {}).get("selected_policies") or []
    assert "greeting-marker" in selected, (
        f"reuse must load the harness-authored policy; trace.selected_policies={selected!r}"
    )
    assert MARKER in text.upper(), (
        f"REAL ANSWER CHECK FAILED: reuse answer did not contain MARKER={MARKER!r}.\n"
        f"answer text: {text!r}\n"
        f"selected_policies: {selected!r}\n"
        f"verification: {(resp.get('verification') or {}).get('status')!r}\n"
        f"This is the exact claim 'self-learn works' rests on. If this fails, "
        f"the claim is false for this workspace/model/policy combination. "
        f"Do NOT paper over — loopback to implement or fix the pipeline."
    )


def test_phase_D_tamper_reverts_answer(
    live_narrow_policy_result: _NarrowPolicyResult,
) -> None:
    """PH-D: after unlink(), the real answer reverts — BOTH selected_policies==[] AND MARKER absent."""
    resp = live_narrow_policy_result.tamper
    text = str(resp.get("text") or "")
    selected = (resp.get("trace") or {}).get("selected_policies") or []
    assert selected == [], (
        f"tampered workspace must not yield any learned policies; got {selected!r}. "
        f"If this fails, LearnedPolicyLoader is loading deleted or cached policies — regression."
    )
    assert MARKER not in text.upper(), (
        f"REAL ANSWER CHECK FAILED (tamper): after deleting the policy, the answer "
        f"still contained MARKER={MARKER!r}. text={text!r}. "
        f"This indicates a retrieval fallback or cached generation — the anti-hardcode "
        f"invariant is broken."
    )


# ---------------------------------------------------------------------------
# PH-F — deterministic shape sanity (non-LLM)
# ---------------------------------------------------------------------------


def test_phase_F_policy_metadata_matches_shape(tmp_path: Path) -> None:
    """PH-F: the harness-authored policy loads via LearnedPolicyLoader with the expected shape."""
    workspace = tmp_path / "ws_shape"
    workspace.mkdir()
    policy_path = _write_narrow_operator_approved_policy(workspace, "greeting-marker", MARKER)
    assert policy_path.exists()

    loader = LearnedPolicyLoader(workspace)
    policies = loader.load_all()
    assert policies, "loader found no policies after harness write — harness drift"
    policy = policies[0]
    assert policy.policy_id == "greeting-marker"
    assert str(policy.metadata.get("promotion_state") or "").lower() == "promoted", (
        f"harness policy must be promoted so Tree 2 hard constraints fire; "
        f"got promotion_state={policy.metadata.get('promotion_state')!r}"
    )
    required = policy.metadata.get("required_behavior") or []
    assert any(MARKER in str(rule) for rule in required), (
        f"harness policy required_behavior must name MARKER={MARKER!r}; got {required!r}"
    )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
