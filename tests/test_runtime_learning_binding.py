from __future__ import annotations

import json
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
        return {"published": True, "policy_path": str(workspace / "policy.md")}

    runtime.learning_manager.learn_from_feedback = fake_learn_from_feedback  # type: ignore[method-assign]
    runtime.refresh_knowledge = lambda: None  # type: ignore[assignment]

    result = runtime.learn("next time keep the shell thread")

    assert result["published"] is True
    assert captured["task_signature"] == "repo/shell_execution"
    assert captured["task_family"] == "repo"
    assert captured["thread_id"] == "thread_42"
    assert captured["failure_class"] == "unsupported_claim_introduced"


def test_runtime_learn_recovers_latest_non_current_session_for_one_shot_feedback(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    runtime = RockyRuntime.load_from(workspace)

    current = runtime.sessions.ensure_current()
    current.created_at = "2026-04-08T01:00:00Z"
    runtime.sessions.save(current)

    recovered = runtime.sessions.create(title="oban 15", make_current=False)
    recovered.created_at = "2026-04-08T02:28:07Z"
    recovered.append("user", "oban 15")
    recovered.append("assistant", '[{"id":"1","product_name":"Oban Cask Strength 15 Years","confidence":"uncertain"}]')
    recovered.meta["last_task_signature"] = "repo/shell_execution"
    recovered.meta["last_verification"] = "pass"
    recovered.meta["last_thread_id"] = "thread_oban"
    recovered.meta["last_updated_at"] = "2026-04-08T02:28:20Z"
    runtime.sessions.save(recovered)

    trace_payload = {
        "route": {"task_signature": "repo/shell_execution"},
        "verification": {"status": "pass", "failure_class": None},
        "selected_tools": ["run_shell_command"],
        "thread": {
            "current_thread": {
                "thread_id": "thread_oban",
                "task_signature": "repo/shell_execution",
                "task_family": "repo",
                "prompt_history": [{"prompt": "oban 15"}],
            }
        },
    }
    trace_path = runtime.workspace.traces_dir / "trace_20260408T022820Z.json"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text(json.dumps(trace_payload), encoding="utf-8")

    fresh_runtime = RockyRuntime.load_from(workspace)

    captured: dict[str, object] = {}

    def fake_learn_from_feedback(**kwargs):
        captured.update(kwargs)
        return {"published": True, "policy_path": str(workspace / "policy.md")}

    fresh_runtime.learning_manager.learn_from_feedback = fake_learn_from_feedback  # type: ignore[method-assign]
    fresh_runtime.refresh_knowledge = lambda: None  # type: ignore[assignment]

    result = fresh_runtime.learn(
        "Return valid JSON only, and do not include cask-strength or other distinct expressions for a plain base product query."
    )

    assert result["published"] is True
    assert fresh_runtime.agent.last_prompt == "oban 15"
    assert "Oban Cask Strength 15 Years" in str(fresh_runtime.agent.last_answer)
    assert captured["prompt"] == "oban 15"
    assert captured["thread_id"] == "thread_oban"
    assert captured["task_signature"] == "repo/shell_execution"
