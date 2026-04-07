from __future__ import annotations

import json
from pathlib import Path

from rocky.app import RockyRuntime
from rocky.core.router import ContinuationResolver
from rocky.core.runtime_state import ThreadRegistry
from rocky.session.store import Session
from rocky.student.store import StudentStore
from rocky.util.time import utc_iso


def test_student_store_can_add_and_retrieve_pattern(tmp_path: Path) -> None:
    store = StudentStore(tmp_path / "student")
    store.add(
        "pattern",
        "catalog-merge-rule",
        "When the artist or edition text differs, do not merge the products.",
        task_signature="repo/shell_execution",
    )

    rows = store.retrieve("merge the catalog products", task_signature="repo/shell_execution")

    assert rows
    assert rows[0]["kind"] == "pattern"
    assert "do not merge" in rows[0]["text"].lower()



def test_thread_registry_keeps_passed_thread_continuable() -> None:
    session = Session(id="ses_test", created_at=utc_iso(), title="session")
    registry = ThreadRegistry(session)
    thread = registry.start_thread(
        workspace_root="/workspace",
        execution_cwd="src",
        task_signature="repo/shell_execution",
        task_family="repo",
    )
    thread.add_verification({"status": "pass", "message": "ok"})
    registry.save()

    assert thread.status == "awaiting_user"
    assert registry.current() is not None
    assert registry.current().thread_id == thread.thread_id

    continuation = ContinuationResolver().resolve(
        "continue and finish it",
        active_threads=registry.active_threads(),
        recent_threads=registry.recent_threads(),
        workspace_root="/workspace",
        execution_cwd="src",
    )

    assert continuation.thread_id == thread.thread_id
    assert continuation.action in {"continue_active_thread", "resume_recent_thread"}



def test_teach_records_single_notebook_entry_when_last_answer_exists(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    runtime = RockyRuntime.load_from(tmp_path / "workspace")
    runtime.agent.last_prompt = "review the catalog"
    runtime.agent.last_answer = "I merged two items"
    runtime.agent.last_trace = {
        "route": {"task_signature": "repo/shell_execution"},
        "thread": {
            "current_thread": {
                "thread_id": "thread_123",
                "task_signature": "repo/shell_execution",
                "task_family": "repo",
            }
        },
        "verification": {"failure_class": "wrong_merge"},
    }
    monkeypatch.setattr(
        runtime.learning_manager,
        "learn_from_feedback",
        lambda **kwargs: {"published": True, "skill": "dummy"},
    )

    result = runtime.teach("Do not merge products when edition text differs.")

    notebook_lines = [
        line
        for line in runtime.student_store.notebook_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert result["teachable"] is True
    assert len(notebook_lines) == 1
    payload = json.loads(notebook_lines[0])
    assert payload["feedback"] == "Do not merge products when edition text differs."


def test_learn_creates_structured_pattern_memory_from_feedback(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    runtime = RockyRuntime.load_from(tmp_path / "workspace")
    runtime.agent.last_prompt = "review the catalog and save inferred merge defaults to memory"
    runtime.agent.last_answer = "I stored the likely defaults in memory without checking the evidence."
    runtime.agent.last_trace = {
        "route": {"task_signature": "repo/shell_execution"},
        "thread": {
            "current_thread": {
                "thread_id": "thread_456",
                "task_signature": "repo/shell_execution",
                "task_family": "repo",
            }
        },
        "verification": {},
        "selected_tools": ["run_shell_command", "read_file"],
    }

    captured: dict[str, object] = {}

    def fake_learn_from_feedback(**kwargs):
        captured.update(kwargs)
        return {"published": True, "skill": "dummy"}

    monkeypatch.setattr(runtime.learning_manager, "learn_from_feedback", fake_learn_from_feedback)

    result = runtime.learn("Do not save unsupported guesses to memory. Inspect the failure first and verify the evidence.")

    assert result["published"] is True
    assert result["analysis"]["failure_class"] == "project_memory_promotion_from_unsupported_inference"
    assert captured["failure_class"] == "project_memory_promotion_from_unsupported_inference"
    pattern_entry = result["student_pattern"]
    assert pattern_entry["kind"] == "pattern"
    pattern_path = Path(pattern_entry["path"])
    assert pattern_path.exists()
    pattern_text = pattern_path.read_text(encoding="utf-8")
    assert "## Do this" in pattern_text
    assert "## Evidence to gather" in pattern_text

    retrieved = runtime.student_store.retrieve(
        "save unsupported memory guesses",
        task_signature="repo/shell_execution",
    )
    assert retrieved
    assert retrieved[0]["kind"] == "pattern"
    assert retrieved[0]["failure_class"] == "project_memory_promotion_from_unsupported_inference"
