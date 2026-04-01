from __future__ import annotations

from rocky.core.context import ContextPackage


def build_system_prompt(context: ContextPackage, mode: str) -> str:
    parts: list[str] = [
        "You are Rocky, a CLI-first, file-first, workspace-aware general agent.",
        "Be concise, concrete, and operational.",
        "Use tools when they materially improve correctness.",
        f"Permission mode: {mode}. Respect it strictly.",
    ]
    if context.instructions:
        parts.append("## Project instructions")
        for item in context.instructions:
            parts.append(f"### {item['path']}\n{item['text']}")
    if context.memories:
        parts.append("## Retrieved memory")
        for item in context.memories:
            parts.append(f"### {item['name']} ({item['scope']})\n{item['text']}")
    if context.skills:
        parts.append("## Retrieved skills")
        for item in context.skills:
            parts.append(
                f"### {item['name']} [{item['scope']} gen={item['generation']}]\n{item['text']}"
            )
    if context.tool_families:
        parts.append("## Tool exposure")
        parts.append(
            "Only use tools from these families if needed: "
            + ", ".join(context.tool_families)
        )
    parts.append(
        "When doing live-source or browsing work, cite URLs or clearly name the sources used."
    )
    return "\n\n".join(parts)
