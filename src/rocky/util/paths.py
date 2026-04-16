from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class WorkspacePaths:
    root: Path
    execution_root: Path
    rocky_dir: Path
    sessions_dir: Path
    memories_dir: Path
    skills_dir: Path
    skills_bundled_dir: Path
    skills_project_dir: Path
    skills_learned_dir: Path
    policies_learned_dir: Path
    student_dir: Path
    student_knowledge_dir: Path
    student_patterns_dir: Path
    student_examples_dir: Path
    episodes_dir: Path
    episodes_support_dir: Path
    episodes_query_dir: Path
    policies_dir: Path
    artifacts_dir: Path
    traces_dir: Path
    eval_dir: Path
    cache_dir: Path
    config_path: Path
    config_local_path: Path

    @property
    def instruction_candidates(self) -> list[Path]:
        return [
            self.root / "AGENTS.md",
            self.root / "ROCKY.md",
            self.root / "CLAUDE.md",
        ]

    @property
    def execution_relative(self) -> str:
        if self.execution_root == self.root:
            return "."
        return str(self.execution_root.relative_to(self.root))

    def ensure_layout(self) -> None:
        for path in [
            self.rocky_dir,
            self.sessions_dir,
            self.memories_dir,
            self.skills_dir,
            self.skills_bundled_dir,
            self.skills_project_dir,
            self.policies_learned_dir,
            self.student_dir,
            self.student_knowledge_dir,
            self.student_patterns_dir,
            self.student_examples_dir,
            self.episodes_dir,
            self.episodes_support_dir,
            self.episodes_query_dir,
            self.policies_dir,
            self.artifacts_dir,
            self.traces_dir,
            self.eval_dir,
            self.cache_dir,
        ]:
            path.mkdir(parents=True, exist_ok=True)


def depth(path: Path, root: Path) -> int:
    rel = path.relative_to(root)
    return len(rel.parts)


def discover_workspace(start: Path, *, state_dir_override: Path | None = None) -> WorkspacePaths:
    current = start.resolve()
    if state_dir_override is not None:
        # Caller explicitly separated state dir from execution dir.
        # Rocky state lives under state_dir_override; shell cwd stays at current.
        state_root = state_dir_override.resolve()
    else:
        for candidate in [current, *current.parents]:
            if (candidate / ".rocky").exists() or (candidate / ".git").exists():
                root = candidate
                break
        else:
            root = current
        state_root = root
    rocky_dir = state_root / ".rocky"
    student_dir = rocky_dir / "student"
    return WorkspacePaths(
        root=state_root,
        execution_root=current,
        rocky_dir=rocky_dir,
        sessions_dir=rocky_dir / "sessions",
        memories_dir=rocky_dir / "memories",
        skills_dir=rocky_dir / "skills",
        skills_bundled_dir=rocky_dir / "skills" / "bundled",
        skills_project_dir=rocky_dir / "skills" / "project",
        skills_learned_dir=rocky_dir / "skills" / "learned",
        policies_learned_dir=rocky_dir / "policies" / "learned",
        student_dir=student_dir,
        student_knowledge_dir=student_dir / "knowledge",
        student_patterns_dir=student_dir / "patterns",
        student_examples_dir=student_dir / "examples",
        episodes_dir=rocky_dir / "episodes",
        episodes_support_dir=rocky_dir / "episodes" / "support",
        episodes_query_dir=rocky_dir / "episodes" / "query",
        policies_dir=rocky_dir / "policies",
        artifacts_dir=rocky_dir / "artifacts",
        traces_dir=rocky_dir / "traces",
        eval_dir=rocky_dir / "eval",
        cache_dir=rocky_dir / "cache",
        config_path=rocky_dir / "config.yaml",
        config_local_path=rocky_dir / "config.local.yaml",
    )


def global_root() -> Path:
    return Path.home() / ".config" / "rocky"


def ensure_global_layout(*, create_layout: bool = True) -> Path:
    root = global_root()
    if create_layout:
        for rel in ["skills", "memories", "providers", "policies", "caches"]:
            (root / rel).mkdir(parents=True, exist_ok=True)
    return root
