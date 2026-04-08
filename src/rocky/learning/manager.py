from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Any

from rocky.config.models import LearningConfig
from rocky.learning.episodes import EpisodeStore
from rocky.learning.slow import SlowLearner
from rocky.learning.synthesis import FeedbackAnalysis, SkillSynthesizer
from rocky.util.text import safe_json
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
        self.reflections_dir = artifacts_dir / "learning_reflections"
        self.policies_dir = policies_dir
        self.config = config
        if create_layout:
            self.learned_root.mkdir(parents=True, exist_ok=True)
            self.artifacts_dir.mkdir(parents=True, exist_ok=True)
            self.reflections_dir.mkdir(parents=True, exist_ok=True)
        self.episode_store = EpisodeStore(
            support_dir=support_dir,
            query_dir=query_dir,
            generation_file=learned_root / "generation.json",
            create_layout=create_layout,
        )
        self.synthesizer = SkillSynthesizer(use_model=True)
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
        *,
        thread_id: str | None = None,
        task_family: str | None = None,
        failure_class: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "task_signature": task_signature,
            "task_family": task_family or task_signature.split("/", 1)[0],
            "thread_id": thread_id,
            "scope": scope,
            "skill_generation_seen": self.current_generation(),
            "prompt_summary": prompt[:240],
            "tool_trace": list((trace or {}).get("selected_tools") or []),
            "failure_type": failure_type,
            "failure_class": failure_class or (trace or {}).get("verification", {}).get("failure_class"),
            "user_feedback": feedback,
            "artifacts": [],
            "last_answer_excerpt": answer[:1200],
        }
        return self.episode_store.record_support(payload)

    def _meta_paths(self) -> list[Path]:
        return sorted(self.learned_root.rglob("SKILL.meta.json"))

    def _read_meta(self, path: Path) -> dict[str, Any] | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _write_meta(self, path: Path, payload: dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _skill_name_from_meta(self, meta: dict[str, Any], meta_path: Path) -> str:
        return str(meta.get("skill_id") or meta_path.parent.name)

    def _promote_skill_meta(self, meta_path: Path, payload: dict[str, Any]) -> None:
        payload["metadata"] = dict(payload.get("metadata") or {})
        payload["metadata"]["promotion_state"] = "promoted"
        payload["metadata"]["verified_success_count"] = int(payload["metadata"].get("verified_success_count") or 0)
        payload["metadata"]["verification"] = {"status": "promoted", "tests": payload["metadata"].get("verification", {}).get("tests", [])}
        payload["promoted_at"] = utc_iso()
        self._write_meta(meta_path, payload)
        skill_path = Path(payload["skill_path"])
        if skill_path.exists():
            text = skill_path.read_text(encoding="utf-8")
            if "promotion_state: candidate" in text:
                skill_path.write_text(text.replace("promotion_state: candidate", "promotion_state: promoted", 1), encoding="utf-8")

    def analyze_feedback(
        self,
        task_signature: str,
        prompt: str,
        answer: str,
        feedback: str,
        trace: dict[str, Any] | None = None,
        *,
        provider: Any | None = None,
        task_family: str | None = None,
        thread_id: str | None = None,
        failure_class: str | None = None,
    ) -> FeedbackAnalysis:
        return self.synthesizer.analyze_feedback(
            task_signature=task_signature,
            feedback=feedback,
            last_prompt=prompt,
            last_answer=answer,
            trace=trace,
            task_family=task_family,
            thread_id=thread_id,
            failure_class=failure_class,
            provider=provider,
        )

    def _write_reflection_artifact(
        self,
        support_episode_id: str,
        *,
        prompt: str,
        answer: str,
        feedback: str,
        trace: dict[str, Any] | None,
        analysis: FeedbackAnalysis,
    ) -> str:
        self.reflections_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "support_episode_id": support_episode_id,
            "published_at": utc_iso(),
            "prompt": prompt,
            "answer": answer,
            "feedback": feedback,
            "analysis": analysis.as_record(),
            "trace_snapshot": self.synthesizer._trace_snapshot(trace or {}),
        }
        path = self.reflections_dir / f"{support_episode_id}.json"
        path.write_text(safe_json(payload) + "\n", encoding="utf-8")
        return str(path)

    def learn_from_feedback(
        self,
        task_signature: str,
        prompt: str,
        answer: str,
        feedback: str,
        trace: dict[str, Any] | None = None,
        scope: str = "project",
        *,
        thread_id: str | None = None,
        task_family: str | None = None,
        failure_class: str | None = None,
        analysis: FeedbackAnalysis | None = None,
        provider: Any | None = None,
    ) -> dict[str, Any]:
        if not self.config.enabled:
            return {"published": False, "reason": "learning disabled"}
        analysis = analysis or self.analyze_feedback(
            task_signature=task_signature,
            prompt=prompt,
            answer=answer,
            feedback=feedback,
            trace=trace,
            provider=provider,
            task_family=task_family,
            thread_id=thread_id,
            failure_class=failure_class,
        )
        support = self.record_support(
            task_signature,
            prompt,
            answer,
            feedback,
            trace,
            scope,
            thread_id=thread_id,
            task_family=task_family,
            failure_class=analysis.failure_class,
        )
        reflection_path = self._write_reflection_artifact(
            support["id"],
            prompt=prompt,
            answer=answer,
            feedback=feedback,
            trace=trace,
            analysis=analysis,
        )
        if not analysis.should_publish_skill:
            return {
                "published": False,
                "reason": "reflection kept this as notebook memory rather than a reusable skill",
                "support_episode_id": support["id"],
                "failure_class": analysis.failure_class,
                "task_family": task_family or task_signature.split("/", 1)[0],
                "thread_id": thread_id,
                "memory_kind": analysis.memory_kind,
                "reflection_source": analysis.reflection_source,
                "reflection_path": reflection_path,
            }
        new_generation = self.current_generation() + 1
        draft = self.synthesizer.build_draft(
            self.learned_root,
            task_signature,
            new_generation,
            feedback,
            support["id"],
            prompt,
            answer,
            trace,
            scope,
            task_family=task_family,
            thread_id=thread_id,
            failure_class=analysis.failure_class,
            analysis=analysis,
            provider=provider,
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
            "reflection_path": reflection_path,
            "rollback": None,
            "metadata": draft.metadata,
            "thread_id": thread_id,
            "task_family": task_family or task_signature.split("/", 1)[0],
            "failure_class": analysis.failure_class,
            "promotion_state": draft.metadata.get("promotion_state", "candidate"),
        }
        meta_path = path.parent / "SKILL.meta.json"
        self._write_meta(meta_path, meta)
        self.episode_store.set_generation(new_generation)
        return {
            "published": True,
            "skill_id": draft.skill_id,
            "skill_path": str(path),
            "meta_path": str(meta_path),
            "support_episode_id": support["id"],
            "generation": new_generation,
            "promotion_state": draft.metadata.get("promotion_state", "candidate"),
            "failure_class": meta["failure_class"],
            "task_family": meta["task_family"],
            "thread_id": thread_id,
            "memory_kind": analysis.memory_kind,
            "reflection_source": analysis.reflection_source,
            "reflection_path": reflection_path,
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
        recorded = {
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
        if skills_used:
            skill_names = set(skills_used)
            for meta_path in self._meta_paths():
                meta = self._read_meta(meta_path)
                if meta is None:
                    continue
                if self._skill_name_from_meta(meta, meta_path) not in skill_names:
                    continue
                metadata = dict(meta.get("metadata") or {})
                metadata["reuse_count"] = int(metadata.get("reuse_count") or 0) + 1
                if result == "success":
                    metadata["verified_success_count"] = int(metadata.get("verified_success_count") or 0) + 1
                meta["metadata"] = metadata
                meta["last_query_result"] = result
                meta["last_query_at"] = utc_iso()
                self._write_meta(meta_path, meta)
                if metadata.get("promotion_state") == "candidate" and int(metadata.get("verified_success_count") or 0) >= 1 and result == "success":
                    self._promote_skill_meta(meta_path, meta)
        return recorded

    def list_learned(self) -> list[dict[str, Any]]:
        rows = []
        for meta_path in self._meta_paths():
            payload = self._read_meta(meta_path)
            if payload is not None:
                rows.append(payload)
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
