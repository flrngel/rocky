"""Live end-to-end production self-learn scenarios.

This file supersedes three prior versions (run-000228: deterministic-only;
run-004405: live but trace-only assertion; run-013706: live + real-answer
but only a marker-injection assertion). The user correctly called the
marker-injection test trivial: it proves instruction-following + retrieval
mechanics, not learning. This version tests **production-realistic**
corrections an engineer would actually give an agent, grounded in
published research (see run-20260412-023455/research.md).

Three scenarios, phased, divide-and-conquer. Each phase is a separate
pytest function asserting on exactly one observable — usually the actual
`response["text"]` from a real `rocky` subprocess against the configured
Ollama provider (`gemma4:26b` via `http://ainbr-research-fast:11434/v1`).

  SC-GEN — Generalization across lexically-different prompts
    A teach-correction given on prompt P1 ("how do I install a new
    dependency?") must transfer to a lexically-different prompt P2 in the
    same domain ("what command should I use to add a TypeScript type
    definition like @types/node?"). This is the Voyager-style skill-
    library reuse test (Wang et al., NeurIPS 2023) + Hyperagents
    cross-domain transfer (arXiv:2603.19461). Load-bearing phase:
    `test_sc_gen_phase_C_reuse_different_prompt_uses_pnpm` asserts the
    reuse answer contains `pnpm (add|install)` in a command context AND
    does not contain `npm install`.

  SC-UNDO — Rollback of a learned correction
    A user runs `rocky undo` expecting the correction to go away.
    Structurally: the policy moves from `.rocky/policies/learned/` to
    `.rocky/artifacts/rollback/` (`learning_manager.rollback_latest()`),
    the retriever returns no policies, `/learned` lists nothing. These
    PASS.
    Behaviorally: the answer no longer reflects the correction. This
    FAILS against current Rocky due to PRD §8 Issue 1 ("one correction
    becomes multiple artifacts"): `/undo` touches only the learned-policy
    store, while `.rocky/student/notebook.jsonl`,
    `.rocky/student/patterns/*.md`, `.rocky/memories/auto/*.json`, and
    `.rocky/memories/project_brief.md` retain the correction and inject
    it into the post-undo system prompt. The behavioral test is marked
    `@pytest.mark.xfail(strict=True)` so the suite still exits 0 today
    while alerting (via XPASS) if a future Phase-1 ledger-unification
    change closes this leak.

  SC-FALSEPOS — Retrieval precision
    A narrow learned policy about Python type hints must NOT fire for a
    zero-overlap unrelated prompt ("What is the capital of France?").
    Grounded in RAGAs (Es et al., EACL 2024) and BenchPreS preference-
    selectivity evaluation.

All scenarios go through the REAL `/teach` pipeline end-to-end — no
narrow-policy hand-authoring harness this run. No `unittest.mock`, no
in-process seeding, no provider patching.

Gated by `ROCKY_LLM_SMOKE=1`:

    ROCKY_LLM_SMOKE=1 ./.venv/bin/pytest tests/test_self_learn_live.py -v

Expected: 8 passed, 1 xfailed (SC-UNDO phase F), 0 failed.
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

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


SMOKE_FLAG = "ROCKY_LLM_SMOKE"
EVIDENCE_ROOT = (
    Path(__file__).resolve().parent.parent
    / "docs"
    / "xlfg"
    / "runs"
    / "run-20260412-023455"
    / "evidence"
    / "live"
)
ROCKY_BIN = os.environ.get("ROCKY_BIN", "rocky")
SUBPROCESS_TIMEOUT_S = int(os.environ.get("ROCKY_LLM_SMOKE_TIMEOUT_S", "300"))


# Load-bearing regexes for SC-GEN and SC-UNDO answer assertions.
# A pnpm command context requires "pnpm" followed by "add" or "install"
# (the canonical pnpm verbs), guarding against an offhand "pnpm" mentioned
# in prose without a command form. The npm absence regex matches the
# canonical "npm install" verb-object pattern used by the baseline gemma
# answer when it enumerates managers.
PNPM_CMD_RE = re.compile(r"pnpm\s+(add|install)", re.IGNORECASE)
NPM_INSTALL_RE = re.compile(r"npm\s+install", re.IGNORECASE)


pytestmark = pytest.mark.skipif(
    os.environ.get(SMOKE_FLAG) != "1",
    reason=(
        f"live production self-learn scenarios require {SMOKE_FLAG}=1 "
        f"(real Ollama via installed rocky CLI)"
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
            # Copy the durable stores that are load-bearing evidence for UNDO's multi-store-leak finding.
            for rel in (
                ".rocky/policies/learned",
                ".rocky/artifacts/rollback",
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
        except Exception as exc:  # pragma: no cover - evidence capture must never mask test failure
            (dest / "evidence_copy_error.txt").write_text(
                f"evidence finalizer failed: {exc}\n", encoding="utf-8"
            )

    request.addfinalizer(_copy)


# ---------------------------------------------------------------------------
# SC-GEN — Generalization across lexically-different prompts
# ---------------------------------------------------------------------------


@dataclass
class _GenResult:
    baseline: dict = field(default_factory=dict)
    teach: dict = field(default_factory=dict)
    reuse: dict = field(default_factory=dict)
    policy_id: str = ""
    policy_path: Path = field(default_factory=Path)


@pytest.fixture(scope="module")
def gen_result(request, tmp_path_factory) -> _GenResult:
    workspace = tmp_path_factory.mktemp("sc_gen_")
    captures: dict = {}
    _install_evidence_finalizer(request, "sc_gen", workspace, captures)

    baseline = _run_rocky(
        workspace,
        "How do I install a new dependency in this project?",
        label="phase_A_baseline",
        captures=captures,
    )
    teach = _run_rocky(
        workspace,
        "teach",
        "This project uses pnpm, not npm. Always use pnpm commands like 'pnpm add' "
        "or 'pnpm install' when answering questions about installing or adding packages.",
        label="phase_B_teach",
        captures=captures,
    )
    data = teach.get("data") or {}
    if not data.get("published"):
        pytest.fail(f"SC-GEN teach did not publish; data={data!r}")
    policy_id = str(data.get("policy_id") or "")
    policy_path = Path(str(data.get("policy_path") or ""))
    captures["policy_id"] = policy_id
    captures["policy_path"] = str(policy_path)

    # Reuse on a LEXICALLY DIFFERENT prompt in the same domain — the
    # generalization acid test. Teach used "install a new dependency"; reuse
    # asks about "add a TypeScript type definition like @types/node".
    # Different verb, different specificity, different technology anchor.
    reuse = _run_rocky(
        workspace,
        "What command should I use to add a TypeScript type definition like @types/node to this project?",
        label="phase_C_reuse",
        captures=captures,
    )
    return _GenResult(
        baseline=baseline, teach=teach, reuse=reuse, policy_id=policy_id, policy_path=policy_path
    )


def test_sc_gen_phase_A_baseline_no_policy(gen_result: _GenResult) -> None:
    """SC-GEN phase A: baseline has no learned policy loaded.

    The load-bearing invariant here is structural: NO policy is loaded
    yet, so the reuse phase's later positive assertion is genuinely
    policy-driven (bit-flip pair with phase C). We do NOT assert on
    baseline verification status — gemma4:26b's baseline answers are
    stochastic and the claim-support verifier may legitimately flag a
    baseline enumeration of package managers as an unsupported
    deterministic claim. That is a baseline answer quality concern, not a
    self-learn concern, so phase A's contract is limited to "no policy
    yet" + "answer non-empty".
    """
    resp = gen_result.baseline
    assert (resp.get("trace") or {}).get("selected_policies") == [], (
        f"baseline must have no policies yet; got {resp.get('trace', {}).get('selected_policies')!r}"
    )
    text = str(resp.get("text") or "")
    assert len(text) > 0, f"baseline answer must be non-empty; got {resp!r}"


def test_sc_gen_phase_B_teach_publishes(gen_result: _GenResult) -> None:
    """SC-GEN phase B: /teach publishes a candidate policy with round-trippable id."""
    data = gen_result.teach.get("data") or {}
    assert data.get("published") is True
    assert data.get("promotion_state") == "candidate", data
    assert gen_result.policy_path.exists(), gen_result.policy_path
    meta_path = gen_result.policy_path.parent / "POLICY.meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert str(meta.get("policy_id") or "") == gen_result.policy_id


def test_sc_gen_phase_C_reuse_different_prompt_uses_pnpm(gen_result: _GenResult) -> None:
    """SC-GEN phase C: the load-bearing generalization proof.

    Teach prompt P1 ("How do I install a new dependency in this project?")
    and reuse prompt P2 ("What command should I use to add a TypeScript
    type definition like @types/node to this project?") differ in verb,
    specificity, and technology anchor. The learned correction ("this
    project uses pnpm, not npm") must transfer to P2 because it names the
    behavior, not the prompt. Context probe in run-20260412-023455 context
    phase confirmed gemma4:26b does transfer: response text was
    `"pnpm add -D @types/node"`. A bit-flip control in a fresh workspace
    (no /teach) produces a neutral enumeration of all four package
    managers — so a pnpm-only answer here is genuinely policy-driven.
    """
    resp = gen_result.reuse
    text = str(resp.get("text") or "")
    assert len(text) > 0, f"reuse answer empty; resp={resp!r}"
    assert PNPM_CMD_RE.search(text), (
        f"SC-GEN-C REAL ANSWER CHECK FAILED: expected a pnpm command form "
        f"(matching {PNPM_CMD_RE.pattern!r}) in the reuse answer; got text={text!r}"
    )
    assert not NPM_INSTALL_RE.search(text), (
        f"SC-GEN-C REAL ANSWER CHECK FAILED: expected no `npm install` in "
        f"the reuse answer; got text={text!r}"
    )
    selected = (resp.get("trace") or {}).get("selected_policies") or []
    assert gen_result.policy_id in selected, (
        f"reuse must load the taught policy {gen_result.policy_id!r}; "
        f"trace.selected_policies={selected!r}"
    )


# ---------------------------------------------------------------------------
# SC-UNDO — Rollback (structural PASS + behavioral XFAIL)
# ---------------------------------------------------------------------------


@dataclass
class _UndoResult:
    baseline: dict = field(default_factory=dict)
    teach: dict = field(default_factory=dict)
    reuse_before_undo: dict = field(default_factory=dict)
    undo_response: dict = field(default_factory=dict)
    learned_list_after_undo: dict = field(default_factory=dict)
    reuse_after_undo: dict = field(default_factory=dict)
    policy_id: str = ""
    policy_path: Path = field(default_factory=Path)
    workspace: Path = field(default_factory=Path)


@pytest.fixture(scope="module")
def undo_result(request, tmp_path_factory) -> _UndoResult:
    workspace = tmp_path_factory.mktemp("sc_undo_")
    captures: dict = {}
    _install_evidence_finalizer(request, "sc_undo", workspace, captures)

    baseline = _run_rocky(
        workspace,
        "How do I install a new dependency in this project?",
        label="phase_A_baseline",
        captures=captures,
    )
    teach = _run_rocky(
        workspace,
        "teach",
        "This project uses pnpm, not npm. Always use pnpm commands like 'pnpm add' "
        "or 'pnpm install' when answering questions about installing or adding packages.",
        label="phase_B_teach",
        captures=captures,
    )
    data = teach.get("data") or {}
    if not data.get("published"):
        pytest.fail(f"SC-UNDO teach did not publish; data={data!r}")
    policy_id = str(data.get("policy_id") or "")
    policy_path = Path(str(data.get("policy_path") or ""))

    reuse_before_undo = _run_rocky(
        workspace,
        "What command should I use to install axios?",
        label="phase_C_reuse_before_undo",
        captures=captures,
    )

    undo_response = _run_rocky(
        workspace,
        "undo",
        label="phase_D_undo",
        captures=captures,
    )

    learned_list = _run_rocky(
        workspace,
        "learned",
        label="phase_E_learned_list",
        captures=captures,
    )

    reuse_after_undo = _run_rocky(
        workspace,
        "What command should I use to install axios?",
        label="phase_F_reuse_after_undo",
        captures=captures,
    )

    captures["policy_id"] = policy_id
    captures["policy_path"] = str(policy_path)

    return _UndoResult(
        baseline=baseline,
        teach=teach,
        reuse_before_undo=reuse_before_undo,
        undo_response=undo_response,
        learned_list_after_undo=learned_list,
        reuse_after_undo=reuse_after_undo,
        policy_id=policy_id,
        policy_path=policy_path,
        workspace=workspace,
    )


def test_sc_undo_phase_C_correction_applies_before_undo(undo_result: _UndoResult) -> None:
    """SC-UNDO phase C: before /undo, the correction is live in the answer."""
    resp = undo_result.reuse_before_undo
    text = str(resp.get("text") or "")
    selected = (resp.get("trace") or {}).get("selected_policies") or []
    assert undo_result.policy_id in selected, (
        f"pre-undo reuse must have the policy loaded; selected={selected!r}"
    )
    assert PNPM_CMD_RE.search(text), (
        f"pre-undo reuse must reflect the correction (pnpm command form); text={text!r}"
    )


def test_sc_undo_phase_D_undo_moves_policy_to_rollback(undo_result: _UndoResult) -> None:
    """SC-UNDO phase D: /undo reports rolled_back=True and moves the policy directory."""
    resp = undo_result.undo_response
    assert resp.get("name") == "undo", f"expected command name 'undo'; got {resp.get('name')!r}"
    data = resp.get("data") or {}
    assert data.get("rolled_back") is True, f"data.rolled_back must be True; data={data!r}"
    to_path = Path(str(data.get("to") or ""))
    assert to_path.exists() and to_path.is_dir(), (
        f"rollback target directory must exist after undo; to={to_path}"
    )
    assert not undo_result.policy_path.exists(), (
        f"original policy dir must be gone after undo; still at {undo_result.policy_path}"
    )


def test_sc_undo_phase_E_structural_retrieval_empty(undo_result: _UndoResult) -> None:
    """SC-UNDO phase E: post-undo retrieval returns no policies (structural)."""
    resp_learned = undo_result.learned_list_after_undo
    data = resp_learned.get("data") or {}
    assert data.get("learned") == [], (
        f"/learned must list no policies after undo; got {data.get('learned')!r}"
    )
    resp_reuse = undo_result.reuse_after_undo
    selected = (resp_reuse.get("trace") or {}).get("selected_policies") or []
    assert selected == [], (
        f"post-undo reuse must have no policies loaded; selected_policies={selected!r}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "PRD §8 Issue 1 — multi-store leak. `/undo` via runtime.undo() → "
        "learning_manager.rollback_latest() only moves .rocky/policies/learned/<id>/ "
        "to .rocky/artifacts/rollback/, but leaves .rocky/student/notebook.jsonl, "
        ".rocky/student/patterns/*.md, .rocky/student/retrospectives/*.md, "
        ".rocky/memories/auto/*.json, and .rocky/memories/project_brief.md intact. "
        "Those artifacts inject the correction into the post-undo system prompt, "
        "so the reuse answer still emits the pnpm command even though the policy "
        "itself is rolled back. Worse, `AgentCore`'s self-learning auto-retrospection "
        "(`self_learning.persisted=True` in the post-undo trace) writes NEW "
        "retrospective + pattern artifacts during the post-undo reuse turn itself, "
        "re-widening the leak on every interaction. A Phase 1 fix must both collapse "
        "existing stores AND gate self_learning promotion on rollback state. "
        "Owner: PRD Phase 1 (canonical learning ledger). Run-20260412-023455 "
        "context probe + architecture review captured this with gemma4:26b. An "
        "XPASS here means someone fixed the multi-store leak AND prevented post-undo "
        "self_learning re-persistence — please update/remove this xfail."
    ),
)
def test_sc_undo_phase_F_behavioral_correction_gone(undo_result: _UndoResult) -> None:
    """SC-UNDO phase F: BEHAVIORAL — post-undo answer must not reflect the correction.

    This is the user-facing contract of `/undo`: the learned behavior goes
    away. It currently fails because of the multi-store leak documented in
    the xfail reason. Keeping this as a strict XFAIL so the suite stays
    green today while alerting (XPASS) on any future fix.
    """
    resp = undo_result.reuse_after_undo
    text = str(resp.get("text") or "")
    assert not PNPM_CMD_RE.search(text), (
        f"BEHAVIORAL: post-undo answer must NOT contain a pnpm command form "
        f"(matching {PNPM_CMD_RE.pattern!r}); got text={text!r}"
    )


# ---------------------------------------------------------------------------
# SC-FALSEPOS — Retrieval precision on zero-overlap unrelated prompt
# ---------------------------------------------------------------------------


@dataclass
class _FpResult:
    baseline: dict = field(default_factory=dict)
    teach: dict = field(default_factory=dict)
    unrelated_reuse: dict = field(default_factory=dict)
    policy_id: str = ""


@pytest.fixture(scope="module")
def fp_result(request, tmp_path_factory) -> _FpResult:
    workspace = tmp_path_factory.mktemp("sc_fp_")
    captures: dict = {}
    _install_evidence_finalizer(request, "sc_fp", workspace, captures)

    baseline = _run_rocky(
        workspace,
        "Write a Python function that reads a file and returns its lines.",
        label="phase_A_baseline",
        captures=captures,
    )
    teach = _run_rocky(
        workspace,
        "teach",
        "For Python code, always add type hints to function parameters and return types. "
        "Use 'from __future__ import annotations' at the top of every new Python file.",
        label="phase_A_teach",
        captures=captures,
    )
    data = teach.get("data") or {}
    if not data.get("published"):
        pytest.fail(f"SC-FALSEPOS teach did not publish; data={data!r}")
    policy_id = str(data.get("policy_id") or "")
    captures["policy_id"] = policy_id

    unrelated = _run_rocky(
        workspace,
        "What is the capital of France?",
        label="phase_B_unrelated",
        captures=captures,
    )
    return _FpResult(
        baseline=baseline, teach=teach, unrelated_reuse=unrelated, policy_id=policy_id
    )


def test_sc_fp_phase_A_teach_narrow_python_policy(fp_result: _FpResult) -> None:
    """SC-FALSEPOS phase A: teach a narrow Python-scoped policy."""
    data = fp_result.teach.get("data") or {}
    assert data.get("published") is True, data
    assert fp_result.policy_id, "policy_id missing from teach response"


def test_sc_fp_phase_B_unrelated_prompt_does_not_load_policy(fp_result: _FpResult) -> None:
    """SC-FALSEPOS phase B: unrelated geography prompt MUST NOT load the Python policy.

    Taught policy is scoped to Python code. Reuse prompt is "What is the
    capital of France?" — zero token overlap with Python/type-hints/
    annotation triggers. A well-behaved retriever must return no policies
    for this prompt. An over-eager retriever that still fires on incidental
    token overlap would fail this test — an honest RED indicating a
    retrieval precision bug (RAGAs-style false positive).
    """
    resp = fp_result.unrelated_reuse
    text = str(resp.get("text") or "")
    selected = (resp.get("trace") or {}).get("selected_policies") or []
    assert fp_result.policy_id not in selected, (
        f"SC-FP-B FAIL: Python-scoped policy {fp_result.policy_id!r} must NOT "
        f"fire for a geography prompt; selected_policies={selected!r}"
    )
    assert selected == [], (
        f"SC-FP-B FAIL: retrieval silence expected; selected_policies={selected!r}"
    )
    assert len(text) > 0, f"answer empty; resp={resp!r}"
    assert "paris" in text.lower(), (
        f"SC-FP-B: sanity — geography question should still be answered; got text={text!r}"
    )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
