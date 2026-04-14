"""Unified ledger-backed retriever with 10-factor ranking (PRD §12.3, Phase 2.3 T2).

Queries `LearningLedgerStore.load_all()` directly and scores each
`LearningRecord` against the current prompt / task_signature / thread with
the 10 factors from PRD §12.3:

    1. Authority (teacher > evidence_backed > self_generated)
    2. Promotion state (promoted > candidate > rejected/stale)
    3. Task-signature match (exact > prefix > none)
    4. Task-family match (exact > empty)
    5. Thread relevance (triggers ∩ thread summary tokens)
    6. Failure-class match
    7. Evidence-support quality (length of evidence list)
    8. Recency (ISO `updated_at` timestamp)
    9. Conflict status (rolled-back records are excluded upstream by the
       ledger filter; this factor is a no-op stub until Phase 3 introduces
       contradiction indexing)
   10. Prior-success attribution (`reuse_stats.verified_success_count`)

Each returned record carries a `rank_breakdown: dict[str, float]` explaining
the score. This is a new capability — it does NOT replace the legacy
retrievers (LearnedPolicyRetriever / MemoryRetriever / StudentStore.retrieve)
in Phase 2.3; those remain in place with their existing scoring. T3 adapter
collapse is deferred.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rocky.core.runtime_state import ActiveTaskThread
from rocky.learning.ledger import LearningLedgerStore, LearningRecord
from rocky.util.text import tokenize_keywords


_AUTHORITY_WEIGHT = {
    "teacher": 4,
    "evidence_backed": 3,
    "self_generated": 2,
}

_PROMOTION_WEIGHT = {
    "promoted": 3,
    "validated": 2,
    "candidate": 1,
    "stale": -1,
    "rejected": -3,
}


@dataclass(slots=True)
class RankedRecord:
    """A ledger record plus its score and a per-factor breakdown."""

    record: LearningRecord
    score: float
    rank_breakdown: dict[str, float]


class LedgerRetriever:
    """Retrieve + rank ledger records against a prompt / task_signature / thread.

    This is additive in Phase 2.3 — existing retrievers are untouched. Future
    Phase 2.4 will wire the three legacy retrievers to delegate internally
    (T3), at which point the public signatures on those classes remain
    unchanged per C2.
    """

    def __init__(self, ledger: LearningLedgerStore) -> None:
        self.ledger = ledger

    def retrieve(
        self,
        prompt: str,
        task_signature: str,
        *,
        thread: ActiveTaskThread | None = None,
        limit: int = 8,
        kind_filter: set[str] | None = None,
    ) -> list[RankedRecord]:
        prompt_tokens = tokenize_keywords(prompt)
        thread_tokens = (
            tokenize_keywords(thread.summary_text()) if thread is not None else set()
        )
        thread_family = thread.task_family if thread is not None else ""
        prompt_lower = prompt.lower()

        ranked: list[RankedRecord] = []
        for record in self.ledger.load_all():
            if record.rolled_back:
                continue
            if kind_filter is not None and record.kind not in kind_filter:
                continue

            breakdown: dict[str, float] = {}

            # 1. Authority
            authority_score = _AUTHORITY_WEIGHT.get(str(record.authority or "").lower(), 0)
            breakdown["authority"] = float(authority_score)

            # 2. Promotion state
            promotion_score = _PROMOTION_WEIGHT.get(
                str(record.promotion_state or "promoted").lower(), 0
            )
            breakdown["promotion_state"] = float(promotion_score)

            # 3. Task-signature match (exact > prefix > none)
            ts_score = 0.0
            declared_sig = str(record.task_signature or "").strip()
            if declared_sig and task_signature:
                if declared_sig == task_signature:
                    ts_score = 6.0
                elif declared_sig.endswith("*") and task_signature.startswith(
                    declared_sig[:-1]
                ):
                    ts_score = 3.0
            breakdown["task_signature"] = ts_score

            # 4. Task-family match
            tf_score = 0.0
            declared_family = str(record.task_family or "").strip()
            if declared_family and thread_family and declared_family == thread_family:
                tf_score = 2.0
            breakdown["task_family"] = tf_score

            # 5. Thread relevance (trigger tokens ∩ thread summary tokens)
            trigger_tokens: set[str] = set()
            for trigger in record.triggers or []:
                trigger_tokens |= tokenize_keywords(str(trigger))
            thread_relevance = float(min(len(thread_tokens & trigger_tokens), 4))
            breakdown["thread_relevance"] = thread_relevance

            # Additionally: prompt-token overlap (separate signal, required for
            # non-thread turns where thread_tokens is empty).
            prompt_overlap = float(min(len(prompt_tokens & trigger_tokens), 4)) * 1.5
            breakdown["prompt_relevance"] = prompt_overlap

            # Trigger literal substring match — legacy compat (LearnedPolicyRetriever
            # gave +6 for this; preserve the signal strength).
            trigger_literal = any(
                str(t).lower() in prompt_lower
                for t in (record.triggers or [])
                if str(t).strip()
            )
            breakdown["trigger_literal"] = 6.0 if trigger_literal else 0.0

            # 6. Failure-class match
            fc_score = 0.0
            failure_class = str(record.failure_class or "").strip()
            if failure_class and any(
                tok in prompt_lower for tok in tokenize_keywords(failure_class)
            ):
                fc_score = 3.0
            breakdown["failure_class"] = fc_score

            # 7. Evidence-support quality
            ev_count = len(record.evidence or [])
            breakdown["evidence_quality"] = float(min(ev_count, 4))

            # 8. Recency — bias toward recent `updated_at` (string-compare is
            # OK for ISO-8601; newer strings sort higher). Map to a small
            # numeric bonus so it doesn't dominate.
            breakdown["recency"] = 1.0 if record.updated_at else 0.0

            # 9. Conflict status — stub (no contradiction index yet in Phase 2.3).
            breakdown["conflict_status"] = 0.0

            # 10. Prior-success attribution
            try:
                vsc = int((record.reuse_stats or {}).get("verified_success_count") or 0)
            except Exception:
                vsc = 0
            breakdown["prior_success"] = float(min(vsc, 4))

            score = float(sum(breakdown.values()))

            # Retrieval gate: require at least SOME signal to appear.
            has_signal = (
                trigger_literal
                or ts_score > 0
                or thread_relevance > 0
                or prompt_overlap > 0
                or fc_score > 0
            )
            if not has_signal:
                continue

            ranked.append(RankedRecord(record=record, score=score, rank_breakdown=breakdown))

        ranked.sort(
            key=lambda r: (r.score, r.record.updated_at or ""),
            reverse=True,
        )
        return ranked[:limit]
