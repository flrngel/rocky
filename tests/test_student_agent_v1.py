from __future__ import annotations

import json
from pathlib import Path

from rocky.app import RockyRuntime
from rocky.providers.base import ProviderResponse
from rocky.core.router import ContinuationResolver
from rocky.core.runtime_state import ThreadRegistry
from rocky.session.store import Session
from rocky.student.store import StudentStore
from rocky.util.time import utc_iso


class _FakeReflectionProvider:
    def __init__(self, *payloads: object) -> None:
        self.payloads = list(payloads)

    def complete(self, system_prompt, messages, stream=False, event_handler=None):  # noqa: ANN001
        payload = self.payloads.pop(0)
        text = payload if isinstance(payload, str) else json.dumps(payload)
        return ProviderResponse(text=text)


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
    assert "## Root cause" in pattern_text
    assert "## Reflection flow" in pattern_text

    retrieved = runtime.student_store.retrieve(
        "save unsupported memory guesses",
        task_signature="repo/shell_execution",
    )
    assert retrieved
    assert retrieved[0]["kind"] == "pattern"
    assert retrieved[0]["failure_class"] == "project_memory_promotion_from_unsupported_inference"


def test_learn_model_reflection_creates_variant_pattern(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    runtime = RockyRuntime.load_from(tmp_path / "workspace")
    runtime.agent.last_prompt = "series alpha 15"
    runtime.agent.last_answer = (
        '[{"id":"1","product_name":"Series Alpha 15","confidence":"confirmed"},'
        '{"id":"2","product_name":"Series Alpha Reserve 15","confidence":"uncertain"}]'
    )
    runtime.agent.last_trace = {
        "route": {"task_signature": "repo/shell_execution"},
        "thread": {
            "current_thread": {
                "thread_id": "thread_series_alpha",
                "task_signature": "repo/shell_execution",
                "task_family": "repo",
            }
        },
        "verification": {},
        "selected_tools": ["run_shell_command"],
    }
    runtime.provider_registry.primary = lambda: _FakeReflectionProvider(  # type: ignore[method-assign]
        {
            "title": "Entity Variant Isolation",
            "summary": "When the observed results establish one clear item family, Rocky should keep that family and exclude different variants.",
            "failure_class": "entity_variant_misclassified",
            "root_cause": "The answer treated a distinct variant as a fallback candidate instead of isolating the established family.",
            "corrected_outcome": "Keep the established family and exclude different variants unless the query explicitly asks for them.",
            "generalization_rationale": "This is a reusable catalog-matching rule about variant isolation, not a one-off product detail.",
            "evidence": [
                "The prior answer mixed a base item with a distinct variant.",
                "The feedback says to exclude different variants unless the query names them.",
            ],
            "debug_steps": [
                "Compared the prior answer against the teacher feedback.",
                "Identified that the error came from mixing one item family with a distinct variant.",
                "Generalized the correction into a reusable variant-isolation rule.",
            ],
            "memory_kind": "pattern",
            "should_publish_skill": True,
            "confidence": 0.92,
            "required_behavior": [
                "Identify the established item family from the observed results.",
                "Keep only that family in the final answer unless the query explicitly names a different variant.",
            ],
            "prohibited_behavior": [
                "Do not include distinct variants as fallback candidates once the item family is established.",
            ],
            "evidence_requirements": [
                "Use the observed results from the current run to determine the established family.",
            ],
            "triggers": ["repo/shell_execution", "catalog matching", "variant isolation"],
            "keywords": ["variant", "family", "catalog"],
        }
    )

    result = runtime.learn(
        "Return valid JSON only. For duplicate matching, exclude different variants unless the query explicitly asks for that specific variant."
    )

    assert result["analysis"]["failure_class"] == "entity_variant_misclassified"
    assert result["analysis"]["reflection_source"] == "model_reflection"
    pattern_path = Path(result["student_pattern"]["path"])
    pattern_text = pattern_path.read_text(encoding="utf-8")
    assert "variant-isolation rule" in pattern_text
    assert "exclude different variants" in pattern_text
    assert "## Evidence observed" in pattern_text


def test_learn_model_reflection_captures_clear_family_rule(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    runtime = RockyRuntime.load_from(tmp_path / "workspace")
    runtime.agent.last_prompt = "series alpha 15"
    runtime.agent.last_answer = "[]"
    runtime.agent.last_trace = {
        "route": {"task_signature": "repo/shell_execution"},
        "thread": {
            "current_thread": {
                "thread_id": "thread_series_alpha_empty",
                "task_signature": "repo/shell_execution",
                "task_family": "repo",
            }
        },
        "verification": {},
        "selected_tools": ["run_shell_command"],
    }
    runtime.provider_registry.primary = lambda: _FakeReflectionProvider(  # type: ignore[method-assign]
        {
            "title": "Established Family Should Not Collapse To Empty",
            "summary": "If the observed results already show a clear matching family, Rocky should not collapse to an empty answer.",
            "failure_class": "over_pruned_established_family",
            "root_cause": "The answer over-pruned and discarded the established matching family instead of keeping it.",
            "corrected_outcome": "Keep the established family in the final answer and exclude unrelated variants.",
            "generalization_rationale": "This is a reusable lesson about over-pruning when the evidence already establishes a matching family.",
            "evidence": [
                "The prior answer was empty.",
                "The feedback says there were confirmed matches for the relevant family.",
            ],
            "debug_steps": [
                "Noted that the prior answer collapsed to an empty list.",
                "Used the teacher feedback to identify over-pruning as the failure.",
                "Generalized a reusable rule about established families and over-pruning.",
            ],
            "memory_kind": "pattern",
            "should_publish_skill": True,
            "confidence": 0.9,
            "required_behavior": [
                "Keep the established matching family when the evidence already supports it.",
            ],
            "prohibited_behavior": [
                "Do not collapse to an empty answer after identifying an established matching family.",
            ],
            "evidence_requirements": [
                "Check whether the observed results already establish a matching family before pruning.",
            ],
            "triggers": ["repo/shell_execution", "over-pruning", "matching family"],
            "keywords": ["empty", "pruning", "family"],
        }
    )

    result = runtime.learn(
        "Your last catalog answer over-pruned and returned an empty array even though the evidence contained confirmed matches for the relevant family. Do not collapse to [] when the matching family is already established."
    )

    analysis = result["analysis"]
    assert analysis["failure_class"] == "over_pruned_established_family"
    assert analysis["reflection_source"] == "model_reflection"
    assert any("established matching family" in item for item in analysis["required_behavior"])
    assert any("empty answer" in item for item in analysis["prohibited_behavior"])


def test_learn_model_reflection_can_keep_case_specific_feedback_as_example(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    runtime = RockyRuntime.load_from(tmp_path / "workspace")
    runtime.agent.last_prompt = "what file did the script create"
    runtime.agent.last_answer = "It created report.md."
    runtime.agent.last_trace = {
        "route": {"task_signature": "repo/shell_execution"},
        "thread": {
            "current_thread": {
                "thread_id": "thread_file_example",
                "task_signature": "repo/shell_execution",
                "task_family": "repo",
            }
        },
        "verification": {},
        "selected_tools": ["run_shell_command", "read_file"],
        "tool_events": [
            {
                "type": "tool_result",
                "name": "run_shell_command",
                "success": True,
                "text": json.dumps(
                    {
                        "summary": "Command exited with 0",
                        "data": {
                            "command": "./build.sh",
                            "stdout": "Created summary-2026-04-08.md\n",
                        },
                    }
                ),
            }
        ],
    }
    runtime.provider_registry.primary = lambda: _FakeReflectionProvider(  # type: ignore[method-assign]
        {
            "title": "repo shell_execution: exact output example",
            "summary": "The feedback is best stored as a worked example tied to one exact observed filename.",
            "failure_class": "workflow_correction",
            "root_cause": "Rocky answered with an invented filename instead of the filename shown in the observed command output.",
            "corrected_outcome": "Answer with `summary-2026-04-08.md` because that is the filename shown in the successful tool result.",
            "generalization_rationale": "This is useful as a concrete example of evidence-grounded answering, but it is too case-specific to become a reusable skill.",
            "evidence": [
                "The successful run_shell_command output says `Created summary-2026-04-08.md`.",
                "The prior answer said `report.md`, which does not appear in the observed output.",
            ],
            "debug_steps": [
                "Recovered the prior prompt, answer, and tool evidence.",
                "Compared the filename in the answer against the filename in the observed command output.",
                "Kept this as an example because the correction is tied to one exact output string.",
            ],
            "memory_kind": "example",
            "should_publish_skill": False,
            "confidence": 0.88,
            "required_behavior": [
                "Use the exact observed filename from the current run when answering this kind of question.",
            ],
            "prohibited_behavior": [
                "Do not invent a different filename than the one shown in the observed output.",
            ],
            "evidence_requirements": [
                "Check the successful command output from this run before naming created files.",
            ],
            "triggers": ["repo/shell_execution", "exact filename", "observed output"],
            "keywords": ["filename", "exact", "evidence"],
        }
    )

    result = runtime.learn("Your last answer invented the filename. Learn from this exact mistake.")

    assert result["published"] is False
    assert result["analysis"]["reflection_source"] == "model_reflection"
    assert result["analysis"]["memory_kind"] == "example"
    assert result["student_memory"]["kind"] == "example"
    assert result["student_pattern"] is None
    assert Path(result["reflection_path"]).exists()
    example_path = Path(result["student_memory"]["path"])
    example_text = example_path.read_text(encoding="utf-8")
    assert "## Reflection flow" in example_text
    assert "too case-specific to become a reusable skill" in example_text
