"""Offline replay canary (PRD §14, §16.6, §20.4).

Runs a deterministic fixed task corpus against a variant's overlaid
`RetrievalConfig` + `PackingConfig`. No LLM calls, no live provider,
no filesystem side effects outside `variant_dir/canary_workspace/`.

Metrics captured per task:
    - `top_k_record_count` — how many records survived the retrieval gate.
    - `top_1_record_id` — identity of the highest-scoring record (stability signal).
    - `top_score` — total score of the highest-scoring record.
    - `packer_char_count` — characters emitted by `_append_learning_pack_blocks`
      on a mini `ContextPackage` built from the retrieved records.

Aggregates:
    - `total_records_returned` = sum of `top_k_record_count`.
    - `total_packer_chars` = sum of `packer_char_count`.
    - `top1_stability_hash` = stable hash of the per-task `top_1_record_id` list
      (lets callers quickly tell whether variants altered the top picks).

Determinism: the corpus is a fixed tuple; records are seeded in insertion
order; no clock or random-source access inside `CanaryRunner.run`.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from rocky.config.models import PackingConfig, RetrievalConfig
from rocky.core.context import ContextPackage
from rocky.core.system_prompt import _append_learning_pack_blocks
from rocky.learning.ledger import LearningLedgerStore, LearningRecord
from rocky.learning.ledger_retriever import LedgerRetriever


@dataclass(slots=True)
class CanaryTask:
    """One fixed canary probe.

    `seed_records` is a tuple of dicts (NOT `LearningRecord` instances) so
    `CanaryTask` is a pure data description. The runner converts them to
    `LearningRecord`s via `LearningRecord.from_dict` when seeding the
    fixture ledger.
    """

    task_id: str
    prompt: str
    task_signature: str
    seed_records: tuple[dict[str, Any], ...]


@dataclass(slots=True)
class CanaryCorpus:
    """An ordered collection of tasks that together exercise the overlay.

    The default corpus (see `default_corpus()`) is designed so that:
      - several promoted teacher policies exist (exercises top-K narrowing)
      - several candidate policies exist (exercises procedural_cap narrowing
        via the packer)
      - one retrospective exists (exercises packer retrospective blocks)
      - at least five records pass the retrieval gate for the top-K task,
        so narrowing from 8 to 2 is observable.
    """

    name: str
    tasks: tuple[CanaryTask, ...]


@dataclass(slots=True)
class CanaryResult:
    """Outcome of running one variant against a corpus."""

    variant_id: str
    corpus_name: str
    per_task: list[dict[str, Any]] = field(default_factory=list)
    aggregate: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CanaryRunner:
    """Deterministic offline canary engine."""

    def __init__(self, corpus: CanaryCorpus) -> None:
        self.corpus = corpus

    def run(
        self,
        variant_id: str,
        retrieval: RetrievalConfig,
        packing: PackingConfig,
        workspace: Path,
    ) -> CanaryResult:
        """Run the corpus; return metrics. `workspace` is a temp root.

        The workspace hosts a scratch `.rocky/ledger/` populated with the
        task's `seed_records`. Ledger state is discarded at the end of
        each task (corpus tasks run independently).
        """
        result = CanaryResult(variant_id=variant_id, corpus_name=self.corpus.name)
        workspace = Path(workspace)
        workspace.mkdir(parents=True, exist_ok=True)
        for task in self.corpus.tasks:
            task_workspace = workspace / task.task_id
            task_workspace.mkdir(parents=True, exist_ok=True)
            ledger = LearningLedgerStore(task_workspace, create_layout=True)
            for raw in task.seed_records:
                ledger.append(LearningRecord.from_dict(dict(raw)))
            retriever = LedgerRetriever(ledger, config=retrieval)
            ranked = retriever.retrieve(task.prompt, task.task_signature)

            # Build a mini learning-pack view and measure packer char count.
            ctx = ContextPackage(
                instructions=[],
                memories=[],
                skills=[],
                learned_policies=[
                    _record_to_policy_dict(r.record) for r in ranked
                ],
                tool_families=[],
            )
            parts: list[str] = []
            _append_learning_pack_blocks(parts, ctx, packing)
            char_count = sum(len(p) for p in parts)

            top_1_id = ranked[0].record.id if ranked else ""
            top_score = ranked[0].score if ranked else 0.0
            result.per_task.append(
                {
                    "task_id": task.task_id,
                    "top_k_record_count": len(ranked),
                    "top_1_record_id": top_1_id,
                    "top_score": top_score,
                    "packer_char_count": char_count,
                }
            )

        total_records = sum(p["top_k_record_count"] for p in result.per_task)
        total_chars = sum(p["packer_char_count"] for p in result.per_task)
        top1_order = [p["top_1_record_id"] for p in result.per_task]
        top1_hash = hashlib.sha1(
            "|".join(top1_order).encode("utf-8"), usedforsecurity=False
        ).hexdigest()[:16]
        result.aggregate = {
            "total_records_returned": total_records,
            "total_packer_chars": total_chars,
            "top1_stability_hash": top1_hash,
            "task_count": len(result.per_task),
        }
        return result


def _record_to_policy_dict(record: LearningRecord) -> dict[str, Any]:
    """Adapt a `LearningRecord` to the dict shape the packer expects.

    `_append_learning_pack_blocks` reads `name`, `description`,
    `promotion_state`, `feedback_excerpt`, `required_behavior`,
    `prohibited_behavior`. We surface those from the record's fields.
    """
    return {
        "name": record.id,
        "description": (record.triggers or [""])[0] if record.triggers else "",
        "promotion_state": record.promotion_state,
        "feedback_excerpt": "",
        "required_behavior": list(record.required_behavior or []),
        "prohibited_behavior": list(record.prohibited_behavior or []),
        "scope": record.scope,
        "generation": 1,
        "origin": (record.origin or {}).get("type", "canary"),
        "text": json.dumps(
            {
                "kind": record.kind,
                "task_signature": record.task_signature,
                "triggers": record.triggers,
            },
            sort_keys=True,
        ),
    }


def default_corpus() -> CanaryCorpus:
    """Fixed canary corpus used by unit tests + registry default.

    Two tasks:
      * `ledger_topk` — 6 competing records all matching the prompt. Lets
        a top-K override to 2 produce a measurable narrowing (6 → 2).
      * `packer_procedural` — 6 candidate policies; packer procedural_cap
        narrowing from 6 → 2 produces a measurable char-count drop.
    """
    # Shared clock so `created_at` / `updated_at` are deterministic across
    # runs of the default corpus.
    stamp = "2026-04-14T00:00:00Z"

    def _rec(
        idx: int,
        kind: str,
        task_signature: str,
        authority: str = "teacher",
        promotion_state: str = "promoted",
        triggers: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        return {
            "id": f"canary-{idx}",
            "kind": kind,
            "scope": "project",
            "authority": authority,
            "promotion_state": promotion_state,
            "activation_mode": "soft",
            "task_signature": task_signature,
            "task_family": "",
            "failure_class": None,
            "triggers": list(triggers),
            "required_behavior": ["use pnpm"],
            "prohibited_behavior": ["use npm"],
            "evidence": [],
            "lineage": {"id": f"canary-lineage-{idx}"},
            "created_at": stamp,
            "updated_at": stamp,
            "origin": {"type": "canary_seed"},
            "reuse_stats": {},
        }

    topk_records: list[dict[str, Any]] = []
    for i in range(6):
        topk_records.append(
            _rec(
                i,
                "procedure",
                "repo/shell_execution",
                triggers=("install dependency",),
            )
        )

    procedural_records: list[dict[str, Any]] = []
    for i in range(6):
        procedural_records.append(
            _rec(
                100 + i,
                "procedure",
                "repo/shell_execution",
                authority="teacher",
                promotion_state="candidate",
                triggers=("install dependency",),
            )
        )

    tasks = (
        CanaryTask(
            task_id="ledger_topk",
            prompt="how do I install a dependency in this repo?",
            task_signature="repo/shell_execution",
            seed_records=tuple(topk_records),
        ),
        CanaryTask(
            task_id="packer_procedural",
            prompt="how do I install a dependency correctly?",
            task_signature="repo/shell_execution",
            seed_records=tuple(procedural_records),
        ),
    )
    return CanaryCorpus(name="default", tasks=tasks)
