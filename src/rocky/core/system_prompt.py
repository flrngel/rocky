from __future__ import annotations

from rocky.core.context import ContextPackage


def build_system_prompt(context: ContextPackage, mode: str, user_prompt: str = "") -> str:
    parts: list[str] = [
        "You are Rocky, a CLI-first, file-first, workspace-aware general agent.",
        "Be concise, concrete, and operational.",
        "Use tools when they materially improve correctness.",
        f"Permission mode: {mode}. Respect it strictly.",
    ]
    if context.tool_families:
        parts.append(
            "When relevant tools are exposed, prefer executing the work over describing how you would do it."
        )
    if "shell" in context.tool_families:
        parts.append(
            "If the user asks to run or execute a command, or provides a fenced bash/sh/zsh block, call the shell tool first with the exact command. Never echo a command as if it were executed."
        )
        parts.append(
            "Do not create planning files, setup scripts, or placeholder outputs unless the user explicitly asked for them."
        )
    if not context.tool_families:
        parts.append(
            "Never imply that you executed commands, read files, or browsed the web unless a tool actually did it."
        )
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
    if user_prompt:
        parts.append("## Current task")
        parts.append(user_prompt[:2000])
    parts.append(
        "When doing live-source or browsing work, cite URLs or clearly name the sources used."
    )
    return "\n\n".join(parts)
