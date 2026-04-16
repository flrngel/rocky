from __future__ import annotations

from pathlib import Path

from rocky.app import RockyRuntime
from rocky.tools.base import Tool


def test_runtime_inspection_prefers_runtime_tools_first(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)

    names = [
        tool.name
        for tool in runtime.tool_registry.select_for_task(
            ["shell"],
            "local/runtime_inspection",
            "what python versions do i have",
        )
    ]

    assert names[:1] == ["run_shell_command"]


def test_shell_execution_tools_focus_on_shell_not_filesystem_shortcuts(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)

    names = [
        tool.name
        for tool in runtime.tool_registry.select_for_task(
            ["filesystem", "shell", "python", "git"],
            "repo/shell_execution",
            "execute ls and count the entries",
        )
    ]

    assert names[0] == "run_shell_command"
    assert names[1:] == ["read_file", "write_file"]


def test_data_tasks_prefer_spreadsheet_tools_and_hide_writers(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)

    names = [
        tool.name
        for tool in runtime.tool_registry.select_for_task(
            ["filesystem", "data", "python", "shell"],
            "data/spreadsheet/analysis",
            "analyze data/sales.csv",
        )
    ]

    assert names[:2] == ["run_shell_command", "read_file"]
    assert "write_file" not in names


def test_extraction_tasks_are_read_only_and_keep_python(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)

    names = [
        tool.name
        for tool in runtime.tool_registry.select_for_task(
            ["filesystem", "python", "data", "shell"],
            "extract/general",
            "normalize the people dataset into json with row count and fields",
        )
    ]

    assert names[:2] == ["read_file", "run_shell_command"]
    assert "read_file" in names
    assert "write_file" not in names


def test_automation_tools_keep_write_and_verify_path(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)

    names = [
        tool.name
        for tool in runtime.tool_registry.select_for_task(
            ["filesystem", "shell", "python"],
            "automation/general",
            "create a repeatable cleanup script for tmp artifacts and verify it",
        )
    ]

    assert names == ["write_file", "read_file", "run_shell_command"]


def test_research_route_exposes_web_tools_and_hides_shell_tools(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)

    names = [
        tool.name
        for tool in runtime.tool_registry.select_for_task(
            ["web", "browser"],
            "research/live_compare/general",
            "find trending openweight llm models under 12B and show me a list",
        )
    ]

    assert names[:3] == ["search_web", "fetch_url", "agent_browser"]
    assert "run_shell_command" not in names
    assert "write_file" not in names
    assert "read_file" not in names


def test_research_route_prefers_fetch_url_first_when_prompt_includes_url(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)

    names = [
        tool.name
        for tool in runtime.tool_registry.select_for_task(
            ["web", "browser"],
            "research/live_compare/general",
            "find text models under 12B parameters that are trending right now. start from https://huggingface.co/models",
        )
    ]

    assert names[:3] == ["fetch_url", "search_web", "agent_browser"]



def test_openai_tool_schema_defaults_to_closed_object_properties() -> None:
    tool = Tool(
        name="demo",
        description="demo",
        input_schema={
            "properties": {
                "path": {"type": "string"},
                "options": {
                    "properties": {
                        "recursive": {"type": "boolean"},
                    }
                },
            },
            "required": ["path"],
        },
        family="filesystem",
        handler=lambda ctx, args: None,  # type: ignore[arg-type]
    )

    schema = tool.openai_schema()["function"]["parameters"]
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["properties"]["options"]["type"] == "object"
    assert schema["properties"]["options"]["additionalProperties"] is False


# ---------------------------------------------------------------------------
# O7 — Tool-registry auto-derivation + error-shape consistency
# ---------------------------------------------------------------------------


def test_tool_route_hints_derived_from_task_tool_priority() -> None:
    """Every tool that appears in TASK_TOOL_PRIORITY must have a route hint,
    so adding a new tool to TASK_TOOL_PRIORITY cannot drift silently."""
    from rocky.tools.registry import TASK_TOOL_PRIORITY, _TOOL_ROUTE_HINTS

    tools_from_priority: set[str] = set()
    for tools_for_sig in TASK_TOOL_PRIORITY.values():
        tools_from_priority.update(tools_for_sig)
    missing = tools_from_priority - _TOOL_ROUTE_HINTS.keys()
    assert missing == set(), (
        f"_TOOL_ROUTE_HINTS is missing derived entries for: {sorted(missing)}. "
        "The derivation should cover every tool listed in TASK_TOOL_PRIORITY."
    )


def test_tool_route_hints_preserve_hand_curated_overrides(monkeypatch) -> None:
    """A synthetic divergent override in ``_TOOL_ROUTE_OVERRIDES`` must win
    over first-appearance derivation so operator-facing reroute hints remain
    predictable. The dict is currently empty (A1 cleanup); the test injects
    a synthetic entry that genuinely diverges from what derivation would
    produce so the override layer's bite is witnessed on a real tool.

    The synthetic override maps ``read_file`` to ``site/understanding/general``.
    First-appearance derivation routes ``read_file`` to ``repo/shell_execution``
    (its first signature in ``TASK_TOOL_PRIORITY``). The two differ, so if the
    override layer ever stops winning over derivation, the first assertion
    fires immediately.
    """
    from rocky.tools import registry as tr

    derived_default = tr._derive_tool_route_hints()
    # Precondition: derivation produces a concrete (non-override) route for
    # the chosen tool. If this ever changes, the test's divergence claim
    # must be re-anchored.
    assert derived_default["read_file"] == "repo/shell_execution", (
        "Test precondition: first-appearance derivation must route read_file "
        "to repo/shell_execution. Rebaseline the synthetic divergent override "
        "if TASK_TOOL_PRIORITY ordering changes."
    )

    synthetic = {"read_file": "site/understanding/general"}
    monkeypatch.setattr(tr, "_TOOL_ROUTE_OVERRIDES", synthetic)
    monkeypatch.setattr(tr, "_TOOL_ROUTE_HINTS", tr._derive_tool_route_hints())
    assert tr._suggest_route_for_tool("read_file") == "site/understanding/general", (
        "Override must win over first-appearance derivation. If this fires, "
        "_derive_tool_route_hints is merging overrides in the wrong order "
        "(derivation silently clobbers operator-facing overrides)."
    )

    # Sensitivity: clearing the override makes derivation take over.
    monkeypatch.setattr(tr, "_TOOL_ROUTE_OVERRIDES", {})
    monkeypatch.setattr(tr, "_TOOL_ROUTE_HINTS", tr._derive_tool_route_hints())
    assert tr._suggest_route_for_tool("read_file") == "repo/shell_execution", (
        "Without an override, derivation routes read_file to its "
        "first-appearance signature (repo/shell_execution)."
    )


def test_error_shapes_use_consistent_reason_key() -> None:
    """All three tool-error dicts (tool_not_exposed, blocked_verification_command,
    tool_name_in_shell) must carry a ``reason`` key. ``message`` is not an
    accepted alternate; consistency is the contract."""
    # Import inline to avoid circulars in test collection.
    from pathlib import Path as _Path
    src = _Path(__file__).resolve().parent.parent / "src" / "rocky" / "tools"
    shell_src = (src / "shell.py").read_text(encoding="utf-8")
    reg_src = (src / "registry.py").read_text(encoding="utf-8")

    # tool_not_exposed lives in registry.py: it must carry "reason"
    assert '"error": "tool_not_exposed"' in reg_src
    assert '"reason"' in reg_src

    # Both shell-side error shapes must carry "reason".
    assert '"error": "blocked_verification_command"' in shell_src
    assert '"error": "tool_name_in_shell"' in shell_src
    # Ensure neither shape still uses the old "message" key on a same-dict line.
    for block_label in ("blocked_verification_command", "tool_name_in_shell"):
        # Walk forward from the label and confirm "reason" appears before the
        # next closing brace (a rough structural check).
        start = shell_src.index(block_label)
        slice_until_brace = shell_src[start:start + 1200]
        closing_brace = slice_until_brace.find("},")
        assert closing_brace > 0
        error_block = slice_until_brace[:closing_brace]
        assert '"reason"' in error_block, (
            f"{block_label} error dict must carry 'reason' key; "
            f"saw: {error_block}"
        )
