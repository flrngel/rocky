from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from rocky.config.models import LearningConfig
from rocky.learning.episodes import EpisodeStore
from rocky.learning.slow import SlowLearner
from rocky.learning.synthesis import SkillSynthesizer
from rocky.util.time import utc_iso


class LearningManager:
    def __init__(
        self,
        support_dir: Path,
        query_dir: Path,
        learned_root: Path,
        artifacts_dir: Path,
        policies_dir: Path,
        config: LearningConfig,
        *,
        create_layout: bool = True,
    ) -> None:
        self.support_dir = support_dir
        self.query_dir = query_dir
        self.learned_root = learned_root
        self.artifacts_dir = artifacts_dir
        self.policies_dir = policies_dir
        self.config = config
        if create_layout:
            self.learned_root.mkdir(parents=True, exist_ok=True)
            self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.episode_store = EpisodeStore(
            support_dir=support_dir,
            query_dir=query_dir,
            generation_file=learned_root / "generation.json",
            create_layout=create_layout,
        )
        self.synthesizer = SkillSynthesizer(use_model=False)
        self.slow_learner = SlowLearner(
            query_dir=query_dir,
            policies_dir=policies_dir,
            create_layout=create_layout,
        )

    def current_generation(self) -> int:
        return self.episode_store.current_generation()

    def record_support(
        self,
        task_signature: str,
        prompt: str,
        answer: str,
        feedback: str,
        trace: dict[str, Any] | None = None,
        scope: str = "project",
        failure_type: str = "user_feedback",
    ) -> dict[str, Any]:
        payload = {
            "task_signature": task_signature,
            "scope": scope,
            "skill_generation_seen": self.current_generation(),
            "prompt_summary": prompt[:240],
            "tool_trace": list((trace or {}).get("selected_tools") or []),
            "failure_type": failure_type,
            "user_feedback": feedback,
            "artifacts": [],
            "last_answer_excerpt": answer[:1200],
        }
        return self.episode_store.record_support(payload)

    def learn_from_feedback(
        self,
        task_signature: str,
        prompt: str,
        answer: str,
        feedback: str,
        trace: dict[str, Any] | None = None,
        scope: str = "project",
    ) -> dict[str, Any]:
        if not self.config.enabled:
            return {"published": False, "reason": "learning disabled"}
        support = self.record_support(
            task_signature,
            prompt,
            answer,
            feedback,
            trace,
            scope,
        )
        new_generation = self.current_generation() + 1
        draft = self.synthesizer.build_draft(
            self.learned_root,
            task_signature,
            new_generation,
            feedback,
            support["id"],
            prompt,
            answer,
            scope,
        )
        path = draft.path
        if path.exists():
            path = path.parent.parent / f"{draft.skill_id}-{new_generation}" / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(draft.content, encoding="utf-8")
        meta = {
            "skill_id": draft.skill_id,
            "skill_path": str(path),
            "scope": scope,
            "generation": new_generation,
            "published": True,
            "published_at": utc_iso(),
            "feedback": feedback,
            "support_episode_id": support["id"],
            "rollback": None,
            "metadata": draft.metadata,
        }
        meta_path = path.parent / "SKILL.meta.json"
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.episode_store.set_generation(new_generation)
        return {
            "published": True,
            "skill_id": draft.skill_id,
            "skill_path": str(path),
            "meta_path": str(meta_path),
            "support_episode_id": support["id"],
            "generation": new_generation,
        }

    def record_query(
        self,
        task_signature: str,
        skills_used: list[str],
        verifier: str,
        result: str,
        usage: dict[str, Any] | None,
        latency_ms: int | None,
    ) -> dict[str, Any]:
        if not self.config.enabled:
            return {"recorded": False, "reason": "learning disabled"}
        return {
            "recorded": True,
            **self.episode_store.record_query(
                {
                    "task_signature": task_signature,
                    "skill_generation_seen": self.current_generation(),
                    "skills_used": skills_used,
                    "verifier": verifier,
                    "result": result,
                    "cost": usage or {},
                    "latency_ms": latency_ms,
                }
            ),
        }

    def list_learned(self) -> list[dict[str, Any]]:
        rows = []
        for meta_path in sorted(self.learned_root.rglob("SKILL.meta.json")):
            try:
                rows.append(json.loads(meta_path.read_text(encoding="utf-8")))
            except Exception:
                continue
        rows.sort(key=lambda item: item.get("published_at", ""), reverse=True)
        return rows

    def rollback_latest(self) -> dict[str, Any] | None:
        learned = self.list_learned()
        if not learned:
            return None
        latest = learned[0]
        skill_path = Path(latest["skill_path"])
        if not skill_path.exists():
            return {"rolled_back": False, "reason": "skill path missing", **latest}
        rollback_dir = (
            self.artifacts_dir
            / "rollback"
            / f"{skill_path.parent.name}__{utc_iso().replace(':', '').replace('-', '')}"
        )
        rollback_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(skill_path.parent), str(rollback_dir))
        return {
            "rolled_back": True,
            "skill_id": latest.get("skill_id"),
            "from": str(skill_path.parent),
            "to": str(rollback_dir),
        }

    def run_slow_learner(self) -> dict[str, Any]:
        if not self.config.slow_learner_enabled:
            return {"ran": False, "reason": "slow learner disabled"}
        return {"ran": True, **self.slow_learner.run_once()}
