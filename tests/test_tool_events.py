from __future__ import annotations

import json
from pathlib import Path

from rocky.tool_events import (
    compact_tool_result_event,
    ensure_tool_result_event,
    normalize_tool_result_event,
    tool_event_debug_text,
)


def test_normalize_tool_result_event_builds_summary_first_shell_context() -> None:
    output = json.dumps(
        {
            "success": True,
            "summary": "Command exited with 0",
            "data": {
                "command": "printf 'ok\\n'",
                "returncode": 0,
                "stdout": "ok\n",
                "stderr": "",
                "cwd": ".",
            },
            "metadata": {},
        }
    )

    event = normalize_tool_result_event(
        "run_shell_command",
        {"command": "printf 'ok\\n'"},
        output,
        tool_call_id="call_1",
    )

    assert event["tool_call_id"] == "call_1"
    assert event["summary_text"] == "Command exited with 0"
    assert event["text"] == event["model_text"]
    assert not event["text"].lstrip().startswith("{")
    assert any(fact["kind"] == "command" for fact in event["facts"])
    assert any(fact["kind"] == "stdout" for fact in event["facts"])


def test_ensure_tool_result_event_derives_summary_fields_from_legacy_event() -> None:
    legacy_event = {
        "type": "tool_result",
        "name": "search_web",
        "arguments": {"query": "rocky"},
        "success": True,
        "text": json.dumps(
            {
                "success": True,
                "summary": "Search returned 1 result(s)",
                "data": [{"title": "Rocky", "url": "https://example.test", "snippet": "summary"}],
            }
        ),
    }

    normalized = ensure_tool_result_event(legacy_event)

    assert normalized["summary_text"] == "Search returned 1 result(s)"
    assert normalized["success"] is True
    assert normalized["text"] == normalized["model_text"]
    assert any(artifact["kind"] == "url" for artifact in normalized["artifacts"])


def test_compact_tool_result_event_offloads_large_raw_output(tmp_path: Path) -> None:
    event = normalize_tool_result_event(
        "read_file",
        {"path": "notes.txt"},
        {
            "success": True,
            "summary": "Read notes.txt",
            "data": "\n".join(f"{index}: line {index}" for index in range(1, 120)),
            "metadata": {"path": "notes.txt", "line_count": 119},
        },
        tool_call_id="call_1",
    )

    compacted = compact_tool_result_event(
        event,
        storage_dir=tmp_path / "tool-results",
        inline_limit=120,
        preview_limit=80,
    )

    assert compacted["raw_ref"]
    assert Path(compacted["raw_ref"]).exists()
    assert Path(compacted["raw_ref"]).read_text(encoding="utf-8") == event["raw_text"]
    assert len(compacted["raw_text"]) < len(event["raw_text"])
    assert "full raw output saved to" in tool_event_debug_text(compacted)


def test_normalize_tool_result_event_summarizes_agent_browser_snapshot_items() -> None:
    event = normalize_tool_result_event(
        "agent_browser",
        {"command": "snapshot -i --json"},
        {
            "success": True,
            "summary": "agent-browser `snapshot -i --json` succeeded for https://huggingface.co/models",
            "data": {
                "command": "snapshot -i --json",
                "url": "https://huggingface.co/models?sort=trending",
                "snapshot": '- link "org/Model-One 7B" [ref=e1]\n- link "org/Model-Two 8B" [ref=e2]',
                "items": [
                    {"name": "org/Model-One 7B", "role": "link", "ref": "e1"},
                    {"name": "org/Model-Two 8B", "role": "link", "ref": "e2"},
                    {"name": "org/Model-Three 9B", "role": "link", "ref": "e3"},
                ],
            },
        },
        tool_call_id="call_web",
    )

    fact_texts = [fact["text"] for fact in event["facts"]]

    assert any(text.startswith("URL: https://huggingface.co/models") for text in fact_texts)
    assert any("org/Model-One 7B" in text for text in fact_texts)
    assert "org/Model-Two 8B" in event["model_text"]
    assert any(artifact["kind"] == "url" for artifact in event["artifacts"])


def test_derive_tool_event_details_emits_browser_hint_for_fetch_url() -> None:
    output = json.dumps(
        {
            "success": False,
            "summary": "Encountered anti-bot challenge while fetching https://example.com/blocked",
            "data": {
                "url": "https://example.com/blocked",
                "error": "anti-bot challenge",
                "text_excerpt": "Please verify you are human.",
            },
            "metadata": {
                "blocked_by_challenge": True,
                "browser_fallback_hint": True,
            },
        }
    )

    event = normalize_tool_result_event(
        "fetch_url",
        {"url": "https://example.com/blocked"},
        output,
        tool_call_id="call_hint",
    )

    fact_texts = [fact["text"] for fact in event["facts"]]
    assert any("agent_browser" in text for text in fact_texts)
    assert any("Hint" in text for text in fact_texts)


def test_derive_tool_event_details_emits_steps_fact_for_search_web() -> None:
    output = json.dumps(
        {
            "success": True,
            "summary": "Search returned 2 result(s)",
            "data": [
                {"title": "Result 1", "url": "https://example.com/1", "snippet": "First"},
                {"title": "Result 2", "url": "https://example.com/2", "snippet": "Second"},
            ],
            "metadata": {
                "engine": "duckduckgo",
                "steps": [
                    {"engine": "duckduckgo", "url": "https://html.duckduckgo.com/html/?q=test", "outcome": "success", "result_count": 2},
                ],
            },
        }
    )

    event = normalize_tool_result_event(
        "search_web",
        {"query": "test"},
        output,
        tool_call_id="call_steps",
    )

    fact_texts = [fact["text"] for fact in event["facts"]]
    assert any("Pipeline" in text or "step" in text.lower() for text in fact_texts)
