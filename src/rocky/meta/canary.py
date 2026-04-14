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

    `task_family` (added run-20260414-221947 for Phase 4 transfer evaluation)
    groups tasks for same-family vs held-out-family delta measurement. The
    `improve_at_n` calculator splits per-family metrics using this field.
    Defaults to "repo" for back-compat with the original two-task corpus.
    """

    task_id: str
    prompt: str
    task_signature: str
    seed_records: tuple[dict[str, Any], ...]
    task_family: str = "repo"


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
                    "task_family": task.task_family,
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

        # Phase 4: per-family breakdown for `improve_at_n` (same-family vs
        # held-out-family delta measurement). Raw sums; `improve_at_n` does
        # the baseline-relative math.
        per_family: dict[str, dict[str, Any]] = {}
        for p in result.per_task:
            family = str(p.get("task_family") or "repo")
            bucket = per_family.setdefault(
                family,
                {"total_records_returned": 0, "total_packer_chars": 0, "task_count": 0, "_top1_order": []},
            )
            bucket["total_records_returned"] += p["top_k_record_count"]
            bucket["total_packer_chars"] += p["packer_char_count"]
            bucket["task_count"] += 1
            bucket["_top1_order"].append(p["top_1_record_id"])
        for family, bucket in per_family.items():
            order = bucket.pop("_top1_order")
            bucket["top1_stability_hash"] = hashlib.sha1(
                "|".join(order).encode("utf-8"), usedforsecurity=False
            ).hexdigest()[:16]

        result.aggregate = {
            "total_records_returned": total_records,
            "total_packer_chars": total_chars,
            "top1_stability_hash": top1_hash,
            "task_count": len(result.per_task),
            "per_family": per_family,
        }
        return result


_IMPROVE_METRICS: tuple[str, ...] = ("total_records_returned", "total_packer_chars")


def improve_at_n(
    canary_results: list[dict[str, Any]],
    baseline: dict[str, Any],
    *,
    target_family: str = "repo",
    metrics: tuple[str, ...] = _IMPROVE_METRICS,
) -> dict[str, Any]:
    """Phase 4 transfer-evaluation calculator.

    Given N canary-aggregate dicts for a variant and one baseline aggregate,
    compute max/mean deltas per metric split by same-family (the variant's
    `target_family`) and held-out-family (all other families in the aggregate's
    `per_family` map).

    Delta sign convention: raw `result_metric - baseline_metric`. Positive
    means the metric value went UP under the variant; negative means it went
    DOWN. This calculator does NOT interpret whether up-is-good or
    down-is-good — direction is a caller concern (e.g. NS-2 metrics dashboard).

    Parameters
    ----------
    canary_results : list of aggregate dicts from `CanaryResult.aggregate`,
                     one per variant canary run.
    baseline : a single aggregate dict (from a no-variant canary run).
    target_family : the family the variant is "targeted" at. Default "repo"
                    matches the default corpus's primary family.
    metrics : which aggregate keys to compute deltas for. Defaults to the
              two core canary metrics.

    Returns
    -------
    dict with keys:
      - `n` : number of canary results
      - `target_family` : echoed input
      - `metrics` : list of metric names evaluated
      - `same_family` : {metric: {max_delta, mean_delta, deltas}}
      - `held_out_family` : {metric: {max_delta, mean_delta, deltas}}
      - `held_out_families` : sorted list of family names aggregated into held-out

    Edge cases:
      - If `canary_results` is empty: returns `{"n": 0, "error": "no canary history", ...}`.
      - If a result lacks `per_family`: falls back to top-level aggregate for
        same-family; that run contributes no data to the held-out side.
      - If baseline lacks `per_family` for a given family: the delta for that
        family on that metric is skipped (prevents spurious deltas vs zero).
    """
    n = len(canary_results)
    if n == 0:
        return {
            "n": 0,
            "target_family": target_family,
            "metrics": list(metrics),
            "error": "no canary history",
            "same_family": {},
            "held_out_family": {},
            "held_out_families": [],
        }

    # Discover held-out families by union across all runs + baseline.
    def _families(agg: dict[str, Any]) -> set[str]:
        pf = agg.get("per_family") or {}
        if isinstance(pf, dict):
            return set(pf.keys())
        return set()

    all_families: set[str] = set(_families(baseline))
    for r in canary_results:
        all_families |= _families(r)
    held_out_families = sorted(f for f in all_families if f != target_family)

    def _family_metric(agg: dict[str, Any], family: str, metric: str) -> float | None:
        pf = agg.get("per_family") or {}
        if family in pf and isinstance(pf[family], dict) and metric in pf[family]:
            return float(pf[family][metric])
        return None

    def _same_family_metric(agg: dict[str, Any], metric: str) -> float | None:
        """For same-family, fall back to top-level aggregate if per_family absent."""
        val = _family_metric(agg, target_family, metric)
        if val is not None:
            return val
        if metric in agg:
            return float(agg[metric])
        return None

    def _summarize(baseline_val: float | None, run_vals: list[float | None]) -> dict[str, Any]:
        deltas: list[float] = []
        for v in run_vals:
            if v is None or baseline_val is None:
                continue
            deltas.append(v - baseline_val)
        if not deltas:
            return {"max_delta": 0.0, "mean_delta": 0.0, "deltas": []}
        # `max` by absolute value so both improvement and regression surface
        # without sign manipulation; callers interpret sign.
        max_delta = max(deltas, key=lambda d: abs(d))
        mean_delta = sum(deltas) / len(deltas)
        return {
            "max_delta": max_delta,
            "mean_delta": mean_delta,
            "deltas": deltas,
        }

    same_family_out: dict[str, dict[str, Any]] = {}
    for metric in metrics:
        baseline_val = _same_family_metric(baseline, metric)
        run_vals = [_same_family_metric(r, metric) for r in canary_results]
        same_family_out[metric] = _summarize(baseline_val, run_vals)

    held_out_out: dict[str, dict[str, Any]] = {}
    for metric in metrics:
        # Aggregate held-out by summing over all non-target families per run/baseline.
        def _held_out_total(agg: dict[str, Any]) -> float | None:
            total = 0.0
            found = False
            for family in held_out_families:
                v = _family_metric(agg, family, metric)
                if v is not None:
                    total += v
                    found = True
            return total if found else None

        baseline_val = _held_out_total(baseline)
        run_vals = [_held_out_total(r) for r in canary_results]
        held_out_out[metric] = _summarize(baseline_val, run_vals)

    return {
        "n": n,
        "target_family": target_family,
        "metrics": list(metrics),
        "same_family": same_family_out,
        "held_out_family": held_out_out,
        "held_out_families": held_out_families,
    }


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

    Three tasks (run-20260414-221947 extended with held-out `research` family):
      * `ledger_topk` [repo] — 6 competing records all matching the prompt.
        Lets a top-K override to 2 produce a measurable narrowing (6 → 2).
      * `packer_procedural` [repo] — 6 candidate policies; packer
        procedural_cap narrowing from 6 → 2 produces a measurable
        char-count drop.
      * `research_discovery` [research] — 5 research-leaning records under
        `site/understanding/general` task_signature. Held-out family for
        `improve_at_n` transfer-evaluation — variant's same-family (repo)
        delta is measured separately from held-out (research) delta.
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

    # Research-family seed: distinct task_signature + triggers from repo-family
    # so retrieval exercises different records. 5 records so a top_k=2 variant
    # produces observable narrowing in BOTH families (same-family AND held-out).
    research_records: list[dict[str, Any]] = []
    for i in range(5):
        research_records.append(
            _rec(
                200 + i,
                "procedure",
                "site/understanding/general",
                triggers=("compare models",),
            )
        )

    tasks = (
        CanaryTask(
            task_id="ledger_topk",
            prompt="how do I install a dependency in this repo?",
            task_signature="repo/shell_execution",
            seed_records=tuple(topk_records),
            task_family="repo",
        ),
        CanaryTask(
            task_id="packer_procedural",
            prompt="how do I install a dependency correctly?",
            task_signature="repo/shell_execution",
            seed_records=tuple(procedural_records),
            task_family="repo",
        ),
        CanaryTask(
            task_id="research_discovery",
            prompt="compare models for this task",
            task_signature="site/understanding/general",
            seed_records=tuple(research_records),
            task_family="research",
        ),
    )
    return CanaryCorpus(name="default", tasks=tasks)
