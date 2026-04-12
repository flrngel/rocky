"""Live end-to-end scenarios for Rocky's AUTONOMOUS self-learning pathways.

This file supersedes FOUR prior versions:
  run-000228 — deterministic in-process unit tests.
  run-004405 — live subprocess but trace-only assertions.
  run-013706 — live + real-answer but MARKER-INJECTION trivial.
  run-023455 — production-realistic but /teach-centric (teacher-initiated)
                + an irrelevant UNDO scenario.

The user's fifth correction was explicit: "focus on SELF-LEARNING … in
VARIOUS OF CASES." Self-learning in Rocky means the agent updates its
own durable state AUTONOMOUSLY during normal `run_prompt` turns, without
the user invoking `/teach`. This file exercises four such pathways:

  SL-MEMORY      `MemoryStore.capture_project_memory` auto-classifies a
                 preference/constraint from a normal prompt, writes a
                 record to `.rocky/memories/candidates/` and — when the
                 classifier+provenance threshold is met — auto-promotes
                 it to `.rocky/memories/auto/`. A fresh subprocess then
                 surfaces that memory in its system prompt and the real
                 answer reflects the captured preference. NO /teach.

  SL-RETROSPECT  `_auto_self_reflect` (app.py:232) fires at the end of
                 every non-META `run_prompt` with non-empty answer and
                 calls `retrospect_episode`. For a substantive task
                 (file creation + verification) the synthesizer returns
                 `should_persist=True` and writes both
                 `.rocky/artifacts/self_reflections/retro_*.json` and
                 `.rocky/student/retrospectives/*.md`. A fresh subprocess
                 on a similar task loads the retrospective via
                 `trace.context.student_notes` and the answer carries
                 the same style (type annotations + verification). NO
                 /teach.

  SL-PROMOTE     After a `/teach` seeds a candidate policy, the next
                 `run_prompt` that matches and succeeds
                 (`verification.status == "pass"`) triggers
                 `record_query` (manager.py:394-411) which calls
                 `_promote_policy_meta` autonomously — no operator
                 action. The test asserts the disk state transition on
                 POLICY.meta.json: candidate → promoted,
                 verified_success_count: 0 → 1. `/teach` is SETUP here;
                 the load-bearing assertion is the autonomous
                 transition, not the /teach.

  SL-BRIEF       `MemoryStore.rebuild_project_brief` (store.py:706)
                 runs unconditionally at the end of every
                 `capture_project_memory` call and writes
                 `.rocky/memories/project_brief.md` aggregating promoted
                 auto-memories. A fresh subprocess's
                 `trace.context.memories` lists a `project-brief` record
                 that was injected into its system prompt. NO /teach.

Gated by `ROCKY_LLM_SMOKE=1`. `ROCKY_BIN` defaults to `.venv/bin/rocky`
(the editable-install binary) if it exists, so the tests exercise the
current source tree and not a stale pipx install. All assertions either
check a concrete disk artifact written by the agent without /teach, OR
the actual `response["text"]` from a fresh `rocky` subprocess call.

    ROCKY_LLM_SMOKE=1 ./.venv/bin/pytest tests/test_self_learn_live.py -v
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
_VENV_ROCKY = _REPO_ROOT / ".venv" / "bin" / "rocky"
DEFAULT_ROCKY_BIN = str(_VENV_ROCKY) if _VENV_ROCKY.exists() else "rocky"
ROCKY_BIN = os.environ.get("ROCKY_BIN", DEFAULT_ROCKY_BIN)

SMOKE_FLAG = "ROCKY_LLM_SMOKE"
EVIDENCE_ROOT = (
    _REPO_ROOT
    / "docs"
    / "xlfg"
    / "runs"
    / "run-20260412-032319"
    / "evidence"
    / "live"
)
SUBPROCESS_TIMEOUT_S = int(os.environ.get("ROCKY_LLM_SMOKE_TIMEOUT_S", "300"))

# SL-RETROSPECT T1's retrospective is titled "Python functional verification
# via shell one-liners" — the retrospective captured the *verification style*
# the agent used (running the function from the shell to prove correctness).
# The T2 prompt asks for a new function with type hints + verification, but
# does NOT prescribe HOW to verify. If the retrospective crossed the process
# boundary and actually shaped generation, T2 will verify using a shell
# invocation pattern (e.g., `python3 -c "..."`, `python divider.py`, or
# equivalent). This regex is chosen deliberately to match a style element that
# the T2 prompt does not mandate — so the assertion measures retrospective
# influence, not prompt compliance.
SHELL_VERIFICATION_RE = re.compile(
    r"python3?\s+(-c\s|\S+\.py)|>>>\s|(?:^|\n)\s*\$\s*python",
    re.IGNORECASE | re.MULTILINE,
)
PNPM_CMD_RE = re.compile(r"pnpm\s+(add|install)", re.IGNORECASE)


pytestmark = pytest.mark.skipif(
    os.environ.get(SMOKE_FLAG) != "1",
    reason=(
        f"autonomous self-learn live scenarios require {SMOKE_FLAG}=1 "
        f"(real Ollama via editable rocky at {ROCKY_BIN})"
    ),
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
            f"autonomous self-learn: `rocky` timed out at label={label} after "
            f"{SUBPROCESS_TIMEOUT_S}s; cmd={cmd}"
        )
    captures[f"{label}__cmd"] = cmd
    captures[f"{label}__returncode"] = proc.returncode
    captures[f"{label}__stdout"] = proc.stdout
    captures[f"{label}__stderr"] = proc.stderr
    if proc.returncode != 0:
        pytest.fail(
            f"autonomous self-learn: rocky exited {proc.returncode} at label={label}\n"
            f"cmd={cmd}\nstderr={proc.stderr[:2000]}\nstdout={proc.stdout[:2000]}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(
            f"autonomous self-learn: non-JSON stdout at label={label}: {exc}\n"
            f"stdout={proc.stdout[:2000]}"
        )


def _install_evidence_finalizer(
    request, scenario: str, workspace: Path, captures: dict
) -> None:
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
            for rel in (
                ".rocky/policies/learned",
                ".rocky/artifacts/rollback",
                ".rocky/artifacts/self_reflections",
                ".rocky/artifacts/learning_reflections",
                ".rocky/student",
                ".rocky/memories",
            ):
                src = workspace / rel
                if not src.exists():
                    continue
                snap = dest / f"snapshot__{rel.replace('/', '__').lstrip('_')}"
                if snap.exists():
                    shutil.rmtree(snap, ignore_errors=True)
                shutil.copytree(src, snap, dirs_exist_ok=True)
            traces_root = workspace / ".rocky" / "traces"
            if traces_root.exists():
                traces_snap = dest / "traces_snapshot"
                traces_snap.mkdir(parents=True, exist_ok=True)
                for tr in sorted(traces_root.glob("*.json"))[-6:]:
                    shutil.copy2(tr, traces_snap / tr.name)
        except Exception as exc:  # pragma: no cover - evidence must not mask failures
            (dest / "evidence_copy_error.txt").write_text(
                f"evidence finalizer failed: {exc}\n", encoding="utf-8"
            )

    request.addfinalizer(_copy)


def _context_memories(response: dict) -> list[dict]:
    trace = response.get("trace") or {}
    context = trace.get("context") or {}
    return list(context.get("memories") or [])


def _context_student_notes(response: dict) -> list[dict]:
    trace = response.get("trace") or {}
    context = trace.get("context") or {}
    return list(context.get("student_notes") or [])


# ---------------------------------------------------------------------------
# SL-MEMORY — autonomous preference capture from a normal prompt
# ---------------------------------------------------------------------------


@dataclass
class _MemoryResult:
    t1: dict = field(default_factory=dict)
    t2: dict = field(default_factory=dict)
    workspace: Path = field(default_factory=Path)


@pytest.fixture(scope="module")
def memory_result(request, tmp_path_factory) -> _MemoryResult:
    workspace = tmp_path_factory.mktemp("sl_memory_")
    captures: dict = {}
    _install_evidence_finalizer(request, "sl_memory", workspace, captures)

    # T1: a normal run_prompt that states a team preference. No /teach.
    # The word "prefer" triggers _classify_text -> "preference" (store.py:453).
    # With user_asserted provenance and stability_score >= 0.7, _should_promote
    # auto-promotes the candidate to .rocky/memories/auto/ — all inside the
    # same capture_project_memory call that also rebuilds project_brief.md.
    t1 = _run_rocky(
        workspace,
        "Our team prefers using uv for all package installs — never pip directly. "
        "Please confirm this is understood.",
        label="t1_preference_prompt",
        captures=captures,
    )

    # T2: fresh subprocess asks a related but lexically different question.
    # If the auto-memory fired, it will be injected via ContextBuilder and
    # the answer will reference "uv".
    t2 = _run_rocky(
        workspace,
        "What package manager should I use for Python installs in this project?",
        label="t2_related_question",
        captures=captures,
    )
    return _MemoryResult(t1=t1, t2=t2, workspace=workspace)


def test_sl_memory_phase_A_auto_promoted_memory_written(memory_result: _MemoryResult) -> None:
    """SL-MEMORY phase A: autonomous preference memory is written to disk.

    No /teach was invoked. The T1 prompt is a normal `run_prompt` with a
    preference statement. `MemoryStore.capture_project_memory` must have
    auto-classified and auto-promoted the candidate.
    """
    auto_dir = memory_result.workspace / ".rocky" / "memories" / "auto"
    assert auto_dir.exists(), f"expected {auto_dir} to exist after T1 with no /teach"
    records = list(auto_dir.glob("*.json"))
    assert records, (
        f"SL-MEMORY phase A FAILED: no auto-memory json files under {auto_dir}. "
        f"MemoryStore.capture_project_memory did not auto-write/promote any record. "
        f"_classify_text may have returned None for the prompt."
    )
    matched = []
    for rec in records:
        payload = json.loads(rec.read_text(encoding="utf-8"))
        text = str(payload.get("text") or "").lower()
        kind = str(payload.get("kind") or "")
        promotion = str(payload.get("promotion_state") or "")
        if "uv" in text and kind in {"constraint", "preference", "workflow_rule"} and promotion == "promoted":
            matched.append(payload)
    assert matched, (
        f"expected at least one auto-memory with kind in {{constraint,preference,workflow_rule}}, "
        f"promotion_state=promoted, and text containing 'uv'; got records={[r.name for r in records]}"
    )


def test_sl_memory_phase_B_fresh_subprocess_reflects_preference(
    memory_result: _MemoryResult,
) -> None:
    """SL-MEMORY phase B: a fresh subprocess's answer reflects the auto-learned preference.

    Proves the autonomous memory crossed the process boundary via the
    real ContextBuilder → LearnedPolicyRetriever / MemoryRetriever →
    system-prompt path and that the model honored it in real output.
    """
    resp = memory_result.t2
    text = str(resp.get("text") or "")
    assert len(text) > 0, f"T2 answer empty; resp={resp!r}"
    assert "uv" in text.lower(), (
        f"SL-MEMORY phase B REAL ANSWER FAILED: T2 answer does not mention the auto-learned "
        f"preference 'uv'. text={text!r}. Either the auto-memory wasn't retrieved "
        f"into context, or the model ignored it."
    )
    memories = _context_memories(resp)
    mem_names = [m.get("name") or m.get("id") for m in memories]
    assert any(
        (isinstance(n, str) and "uv" in n.lower()) or (isinstance(n, str) and "prefer" in n.lower()) or n == "project-brief"
        for n in mem_names
    ), (
        f"expected T2 trace.context.memories to include the auto-memory record or "
        f"project-brief; got names={mem_names!r}"
    )


# ---------------------------------------------------------------------------
# SL-RETROSPECT — autonomous self-reflection persists and influences next turn
# ---------------------------------------------------------------------------


@dataclass
class _RetrospectResult:
    t1: dict = field(default_factory=dict)
    t2: dict = field(default_factory=dict)
    workspace: Path = field(default_factory=Path)


@pytest.fixture(scope="module")
def retrospect_result(request, tmp_path_factory) -> _RetrospectResult:
    workspace = tmp_path_factory.mktemp("sl_retrospect_")
    captures: dict = {}
    _install_evidence_finalizer(request, "sl_retrospect", workspace, captures)

    # T1: a substantive task (file creation + verification) that produces
    # non-trivial tool events. The synthesizer's retrospect_episode model call
    # will typically set should_persist=True for this shape of task.
    t1 = _run_rocky(
        workspace,
        "Create a file called calculator.py that defines a Python function named multiply "
        "that multiplies two integers with type hints and a docstring. Then run it to verify "
        "it works.",
        label="t1_substantive_task",
        captures=captures,
    )

    # T2: fresh subprocess on a similar task. The prior retrospective should
    # load into trace.context.student_notes and influence the answer style.
    t2 = _run_rocky(
        workspace,
        "Create a file called divider.py that defines a Python function named divide that "
        "divides two integers with type hints. Verify it works.",
        label="t2_similar_task",
        captures=captures,
    )
    return _RetrospectResult(t1=t1, t2=t2, workspace=workspace)


def test_sl_retrospect_phase_A_self_reflection_persisted(
    retrospect_result: _RetrospectResult,
) -> None:
    """SL-RETROSPECT phase A: autonomous retrospective is persisted after T1.

    `_auto_self_reflect` runs in app.py:232 after every non-META run_prompt,
    calls `retrospect_episode`, and when the model returns `should_persist=True`
    writes artifacts to two distinct locations. No /teach invoked.
    """
    trace = retrospect_result.t1.get("trace") or {}
    sl = trace.get("self_learning") or {}
    assert sl.get("persisted") is True, (
        f"SL-RETROSPECT phase A FAILED: trace.self_learning.persisted is not True. "
        f"Either _auto_self_reflect didn't fire, or the synthesizer returned "
        f"should_persist=False for this task. trace.self_learning={sl!r}"
    )
    self_reflections_dir = retrospect_result.workspace / ".rocky" / "artifacts" / "self_reflections"
    retro_files = list(self_reflections_dir.glob("retro_*.json"))
    assert retro_files, (
        f"expected at least one retro_*.json under {self_reflections_dir}; got {list(self_reflections_dir.glob('*'))}"
    )
    retro_notes_dir = retrospect_result.workspace / ".rocky" / "student" / "retrospectives"
    retro_notes = list(retro_notes_dir.glob("*.md"))
    assert retro_notes, (
        f"expected at least one retrospective markdown under {retro_notes_dir}; got {list(retro_notes_dir.glob('*'))}"
    )


def test_sl_retrospect_phase_B_structural_retrospective_loaded(
    retrospect_result: _RetrospectResult,
) -> None:
    """SL-RETROSPECT phase B structural: the autonomous retrospective is loaded into T2's context.

    The retrospective artifact from phase A must cross the process
    boundary into T2's system prompt. Asserted by the presence of a
    `kind=retrospective` entry in `trace.context.student_notes`. This is
    the structural load-bearing proof that the autonomous pathway reached
    the next turn's context.
    """
    resp = retrospect_result.t2
    text = str(resp.get("text") or "")
    assert len(text) > 0, f"T2 answer empty; resp={resp!r}"
    notes = _context_student_notes(resp)
    retrospectives = [n for n in notes if str(n.get("kind") or "") == "retrospective"]
    assert retrospectives, (
        f"SL-RETROSPECT phase B structural FAILED: T2 trace.context.student_notes "
        f"contains no retrospective entry. student_notes={notes!r}. The autonomous "
        f"retrospective did not cross the process boundary into T2's context."
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "gemma4:26b — autonomous retrospective loads into T2's context (structural "
        "phase B passes) but does NOT reliably shape generation in a style-specific "
        "way. Live probe captured the retrospective title 'Python functional "
        "verification via shell one-liners' persisted to disk and loaded into T2's "
        "context.student_notes, yet the T2 answer for 'Create divider.py that divides "
        "two integers with type hints — verify it works' emits an 'Observed output:' "
        "code block instead of a `python3 -c` shell one-liner. The retrospective "
        "content is INJECTED but does not measurably influence the model's chosen "
        "verification form. This is an honest self-learning limitation of the "
        "retrospect → generation pipeline against the current model: retrospectives "
        "persist but their style-specific guidance is diluted in context. An XPASS "
        "here means either a ranker/context-packer change made retrospective style "
        "more influential (Phase 2 retrieval rewrite target) or a stronger model was "
        "swapped in — update/remove this xfail when that happens."
    ),
)
def test_sl_retrospect_phase_B_behavioral_style_carries_over(
    retrospect_result: _RetrospectResult,
) -> None:
    """SL-RETROSPECT phase B behavioral (XFAIL): retrospective style shapes T2 generation.

    The retrospective persisted in phase A is titled 'Python functional
    verification via shell one-liners' — it captured a *verification
    style* (running `python3 -c "..."` to prove a function works) that
    is NOT mandated by the T2 prompt. If the retrospective genuinely
    shapes generation, T2 will verify using a shell invocation pattern.
    Currently (live-verified), the retrospective loads into context but
    does not measurably influence verification form. Marked
    `xfail(strict=True)` so any future improvement (stronger model,
    ranker change, or context packer fix) trips XPASS and forces this
    xfail to be removed.
    """
    resp = retrospect_result.t2
    text = str(resp.get("text") or "")
    assert SHELL_VERIFICATION_RE.search(text), (
        f"SL-RETROSPECT phase B BEHAVIORAL: expected a shell-verification pattern "
        f"(matching {SHELL_VERIFICATION_RE.pattern!r}) in T2 answer — this style was "
        f"captured by the autonomous retrospective and is NOT requested by the T2 "
        f"prompt. Got text={text!r}."
    )


# ---------------------------------------------------------------------------
# SL-PROMOTE — autonomous candidate→promoted transition on verified reuse
# ---------------------------------------------------------------------------


@dataclass
class _PromoteResult:
    baseline: dict = field(default_factory=dict)
    teach: dict = field(default_factory=dict)
    reuse: dict = field(default_factory=dict)
    meta_before: dict = field(default_factory=dict)
    meta_after: dict = field(default_factory=dict)
    policy_id: str = ""
    policy_path: Path = field(default_factory=Path)
    workspace: Path = field(default_factory=Path)


@pytest.fixture(scope="module")
def promote_result(request, tmp_path_factory) -> _PromoteResult:
    workspace = tmp_path_factory.mktemp("sl_promote_")
    captures: dict = {}
    _install_evidence_finalizer(request, "sl_promote", workspace, captures)

    baseline = _run_rocky(
        workspace,
        "How do I install a new dependency in this project?",
        label="t1_baseline",
        captures=captures,
    )
    # /teach is SETUP here. The load-bearing assertion is phase C's
    # autonomous transition triggered by record_query, not this /teach call.
    teach = _run_rocky(
        workspace,
        "teach",
        "This project uses pnpm, not npm. Always use pnpm commands like 'pnpm add' for package installs.",
        label="t2_teach_setup",
        captures=captures,
    )
    data = teach.get("data") or {}
    if not data.get("published"):
        pytest.fail(f"SL-PROMOTE setup teach did not publish; data={data!r}")
    policy_id = str(data.get("policy_id") or "")
    policy_path = Path(str(data.get("policy_path") or ""))
    meta_path = policy_path.parent / "POLICY.meta.json"
    meta_before = json.loads(meta_path.read_text(encoding="utf-8"))
    captures["meta_before"] = meta_before

    # T3 fresh reuse that should trigger autonomous promotion via record_query.
    reuse = _run_rocky(
        workspace,
        "What command should I use to add axios?",
        label="t3_reuse_triggers_autonomous_promotion",
        captures=captures,
    )
    meta_after = json.loads(meta_path.read_text(encoding="utf-8"))
    captures["meta_after"] = meta_after

    return _PromoteResult(
        baseline=baseline,
        teach=teach,
        reuse=reuse,
        meta_before=meta_before,
        meta_after=meta_after,
        policy_id=policy_id,
        policy_path=policy_path,
        workspace=workspace,
    )


def test_sl_promote_phase_A_candidate_before_reuse(promote_result: _PromoteResult) -> None:
    """SL-PROMOTE phase A: after setup /teach, the policy is candidate with 0 verified successes."""
    meta = promote_result.meta_before
    metadata = meta.get("metadata") or {}
    assert str(metadata.get("promotion_state") or "").lower() == "candidate", (
        f"expected candidate before reuse; meta_before={meta!r}"
    )
    assert int(metadata.get("verified_success_count") or 0) == 0, (
        f"expected verified_success_count=0 before reuse; meta_before={meta!r}"
    )


def test_sl_promote_phase_B_reuse_succeeds(promote_result: _PromoteResult) -> None:
    """SL-PROMOTE phase B: fresh subprocess reuse succeeds and emits the pnpm command."""
    resp = promote_result.reuse
    assert (resp.get("verification") or {}).get("status") == "pass", (
        f"SL-PROMOTE reuse verification must pass for record_query to trigger autonomous "
        f"promotion; got verification={resp.get('verification')!r}"
    )
    selected = (resp.get("trace") or {}).get("selected_policies") or []
    assert promote_result.policy_id in selected, (
        f"reuse must load the candidate policy {promote_result.policy_id!r}; "
        f"selected_policies={selected!r}"
    )
    text = str(resp.get("text") or "")
    assert PNPM_CMD_RE.search(text), (
        f"reuse answer must contain a pnpm command form; got text={text!r}"
    )


def test_sl_promote_phase_C_autonomous_promotion(promote_result: _PromoteResult) -> None:
    """SL-PROMOTE phase C: the load-bearing autonomous transition.

    `record_query` (manager.py:394-411) autonomously calls
    `_promote_policy_meta` when the candidate was reused with
    `result == "success"` and `verified_success_count >= 1`. No operator
    action triggered this — it is pure self-learning from the reuse
    outcome. We assert the disk state transition on POLICY.meta.json.
    """
    before = promote_result.meta_before
    after = promote_result.meta_after
    before_meta = before.get("metadata") or {}
    after_meta = after.get("metadata") or {}
    assert str(after_meta.get("promotion_state") or "").lower() == "promoted", (
        f"SL-PROMOTE phase C FAILED: metadata.promotion_state did not autonomously "
        f"transition to 'promoted' after a verified reuse. before={before_meta!r}, "
        f"after={after_meta!r}. record_query's promotion branch did not fire."
    )
    assert str(after.get("promotion_state") or "").lower() == "promoted", (
        f"SL-PROMOTE phase C FAILED: top-level promotion_state did not transition to "
        f"'promoted'; before={before.get('promotion_state')!r}, after={after.get('promotion_state')!r}. "
        f"_promote_policy_meta did not sync the top-level field (run 000228 loopback fix)."
    )
    assert int(after_meta.get("verified_success_count") or 0) >= 1, (
        f"SL-PROMOTE phase C FAILED: verified_success_count did not autonomously increment; "
        f"before={before_meta.get('verified_success_count')!r}, after={after_meta.get('verified_success_count')!r}"
    )


# ---------------------------------------------------------------------------
# SL-BRIEF — autonomous project-brief synthesis from promoted memories
# ---------------------------------------------------------------------------


@dataclass
class _BriefResult:
    t1: dict = field(default_factory=dict)
    t2: dict = field(default_factory=dict)
    workspace: Path = field(default_factory=Path)


@pytest.fixture(scope="module")
def brief_result(request, tmp_path_factory) -> _BriefResult:
    workspace = tmp_path_factory.mktemp("sl_brief_")
    captures: dict = {}
    _install_evidence_finalizer(request, "sl_brief", workspace, captures)

    t1 = _run_rocky(
        workspace,
        "Our team prefers using uv for all package installs — never pip directly. "
        "Please confirm this is understood.",
        label="t1_preference",
        captures=captures,
    )
    t2 = _run_rocky(
        workspace,
        "Give me a one-sentence summary of this project.",
        label="t2_summary_question",
        captures=captures,
    )
    return _BriefResult(t1=t1, t2=t2, workspace=workspace)


def test_sl_brief_phase_A_project_brief_synthesised(brief_result: _BriefResult) -> None:
    """SL-BRIEF phase A: autonomous project brief is written to disk after T1."""
    brief_path = brief_result.workspace / ".rocky" / "memories" / "project_brief.md"
    assert brief_path.exists(), (
        f"SL-BRIEF phase A FAILED: {brief_path} not synthesised after T1 with no /teach. "
        f"rebuild_project_brief did not run or found no promoted memories to include."
    )
    body = brief_path.read_text(encoding="utf-8")
    assert len(body.strip()) > 0, f"project_brief.md is empty; body={body!r}"
    assert "uv" in body.lower(), (
        f"expected project_brief.md to reference the seeded preference 'uv'; body={body!r}"
    )


def test_sl_brief_phase_B_fresh_subprocess_loads_brief(brief_result: _BriefResult) -> None:
    """SL-BRIEF phase B: the autonomous brief is injected into T2's context."""
    memories = _context_memories(brief_result.t2)
    names = [m.get("name") or m.get("id") for m in memories]
    assert any(
        isinstance(n, str) and "brief" in n.lower()
        for n in names
    ), (
        f"SL-BRIEF phase B FAILED: T2 trace.context.memories did not include a "
        f"project-brief entry; names={names!r}. The autonomously-synthesised brief "
        f"did not reach the fresh subprocess's system prompt."
    )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
