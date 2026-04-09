from __future__ import annotations

from pathlib import Path

from rocky.learning.policies import LearnedPolicy, LearnedPolicyLoader, LearnedPolicyRetriever


def _policy(
    name: str,
    description: str,
    *,
    triggers: list[str] | None = None,
    task_signatures: list[str] | None = None,
) -> LearnedPolicy:
    metadata: dict[str, object] = {"policy_id": name, "description": description}
    if triggers:
        metadata["retrieval"] = {"triggers": triggers}
    if task_signatures:
        metadata["task_signatures"] = task_signatures
    return LearnedPolicy(
        policy_id=name,
        scope="project",
        path=Path(f"/tmp/{name}/POLICY.md"),
        body=f"# {name}\n",
        metadata=metadata,
        origin="learned",
    )


def test_learned_policy_retrieval_prefers_exact_task_signature() -> None:
    retriever = LearnedPolicyRetriever(
        [
            _policy(
                "catalog-output-contract",
                "Return only the exact JSON contract the teacher asked for.",
                triggers=["merge decisions", "exact json"],
                task_signatures=["repo/shell_execution"],
            ),
            _policy(
                "false-web-refusal",
                "Use web tools instead of falsely refusing live questions.",
                triggers=["current", "latest"],
                task_signatures=["research/live_compare/general"],
            ),
        ]
    )

    results = retriever.retrieve(
        "execute pending_catalog.sh and return the exact json merge decisions",
        "repo/shell_execution",
    )

    assert [policy.policy_id for policy in results] == ["catalog-output-contract"]


def test_learned_policy_loader_reads_new_and_legacy_layouts(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    new_policy = workspace / ".rocky" / "policies" / "learned" / "catalog-contract" / "POLICY.md"
    legacy_policy = workspace / ".rocky" / "skills" / "learned" / "legacy-contract" / "SKILL.md"
    new_policy.parent.mkdir(parents=True, exist_ok=True)
    legacy_policy.parent.mkdir(parents=True, exist_ok=True)
    new_policy.write_text(
        "---\npolicy_id: catalog-contract\ndescription: exact json\nretrieval:\n  triggers:\n    - exact json\n---\n\n# Learned corrective policy\n",
        encoding="utf-8",
    )
    legacy_policy.write_text(
        "---\nname: legacy-contract\ndescription: legacy correction\nretrieval:\n  triggers:\n    - legacy\n---\n\n# Learned corrective workflow\n",
        encoding="utf-8",
    )

    loader = LearnedPolicyLoader(workspace)
    policies = loader.load_all()

    assert [policy.policy_id for policy in policies] == ["catalog-contract", "legacy-contract"]
