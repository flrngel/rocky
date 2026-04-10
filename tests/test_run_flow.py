from __future__ import annotations

from pathlib import Path

from rocky.core.run_flow import RunFlowManager
from rocky.core.runtime_state import EvidenceGraph
from rocky.core.verifiers import VerificationResult


def test_run_flow_manager_writes_run_local_flow_and_task_files(tmp_path: Path) -> None:
    manager = RunFlowManager(
        tmp_path / ".rocky" / "runs",
        prompt=(
            "find huggingface openweight llm models that are trending right now. "
            "filter models that have parameters under 12B. "
            "you should find at least 10 models and show me as a list."
        ),
        task_signature="research/live_compare/general",
        task_class="research",
        execution_cwd=".",
        minimum_list_items=10,
    )

    assert manager.flow_path.exists()
    assert (manager.tasks_dir / "T1.md").exists()
    assert (manager.tasks_dir / "T2.md").exists()
    assert (manager.tasks_dir / "T3.md").exists()

    flow_text = manager.flow_path.read_text(encoding="utf-8")
    task_text = (manager.tasks_dir / "T2.md").read_text(encoding="utf-8")

    assert "Task Tree" in flow_text
    assert "ROOT -> T1" in flow_text
    assert "Gather candidate evidence" in task_text
    assert "Inspect listing or detail pages and collect grounded candidate items." in task_text


def test_run_flow_manager_rolls_discovery_into_next_task(tmp_path: Path) -> None:
    manager = RunFlowManager(
        tmp_path / ".rocky" / "runs",
        prompt="find current github repos right now",
        task_signature="research/live_compare/general",
        task_class="research",
        execution_cwd=".",
    )
    evidence_graph = EvidenceGraph(thread_id="thread_test")
    fetch_event = {
        "type": "tool_result",
        "name": "fetch_url",
        "success": True,
        "summary_text": "Fetched https://github.com/trending",
        "text": "Fetched https://github.com/trending",
        "artifacts": [{"kind": "url", "ref": "https://github.com/trending", "source": "fetch_url"}],
        "facts": [
            {
                "kind": "link_item",
                "text": "Link item: microsoft/TypeScript (https://github.com/microsoft/TypeScript)",
            }
        ],
    }

    manager.ingest_tool_event(fetch_event)
    advanced = manager.advance(evidence_graph=evidence_graph, tool_events=[fetch_event], final_output_ready=False)

    assert advanced is True
    assert manager.run.active_task_id == "T2"
    next_task = manager.run.active_task()
    assert next_task.task_id == "T2"
    assert any("T1:" in item for item in next_task.imports)
    assert any("microsoft/TypeScript" in item for item in next_task.imports)


def test_run_flow_returns_from_finalize_to_gather_on_missing_research_evidence(tmp_path: Path) -> None:
    manager = RunFlowManager(
        tmp_path / ".rocky" / "runs",
        prompt="find at least 10 current open source projects and show me as a list",
        task_signature="research/live_compare/general",
        task_class="research",
        execution_cwd=".",
        minimum_list_items=10,
    )
    manager.run.active_task_id = "T3"
    for task in manager.run.tasks:
        task.status = "done" if task.task_id in {"T1", "T2"} else "doing"

    manager.note_verification_failure(
        VerificationResult(
            "list_requirements_v1",
            "fail",
            "Rocky presented a complete counted list even though live item evidence is still missing.",
            failure_class="counted_list_missing_live_evidence",
        )
    )

    assert manager.run.active_task_id == "T2"
    assert manager.run.active_task().status == "doing"
    assert any("verification gap" in item for item in manager.run.active_task().imports)


def test_run_flow_names_best_search_lead_as_next_move(tmp_path: Path) -> None:
    manager = RunFlowManager(
        tmp_path / ".rocky" / "runs",
        prompt="find trending models on Hugging Face",
        task_signature="research/live_compare/general",
        task_class="research",
        execution_cwd=".",
    )
    manager.ingest_tool_event(
        {
            "type": "tool_result",
            "name": "search_web",
            "success": True,
            "summary_text": "Search returned 2 result(s)",
            "facts": [
                {
                    "kind": "result",
                    "text": "Lead: Blog post (https://example.com/blog/models)",
                    "title": "Blog post",
                    "url": "https://example.com/blog/models",
                },
                {
                    "kind": "result",
                    "text": "Lead: Models - Hugging Face (https://huggingface.co/models?sort=trending)",
                    "title": "Models - Hugging Face",
                    "url": "https://huggingface.co/models?sort=trending",
                },
            ],
            "artifacts": [],
        }
    )

    assert manager.run.active_task().next_move == "Open the strongest search lead with fetch_url: https://huggingface.co/models?sort=trending"


def test_run_flow_refines_observed_range_filter_lead_from_prompt(tmp_path: Path) -> None:
    manager = RunFlowManager(
        tmp_path / ".rocky" / "runs",
        prompt="find text models under 12B on Hugging Face",
        task_signature="research/live_compare/general",
        task_class="research",
        execution_cwd=".",
        minimum_list_items=10,
    )
    manager.ingest_tool_event(
        {
            "type": "tool_result",
            "name": "search_web",
            "success": True,
            "summary_text": "Search returned 1 result(s)",
            "facts": [
                {
                    "kind": "result",
                    "text": "Lead: Text Generation Models – Hugging Face (https://huggingface.co/models?pipeline_tag=text-generation&num_parameters=min:12B,max:24B&sort=trending)",
                    "title": "Text Generation Models – Hugging Face",
                    "url": "https://huggingface.co/models?pipeline_tag=text-generation&num_parameters=min:12B,max:24B&sort=trending",
                },
            ],
            "artifacts": [],
        }
    )

    assert manager.run.active_task().next_move == (
        "Open the strongest search lead with fetch_url: "
        "https://huggingface.co/models?pipeline_tag=text-generation&num_parameters=min:0,max:12B&sort=trending"
    )


def test_run_flow_derives_numeric_search_slice_from_observed_listing_url(tmp_path: Path) -> None:
    manager = RunFlowManager(
        tmp_path / ".rocky" / "runs",
        prompt="find text models under 12B parameters that are trending right now",
        task_signature="research/live_compare/general",
        task_class="research",
        execution_cwd=".",
        minimum_list_items=10,
    )
    manager.ingest_tool_event(
        {
            "type": "tool_result",
            "name": "fetch_url",
            "success": True,
            "summary_text": "Fetched https://example.test/models?sort=trending&task=text-generation",
            "facts": [
                {
                    "kind": "link_item",
                    "text": "Link item: org/big-model Text Generation • 70B (https://example.test/org/big-model)",
                }
            ],
            "artifacts": [
                {
                    "kind": "url",
                    "ref": "https://example.test/models?sort=trending&task=text-generation",
                    "source": "fetch_url",
                }
            ],
            "text": "Fetched listing",
        }
    )

    assert manager.suggested_fetch_url() == "https://example.test/models?sort=trending&task=text-generation&search=8B"
