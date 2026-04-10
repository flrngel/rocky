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
