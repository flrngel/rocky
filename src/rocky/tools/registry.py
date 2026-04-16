from __future__ import annotations

import re

from rocky.tools import browser, filesystem, shell, web
from rocky.tools.base import Tool, ToolContext, ToolResult


# Hand-curated route overrides — each entry is a conscious choice where the
# "most natural" route for a tool differs from what a strict first-appearance
# derivation would produce. The dict is intentionally kept as an extension
# point; entries should be rare and added only when derivation demonstrably
# routes a tool to the wrong integrator-facing signature.
_TOOL_ROUTE_OVERRIDES: dict[str, str] = {}


def _derive_tool_route_hints() -> dict[str, str]:
    """Build tool_name -> canonical route signature by inverting
    :data:`TASK_TOOL_PRIORITY`.

    Rule: for each tool name, the canonical route is the *first* task
    signature in ``TASK_TOOL_PRIORITY`` whose tool list contains that name.
    Hand-curated overrides in :data:`_TOOL_ROUTE_OVERRIDES` win when present.

    Auto-derivation protects against drift: a new tool added to
    ``TASK_TOOL_PRIORITY`` is automatically rerouteable without requiring a
    parallel edit to a separate dict.
    """
    derived: dict[str, str] = {}
    # First, iterate signatures in order and claim each tool by its first
    # appearance. Later appearances are ignored (the earliest route wins).
    for signature, tools_for_sig in TASK_TOOL_PRIORITY.items():
        for tool_name in tools_for_sig:
            derived.setdefault(tool_name, signature)
    # Overrides take precedence to preserve pre-existing hand-tuned choices.
    derived.update(_TOOL_ROUTE_OVERRIDES)
    return derived


# Read-only structure — derived at import time from TASK_TOOL_PRIORITY +
# _TOOL_ROUTE_OVERRIDES. Do not mutate at runtime; callers should go through
# _suggest_route_for_tool().
_TOOL_ROUTE_HINTS: dict[str, str]  # populated after TASK_TOOL_PRIORITY is defined


def _suggest_route_for_tool(name: str) -> str | None:
    """Return the canonical route signature for ``name``, or None if unknown.

    Used to populate ``reroute_to`` in blocked-tool ToolResult payloads and
    by the O12 argv[0] guard. Derived from :data:`TASK_TOOL_PRIORITY` with
    explicit overrides in :data:`_TOOL_ROUTE_OVERRIDES`.
    """
    return _TOOL_ROUTE_HINTS.get(name)


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

# Module-level frozenset of all built-in tool names registered at import time.
# Used by O12 argv[0] detection in shell.py so it can check without a registry
# instance.  Keep in sync with the Tool(...) registrations in
# filesystem, shell, web, and browser modules.
ALL_TOOL_NAMES: frozenset[str] = frozenset({
    "read_file",
    "write_file",
    "run_shell_command",
    "fetch_url",
    "search_web",
    "agent_browser",
})


# Now that TASK_TOOL_PRIORITY is defined, populate the derived map.
_TOOL_ROUTE_HINTS = _derive_tool_route_hints()

_URL_RE = re.compile(r"https?://\S+", re.I)


def _prompt_contains_explicit_url(user_prompt: str) -> bool:
    return bool(_URL_RE.search(user_prompt or ""))


class ToolRegistry:
    def __init__(
        self,
        context: ToolContext,
        *,
        exposed_names: frozenset[str] | None = None,
        current_route: str = "",
    ) -> None:
        self.context = context
        self._exposed_names: frozenset[str] | None = exposed_names
        self._current_route: str = current_route
        items: list[Tool] = []
        for module in [filesystem, shell, web, browser]:
            items.extend(module.tools())
        self.tools = {tool.name: tool for tool in items}

    @property
    def tool_names(self) -> frozenset[str]:
        """All registered tool names regardless of route.

        Used by O12 argv[0] detection to check whether a shell command matches
        a known Rocky tool name without requiring a route context.
        """
        return frozenset(self.tools.keys())

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
        tool_families_override: list[str] | None = None,
    ) -> list[dict]:
        return [tool.openai_schema() for tool in self.select_for_task(families, task_signature, tool_families_override=tool_families_override)]

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
        tool_families_override: list[str] | None = None,
    ) -> list[Tool]:
        # Merge override families additively with the route's default families.
        effective_families: list[str] | None = families
        override_set: frozenset[str] = frozenset(tool_families_override or [])
        if tool_families_override:
            base = list(families or [])
            merged = list(base)
            for fam in tool_families_override:
                if fam not in merged:
                    merged.append(fam)
            effective_families = merged

        selected = self.select(effective_families) if effective_families else list(self.tools.values())
        if task_signature in READ_ONLY_TASK_SIGNATURES:
            # Apply the read-only gate, but bypass it for tools whose family is
            # explicitly in the override.  This is what allows `write_file`
            # (family=filesystem) on a research route when the caller opts in
            # via tool_families_override=["filesystem"].  Families NOT in the
            # override still obey the read-only constraint.
            selected = [
                tool for tool in selected
                if tool.name in READ_ONLY_TOOL_NAMES or tool.family in override_set
            ]
        priority = list(TASK_TOOL_PRIORITY.get(task_signature, []))
        if task_signature == "research/live_compare/general" and _prompt_contains_explicit_url(user_prompt):
            priority = ["fetch_url", "search_web", "agent_browser"]
        order = {name: index for index, name in enumerate(priority)}
        fallback = len(order) + 100
        preferred_families = set(effective_families or [])
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
        if self._exposed_names is not None and name not in self._exposed_names:
            reroute = _suggest_route_for_tool(name)
            route_label = self._current_route or "current route"
            reason = (
                f"Tool {name} is not exposed for route {route_label}. "
                f"Use one of the route's tools, or invoke rocky with "
                f"--route {reroute} to access this tool."
                if reroute
                else (
                    f"Tool {name} is not exposed for route {route_label}. "
                    f"Use one of the route's exposed tools instead."
                )
            )
            data: dict = {
                "error": "tool_not_exposed",
                "tool": name,
                "reroute_to": reroute,
                "reason": reason,
            }
            summary = (
                f"Tool `{name}` is not exposed for route `{route_label}`. "
                + (f"Try --route {reroute}." if reroute else "Use an exposed tool.")
            )
            return ToolResult(False, data, summary)
        return self.tools[name].handler(self.context, arguments)
