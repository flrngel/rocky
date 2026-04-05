from __future__ import annotations

from pathlib import Path

from rocky.app import RockyRuntime


def test_runtime_learn_binds_to_thread_snapshot_over_last_route(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    runtime = RockyRuntime.load_from(workspace)

    runtime.agent.last_prompt = "continue the shell task"
    runtime.agent.last_answer = "done"
    runtime.agent.last_trace = {
        "route": {"task_signature": "conversation/general"},
        "verification": {"failure_class": "unsupported_claim_introduced"},
        "thread": {
            "current_thread": {
                "thread_id": "thread_42",
                "task_signature": "repo/shell_execution",
                "task_family": "repo",
            }
        },
    }

    captured: dict[str, object] = {}

    def fake_learn_from_feedback(**kwargs):
        captured.update(kwargs)
        return {"published": True, "skill_path": str(workspace / "skill.md")}

    runtime.learning_manager.learn_from_feedback = fake_learn_from_feedback  # type: ignore[method-assign]
    runtime.refresh_knowledge = lambda: None  # type: ignore[assignment]

    result = runtime.learn("next time keep the shell thread")

    assert result["published"] is True
    assert captured["task_signature"] == "repo/shell_execution"
    assert captured["task_family"] == "repo"
    assert captured["thread_id"] == "thread_42"
    assert captured["failure_class"] == "unsupported_claim_introduced"
