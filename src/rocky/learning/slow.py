from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from rocky.util.time import utc_iso


class SlowLearner:
    def __init__(self, query_dir: Path, policies_dir: Path, *, create_layout: bool = True) -> None:
        self.query_dir = query_dir
        self.policies_dir = policies_dir
        if create_layout:
            self.policies_dir.mkdir(parents=True, exist_ok=True)

    def run_once(self) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        for path in sorted(self.query_dir.glob("qry_*.json")):
            try:
                rows.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
        total = len(rows)
        success = sum(1 for row in rows if row.get("result") == "success")
        by_signature: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "success": 0}
        )
        policy_counter: Counter[str] = Counter()
        for row in rows:
            signature = str(row.get("task_signature", "unknown"))
            by_signature[signature]["count"] += 1
            if row.get("result") == "success":
                by_signature[signature]["success"] += 1
            observed_policies = row.get("policies_used") or row.get("skills_used") or []
            for policy in observed_policies:
                policy_counter[str(policy)] += 1
        report = {
            "generated_at": utc_iso(),
            "query_episode_count": total,
            "success_rate": round(success / total, 3) if total else 0.0,
            "top_task_signatures": [
                {
                    "task_signature": signature,
                    "count": values["count"],
                    "success": values["success"],
                    "success_rate": round(values["success"] / values["count"], 3)
                    if values["count"]
                    else 0.0,
                }
                for signature, values in sorted(
                    by_signature.items(),
                    key=lambda item: item[1]["count"],
                    reverse=True,
                )[:10]
            ],
            "top_policies": [
                {"policy": policy, "count": count}
                for policy, count in policy_counter.most_common(10)
            ],
            "notes": [
                "This is a heuristic slow-learner report.",
                "Use it to inspect post-adaptation query behavior before adding policy optimization.",
            ],
        }
        output_path = self.policies_dir / "slow_learner_report.json"
        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return {**report, "path": str(output_path)}
