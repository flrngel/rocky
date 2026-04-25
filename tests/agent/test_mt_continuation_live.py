"""MT-CONTINUATION — multi-turn in-session continuation across subprocess calls.

Witnesses that Rocky's session persistence works honestly across
subprocess boundaries: a unique token introduced in T1 must be
recallable by T3 via the on-disk session JSON, NOT via the lexical
"empty-context fallback" at ``agent.py:_wants_prior_turn_context``.

Pathway:
- T1 plants a unique codename in the LLM context via a normal prompt.
- T2 is an unrelated intermediate turn (would never produce the token
  by chance).
- T3 asks for an artifact that must include the codename. Because all
  three turns pass ``--continue``, ``SessionStore.peek_current`` reads
  ``.rocky/sessions/.current`` from disk and rehydrates the session for
  each subprocess. The agent's ``recent_messages`` slice (agent.py:3216)
  includes T1+T2's messages when T3 runs, so the LLM sees the
  codename naturally.

Bit-flip negative: a separate workspace runs only T3 with
``--continue``. No prior session exists, so the codename cannot be
recalled — T3's response must NOT contain it.

Gated by ``ROCKY_LLM_SMOKE=1``. Helpers come from
``tests/agent/_helpers.py`` ``__all__`` only.
"""

from __future__ import annotations

import json
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
)


pytestmark = pytest.mark.skipif(
    os.environ.get(SMOKE_FLAG) != "1",
    reason=(
        f"mt-continuation live scenario requires {SMOKE_FLAG}=1 "
        f"(real Ollama via editable rocky at {ROCKY_BIN})"
    ),
)


_CODENAME = "aurora-hawk-92"
# Allow the model to render the hyphenated identifier with optional
# spaces or underscores (and case folding) — `aurora hawk 92` and
# `Aurora-Hawk-92` are still recall, not invention.
_CODENAME_RE = re.compile(r"aurora[-\s_]?hawk[-\s_]?92", re.IGNORECASE)
_T1_PROMPT = (
    f"For this conversation, the project codename is {_CODENAME}. "
    f"Acknowledge and we'll continue."
)
_T2_PROMPT = "List two release-note formats commonly used in open-source projects."
_T3_PROMPT = "Now compose a one-line release note that includes the project codename."


@dataclass
class _MtContinuationResult:
    t1: dict = field(default_factory=dict)
    t2: dict = field(default_factory=dict)
    t3: dict = field(default_factory=dict)
    workspace: Path = field(default_factory=Path)


@dataclass
class _MtContinuationBaseline:
    t3: dict = field(default_factory=dict)
    workspace: Path = field(default_factory=Path)


@pytest.fixture(scope="module")
def mt_continuation_continued(request, tmp_path_factory) -> _MtContinuationResult:
    workspace = tmp_path_factory.mktemp("mt_continuation_continued_")
    captures: dict = {}
    _install_evidence_finalizer(request, "mt_continuation_continued", workspace, captures)

    t1 = _run_rocky(
        workspace,
        "--continue",
        _T1_PROMPT,
        label="t1_codename_seed",
        captures=captures,
    )
    t2 = _run_rocky(
        workspace,
        "--continue",
        _T2_PROMPT,
        label="t2_intermediate",
        captures=captures,
    )
    t3 = _run_rocky(
        workspace,
        "--continue",
        _T3_PROMPT,
        label="t3_recall_via_session",
        captures=captures,
    )
    return _MtContinuationResult(t1=t1, t2=t2, t3=t3, workspace=workspace)


@pytest.fixture(scope="module")
def mt_continuation_baseline(request, tmp_path_factory) -> _MtContinuationBaseline:
    workspace = tmp_path_factory.mktemp("mt_continuation_baseline_")
    captures: dict = {}
    _install_evidence_finalizer(request, "mt_continuation_baseline", workspace, captures)

    t3 = _run_rocky(
        workspace,
        "--continue",
        _T3_PROMPT,
        label="t3_baseline_no_prior_session",
        captures=captures,
    )
    return _MtContinuationBaseline(t3=t3, workspace=workspace)


def test_mt_continuation_phase_A_session_persisted_across_subprocesses(
    mt_continuation_continued: _MtContinuationResult,
) -> None:
    """Structural: the on-disk session must show all three turns accumulated."""
    sessions_dir = mt_continuation_continued.workspace / ".rocky" / "sessions"
    current_path = sessions_dir / ".current"
    assert current_path.exists(), (
        f"MT-CONTINUATION phase A FAILED: no .current pointer at {current_path!r}. "
        f"--continue did not persist a session across subprocesses."
    )
    session_id = current_path.read_text(encoding="utf-8").strip()
    assert session_id, f".current is empty; cannot resolve session JSON"
    session_json = sessions_dir / f"{session_id}.json"
    assert session_json.exists(), (
        f"session JSON missing at {session_json!r}; SessionStore did not persist"
    )
    data = json.loads(session_json.read_text(encoding="utf-8"))
    messages = data.get("messages") or []
    assert len(messages) >= 6, (
        f"MT-CONTINUATION phase A FAILED: expected >=6 messages (3 user + "
        f"3 assistant) by T3, got {len(messages)}. Either --continue did "
        f"not rehydrate, or one of the turns failed silently. messages={messages!r}"
    )


def test_mt_continuation_phase_B_t3_recalls_t1_token(
    mt_continuation_continued: _MtContinuationResult,
) -> None:
    """Behavioral: T3's answer must contain the codename introduced in T1."""
    text = str(mt_continuation_continued.t3.get("text") or "")
    assert text, f"T3 answer empty; resp={mt_continuation_continued.t3!r}"
    assert _CODENAME_RE.search(text), (
        f"MT-CONTINUATION phase B FAILED: T3 answer does not contain the "
        f"codename {_CODENAME!r} (case/spacing tolerant). The recent_messages "
        f"slice did not reach the LLM, or the model ignored it. "
        f"text={text[:1000]!r}"
    )


def test_mt_continuation_phase_C_t3_used_continuation_not_fallback(
    mt_continuation_continued: _MtContinuationResult,
) -> None:
    """The continuation must come from the session, not from the lexical
    `_wants_prior_turn_context` empty-context fallback (agent.py:586-599).

    Ensures T3 used the real session-resume path: the trace's continuation
    decision should be `continue_active_thread` or `resume_recent_thread`
    (not `start_new_thread`) when the in-session history is non-empty.
    """
    trace = mt_continuation_continued.t3.get("trace") or {}
    continuation = trace.get("continuation") or {}
    action = str(continuation.get("action") or "")
    assert action in {"continue_active_thread", "resume_recent_thread"}, (
        f"MT-CONTINUATION phase C FAILED: T3 continuation action is "
        f"{action!r}, expected continue_active_thread or "
        f"resume_recent_thread. The continuation resolver did not detect "
        f"the active thread. continuation={continuation!r}"
    )


def test_mt_continuation_phase_D_baseline_cannot_recall_codename(
    mt_continuation_baseline: _MtContinuationBaseline,
) -> None:
    """Bit-flip negative: a baseline workspace with no prior session must
    not be able to produce the codename — proves the positive test
    measures recall, not training-prior coincidence."""
    text = str(mt_continuation_baseline.t3.get("text") or "")
    assert text, f"baseline T3 answer empty; resp={mt_continuation_baseline.t3!r}"
    assert not _CODENAME_RE.search(text), (
        f"MT-CONTINUATION bit-flip FAILED: baseline T3 (no prior session) "
        f"emitted the codename {_CODENAME!r}. Either gemma fabricated a "
        f"matching token by chance or .current was leaked from another "
        f"workspace. text={text[:1000]!r}"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
