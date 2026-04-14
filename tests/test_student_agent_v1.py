from __future__ import annotations

import json
from pathlib import Path

from rocky.app import RockyRuntime
from rocky.providers.base import ProviderResponse
from rocky.core.router import ContinuationResolver
from rocky.learning.synthesis import PolicySynthesizer
from rocky.core.runtime_state import ThreadRegistry
from rocky.session.store import Session
from rocky.student.store import StudentStore
from rocky.util.time import utc_iso


class _FakeReflectionProvider:
    def __init__(self, *payloads: object) -> None:
        self.payloads = list(payloads)
        self.calls: list[dict[str, object]] = []

    def complete(self, system_prompt, messages, stream=False, event_handler=None):  # noqa: ANN001
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "messages": messages,
                "stream": stream,
            }
        )
        payload = self.payloads.pop(0)
        text = payload if isinstance(payload, str) else json.dumps(payload)
        return ProviderResponse(text=text)


class _SimpleTaskProvider:
    def __init__(self, text: str = "ok") -> None:
        self.text = text
        self.calls: list[dict[str, object]] = []

    def complete(self, system_prompt, messages, stream=False, event_handler=None):  # noqa: ANN001
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "messages": messages,
                "mode": "complete",
            }
        )
        return ProviderResponse(text=self.text)

    def run_with_tools(self, system_prompt, messages, tools, execute_tool, max_rounds=8, event_handler=None):  # noqa: ANN001
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "messages": messages,
                "tools": tools,
                "mode": "run_with_tools",
            }
        )
        return ProviderResponse(text=self.text, raw={"rounds": []}, tool_events=[])


class _CompositeProviderRegistry:
    def __init__(self, task_provider, reflection_provider) -> None:  # noqa: ANN001
        self.task_provider = task_provider
        self.reflection_provider = reflection_provider

    def provider_for_task(self, needs_tools=False):  # noqa: ANN001
        return self.task_provider

    def primary(self):
        return self.reflection_provider


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


def test_student_store_can_add_and_retrieve_retrospective(tmp_path: Path) -> None:
    store = StudentStore(tmp_path / "student")
    store.add(
        "retrospective",
        "Shell episodes need a reread step",
        "# Self retrospective\n\n## Learned\n\nReread command output before concluding.\n",
        task_signature="repo/shell_execution",
        tags=["shell", "reread", "output"],
        origin="self_reflection",
    )

    rows = store.retrieve("reread the shell output before concluding", task_signature="repo/shell_execution")

    assert rows
    assert rows[0]["kind"] == "retrospective"
    assert "reread command output" in rows[0]["text"].lower()


def test_runtime_auto_self_reflection_persists_compact_retrospective_and_recalls_it_next_session(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    first_runtime = RockyRuntime.load_from(workspace)
    first_task_provider = _SimpleTaskProvider("Hello there.")
    first_reflection_provider = _FakeReflectionProvider(
        {
            "title": "Keep greeting turns compact",
            "summary": "For simple greeting-style turns, answer briefly instead of adding extra explanation.",
            "should_persist": True,
            "confidence": 0.91,
            "repeat_next_time": ["Answer in one short sentence for lightweight greeting turns."],
            "avoid_next_time": ["Do not add workflow narration when the user only wants a greeting."],
            "recall_when": ["greeting-like prompts", "conversation/general turns"],
            "keywords": ["greeting", "concise", "conversation"],
            "evidence": ["The finished turn was a lightweight greeting request and a direct short answer fit the task."],
        }
    )
    first_registry = _CompositeProviderRegistry(first_task_provider, first_reflection_provider)
    first_runtime.provider_registry = first_registry
    first_runtime.agent.provider_registry = first_registry

    first_response = first_runtime.run_prompt("say hello politely", continue_session=False)

    assert first_response.text == "Hello there."
    assert len(first_reflection_provider.calls) == 1
    assert first_response.trace["self_learning"]["persisted"] is True

    stored_notes = [note for note in first_runtime.student_store.load_all() if note.kind == "retrospective"]
    assert len(stored_notes) == 1
    stored_note = stored_notes[0]
    assert stored_note.prompt == ""
    assert stored_note.answer == ""
    assert stored_note.feedback == ""
    assert "greeting-style turns" in stored_note.text

    second_runtime = RockyRuntime.load_from(workspace)
    second_task_provider = _SimpleTaskProvider("Hi again.")
    second_reflection_provider = _FakeReflectionProvider(
        {
            "title": "No new durable lesson",
            "summary": "The prior retrospective already covers this lightweight greeting pattern.",
            "should_persist": False,
            "confidence": 0.8,
            "repeat_next_time": ["Keep direct greeting turns short."],
            "avoid_next_time": ["Do not restate the whole context."],
            "recall_when": ["greeting-like prompts"],
            "keywords": ["greeting", "concise"],
            "evidence": ["The earlier retrospective already matches this turn."],
        }
    )
    second_registry = _CompositeProviderRegistry(second_task_provider, second_reflection_provider)
    second_runtime.provider_registry = second_registry
    second_runtime.agent.provider_registry = second_registry

    second_response = second_runtime.run_prompt("say hello again", continue_session=False)

    assert second_response.text == "Hi again."
    assert second_response.trace["context"]["student_notes"]
    assert second_response.trace["context"]["student_notes"][0]["kind"] == "retrospective"
    assert "Keep greeting turns compact" in str(second_task_provider.calls[0]["system_prompt"])
    assert "For simple greeting-style turns, answer briefly instead of adding extra explanation." in str(
        second_task_provider.calls[0]["system_prompt"]
    )
    # Phase 2.3 packer: soft-conventions framing moved from "Self retrospectives are..."
    # to "Style guidance extracted from prior self-retrospectives...". Invariant preserved.
    assert "Style guidance extracted from prior self-retrospectives" in str(second_task_provider.calls[0]["system_prompt"])



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
        lambda **kwargs: {"published": True, "policy": "dummy"},
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
        return {"published": True, "policy": "dummy"}

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


def test_learn_does_not_publish_skill_when_feedback_is_already_satisfied(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    runtime = RockyRuntime.load_from(tmp_path / "workspace")
    runtime.agent.last_prompt = "brindle 10"
    runtime.agent.last_answer = (
        '[{"id":"B10-001","product_name":"Brindle 10 Orchard Edition","lineage_code":"A1"},'
        '{"id":"B10-002","product_name":"Brindle 10 Orchard Edition Gift Tin","lineage_code":"A1"}]'
    )
    runtime.agent.last_trace = {
        "route": {"task_signature": "repo/shell_execution"},
        "thread": {
            "current_thread": {
                "thread_id": "thread_lineage_ok",
                "task_signature": "repo/shell_execution",
                "task_family": "repo",
            }
        },
        "verification": {},
        "selected_tools": ["run_shell_command"],
        "tool_events": [
            {
                "type": "tool_result",
                "name": "run_shell_command",
                "success": True,
                "text": json.dumps(
                    {
                        "summary": "Command exited with 0",
                        "data": {
                            "command": 'sh catalog.sh "brindle 10"',
                            "stdout": json.dumps(
                                {
                                    "query": "brindle 10",
                                    "products": [{"product_id": "P-BRINDLE-10", "lineage_code": "A1"}],
                                    "candidates": [
                                        {"id": "B10-001", "lineage_code": "A1"},
                                        {"id": "B10-002", "lineage_code": "A1"},
                                        {"id": "B10-003", "lineage_code": "N4"},
                                    ],
                                }
                            ),
                        },
                    }
                ),
            }
        ],
    }
    runtime.provider_registry.primary = lambda: _FakeReflectionProvider(  # type: ignore[method-assign]
        {
            "title": "No new failure observed",
            "summary": "The prior answer already respected lineage_code as a hard boundary, so there is no new corrective failure to publish.",
            "failure_class": "filtering_logic_error",
            "observed_failure": False,
            "root_cause": "The feedback restates a rule that the prior answer already followed.",
            "corrected_outcome": "No answer change is required.",
            "generalization_rationale": "Keep this as a notebook lesson only.",
            "evidence": [
                "The prior answer only contains candidates with lineage_code A1.",
                "The product lineage_code in the observed tool output is also A1.",
            ],
            "debug_steps": [
                "Compared the teacher feedback against the prior answer.",
                "Checked the product lineage_code in the tool output.",
                "Found that the prior answer already followed the requested rule.",
            ],
            "memory_kind": "lesson",
            "should_publish_policy": False,
            "confidence": 0.94,
            "required_behavior": [
                "Keep this as a notebook reminder only.",
            ],
            "prohibited_behavior": [
                "Do not publish a reusable corrective skill when there is no mismatch.",
            ],
            "evidence_requirements": [
                "Verify that the prior answer actually violated the feedback before publishing a skill.",
            ],
            "triggers": ["repo/shell_execution", "lineage_code"],
            "keywords": ["lineage_code", "already satisfied"],
        }
    )

    result = runtime.learn(
        "When duplicate-review candidates share the same core name and age but have different lineage_code values, use the product lineage_code from the observed output as a hard boundary. Keep only candidates whose lineage_code matches the product lineage_code."
    )

    assert result["published"] is False
    assert result["analysis"]["observed_failure"] is False
    assert result["analysis"]["memory_kind"] == "lesson"
    assert result["student_memory"] is None
    assert result["student_pattern"] is None


def test_learn_heuristic_does_not_publish_when_answer_already_matches_output_schema(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    runtime = RockyRuntime.load_from(tmp_path / "workspace")
    runtime.agent.last_prompt = "marlow 14"
    runtime.agent.last_answer = (
        '[{"id":"M14-001","product_name":"Marlow 14 Riverside Batch"},'
        '{"id":"M14-002","product_name":"Marlow 14 Riverside Batch Gift Set"}]'
    )
    runtime.agent.last_trace = {
        "route": {"task_signature": "repo/shell_execution"},
        "thread": {
            "current_thread": {
                "thread_id": "thread_schema_ok",
                "task_signature": "repo/shell_execution",
                "task_family": "repo",
            }
        },
        "verification": {},
        "selected_tools": ["run_shell_command"],
    }
    runtime.provider_registry.primary = lambda: None  # type: ignore[method-assign]

    result = runtime.learn(
        "For duplicate-review answers in this workspace, the final JSON should contain only `id` and `product_name`. Treat helper fields like `same_core_name`, `same_age_statement`, `lineage_code`, and `duplicate_signal` as internal evidence, not deliverable output."
    )

    assert result["published"] is False
    assert result["analysis"]["observed_failure"] is False
    assert result["analysis"]["memory_kind"] == "lesson"
    assert result["student_memory"] is None
    assert result["student_pattern"] is None


def test_learn_locks_in_evidence_backed_schema_failure_when_reflection_denies_it(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    runtime = RockyRuntime.load_from(tmp_path / "workspace")
    runtime.agent.last_prompt = "brindle 10"
    runtime.agent.last_answer = (
        '[{"id":"B10-001","product_name":"Brindle 10 Orchard Edition","same_core_name":true,"lineage_code":"A1"}]'
    )
    runtime.agent.last_trace = {
        "route": {"task_signature": "repo/shell_execution"},
        "thread": {
            "current_thread": {
                "thread_id": "thread_schema_mismatch",
                "task_signature": "repo/shell_execution",
                "task_family": "repo",
            }
        },
        "verification": {},
        "selected_tools": ["run_shell_command"],
    }
    runtime.provider_registry.primary = lambda: _FakeReflectionProvider(  # type: ignore[method-assign]
        {
            "title": "Restrict JSON output fields for duplicate-review tasks",
            "summary": "Only return id and product_name for duplicate-review output.",
            "failure_class": "output_format_violation",
            "observed_failure": False,
            "root_cause": "The answer already satisfies the requested schema.",
            "corrected_outcome": "No change required.",
            "generalization_rationale": "Keep it as a lesson only.",
            "evidence": ["The answer already matches the expected schema."],
            "debug_steps": ["Reviewed the previous answer against the feedback."],
            "memory_kind": "lesson",
            "should_publish_policy": False,
            "confidence": 0.95,
            "required_behavior": ["Keep only notebook memory."],
            "prohibited_behavior": ["Do not publish a skill."],
            "evidence_requirements": ["None."],
            "triggers": ["repo/shell_execution"],
            "keywords": ["schema"],
        }
    )

    result = runtime.learn(
        "For duplicate-review answers in this workspace, the final JSON should contain only `id` and `product_name`."
    )

    assert result["published"] is True
    assert result["analysis"]["observed_failure"] is True
    assert result["analysis"]["reflection_source"] == "heuristic_locked"
    assert result["analysis"]["memory_kind"] == "pattern"
    assert result["student_pattern"] is not None


def test_build_draft_adds_research_signature_for_web_search_tool_refusal(tmp_path: Path) -> None:
    synthesizer = PolicySynthesizer()
    analysis = synthesizer.analyze_feedback(
        "conversation/general",
        "you must use web search and you do have search tools",
        "what is current github trending repos",
        "I do not have web search tools available.",
        trace={
            "verification": {"failure_class": "tool_use_refusal"},
            "selected_tools": [],
        },
        task_family="conversation",
        thread_id="thread_research_learn",
        failure_class="tool_use_refusal",
        provider=None,
    )

    draft = synthesizer.build_draft(
        tmp_path / "policies" / "learned",
        "conversation/general",
        1,
        "you must use web search and you do have search tools",
        "sup_research",
        "what is current github trending repos",
        "I do not have web search tools available.",
        trace={
            "verification": {"failure_class": "tool_use_refusal"},
            "selected_tools": [],
        },
        task_family="conversation",
        thread_id="thread_research_learn",
        failure_class="tool_use_refusal",
        analysis=analysis,
    )

    assert "conversation/general" in draft.metadata["task_signatures"]
    assert "research/live_compare/general" in draft.metadata["task_signatures"]


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
            "should_publish_policy": True,
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
            "should_publish_policy": True,
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
            "generalization_rationale": "This is useful as a concrete example of evidence-grounded answering, but it is too case-specific to become a reusable policy.",
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
            "should_publish_policy": False,
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
    assert "too case-specific to become a reusable policy" in example_text
