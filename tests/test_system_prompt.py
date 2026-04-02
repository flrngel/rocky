from __future__ import annotations

from rocky.core.context import ContextPackage
from rocky.core.system_prompt import build_system_prompt


def test_system_prompt_warns_against_inventing_prior_turns() -> None:
    prompt = build_system_prompt(
        ContextPackage(instructions=[], memories=[], skills=[], tool_families=[]),
        mode="bypass",
        user_prompt="what was my previous question?",
    )

    assert "Do not pretend to remember earlier turns" in prompt


def test_system_prompt_pushes_multi_step_tool_use() -> None:
    prompt = build_system_prompt(
        ContextPackage(instructions=[], memories=[], skills=[], tool_families=["shell", "filesystem"]),
        mode="bypass",
        user_prompt="show me what python versions i have and where they live",
    )

    assert "decompose the request into enough tool calls" in prompt
    assert "After each tool result, decide whether another tool is needed" in prompt
