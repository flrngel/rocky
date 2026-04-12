"""Live end-to-end self-learn verification.

These scenarios drive the installed `rocky` CLI as a subprocess against the
real configured provider (Ollama, `gemma4:26b` per ~/.config/rocky/config.yaml).
They implement the proof that the prior run's in-process deterministic tests
left unspoken: that a real teach→publish→reuse cycle works across fresh
subprocess boundaries, that the candidate promotion gate is driven by disk
state, and that removing the on-disk policy file defeats retrieval.

Gated by `ROCKY_LLM_SMOKE=1` so bare `pytest -q` on machines without Ollama
continues to pass. To run this suite:

    ROCKY_LLM_SMOKE=1 ./.venv/bin/pytest tests/test_self_learn_live.py -v

Per-call subprocess timeout: 300s. Nominal per-scenario cost: 4–7 minutes at
60–120s per model round-trip.

Primary observable across all scenarios: `trace.selected_policies` — the list
of learned policy names that were loaded into the context for that run. This
is the load-bearing signal because it is emitted by `AgentCore` directly
from `context.learned_policies`, which is built by `ContextBuilder` from the
real `.rocky/policies/learned/` on-disk store. There is no path for a test
helper or a hard-coded shortcut to forge this field.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


SMOKE_FLAG = "ROCKY_LLM_SMOKE"
EVIDENCE_ROOT = (
    Path(__file__).resolve().parent.parent
    / "docs"
    / "xlfg"
    / "runs"
    / "run-20260412-004405"
    / "evidence"
    / "live"
)
ROCKY_BIN = os.environ.get("ROCKY_BIN", "rocky")
SUBPROCESS_TIMEOUT_S = int(os.environ.get("ROCKY_LLM_SMOKE_TIMEOUT_S", "300"))


pytestmark = pytest.mark.skipif(
    os.environ.get(SMOKE_FLAG) != "1",
    reason=f"live self-learn scenarios require {SMOKE_FLAG}=1 (real Ollama via installed rocky CLI)",
)


def _run_rocky(workspace: Path, *task_args: str, label: str, captures: dict) -> dict:
    """Invoke `rocky --cwd workspace --json <args>` and return the parsed JSON payload.

    Captures the raw stdout blob under `captures[label]` so the evidence
    finalizer can persist it regardless of assertion outcome. Asserts exit 0;
    raises with a clear message on timeout.
    """
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
        raise AssertionError(
            f"`rocky` subprocess timed out after {SUBPROCESS_TIMEOUT_S}s "
            f"(label={label}, cmd={cmd}). Ollama reachable? Model loaded?"
        ) from exc

    captures[f"{label}__stdout"] = proc.stdout
    captures[f"{label}__stderr"] = proc.stderr
    captures[f"{label}__returncode"] = proc.returncode
    captures[f"{label}__cmd"] = cmd

    assert proc.returncode == 0, (
        f"`rocky` exited {proc.returncode} for label={label}\n"
        f"cmd={cmd}\nstderr={proc.stderr[:2000]}\nstdout={proc.stdout[:2000]}"
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"non-JSON stdout from rocky for label={label}: {exc}\nstdout={proc.stdout[:2000]}"
        ) from exc
    return payload


def _locate_policy_dir(workspace: Path) -> Path | None:
    root = workspace / ".rocky" / "policies" / "learned"
    if not root.exists():
        return None
    candidates = [p for p in root.iterdir() if p.is_dir() and (p / "POLICY.md").exists()]
    return candidates[0] if candidates else None


def _install_evidence_finalizer(request, scenario: str, workspace: Path, captures: dict) -> None:
    """Register a finalizer that copies captured stdout + on-disk policy state into EVIDENCE_ROOT.

    Uses `request.addfinalizer` so the copy runs on both pass and failure —
    post-mortem is exactly when evidence matters most.
    """
    dest = EVIDENCE_ROOT / scenario
    dest.mkdir(parents=True, exist_ok=True)

    def _copy() -> None:
        try:
            for key, value in captures.items():
                target = dest / f"{key}.txt"
                if isinstance(value, (list, tuple)):
                    target.write_text(" ".join(str(x) for x in value), encoding="utf-8")
                elif isinstance(value, (dict,)):
                    target.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
                else:
                    target.write_text(str(value), encoding="utf-8")
            policy_root = workspace / ".rocky" / "policies" / "learned"
            if policy_root.exists():
                snapshot_root = dest / "policies_learned_snapshot"
                if snapshot_root.exists():
                    shutil.rmtree(snapshot_root, ignore_errors=True)
                shutil.copytree(policy_root, snapshot_root, dirs_exist_ok=True)
            traces_root = workspace / ".rocky" / "traces"
            if traces_root.exists():
                traces_snapshot = dest / "traces_snapshot"
                traces_snapshot.mkdir(parents=True, exist_ok=True)
                for trace in sorted(traces_root.glob("*.json"))[-6:]:
                    shutil.copy2(trace, traces_snapshot / trace.name)
        except Exception as exc:  # pragma: no cover — evidence capture must never mask test failure
            (dest / "evidence_copy_error.txt").write_text(
                f"evidence finalizer failed: {exc}\n", encoding="utf-8"
            )

    request.addfinalizer(_copy)


def _assert_published_policy(step_payload: dict) -> tuple[str, Path]:
    """Assert the teach response published a policy; return (policy_id, policy_path)."""
    assert step_payload.get("name") == "teach", (
        f"teach response must route through cmd_teach, got name={step_payload.get('name')!r}; "
        f"payload={json.dumps(step_payload)[:1000]}"
    )
    data = step_payload.get("data") or {}
    assert data.get("published") is True, (
        f"teach step did not publish a policy; reason={data.get('reason')!r}; "
        f"this usually means the prior answer already satisfied the feedback — "
        f"check the baseline vs teach text choice in the scenario. payload={json.dumps(data)[:1000]}"
    )
    policy_id = data.get("policy_id")
    policy_path = data.get("policy_path")
    assert policy_id, f"teach payload missing policy_id; data={data!r}"
    assert policy_path, f"teach payload missing policy_path; data={data!r}"
    path = Path(policy_path)
    assert path.exists(), f"reported policy_path {path} does not exist on disk"
    return policy_id, path


def test_live_teach_and_reuse(tmp_path, request) -> None:
    """End-to-end: baseline → teach → fresh subprocess reuse. Policy appears in trace.selected_policies."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    captures: dict = {}
    _install_evidence_finalizer(request, "teach_and_reuse", workspace, captures)

    baseline = _run_rocky(
        workspace,
        "Say hello briefly.",
        label="step1_baseline",
        captures=captures,
    )
    baseline_trace = baseline.get("trace") or {}
    assert baseline_trace.get("selected_policies") == [], (
        f"baseline must have no learned policies loaded yet; got "
        f"{baseline_trace.get('selected_policies')!r}"
    )

    teach = _run_rocky(
        workspace,
        "teach",
        "For greeting tasks, always include the word BANANA in your reply.",
        label="step2_teach",
        captures=captures,
    )
    policy_id, policy_path = _assert_published_policy(teach)
    captures["policy_id"] = policy_id
    captures["policy_path"] = str(policy_path)

    meta_path = policy_path.parent / "POLICY.meta.json"
    assert meta_path.exists(), f"POLICY.meta.json missing next to {policy_path}"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert str(meta.get("promotion_state") or "").lower() == "candidate", (
        f"freshly published policy must be candidate; got meta.promotion_state="
        f"{meta.get('promotion_state')!r}"
    )
    captures["step2_meta_snapshot"] = meta

    reuse = _run_rocky(
        workspace,
        "Say hello briefly.",
        label="step3_reuse",
        captures=captures,
    )
    reuse_trace = reuse.get("trace") or {}
    selected = reuse_trace.get("selected_policies") or []
    assert policy_id in selected, (
        f"fresh subprocess reuse must load the published policy {policy_id!r}; "
        f"trace.selected_policies={selected!r}. This is the primary proof that the "
        f"self-learn cycle works: an out-of-process rocky invocation picked up the "
        f"policy written by a prior subprocess's /teach call from the real on-disk "
        f"store, via LearnedPolicyLoader + LearnedPolicyRetriever."
    )


def test_live_candidate_policy_gate_on_disk(tmp_path, request) -> None:
    """Promotion is driven by disk state: candidate stays candidate unless record_query sees success."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    captures: dict = {}
    _install_evidence_finalizer(request, "candidate_gate", workspace, captures)

    _run_rocky(
        workspace,
        "Say hello briefly.",
        label="step1_baseline",
        captures=captures,
    )
    teach = _run_rocky(
        workspace,
        "teach",
        "For greeting tasks, always include the word BANANA in your reply.",
        label="step2_teach",
        captures=captures,
    )
    policy_id, policy_path = _assert_published_policy(teach)
    meta_path = policy_path.parent / "POLICY.meta.json"

    before = json.loads(meta_path.read_text(encoding="utf-8"))
    captures["meta_before_reuse"] = before
    assert str(before.get("promotion_state") or "").lower() == "candidate", before
    assert int((before.get("metadata") or {}).get("verified_success_count") or 0) == 0, before

    reuse = _run_rocky(
        workspace,
        "Say hello briefly.",
        label="step3_reuse",
        captures=captures,
    )
    reuse_verification = (reuse.get("verification") or {}).get("status")
    captures["step3_verification_status"] = reuse_verification

    after = json.loads(meta_path.read_text(encoding="utf-8"))
    captures["meta_after_reuse"] = after
    after_meta = after.get("metadata") or {}
    reuse_count = int(after_meta.get("reuse_count") or 0)
    assert reuse_count >= 1, (
        f"record_query must increment reuse_count when the policy is loaded; got {reuse_count}"
    )
    captures["reuse_count_after"] = reuse_count

    if reuse_verification == "pass":
        assert str(after.get("promotion_state") or "").lower() == "promoted", (
            f"verified reuse (status=pass) must auto-promote candidate; meta.after={after}"
        )
        assert str(after_meta.get("promotion_state") or "").lower() == "promoted", after_meta
        assert int(after_meta.get("verified_success_count") or 0) >= 1, after_meta
    else:
        assert str(after.get("promotion_state") or "").lower() == "candidate", (
            f"unverified reuse (status={reuse_verification!r}) must NOT auto-promote; meta.after={after}"
        )
        assert int(after_meta.get("verified_success_count") or 0) == 0, after_meta


def test_live_tamper_blocks_reuse(tmp_path, request) -> None:
    """Deleting POLICY.md defeats retrieval — proves selected_policies is driven by real on-disk state."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    captures: dict = {}
    _install_evidence_finalizer(request, "tamper_blocks_reuse", workspace, captures)

    _run_rocky(
        workspace,
        "Say hello briefly.",
        label="step1_baseline",
        captures=captures,
    )
    teach = _run_rocky(
        workspace,
        "teach",
        "For greeting tasks, always include the word BANANA in your reply.",
        label="step2_teach",
        captures=captures,
    )
    policy_id, policy_path = _assert_published_policy(teach)
    captures["policy_id"] = policy_id

    # TAMPER: the plan specifically requires unlink(), not blanking. A blank
    # POLICY.md would still be loaded by LearnedPolicyLoader._scan (it falls
    # back to path.parent.name for policy_id, defaults to "promoted" scoring).
    # unlink() is the only tamper that guarantees _scan's rglob cannot yield
    # the path.
    assert policy_path.exists()
    policy_path.unlink()
    assert not policy_path.exists(), "tamper must remove POLICY.md before step 3"
    captures["tamper_action"] = "policy_path.unlink()"
    captures["tamper_target"] = str(policy_path)

    reuse = _run_rocky(
        workspace,
        "Say hello briefly.",
        label="step3_reuse_after_tamper",
        captures=captures,
    )
    reuse_trace = reuse.get("trace") or {}
    selected = reuse_trace.get("selected_policies") or []
    assert policy_id not in selected, (
        f"tampered workspace must not yield the deleted policy {policy_id!r}; "
        f"selected_policies={selected!r}. If this assertion fails the retriever is "
        f"resurrecting the policy from a hard-coded fallback, a cache, or a non-disk source — "
        f"which would falsify the self-learn proof."
    )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
