"""
Tests for O14: --freeze implies --ignore-retros.

When freeze=True, ContextBuilder must not load prior retrospectives from the
student store into context (poisoned retros would reproduce wrong behavior).
Learned policies and skills continue to load; only retrospective-kind notes
are filtered.

Status: DONE  # xlfg artifact marker — O14 freeze-ignores-retros
"""
from __future__ import annotations

from pathlib import Path

import pytest

from rocky.core.context import ContextBuilder
from rocky.learning.policies import LearnedPolicyRetriever
from rocky.memory.retriever import MemoryRetriever
from rocky.skills.retriever import SkillRetriever
from rocky.student.store import StudentStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SENTINEL = "POISONED_SENTINEL_xlfg_test"


def _make_student_store(tmp_path: Path) -> StudentStore:
    """Create a StudentStore with a retrospective containing the sentinel."""
    store = StudentStore(tmp_path / "student", create_layout=True)
    retro_path = store.retrospectives_dir / "poisoned-retro.md"
    retro_path.write_text(
        "---\n"
        "id: retro_test_001\n"
        "kind: retrospective\n"
        "title: poisoned retro\n"
        "created_at: '2026-01-01T00:00:00Z'\n"
        "updated_at: '2026-01-01T00:00:00Z'\n"
        "task_signature: test/task\n"
        "tags:\n"
        "  - test\n"
        "origin: self_reflection\n"
        "---\n"
        f"{SENTINEL}\n",
        encoding="utf-8",
    )
    return store


def _make_context_builder(
    tmp_path: Path,
    student_store: StudentStore,
    *,
    ignore_retros: bool,
) -> ContextBuilder:
    skill_retriever = SkillRetriever([])
    policy_retriever = LearnedPolicyRetriever([])
    memory_retriever = MemoryRetriever([])
    return ContextBuilder(
        workspace_root=tmp_path,
        execution_root=tmp_path,
        instruction_candidates=[],
        skill_retriever=skill_retriever,
        policy_retriever=policy_retriever,
        memory_retriever=memory_retriever,
        session_store=None,
        student_store=student_store,
        ledger=None,
        ignore_retros=ignore_retros,
    )


# ---------------------------------------------------------------------------
# 1. ignore_retros=True: sentinel must NOT appear in student_notes
# ---------------------------------------------------------------------------

def test_ignore_retros_true_excludes_retros(tmp_path: Path) -> None:
    store = _make_student_store(tmp_path)
    builder = _make_context_builder(tmp_path, store, ignore_retros=True)

    pkg = builder.build(
        prompt="test task",
        task_signature="test/task",
        tool_families=[],
    )

    note_texts = " ".join(n.get("text", "") + n.get("title", "") for n in pkg.student_notes)
    assert SENTINEL not in note_texts, (
        f"Expected sentinel to be absent when ignore_retros=True, "
        f"but found it in student_notes: {pkg.student_notes}"
    )


# ---------------------------------------------------------------------------
# 2. ignore_retros=False (default): sentinel MUST appear — fixture validity
# ---------------------------------------------------------------------------

def test_ignore_retros_false_includes_retros(tmp_path: Path) -> None:
    store = _make_student_store(tmp_path)
    builder = _make_context_builder(tmp_path, store, ignore_retros=False)

    pkg = builder.build(
        prompt="test task",
        task_signature="test/task",
        tool_families=[],
    )

    note_texts = " ".join(n.get("text", "") + n.get("title", "") for n in pkg.student_notes)
    assert SENTINEL in note_texts, (
        f"Expected sentinel to be present when ignore_retros=False "
        f"(fixture validity check), but student_notes were: {pkg.student_notes}"
    )


# ---------------------------------------------------------------------------
# 3. Non-retro student notes still load when ignore_retros=True
# ---------------------------------------------------------------------------

def test_ignore_retros_preserves_non_retro_notes(tmp_path: Path) -> None:
    KNOWLEDGE_SENTINEL = "KNOWLEDGE_SENTINEL_xlfg_test"
    store = _make_student_store(tmp_path)
    knowledge_path = store.knowledge_dir / "good-knowledge.md"
    knowledge_path.write_text(
        "---\n"
        "id: knowledge_test_001\n"
        "kind: knowledge\n"
        "title: good knowledge\n"
        "created_at: '2026-01-01T00:00:00Z'\n"
        "updated_at: '2026-01-01T00:00:00Z'\n"
        "task_signature: test/task\n"
        "tags:\n"
        "  - test\n"
        "origin: teacher\n"
        "---\n"
        f"{KNOWLEDGE_SENTINEL}\n",
        encoding="utf-8",
    )

    builder = _make_context_builder(tmp_path, store, ignore_retros=True)
    pkg = builder.build(
        prompt="test task",
        task_signature="test/task",
        tool_families=[],
    )

    note_texts = " ".join(n.get("text", "") + n.get("title", "") for n in pkg.student_notes)

    assert SENTINEL not in note_texts, (
        "Retrospective sentinel should be absent when ignore_retros=True"
    )
    assert KNOWLEDGE_SENTINEL in note_texts, (
        f"Knowledge sentinel should be present even when ignore_retros=True; "
        f"student_notes: {pkg.student_notes}"
    )


# ---------------------------------------------------------------------------
# 4. ignore_retros=True does NOT suppress skills or learned_policies
# ---------------------------------------------------------------------------

def test_ignore_retros_does_not_affect_skills_or_policies(tmp_path: Path) -> None:
    store = _make_student_store(tmp_path)
    builder = _make_context_builder(tmp_path, store, ignore_retros=True)

    pkg = builder.build(
        prompt="test task",
        task_signature="test/task",
        tool_families=["shell"],
    )

    assert pkg.tool_families == ["shell"]
    assert isinstance(pkg.workspace_focus, dict)
    assert "workspace_root" in pkg.workspace_focus
    assert isinstance(pkg.skills, list)
    assert isinstance(pkg.learned_policies, list)


# ---------------------------------------------------------------------------
# 5. Runtime threading: RockyRuntime.load_from(freeze=True) sets ignore_retros=True
# ---------------------------------------------------------------------------

def test_runtime_freeze_sets_ignore_retros(tmp_path: Path) -> None:
    """
    Verify that load_from(freeze=True) wires ignore_retros=True on the
    ContextBuilder, so the two flags are coupled without a separate CLI flag.
    """
    from rocky.app import RockyRuntime

    runtime = RockyRuntime.load_from(cwd=tmp_path, freeze=True)
    assert runtime.context_builder.ignore_retros is True, (
        "Expected context_builder.ignore_retros=True when load_from(freeze=True)"
    )


def test_runtime_no_freeze_leaves_ignore_retros_false(tmp_path: Path) -> None:
    """CF-4: callers without freeze must not have retros suppressed."""
    from rocky.app import RockyRuntime

    runtime = RockyRuntime.load_from(cwd=tmp_path, freeze=False)
    assert runtime.context_builder.ignore_retros is False, (
        "Expected context_builder.ignore_retros=False when load_from(freeze=False)"
    )


# ---------------------------------------------------------------------------
# 5b. O1 follow-up: refresh_knowledge() must thread freeze_enabled into the
# rebuilt ContextBuilder. The load_from path already sets it; this guards the
# direct-caller path where _auto_self_reflect writes a memory, freezes out, and
# later code paths re-enter refresh_knowledge outside the gate.
# ---------------------------------------------------------------------------


def test_refresh_knowledge_preserves_ignore_retros_under_freeze(tmp_path: Path) -> None:
    """After refresh_knowledge, the ContextBuilder must still ignore retros
    when the runtime was created with freeze=True. Without the fix, the new
    ContextBuilder would silently default ignore_retros=False."""
    from rocky.app import RockyRuntime

    runtime = RockyRuntime.load_from(cwd=tmp_path, freeze=True)
    assert runtime.context_builder.ignore_retros is True

    retro_path = runtime.student_store.retrospectives_dir / "poisoned-retro.md"
    retro_path.parent.mkdir(parents=True, exist_ok=True)
    retro_path.write_text(
        "---\n"
        "id: retro_refresh_001\n"
        "kind: retrospective\n"
        "title: refresh-scoped retro\n"
        "created_at: '2026-01-01T00:00:00Z'\n"
        "updated_at: '2026-01-01T00:00:00Z'\n"
        "task_signature: general/task\n"
        "tags:\n"
        "  - general\n"
        "origin: self_reflection\n"
        "---\n"
        f"{SENTINEL}\n",
        encoding="utf-8",
    )

    runtime.refresh_knowledge()

    assert runtime.context_builder.ignore_retros is True, (
        "After refresh_knowledge on a frozen runtime, the new ContextBuilder "
        "must still ignore retros. Without the fix this would be False."
    )

    pkg = runtime.context_builder.build(
        prompt="general task",
        task_signature="general/task",
        tool_families=[],
    )
    note_texts = " ".join(n.get("text", "") + n.get("title", "") for n in pkg.student_notes)
    assert SENTINEL not in note_texts, (
        "Retro sentinel leaked into context after refresh_knowledge on a frozen runtime."
    )


def test_refresh_knowledge_no_freeze_leaves_retros_loadable(tmp_path: Path) -> None:
    """CF-4: on a non-frozen runtime, refresh_knowledge must keep
    ignore_retros=False so retros continue to load normally."""
    from rocky.app import RockyRuntime

    runtime = RockyRuntime.load_from(cwd=tmp_path, freeze=False)
    assert runtime.context_builder.ignore_retros is False

    runtime.refresh_knowledge()
    assert runtime.context_builder.ignore_retros is False


# ---------------------------------------------------------------------------
# 6. End-to-end: workspace with sentinel retro + freeze=True => not in context
# ---------------------------------------------------------------------------

def test_freeze_runtime_context_excludes_retro_sentinel(tmp_path: Path) -> None:
    """
    Full integration path: place a retro with the sentinel in a workspace,
    build context via the runtime's context_builder with ignore_retros=True,
    assert sentinel absent.
    """
    from rocky.app import RockyRuntime

    runtime = RockyRuntime.load_from(cwd=tmp_path, freeze=True)

    retro_path = runtime.student_store.retrospectives_dir / "poisoned-retro.md"
    retro_path.parent.mkdir(parents=True, exist_ok=True)
    retro_path.write_text(
        "---\n"
        "id: retro_runtime_001\n"
        "kind: retrospective\n"
        "title: poisoned retro runtime\n"
        "created_at: '2026-01-01T00:00:00Z'\n"
        "updated_at: '2026-01-01T00:00:00Z'\n"
        "task_signature: general/task\n"
        "tags:\n"
        "  - general\n"
        "origin: self_reflection\n"
        "---\n"
        f"{SENTINEL}\n",
        encoding="utf-8",
    )

    pkg = runtime.context_builder.build(
        prompt="general task",
        task_signature="general/task",
        tool_families=[],
    )

    note_texts = " ".join(n.get("text", "") + n.get("title", "") for n in pkg.student_notes)
    assert SENTINEL not in note_texts, (
        f"Sentinel must not appear in context when runtime was created with freeze=True; "
        f"student_notes: {pkg.student_notes}"
    )
