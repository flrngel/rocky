from __future__ import annotations

from rocky.tools import browser, filesystem, git_tools, python_exec, shell, spreadsheet, web
from rocky.tools.base import Tool, ToolContext, ToolResult


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

    def select(self, families: list[str] | None = None) -> list[Tool]:
        if families is None:
            return list(self.tools.values())
        if not families:
            return []
        seen = set(families)
        return [tool for tool in self.tools.values() if tool.family in seen]

    def run(self, name: str, arguments: dict) -> ToolResult:
        if name not in self.tools:
            return ToolResult(False, {}, f'Unknown tool: {name}')
        return self.tools[name].handler(self.context, arguments)
