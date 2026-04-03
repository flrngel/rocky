from __future__ import annotations

from pathlib import Path

from rocky.skills.models import Skill
from rocky.skills.retriever import SkillRetriever


def _skill(name: str, description: str, *, triggers: list[str] | None = None, task_signatures: list[str] | None = None) -> Skill:
    metadata: dict[str, object] = {"description": description}
    if triggers:
        metadata["retrieval"] = {"triggers": triggers}
    if task_signatures:
        metadata["task_signatures"] = task_signatures
    return Skill(
        name=name,
        scope="global",
        path=Path(f"/tmp/{name}/SKILL.md"),
        body=f"# {name}\n",
        metadata=metadata,
        origin="test",
    )


def test_skill_retrieval_ignores_single_generic_word_overlap() -> None:
    retriever = SkillRetriever(
        [
            _skill(
                "find-skills",
                "Helps users discover and install agent skills.",
            ),
            _skill(
                "planning-with-files",
                "Use markdown files as persistent working memory for complex tasks.",
            ),
            _skill(
                "general-operator",
                "General operating guidance for Rocky across code and workflow tasks.",
                task_signatures=["repo/*"],
            ),
        ]
    )

    results = retriever.retrieve("execute command and find information about me", "repo/shell_execution")

    assert [skill.name for skill in results] == ["general-operator"]


def test_skill_retrieval_prefers_explicit_trigger_matches() -> None:
    retriever = SkillRetriever(
        [
            _skill("general-operator", "General repo help.", task_signatures=["repo/*"]),
            _skill(
                "shell-runner",
                "Execute operator-provided shell commands safely.",
                triggers=["execute command"],
            ),
        ]
    )

    results = retriever.retrieve("please execute command and summarize the result", "repo/shell_execution")

    assert [skill.name for skill in results][:2] == ["shell-runner", "general-operator"]


def test_skill_retrieval_still_supports_explicit_skill_discovery_queries() -> None:
    retriever = SkillRetriever(
        [
            _skill(
                "find-skills",
                "Helps users discover and install agent skills when they ask to find a skill for something.",
            )
        ]
    )

    results = retriever.retrieve("find a skill for browser testing", "conversation/general")

    assert [skill.name for skill in results] == ["find-skills"]


def test_skill_retrieval_prefers_learned_project_skill_for_exact_task_signature() -> None:
    learned = Skill(
        name="repo-shell-execution",
        scope="project",
        path=Path("/tmp/repo-shell-execution/SKILL.md"),
        body="# repo-shell-execution\nUse run_python after the shell command.\n",
        metadata={
            "description": "Learned correction for shell execution response analysis.",
            "task_signatures": ["repo/shell_execution"],
            "generation": 2,
            "retrieval": {"triggers": ["pending_catalog.sh", "merge decisions"]},
        },
        origin="learned",
    )
    bundled = _skill(
        "general-operator",
        "General repo help.",
        task_signatures=["repo/*"],
    )
    retriever = SkillRetriever([bundled, learned])

    results = retriever.retrieve(
        "execute pending_catalog.sh and decide merge decisions",
        "repo/shell_execution",
    )

    assert [skill.name for skill in results][:2] == ["repo-shell-execution", "general-operator"]
