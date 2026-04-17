from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rocky.config.models import RetrievalConfig
from rocky.core.runtime_state import ActiveTaskThread
from rocky.util.io import read_text
from rocky.util.text import tokenize_keywords
from rocky.util.yamlx import split_frontmatter


WEAK_MATCH_TOKENS = {"command", "find", "help", "information", "task", "user"}
# Per-domain weak-token allowlist (F1 fix).
# Tokens in WEAK_MATCH_TOKENS that are demoted from the strong-match gate for a
# specific task_family. Add new families here when a weak token is legitimately
# discriminative in that domain (e.g. "command" is meaningful in repo workflows).
# LedgerRetriever (ledger_retriever.py) has a parallel scoring path that does NOT
# yet apply this allowlist; reconcile during Phase 2.4 T3 collapse.
_DOMAIN_ALLOWED_WEAK_TOKENS: dict[str, frozenset[str]] = {
    "repo": frozenset({"command"}),
}
PROMOTION_WEIGHT = {"promoted": 3, "candidate": 1, "rejected": -2, "stale": -1}


@dataclass(slots=True)
class LearnedPolicy:
    policy_id: str
    scope: str
    path: Path
    body: str
    metadata: dict[str, Any]
    origin: str = "learned"
    storage_format: str = "policy"

    @property
    def kind(self) -> str:
        return "learned_policy"

    @property
    def name(self) -> str:
        return self.policy_id

    @property
    def description(self) -> str:
        if self.metadata.get("description"):
            return str(self.metadata["description"])
        for line in self.body.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                return stripped[:120]
        return "No description"

    @property
    def task_signatures(self) -> list[str]:
        return [str(item) for item in (self.metadata.get("task_signatures") or [])]

    @property
    def triggers(self) -> list[str]:
        retrieval = self.metadata.get("retrieval") or {}
        return [str(item) for item in (retrieval.get("triggers") or [])]

    @property
    def retrieval_keywords(self) -> list[str]:
        retrieval = self.metadata.get("retrieval") or {}
        keywords = [str(item) for item in (retrieval.get("keywords") or [])]
        keywords.extend(str(item) for item in (self.metadata.get("paths") or []))
        keywords.extend(str(item) for item in (self.metadata.get("tools") or []))
        return keywords

    @property
    def generation(self) -> int:
        try:
            return int(self.metadata.get("generation", 0))
        except Exception:
            return 0

    def as_record(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "name": self.policy_id,
            "scope": self.scope,
            "origin": self.origin,
            "storage_format": self.storage_format,
            "generation": self.generation,
            "description": self.description,
            "path": str(self.path),
            "failure_class": self.metadata.get("failure_class"),
            "promotion_state": self.metadata.get("promotion_state"),
        }


class LearnedPolicyLoader:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        rocky_root = workspace_root / ".rocky"
        self.policy_root = rocky_root / "policies" / "learned"
        self.legacy_root = rocky_root / "skills" / "learned"

    def _scan(
        self,
        root: Path,
        filename: str,
        *,
        origin: str,
        storage_format: str,
    ) -> list[LearnedPolicy]:
        policies: list[LearnedPolicy] = []
        if not root.exists():
            return policies
        for path in sorted(root.rglob(filename)):
            try:
                raw = read_text(path)
                metadata, body = split_frontmatter(raw)
                policy_id = str(
                    metadata.get("policy_id")
                    or metadata.get("name")
                    or metadata.get("skill_id")
                    or path.parent.name
                )
                normalized = dict(metadata)
                normalized.setdefault("policy_id", policy_id)
                if "should_publish_policy" not in normalized and "should_publish_skill" in normalized:
                    normalized["should_publish_policy"] = normalized["should_publish_skill"]
                policies.append(
                    LearnedPolicy(
                        policy_id=policy_id,
                        scope=str(normalized.get("scope") or "project"),
                        path=path,
                        body=body,
                        metadata=normalized,
                        origin=origin,
                        storage_format=storage_format,
                    )
                )
            except Exception:
                continue
        return policies

    def load_all(self) -> list[LearnedPolicy]:
        current = self._scan(
            self.policy_root,
            "POLICY.md",
            origin="learned",
            storage_format="policy",
        )
        legacy = self._scan(
            self.legacy_root,
            "SKILL.md",
            origin="learned_legacy",
            storage_format="legacy_skill",
        )
        by_id: dict[str, LearnedPolicy] = {item.policy_id: item for item in current}
        for item in legacy:
            by_id.setdefault(item.policy_id, item)
        return sorted(
            by_id.values(),
            key=lambda item: (0 if item.scope == "project" else 1, item.policy_id),
        )


class LearnedPolicyRetriever:
    _LEGACY_DEFAULT_LIMIT = 4

    def __init__(
        self,
        policies: list[LearnedPolicy],
        config: RetrievalConfig | None = None,
    ) -> None:
        self.policies = policies
        # Phase 3 T3 (limit-narrowed): when an active meta-variant supplies a
        # `RetrievalConfig` overlay, top-K is sourced from `config.top_k_limit`.
        # Without an overlay, the legacy default (4) is preserved bit-identically.
        self.config = config

    def inventory(self) -> list[dict[str, Any]]:
        return [policy.as_record() for policy in self.policies]

    def retrieve(
        self,
        prompt: str,
        task_signature: str,
        *,
        thread: ActiveTaskThread | None = None,
        limit: int | None = None,
    ) -> list[LearnedPolicy]:
        if limit is None:
            limit = (
                self.config.top_k_limit
                if self.config is not None
                else self._LEGACY_DEFAULT_LIMIT
            )
        prompt_lower = prompt.lower()
        query_words = tokenize_keywords(prompt)
        thread_words = tokenize_keywords(thread.summary_text()) if thread is not None else set()
        scored: list[tuple[tuple[int, int, int], LearnedPolicy]] = []
        for policy in self.policies:
            score = 0
            trigger_match = any(trigger.lower() in prompt_lower for trigger in policy.triggers)
            name_tokens = tokenize_keywords(policy.policy_id)
            description_tokens = tokenize_keywords(policy.description)
            trigger_tokens = set().union(*(tokenize_keywords(trigger) for trigger in policy.triggers))
            keyword_tokens = set().union(*(tokenize_keywords(keyword) for keyword in policy.retrieval_keywords))
            token_matches = (
                (query_words & name_tokens)
                | (query_words & description_tokens)
                | (query_words & trigger_tokens)
                | (query_words & keyword_tokens)
                | (thread_words & keyword_tokens)
            )
            policy_task_family = str(policy.metadata.get("task_family") or "")
            effective_weak = WEAK_MATCH_TOKENS - _DOMAIN_ALLOWED_WEAK_TOKENS.get(policy_task_family, frozenset())
            strong_token_matches = token_matches - effective_weak
            token_overlap = (
                len(query_words & name_tokens) * 3
                + len(query_words & description_tokens)
                + len(query_words & trigger_tokens) * 2
                + len(query_words & keyword_tokens) * 2
                + len(thread_words & keyword_tokens)
            )
            score += token_overlap
            if trigger_match:
                score += 6
            task_signature_score = 0
            task_signature_score += sum(3 for sig in policy.task_signatures if sig.endswith("*") and task_signature.startswith(sig[:-1]))
            task_signature_score += sum(6 for sig in policy.task_signatures if sig == task_signature)
            score += task_signature_score
            if policy.scope == "project":
                score += 2
            if task_signature_score:
                score += 4
            failure_class = str(policy.metadata.get("failure_class") or "")
            if failure_class and any(token in prompt_lower for token in tokenize_keywords(failure_class)):
                score += 3
            task_family = str(policy.metadata.get("task_family") or "")
            if thread is not None and task_family and task_family == thread.task_family:
                score += 3
            score += PROMOTION_WEIGHT.get(str(policy.metadata.get("promotion_state") or "promoted"), 0)
            score += min(int(policy.metadata.get("verified_success_count") or 0), 4)
            if policy.generation:
                score += min(policy.generation, 3)
            if not trigger_match and not task_signature_score and not strong_token_matches:
                continue
            if score < 2 and not trigger_match:
                continue
            scored.append(((score, policy.generation, 1 if policy.scope == "project" else 0), policy))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [policy for _, policy in scored[:limit]]
