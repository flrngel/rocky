from __future__ import annotations

import json
from pathlib import Path

from rocky.core.router import Router
from rocky.core.runtime_state import ActiveTaskThread, AnswerContractBuilder, EvidenceAccumulator, EvidenceGraph
from rocky.memory.store import MemoryStore


def test_router_resolve_inherits_active_thread_for_short_follow_up() -> None:
    router = Router()
    thread = ActiveTaskThread(
        thread_id="thread_1",
        workspace_root="/workspace",
        execution_cwd="src",
        task_family="repo",
        task_signature="repo/shell_execution",
        artifact_refs=["reports/output.json"],
        entity_refs=["report", "output"],
        status="active",
    )
    thread.unresolved_questions.append("verify the output file")

    decision, continuation = router.resolve(
        "continue and verify it",
        active_threads=[thread],
        recent_threads=[],
        workspace_root="/workspace",
        execution_cwd="src",
    )

    assert continuation.action == "continue_active_thread"
    assert decision.task_signature == "repo/shell_execution"
    assert decision.continued_thread_id == thread.thread_id
    assert decision.source.startswith("continuation_")



def test_answer_contract_forbids_inference_without_support() -> None:
    graph = EvidenceGraph(thread_id="thread_1")
    observed = graph.add_claim(
        "Observed path reports/output.json via read_file",
        "tool_observed",
        "read_file",
        confidence=0.9,
    )
    inferred = graph.add_claim(
        "The report is production ready",
        "agent_inferred",
        "assistant",
        confidence=0.4,
    )

    builder = AnswerContractBuilder()
    contract = builder.build(
        "what path did you inspect and is the report ready?",
        "repo/general",
        None,
        graph,
        prior_answer="This was a much longer previous answer that should not be repeated in full.",
    )

    assert observed.claim_id in contract.allowed_claim_ids
    assert inferred.claim_id in contract.forbidden_claim_ids

    graph_only_inference = EvidenceGraph(thread_id="thread_2")
    graph_only_inference.add_claim("Probably ready for release", "agent_inferred", "assistant", confidence=0.3)
    uncertain = builder.build("is it ready?", "repo/general", None, graph_only_inference)
    assert uncertain.uncertainty_required is True
    assert uncertain.missing_evidence



def test_memory_capture_uses_supported_claims_not_answer_rhetoric(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "project", tmp_path / "global")

    result = store.capture_project_memory(
        prompt="check reports/output.json",
        answer="The deployment is definitely ready and all safeguards passed.",
        task_signature="repo/shell_execution",
        trace={"tool_events": []},
        supported_claims=[
            {
                "claim_id": "claim_1",
                "thread_id": "thread_1",
                "text": "Observed path reports/output.json via read_file",
                "provenance_type": "tool_observed",
                "provenance_source": "read_file",
                "confidence": 0.9,
                "support_refs": [],
                "contradiction_refs": [],
                "status": "active",
                "created_at": "2026-04-04T00:00:00Z",
            }
        ],
        thread_id="thread_1",
    )

    assert result["written"] >= 1
    notes = store.load_project_auto_notes()
    assert notes
    note_texts = [note.text for note in notes]
    assert any("reports/output.json" in text for text in note_texts)
    assert all("deployment is definitely ready" not in text.lower() for text in note_texts)


def test_evidence_accumulator_records_runtime_variants_from_runtime_inspection() -> None:
    graph = EvidenceGraph(thread_id="thread_1")
    accumulator = EvidenceAccumulator()
    payload = {
        "success": True,
        "data": {
            "targets": [
                {
                    "target": "python3",
                    "exact_available": True,
                    "exact_path": "/workspace/.harness_bin/python3",
                    "matches": [
                        {
                            "command": "python3",
                            "exact": True,
                            "path": "/workspace/.harness_bin/python3",
                            "version": "Python 3.14.3",
                        },
                        {
                            "command": "python3.13",
                            "exact": False,
                            "path": "/workspace/.harness_bin/python3.13",
                            "version": "Python 3.13.5",
                        },
                    ],
                }
            ]
        },
    }

    accumulator.ingest_tool_events(
        graph,
        [
            {
                "type": "tool_result",
                "name": "inspect_runtime_versions",
                "success": True,
                "text": json.dumps(payload),
            }
        ],
    )

    claim_texts = [claim.text for claim in graph.claims]
    entity_values = [entity["value"] for entity in graph.entities]
    artifact_refs = [artifact["ref"] for artifact in graph.artifacts]

    assert "python3" in entity_values
    assert "python3.13" in entity_values
    assert "/workspace/.harness_bin/python3" in artifact_refs
    assert "/workspace/.harness_bin/python3.13" in artifact_refs
    assert any("Observed runtime command python3: version Python 3.14.3, path /workspace/.harness_bin/python3" == text for text in claim_texts)
    assert any("Observed runtime command python3.13: version Python 3.13.5, path /workspace/.harness_bin/python3.13" == text for text in claim_texts)
    assert any("Exact runtime command python3 resolves to /workspace/.harness_bin/python3" == text for text in claim_texts)


def test_evidence_accumulator_keeps_bounded_informative_python_output_lines() -> None:
    graph = EvidenceGraph(thread_id="thread_1")
    accumulator = EvidenceAccumulator()
    payload = {
        "success": True,
        "data": {
            "stdout": "\n".join(
                [
                    "=== Duplicate Product Review Analysis ===",
                    "Product P1101: Juniper Original Small (SKU: JUN-ORI-011)",
                    "C1101: Juniper Original Small (JUN-ORI-011) - EXACT MATCH",
                    "C1106: Northwind Tea Alt (NOR-TEA-031) - NO MATCH",
                ]
            )
        },
    }

    accumulator.ingest_tool_events(
        graph,
        [
            {
                "type": "tool_result",
                "name": "run_python",
                "success": True,
                "text": json.dumps(payload),
            }
        ],
    )

    claim_texts = [claim.text for claim in graph.claims]
    assert any("Python output line: Product P1101: Juniper Original Small (SKU: JUN-ORI-011)" == text for text in claim_texts)
    assert any("Python output line: C1101: Juniper Original Small (JUN-ORI-011) - EXACT MATCH" == text for text in claim_texts)
    assert any("Python output line: C1106: Northwind Tea Alt (NOR-TEA-031) - NO MATCH" == text for text in claim_texts)


def test_evidence_accumulator_captures_read_file_lines_and_grep_hits() -> None:
    graph = EvidenceGraph(thread_id="thread_1")
    accumulator = EvidenceAccumulator()
    read_payload = {
        "success": True,
        "data": "1: from argparse import ArgumentParser\n3: ALIASES = ['configure', 'setup', 'set-up']\n5: def build_parser():\n6:     parser = ArgumentParser(prog='rocky')\n7:     return parser",
        "metadata": {"path": "src/example_cli.py"},
    }
    grep_payload = {
        "success": True,
        "data": [
            {"path": "src/example_cli.py", "line": 3, "text": "ALIASES = ['configure', 'setup', 'set-up']"},
            {"path": "src/example_cli.py", "line": 6, "text": "    parser = ArgumentParser(prog='rocky')"},
        ],
    }

    accumulator.ingest_tool_events(
        graph,
        [
            {
                "type": "tool_result",
                "name": "read_file",
                "success": True,
                "arguments": {"path": "src/example_cli.py"},
                "text": json.dumps(read_payload),
            },
            {
                "type": "tool_result",
                "name": "grep_files",
                "success": True,
                "text": json.dumps(grep_payload),
            },
        ],
    )

    claim_texts = [claim.text for claim in graph.claims]

    assert any("Observed file line src/example_cli.py: 3: ALIASES = ['configure', 'setup', 'set-up']" == text for text in claim_texts)
    assert any("Observed file line src/example_cli.py: 6: parser = ArgumentParser(prog='rocky')" == text for text in claim_texts)
    assert any("Observed grep hit src/example_cli.py:3: ALIASES = ['configure', 'setup', 'set-up']" == text for text in claim_texts)
    assert any("Observed grep hit src/example_cli.py:6: parser = ArgumentParser(prog='rocky')" == text for text in claim_texts)


def test_evidence_accumulator_captures_spreadsheet_headers_and_sheet_rows() -> None:
    graph = EvidenceGraph(thread_id="thread_1")
    accumulator = EvidenceAccumulator()
    payload = {
        "success": True,
        "data": {
            "path": "data/metrics.xlsx",
            "format": "xlsx",
            "sheets": [
                {
                    "name": "Summary",
                    "rows": 10,
                    "columns": 3,
                    "headers": ["month", "total", "region"],
                    "sample_rows": [["jan", 101, "US"], ["feb", 118, "CA"]],
                },
                {
                    "name": "Regions",
                    "rows": 5,
                    "columns": 2,
                    "headers": ["region", "sales"],
                    "sample_rows": [["US", 101], ["CA", 118]],
                },
            ],
        },
    }

    accumulator.ingest_tool_events(
        graph,
        [
            {
                "type": "tool_result",
                "name": "inspect_spreadsheet",
                "success": True,
                "text": json.dumps(payload),
            }
        ],
    )

    claim_texts = [claim.text for claim in graph.claims]
    entity_values = [entity["value"] for entity in graph.entities]

    assert "Summary" in entity_values
    assert "Regions" in entity_values
    assert any("Workbook data/metrics.xlsx has 2 sheet(s)" == text for text in claim_texts)
    assert any("Sheet Summary in data/metrics.xlsx: 10 rows, 3 columns, headers month, total, region" == text for text in claim_texts)
    assert any("Sheet Summary sample row in data/metrics.xlsx: jan, 101, US" == text for text in claim_texts)
    assert any("Sheet Regions in data/metrics.xlsx: 5 rows, 2 columns, headers region, sales" == text for text in claim_texts)
