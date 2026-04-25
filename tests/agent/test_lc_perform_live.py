"""LC-PERFORM — learn-then-perform end-to-end live scenario.

Closes the "Rocky learns from teaching, loads what he learned properly,
and performs tasks well" claim in a single subprocess-spanning loop:

  T1 (teach)    — `/teach` registers a safety constraint as a learned
                  policy. Gated by `_run_rocky_until` on
                  ``data.published == True`` so gemma's stochastic
                  classification of "generalizable feedback" does not
                  produce a silent-skip false positive (SL-PROMOTE
                  pattern from run-20260414-215348).
  T2 (perform)  — a FRESH subprocess gives a lexically-different task
                  ("create backup.sh ...") that exercises real tool
                  dispatch (filesystem write). The produced artifact and
                  the response text must both reflect the learned
                  constraint.

Bit-flip negative — a separate baseline workspace runs the same T3
prompt without the prior teach. The artifact should NOT contain the
constraint marker. xfail(strict=False) on the negative because the
model occasionally adds `set -euo pipefail` from its own training prior
even without the teach; we surface the observation rather than mask
it.

Gated by `ROCKY_LLM_SMOKE=1`. Helpers come from
``tests/agent/_helpers.py`` `__all__` only — no parallel harness.
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
    _context_memories,
    _install_evidence_finalizer,
    _run_rocky,
    _run_rocky_until,
)


pytestmark = pytest.mark.skipif(
    os.environ.get(SMOKE_FLAG) != "1",
    reason=(
        f"lc-perform live scenario requires {SMOKE_FLAG}=1 "
        f"(real Ollama via editable rocky at {ROCKY_BIN})"
    ),
)


_PIPEFAIL_RE = re.compile(r"set\s+-euo\s+pipefail", re.IGNORECASE)
_TEACH_FEEDBACK = (
    "All bash scripts in this project must start with 'set -euo pipefail' "
    "immediately after the shebang line. This is mandatory for safety."
)
_T3_PROMPT = (
    "Create a backup.sh script in this workspace that copies files from "
    "/tmp/source to /tmp/dest."
)


@dataclass
class _LcPerformResult:
    t2: dict = field(default_factory=dict)
    t3: dict = field(default_factory=dict)
    workspace: Path = field(default_factory=Path)


@dataclass
class _LcPerformBaseline:
    t3: dict = field(default_factory=dict)
    workspace: Path = field(default_factory=Path)


@pytest.fixture(scope="module")
def lc_perform_taught(request, tmp_path_factory) -> _LcPerformResult:
    workspace = tmp_path_factory.mktemp("lc_perform_taught_")
    captures: dict = {}
    _install_evidence_finalizer(request, "lc_perform_taught", workspace, captures)

    t2 = _run_rocky_until(
        workspace,
        "teach",
        _TEACH_FEEDBACK,
        label="t2_teach_pipefail",
        captures=captures,
        predicate=lambda payload: bool((payload.get("data") or {}).get("published")),
        predicate_reason=(
            "the teach must publish a learned policy "
            "(data.published == True) for the constraint to land on disk"
        ),
    )

    t3 = _run_rocky(
        workspace,
        _T3_PROMPT,
        label="t3_perform_fresh_subprocess",
        captures=captures,
    )

    return _LcPerformResult(t2=t2, t3=t3, workspace=workspace)


@pytest.fixture(scope="module")
def lc_perform_baseline(request, tmp_path_factory) -> _LcPerformBaseline:
    workspace = tmp_path_factory.mktemp("lc_perform_baseline_")
    captures: dict = {}
    _install_evidence_finalizer(request, "lc_perform_baseline", workspace, captures)

    t3 = _run_rocky(
        workspace,
        _T3_PROMPT,
        label="t3_baseline_no_teach",
        captures=captures,
    )
    return _LcPerformBaseline(t3=t3, workspace=workspace)


def test_lc_perform_phase_A_teach_publishes_policy(lc_perform_taught: _LcPerformResult) -> None:
    """T2 must publish a real learned policy on disk (gate condition)."""
    data = lc_perform_taught.t2.get("data") or {}
    assert data.get("published") is True, (
        f"LC-PERFORM phase A FAILED: teach did not publish; data={data!r}"
    )
    policy_id = str(data.get("policy_id") or "")
    assert policy_id, f"teach data is missing policy_id; data={data!r}"
    policy_path = Path(str(data.get("policy_path") or ""))
    assert policy_path and policy_path.exists(), (
        f"published policy file should exist on disk; policy_path={policy_path!r}"
    )


def test_lc_perform_phase_B_artifact_obeys_constraint(lc_perform_taught: _LcPerformResult) -> None:
    """The fresh-subprocess T3 must have produced backup.sh containing 'set -euo pipefail'.

    This is the load-bearing behavioral assertion: the learned constraint
    crossed the process boundary AND shaped the produced artifact (not
    just the answer prose).
    """
    backup = lc_perform_taught.workspace / "backup.sh"
    assert backup.exists(), (
        f"LC-PERFORM phase B FAILED: T3 did not produce backup.sh at {backup!r}. "
        f"Either the agent failed to dispatch the filesystem write, or it placed "
        f"the file outside the workspace."
    )
    content = backup.read_text(encoding="utf-8")
    assert _PIPEFAIL_RE.search(content), (
        f"LC-PERFORM phase B FAILED: backup.sh exists but does not contain "
        f"'set -euo pipefail'. The learned constraint did not cross into "
        f"the produced artifact. content={content!r}"
    )


def test_lc_perform_phase_C_response_references_constraint(lc_perform_taught: _LcPerformResult) -> None:
    """T3's response_text must reference the learned safety guidance."""
    text = str(lc_perform_taught.t3.get("text") or "").lower()
    assert text, f"T3 response text empty; resp={lc_perform_taught.t3!r}"
    assert ("pipefail" in text) or ("safety" in text) or ("strict mode" in text), (
        f"LC-PERFORM phase C FAILED: T3 answer does not reference the learned "
        f"constraint (no 'pipefail', 'safety', or 'strict mode' token). "
        f"The policy may have landed in context but the model ignored it. "
        f"text={text[:1000]!r}"
    )


def test_lc_perform_phase_D_policy_loaded_into_context(lc_perform_taught: _LcPerformResult) -> None:
    """Structural witness: T3's trace shows the teach POLICY reached its context.

    Asserts on the policy retrieval channel only (selected_policies or
    context.learned_policies). Memory injection alone is not sufficient
    proof — the load-bearing claim is that the *learned policy* itself
    crossed the process boundary, not that some related memory did.
    Memory observations are surfaced in the failure message for
    diagnostic context but are not asserted on.
    """
    data = lc_perform_taught.t2.get("data") or {}
    teach_policy_id = str(data.get("policy_id") or "")
    assert teach_policy_id, f"teach payload missing policy_id; data={data!r}"

    trace = lc_perform_taught.t3.get("trace") or {}
    selected = trace.get("selected_policies") or []
    context = trace.get("context") or {}
    learned = context.get("learned_policies") or []
    learned_names = [str(p.get("name") or p.get("policy_id") or "") for p in learned]

    in_selected = teach_policy_id in selected
    in_context = any(teach_policy_id == name or teach_policy_id in name for name in learned_names)
    memories = _context_memories(lc_perform_taught.t3)
    memory_names = [str(m.get("name") or m.get("id") or "") for m in memories]

    assert in_selected or in_context, (
        f"LC-PERFORM phase D FAILED: teach policy {teach_policy_id!r} did not "
        f"reach T3's policy-retrieval context (selected_policies={selected!r}, "
        f"context.learned_policies names={learned_names!r}). "
        f"Memory channel observed names (diagnostic only)={memory_names!r}. "
        f"Either retrieval missed it or the loader silently skipped it."
    )


@pytest.mark.xfail(
    strict=False,
    reason=(
        "bit-flip negative — gemma occasionally adds 'set -euo pipefail' from "
        "its training prior even without the teach; surface as observation, "
        "not a hard fail. The positive cases above are strict."
    ),
)
def test_lc_perform_phase_E_baseline_does_not_obey_without_teach(
    lc_perform_baseline: _LcPerformBaseline,
) -> None:
    """Bit-flip negative: same T3 task in a fresh workspace with NO teach."""
    backup = lc_perform_baseline.workspace / "backup.sh"
    assert backup.exists(), (
        f"baseline T3 should still produce backup.sh; missing at {backup!r}"
    )
    content = backup.read_text(encoding="utf-8")
    assert not _PIPEFAIL_RE.search(content), (
        f"LC-PERFORM bit-flip: baseline backup.sh contains 'set -euo pipefail' "
        f"WITHOUT a prior teach — the model added it from its own prior. "
        f"This means the positive test's behavioral signal is ambiguous. "
        f"content={content!r}"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
