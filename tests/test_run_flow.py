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


def _make_fetch_event(url: str, link_items: list[str] | None = None) -> dict:
    """Build a realistic fetch_url tool_result event with optional link_item facts."""
    facts = []
    for item in (link_items or []):
        facts.append({"kind": "link_item", "text": f"Link item: {item} (https://example.test/{item})"})
    return {
        "type": "tool_result",
        "name": "fetch_url",
        "success": True,
        "summary_text": f"Fetched {url}",
        "text": f"Fetched {url}",
        "artifacts": [{"kind": "url", "ref": url, "source": "fetch_url"}],
        "facts": facts,
    }


def test_run_flow_full_t1_t2_t3_burst_sequence(tmp_path: Path) -> None:
    """Exercise the complete multi-burst loop: T1 (discover) → T2 (gather) → T3 (finalize).

    Each step transition is individually asserted to prove divide-and-conquer
    tracking works end-to-end without any LLM call.
    """
    manager = RunFlowManager(
        tmp_path / ".rocky" / "runs",
        prompt="find trending repos on GitHub right now",
        task_signature="research/live_compare/general",
        task_class="research",
        execution_cwd=".",
    )
    evidence_graph = EvidenceGraph(thread_id="thread_burst_test")

    # --- Step 1: Verify initial state ---
    assert manager.run.active_task_id == "T1"
    t1 = manager.run.active_task()
    assert t1.task_id == "T1"
    assert t1.kind == "discover"
    assert t1.next_move  # non-empty

    # --- Step 2: Simulate T1 burst — fetch a live page ---
    fetch_event_1 = _make_fetch_event(
        "https://github.com/trending",
        link_items=["microsoft/TypeScript", "rust-lang/rust"],
    )
    manager.ingest_tool_event(fetch_event_1)
    advanced_t1 = manager.advance(
        evidence_graph=evidence_graph,
        tool_events=[fetch_event_1],
        final_output_ready=False,
    )

    # T1 (discover) should advance: we fetched a live page with url artifact
    assert advanced_t1 is True, "T1 discover should advance after fetching a live page"
    assert manager.run.active_task_id == "T2"
    t2 = manager.run.active_task()
    assert t2.task_id == "T2"
    assert t2.kind == "gather"
    assert t2.status == "doing"
    # Context carry-forward: T2 imports should contain T1 rollup
    assert any("T1" in item for item in t2.imports), "T2 should have imported context from T1"

    # --- Step 3: Simulate T2 burst — fetch another page (gather evidence) ---
    fetch_event_2 = _make_fetch_event(
        "https://github.com/trending?since=weekly",
        link_items=["facebook/react", "torvalds/linux", "golang/go", "python/cpython"],
    )
    manager.ingest_tool_event(fetch_event_2)
    advanced_t2 = manager.advance(
        evidence_graph=evidence_graph,
        tool_events=[fetch_event_2],
        final_output_ready=False,
    )

    # T2 (gather) with no minimum_list_items: should advance since live_pages >= 1
    assert advanced_t2 is True, "T2 gather should advance after fetching a live page"
    assert manager.run.active_task_id == "T3"
    t3 = manager.run.active_task()
    assert t3.task_id == "T3"
    assert t3.kind == "finalize"
    assert t3.status == "doing"
    # Context carry-forward: T3 imports should contain T2 rollup
    assert any("T2" in item for item in t3.imports), "T3 should have imported context from T2"

    # --- Step 4: Simulate T3 finalization ---
    advanced_t3 = manager.advance(
        evidence_graph=evidence_graph,
        tool_events=[],
        final_output_ready=True,
    )

    # T3 (finalize) should advance with final_output_ready=True
    assert advanced_t3 is True, "T3 finalize should advance when final_output_ready=True"
    assert manager.run.status == "done"
    # All tasks should be done
    for task in manager.run.tasks:
        assert task.status == "done", f"Task {task.task_id} should be done, got {task.status}"


def test_run_flow_t3_does_not_advance_without_final_output(tmp_path: Path) -> None:
    """T3 (finalize) must NOT advance unless final_output_ready=True."""
    manager = RunFlowManager(
        tmp_path / ".rocky" / "runs",
        prompt="find trending repos right now",
        task_signature="research/live_compare/general",
        task_class="research",
        execution_cwd=".",
    )
    evidence_graph = EvidenceGraph(thread_id="thread_no_final")

    # Advance through T1 and T2
    fetch_1 = _make_fetch_event("https://example.test/page1", link_items=["item/a"])
    manager.ingest_tool_event(fetch_1)
    manager.advance(evidence_graph=evidence_graph, tool_events=[fetch_1], final_output_ready=False)

    fetch_2 = _make_fetch_event("https://example.test/page2", link_items=["item/b"])
    manager.ingest_tool_event(fetch_2)
    manager.advance(evidence_graph=evidence_graph, tool_events=[fetch_2], final_output_ready=False)

    assert manager.run.active_task_id == "T3"

    # T3 should NOT advance without final_output_ready
    stuck = manager.advance(
        evidence_graph=evidence_graph,
        tool_events=[],
        final_output_ready=False,
    )
    assert stuck is False, "T3 finalize should not advance without final_output_ready"
    assert manager.run.active_task_id == "T3"
    assert manager.run.active_task().status == "doing"
    assert manager.run.status != "done"


def test_run_flow_context_carries_facts_across_task_boundaries(tmp_path: Path) -> None:
    """Verify that facts and artifacts from T1 are carried into T2 imports."""
    manager = RunFlowManager(
        tmp_path / ".rocky" / "runs",
        prompt="find current open source projects and show as a list",
        task_signature="research/live_compare/general",
        task_class="research",
        execution_cwd=".",
    )
    evidence_graph = EvidenceGraph(thread_id="thread_carry")

    fetch_event = _make_fetch_event(
        "https://github.com/trending",
        link_items=["facebook/react", "vercel/next.js"],
    )
    manager.ingest_tool_event(fetch_event)
    manager.advance(
        evidence_graph=evidence_graph,
        tool_events=[fetch_event],
        final_output_ready=False,
    )

    t2 = manager.run.active_task()
    assert t2.task_id == "T2"

    # T2 imports should contain the T1 rollup
    t2_import_text = " ".join(t2.imports)
    assert "T1" in t2_import_text, "T2 imports must reference T1"
    # T2 imports should contain the discovered facts (link items)
    assert any("react" in item.lower() or "next" in item.lower() for item in t2.imports), \
        "T2 imports should contain discovered link items from T1"


def test_run_flow_task_files_written_to_disk_at_each_step(tmp_path: Path) -> None:
    """Verify that flow.md and task files are written at each advance step."""
    manager = RunFlowManager(
        tmp_path / ".rocky" / "runs",
        prompt="find trending repos right now",
        task_signature="research/live_compare/general",
        task_class="research",
        execution_cwd=".",
    )
    evidence_graph = EvidenceGraph(thread_id="thread_disk")

    # Initial write should exist
    assert manager.flow_path.exists()
    initial_flow = manager.flow_path.read_text(encoding="utf-8")
    assert "T1" in initial_flow

    # Advance T1 → T2
    fetch = _make_fetch_event("https://example.test/page", link_items=["org/repo"])
    manager.ingest_tool_event(fetch)
    manager.advance(evidence_graph=evidence_graph, tool_events=[fetch], final_output_ready=False)

    # Flow file should be updated with new status
    updated_flow = manager.flow_path.read_text(encoding="utf-8")
    assert "doing" in updated_flow or "done" in updated_flow


def _make_tool_result_event(name: str, success: bool = True) -> dict:
    """Build a minimal tool_result event for non-research task kinds."""
    return {
        "type": "tool_result",
        "name": name,
        "success": success,
        "summary_text": f"Ran {name}",
        "text": f"Ran {name}",
        "artifacts": [],
        "facts": [],
    }


def test_run_flow_build_verify_finalize_sequence(tmp_path: Path) -> None:
    """Exercise the build→verify→finalize path for repo/automation tasks.

    This proves divide-and-conquer works for non-research tasks that use
    shell commands and file operations.
    """
    manager = RunFlowManager(
        tmp_path / ".rocky" / "runs",
        prompt="create a shell script that counts lines in all Python files",
        task_signature="repo/shell_execution",
        task_class="repo",
        execution_cwd=".",
    )
    evidence_graph = EvidenceGraph(thread_id="thread_build")

    # --- Verify initial state: T1 is build kind ---
    assert manager.run.active_task_id == "T1"
    t1 = manager.run.active_task()
    assert t1.kind == "build"

    # --- T1 (build): advances on any successful tool ---
    shell_event = _make_tool_result_event("run_shell_command")
    manager.ingest_tool_event(shell_event)
    advanced = manager.advance(
        evidence_graph=evidence_graph,
        tool_events=[shell_event],
        final_output_ready=False,
    )
    assert advanced is True, "T1 build should advance after a successful tool call"
    assert manager.run.active_task_id == "T2"
    t2 = manager.run.active_task()
    assert t2.kind == "verify"
    assert t2.status == "doing"
    assert any("T1" in item for item in t2.imports), "T2 should carry context from T1"

    # --- T2 (verify): advances on run_shell_command or read_file ---
    verify_event = _make_tool_result_event("run_shell_command")
    manager.ingest_tool_event(verify_event)
    advanced = manager.advance(
        evidence_graph=evidence_graph,
        tool_events=[verify_event],
        final_output_ready=False,
    )
    assert advanced is True, "T2 verify should advance after run_shell_command"
    assert manager.run.active_task_id == "T3"
    t3 = manager.run.active_task()
    assert t3.kind == "finalize"
    assert t3.status == "doing"

    # --- T3 (finalize): advances only on final_output_ready ---
    advanced = manager.advance(
        evidence_graph=evidence_graph,
        tool_events=[],
        final_output_ready=True,
    )
    assert advanced is True, "T3 finalize should advance when final_output_ready"
    assert manager.run.status == "done"
    for task in manager.run.tasks:
        assert task.status == "done", f"Task {task.task_id} should be done"


def test_run_flow_inspect_produce_finalize_sequence(tmp_path: Path) -> None:
    """Exercise the inspect→produce→finalize path for extract/data tasks."""
    manager = RunFlowManager(
        tmp_path / ".rocky" / "runs",
        prompt="extract the exact json from data.csv",
        task_signature="extract/general",
        task_class="extract",
        execution_cwd=".",
    )
    evidence_graph = EvidenceGraph(thread_id="thread_extract")

    # --- T1 (inspect): advances on any successful tool ---
    assert manager.run.active_task_id == "T1"
    assert manager.run.active_task().kind == "inspect"

    read_event = _make_tool_result_event("read_file")
    manager.ingest_tool_event(read_event)
    advanced = manager.advance(
        evidence_graph=evidence_graph,
        tool_events=[read_event],
        final_output_ready=False,
    )
    assert advanced is True, "T1 inspect should advance after read_file"
    assert manager.run.active_task_id == "T2"
    assert manager.run.active_task().kind == "produce"

    # --- T2 (produce): advances on any successful tool ---
    write_event = _make_tool_result_event("write_file")
    manager.ingest_tool_event(write_event)
    advanced = manager.advance(
        evidence_graph=evidence_graph,
        tool_events=[write_event],
        final_output_ready=False,
    )
    assert advanced is True, "T2 produce should advance after write_file"
    assert manager.run.active_task_id == "T3"
    assert manager.run.active_task().kind == "finalize"

    # --- T3 (finalize) ---
    advanced = manager.advance(
        evidence_graph=evidence_graph,
        tool_events=[],
        final_output_ready=True,
    )
    assert advanced is True
    assert manager.run.status == "done"


def test_run_flow_fallback_inspect_finalize_sequence(tmp_path: Path) -> None:
    """Exercise the fallback inspect→finalize path (2-task flow)."""
    manager = RunFlowManager(
        tmp_path / ".rocky" / "runs",
        prompt="what is the current git branch",
        task_signature="conversation/general",
        task_class="general",
        execution_cwd=".",
    )
    evidence_graph = EvidenceGraph(thread_id="thread_fallback")

    # --- Fallback: only 2 tasks (T1 inspect, T2 finalize) ---
    assert len(manager.run.tasks) == 2
    assert manager.run.active_task_id == "T1"
    assert manager.run.active_task().kind == "inspect"

    tool_event = _make_tool_result_event("run_shell_command")
    manager.ingest_tool_event(tool_event)
    advanced = manager.advance(
        evidence_graph=evidence_graph,
        tool_events=[tool_event],
        final_output_ready=False,
    )
    assert advanced is True, "T1 inspect should advance after a tool call"
    assert manager.run.active_task_id == "T2"
    assert manager.run.active_task().kind == "finalize"

    advanced = manager.advance(
        evidence_graph=evidence_graph,
        tool_events=[],
        final_output_ready=True,
    )
    assert advanced is True
    assert manager.run.status == "done"


def test_run_flow_verify_task_requires_shell_or_read(tmp_path: Path) -> None:
    """Verify kind tasks should NOT advance on arbitrary tool success — only
    run_shell_command or read_file."""
    manager = RunFlowManager(
        tmp_path / ".rocky" / "runs",
        prompt="build a simple web page",
        task_signature="automation/general",
        task_class="automation",
        execution_cwd=".",
    )
    evidence_graph = EvidenceGraph(thread_id="thread_verify_guard")

    # Advance past T1 (build)
    build_event = _make_tool_result_event("write_file")
    manager.ingest_tool_event(build_event)
    manager.advance(evidence_graph=evidence_graph, tool_events=[build_event], final_output_ready=False)
    assert manager.run.active_task_id == "T2"
    assert manager.run.active_task().kind == "verify"

    # A write_file event should NOT advance a verify task
    wrong_event = _make_tool_result_event("write_file")
    manager.ingest_tool_event(wrong_event)
    stuck = manager.advance(
        evidence_graph=evidence_graph,
        tool_events=[wrong_event],
        final_output_ready=False,
    )
    assert stuck is False, "verify task should not advance on write_file"
    assert manager.run.active_task_id == "T2"

    # A read_file event SHOULD advance a verify task
    read_event = _make_tool_result_event("read_file")
    manager.ingest_tool_event(read_event)
    advanced = manager.advance(
        evidence_graph=evidence_graph,
        tool_events=[read_event],
        final_output_ready=False,
    )
    assert advanced is True, "verify task should advance on read_file"
    assert manager.run.active_task_id == "T3"


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
