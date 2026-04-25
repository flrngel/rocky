"""STRESS-20X — learn-load-perform stability across 20 iterations.

Witnesses that Rocky's teach → publish → reuse-fresh-process pipeline
is stable under repetition: one teach, then 19 lexically-different
bash-script tasks in the SAME workspace. Asserts:

1. The teach published exactly one learned policy.
2. Across 19 reuse turns, >=18 produced bash files containing
   ``set -euo pipefail`` (gemma stochasticity tolerance: 1 flake
   permitted).
3. End-state ``.rocky/policies/learned/`` has exactly one policy_id
   directory (no duplicate teach landings, no fragmentation).

(A previous draft asserted bounded auto-memory growth as a phase D —
that was an over-tightened invariant: 19 lexically-distinct reuse
prompts legitimately produce distinct fingerprints, so dedup *correctly*
does not collapse them. The catalog-dedup scenario covers the real
fingerprint-collapse claim; stress-20x's value is the policy-dedup +
reuse-honor invariants A/B/C above.)

Wall-clock budget: ~10-12 minutes per single ship_check on
gemma4:26b @ ainbr-research-fast:11434. ``pytest.mark.slow``; opt in
with ``-m slow``.

This is the load-bearing answer to the user's question: "does Rocky
actually learn from teaching, load what he learned properly, and
perform tasks well — repeatedly?"

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
    _run_rocky_until,
)


pytestmark = [
    pytest.mark.skipif(
        os.environ.get(SMOKE_FLAG) != "1",
        reason=(
            f"stress-20x live scenario requires {SMOKE_FLAG}=1 "
            f"(real Ollama via editable rocky at {ROCKY_BIN})"
        ),
    ),
    pytest.mark.slow,
]


_PIPEFAIL_RE = re.compile(r"set\s+-euo\s+pipefail", re.IGNORECASE)
_TEACH_FEEDBACK = (
    "All bash scripts in this project must start with 'set -euo pipefail' "
    "immediately after the shebang line. This is mandatory for safety."
)

_REUSE_TASKS: list[tuple[str, str]] = [
    ("hello.sh", "Create a hello.sh script in this workspace that echoes hello."),
    ("date.sh", "Create a date.sh script in this workspace that prints today's date."),
    ("count.sh", "Create a count.sh script in this workspace that counts files in /tmp."),
    ("list.sh", "Create a list.sh script in this workspace that lists the current directory."),
    ("check.sh", "Create a check.sh script in this workspace that checks disk usage with df."),
    ("ping.sh", "Create a ping.sh script in this workspace that pings localhost three times."),
    ("echo.sh", "Create an echo.sh script in this workspace that echoes its first argument."),
    ("env.sh", "Create an env.sh script in this workspace that prints the PATH variable."),
    ("uptime.sh", "Create an uptime.sh script in this workspace that prints system uptime."),
    ("who.sh", "Create a who.sh script in this workspace that lists logged-in users via who."),
    ("clear.sh", "Create a clear.sh script in this workspace that clears the terminal."),
    ("yes.sh", "Create a yes.sh script in this workspace that prints yes five times."),
    ("wc.sh", "Create a wc.sh script in this workspace that counts words in a given file."),
    ("head.sh", "Create a head.sh script in this workspace that prints first 5 lines of /etc/hosts."),
    ("tail.sh", "Create a tail.sh script in this workspace that prints last 5 lines of /etc/hosts."),
    ("cat.sh", "Create a cat.sh script in this workspace that cats /etc/hosts."),
    ("sort.sh", "Create a sort.sh script in this workspace that sorts the lines of a given file."),
    ("grep.sh", "Create a grep.sh script in this workspace that greps for 'localhost' in /etc/hosts."),
    ("awk.sh", "Create an awk.sh script in this workspace that prints the first column of /etc/hosts."),
]


@dataclass
class _StressResult:
    setup: dict = field(default_factory=dict)
    teach: dict = field(default_factory=dict)
    reuse_turns: list[dict] = field(default_factory=list)
    workspace: Path = field(default_factory=Path)


@pytest.fixture(scope="module")
def stress_20x_taught(request, tmp_path_factory) -> _StressResult:
    workspace = tmp_path_factory.mktemp("stress_20x_taught_")
    captures: dict = {}
    _install_evidence_finalizer(request, "stress_20x_taught", workspace, captures)

    # T1 setup — anchors /teach. runtime.teach(feedback) at
    # app.py:1156-1174 only publishes a learned policy when the agent
    # has a prior prompt/answer/trace to attach the feedback to.
    # Without this anchor turn, teach writes only to student notebook
    # with published=False (silent no-op for our predicate).
    setup = _run_rocky(
        workspace,
        "Write a small bash script greet.sh that echoes hello.",
        label="t1_setup_anchors_teach",
        captures=captures,
    )

    teach = _run_rocky_until(
        workspace,
        "teach",
        _TEACH_FEEDBACK,
        label="t2_teach_pipefail",
        captures=captures,
        predicate=lambda payload: bool((payload.get("data") or {}).get("published")),
        predicate_reason=(
            "the teach must publish a learned policy "
            "(data.published == True) for the 19 reuse turns to have "
            "anything to load and apply"
        ),
    )

    reuse_turns: list[dict] = []
    for index, (filename, prompt) in enumerate(_REUSE_TASKS, start=3):
        turn = _run_rocky(
            workspace,
            prompt,
            label=f"t{index}_reuse_{filename}",
            captures=captures,
        )
        reuse_turns.append(turn)

    return _StressResult(setup=setup, teach=teach, reuse_turns=reuse_turns, workspace=workspace)


def test_stress_20x_phase_A_teach_published_policy(stress_20x_taught: _StressResult) -> None:
    """Gate: the teach must publish; downstream phases require this."""
    data = stress_20x_taught.teach.get("data") or {}
    assert data.get("published") is True, (
        f"STRESS-20X phase A FAILED: teach did not publish; data={data!r}"
    )
    assert str(data.get("policy_id") or ""), (
        f"teach data missing policy_id; data={data!r}"
    )


def test_stress_20x_phase_B_reuse_honors_constraint(
    stress_20x_taught: _StressResult,
) -> None:
    """Load-bearing: across 19 reuse turns in a fresh subprocess each,
    >=18 produced *.sh files must contain 'set -euo pipefail'.

    A pass rate >=94.7% (18/19) is the gemma-stochasticity tolerance
    set by AGENTS.md L20 cohort variance. Below 18/19 is a real signal
    that retrieval+injection drifted.
    """
    workspace = stress_20x_taught.workspace
    successes = 0
    failures: list[str] = []
    missing: list[str] = []
    for filename, _prompt in _REUSE_TASKS:
        path = workspace / filename
        if not path.exists():
            missing.append(filename)
            continue
        content = path.read_text(encoding="utf-8")
        if _PIPEFAIL_RE.search(content):
            successes += 1
        else:
            failures.append(filename)
    assert successes >= 18, (
        f"STRESS-20X phase B FAILED: only {successes}/19 reuse turns "
        f"honored the learned constraint. "
        f"missing={missing!r} failed_to_honor={failures!r}. "
        f"This signals retrieval drift across iterations or a regression "
        f"in policy injection at scale."
    )


def test_stress_20x_phase_C_exactly_one_learned_policy(
    stress_20x_taught: _StressResult,
) -> None:
    """End-state .rocky/policies/learned/ must have exactly 1 policy_id
    directory.

    Multiple directories would mean the teach was registered more than
    once during the 20 turns, OR a reuse turn somehow produced a fresh
    teach lineage. Either is a regression in the policy-dedup invariant.
    """
    learned_root = stress_20x_taught.workspace / ".rocky" / "policies" / "learned"
    if not learned_root.exists():
        pytest.fail(
            f"STRESS-20X phase C FAILED: {learned_root} does not exist after 20 turns"
        )
    dirs = sorted(p for p in learned_root.iterdir() if p.is_dir())
    assert len(dirs) == 1, (
        f"STRESS-20X phase C FAILED: expected exactly 1 learned-policy "
        f"directory, got {len(dirs)}: {[d.name for d in dirs]!r}. "
        f"The teach landed multiple times OR a reuse turn produced a "
        f"new lineage."
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "slow"])
