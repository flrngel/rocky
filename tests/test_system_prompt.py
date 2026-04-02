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
