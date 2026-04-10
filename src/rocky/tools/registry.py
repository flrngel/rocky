from __future__ import annotations

from rocky.tools import browser, filesystem, shell, web
from rocky.tools.base import Tool, ToolContext, ToolResult


READ_ONLY_TOOL_NAMES = {
    "read_file",
    "run_shell_command",
    "fetch_url",
    "search_web",
    "agent_browser",
}

READ_ONLY_TASK_SIGNATURES = {
    "repo/shell_inspection",
    "local/runtime_inspection",
    "repo/general",
    "data/spreadsheet/analysis",
    "extract/general",
    "research/live_compare/general",
    "site/understanding/general",
}

TASK_TOOL_PRIORITY: dict[str, list[str]] = {
    "repo/shell_execution": [
        "run_shell_command",
        "read_file",
        "write_file",
    ],
    "repo/shell_inspection": [
        "run_shell_command",
        "read_file",
    ],
    "local/runtime_inspection": [
        "run_shell_command",
        "read_file",
    ],
    "repo/general": [
        "run_shell_command",
        "read_file",
    ],
    "data/spreadsheet/analysis": [
        "run_shell_command",
        "read_file",
    ],
    "extract/general": [
        "read_file",
        "run_shell_command",
    ],
    "research/live_compare/general": [
        "search_web",
        "fetch_url",
        "agent_browser",
    ],
    "site/understanding/general": [
        "fetch_url",
        "search_web",
        "agent_browser",
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
        for module in [filesystem, shell, web, browser]:
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
        selected = self.select(families) if families else list(self.tools.values())
        if task_signature in READ_ONLY_TASK_SIGNATURES:
            selected = [tool for tool in selected if tool.name in READ_ONLY_TOOL_NAMES]
        priority = TASK_TOOL_PRIORITY.get(task_signature, [])
        order = {name: index for index, name in enumerate(priority)}
        fallback = len(order) + 100
        preferred_families = set(families or [])
        return sorted(
            selected,
            key=lambda tool: (
                0 if tool.name in order else 1,
                order.get(tool.name, fallback),
                0 if preferred_families and tool.family in preferred_families else 1,
                tool.family,
                tool.name,
            ),
        )

    def get(self, name: str) -> Tool | None:
        return self.tools.get(name)

    def run(self, name: str, arguments: dict) -> ToolResult:
        if name not in self.tools:
            return ToolResult(False, {}, f'Unknown tool: {name}')
        return self.tools[name].handler(self.context, arguments)
