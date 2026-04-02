from __future__ import annotations

from rocky.tools import browser, filesystem, git_tools, python_exec, shell, spreadsheet, web
from rocky.tools.base import Tool, ToolContext, ToolResult


READ_ONLY_TOOL_NAMES = {
    "list_files",
    "stat_path",
    "glob_paths",
    "read_file",
    "grep_files",
    "run_shell_command",
    "inspect_shell_environment",
    "read_shell_history",
    "inspect_runtime_versions",
    "run_python",
    "fetch_url",
    "search_web",
    "extract_links",
    "browser_render_page",
    "browser_screenshot",
    "inspect_spreadsheet",
    "read_sheet_range",
    "git_status",
    "git_diff",
    "git_recent_commits",
}

READ_ONLY_TASK_SIGNATURES = {
    "repo/general",
    "repo/shell_inspection",
    "local/runtime_inspection",
    "data/spreadsheet/analysis",
    "extract/general",
}

TASK_ALLOWED_TOOL_NAMES: dict[str, set[str]] = {
    "repo/shell_execution": {
        "run_shell_command",
        "inspect_runtime_versions",
        "read_file",
        "stat_path",
        "git_recent_commits",
    },
    "repo/shell_inspection": {
        "inspect_shell_environment",
        "read_shell_history",
        "run_shell_command",
        "read_file",
        "stat_path",
    },
    "local/runtime_inspection": {
        "inspect_runtime_versions",
        "run_shell_command",
        "inspect_shell_environment",
        "read_shell_history",
    },
    "data/spreadsheet/analysis": {
        "inspect_spreadsheet",
        "read_sheet_range",
        "run_python",
    },
    "automation/general": {
        "write_file",
        "read_file",
        "run_shell_command",
    },
}

TASK_TOOL_PRIORITY: dict[str, list[str]] = {
    "repo/shell_execution": [
        "run_shell_command",
        "inspect_runtime_versions",
        "git_recent_commits",
        "read_file",
        "stat_path",
    ],
    "repo/shell_inspection": [
        "inspect_shell_environment",
        "read_shell_history",
        "run_shell_command",
        "stat_path",
        "read_file",
    ],
    "local/runtime_inspection": [
        "inspect_runtime_versions",
        "run_shell_command",
        "inspect_shell_environment",
        "read_shell_history",
    ],
    "repo/general": [
        "grep_files",
        "read_file",
        "list_files",
        "glob_paths",
        "stat_path",
        "git_status",
        "git_recent_commits",
        "git_diff",
        "run_python",
        "run_shell_command",
    ],
    "data/spreadsheet/analysis": [
        "inspect_spreadsheet",
        "read_sheet_range",
        "run_python",
    ],
    "extract/general": [
        "glob_paths",
        "stat_path",
        "run_python",
        "read_file",
        "grep_files",
        "list_files",
    ],
    "automation/general": [
        "write_file",
        "read_file",
        "run_shell_command",
    ],
}


class ToolRegistry:
    def __init__(self, context: ToolContext) -> None:
        self.context = context
        items: list[Tool] = []
        for module in [filesystem, shell, python_exec, web, browser, spreadsheet, git_tools]:
            items.extend(module.tools())
        self.tools = {tool.name: tool for tool in items}

    def list_tools(self) -> list[dict]:
        return [
            {
                'name': tool.name,
                'family': tool.family,
                'description': tool.description,
            }
            for tool in self.tools.values()
        ]

    def get_openai_schemas(self, families: list[str] | None = None) -> list[dict]:
        selected = self.select(families)
        return [tool.openai_schema() for tool in selected]

    def get_openai_schemas_for_task(
        self,
        families: list[str] | None,
        task_signature: str,
    ) -> list[dict]:
        return [tool.openai_schema() for tool in self.select_for_task(families, task_signature)]

    def select(self, families: list[str] | None = None) -> list[Tool]:
        if families is None:
            return list(self.tools.values())
        if not families:
            return []
        seen = set(families)
        return [tool for tool in self.tools.values() if tool.family in seen]

    def select_for_task(
        self,
        families: list[str] | None,
        task_signature: str,
        user_prompt: str = "",
    ) -> list[Tool]:
        selected = self.select(families)
        allowed = TASK_ALLOWED_TOOL_NAMES.get(task_signature)
        if allowed is not None:
            selected = [tool for tool in selected if tool.name in allowed]
        lowered = user_prompt.lower()
        if task_signature == "repo/shell_execution" and "shell environment" in lowered:
            selected.extend(
                tool
                for tool in self.select(families)
                if tool.name == "inspect_shell_environment" and tool not in selected
            )
        if task_signature in READ_ONLY_TASK_SIGNATURES:
            selected = [tool for tool in selected if tool.name in READ_ONLY_TOOL_NAMES]
        if task_signature == "extract/general":
            spreadsheetish = any(token in lowered for token in (".csv", ".xlsx", "spreadsheet", "sheet"))
            if not spreadsheetish:
                selected = [
                    tool
                    for tool in selected
                    if tool.name not in {"inspect_spreadsheet", "read_sheet_range"}
                ]

        priority = TASK_TOOL_PRIORITY.get(task_signature, [])
        order = {name: index for index, name in enumerate(priority)}
        fallback = len(order) + 100
        return sorted(
            selected,
            key=lambda tool: (order.get(tool.name, fallback), tool.family, tool.name),
        )

    def run(self, name: str, arguments: dict) -> ToolResult:
        if name not in self.tools:
            return ToolResult(False, {}, f'Unknown tool: {name}')
        return self.tools[name].handler(self.context, arguments)
