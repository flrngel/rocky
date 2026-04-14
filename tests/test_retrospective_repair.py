"""Phase 2.5 — retrospective workflow extraction + style-gap repair.

Two deterministic seams:

1. `_extract_retrospective_workflow(note)` parses structured
   `## Repeat next time` / `## Avoid next time` / `## Recall when`
   sections out of a retrospective markdown body. The packer uses these
   to emit imperative workflow bullets into the Verification block.

2. `AgentCore._retrospective_style_gaps(output, context)` + the paired
   `_repair_retrospective_style_gap` invocation. The gaps function
   reports when a retrospective with the `shell` style family applies
   to the current context BUT the candidate answer lacks an explicit
   shell-command invocation literal. The repair path is invoked only
   when the gap is non-empty.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from rocky.core.context import ContextPackage
from rocky.core.system_prompt import (
    _extract_retrospective_workflow,
    _style_cue_from_retrospective,
    build_system_prompt,
)


# ---------------------------------------------------------------------------
# O1 — workflow extraction
# ---------------------------------------------------------------------------


def test_extract_workflow_parses_repeat_and_avoid_sections() -> None:
    note = {
        "title": "Verification loop for file creation and execution",
        "text": (
            "# Self retrospective\n\n"
            "## Learned\n\nWrite-Execute-Verify for new scripts.\n\n"
            "## Repeat next time\n\n"
            "- Write file using write_file\n"
            "- Execute script using run_shell_command\n"
            "- Verify output via stdout\n"
            "- Confirm file existence with ls -l\n\n"
            "## Avoid next time\n\n"
            "- Using heredoc (cat << 'EOF') when write_file is available\n"
            "- Skipping the execution step\n\n"
            "## Recall when\n\n"
            "- task_family: repo\n"
            "- Writing a new script and verifying it works\n"
        ),
    }
    wf = _extract_retrospective_workflow(note)
    assert wf["repeat"] == [
        "Write file using write_file",
        "Execute script using run_shell_command",
        "Verify output via stdout",
        "Confirm file existence with ls -l",
    ]
    assert wf["avoid"] == [
        "Using heredoc (cat << 'EOF') when write_file is available",
        "Skipping the execution step",
    ]
    assert wf["recall"] == [
        "task_family: repo",
        "Writing a new script and verifying it works",
    ]


def test_extract_workflow_empty_on_absent_sections() -> None:
    wf = _extract_retrospective_workflow({"title": "X", "text": "no structured sections here"})
    assert wf == {"repeat": [], "avoid": [], "recall": []}


def test_workflow_bullets_reach_system_prompt() -> None:
    """Repeat / avoid bullets must appear in the Verification block of the prompt."""
    note = {
        "id": "retro_wf",
        "kind": "retrospective",
        "title": "Shell verification loop",
        "text": (
            "# Self retrospective\n\n"
            "## Repeat next time\n\n"
            "- Write file using write_file\n"
            "- Execute script using `python3 <file>.py`\n\n"
            "## Avoid next time\n\n"
            "- Using heredoc for file creation\n"
        ),
    }
    prompt = build_system_prompt(
        ContextPackage(
            instructions=[], memories=[], skills=[], learned_policies=[],
            tool_families=["shell"], student_notes=[note],
        ),
        mode="bypass",
        user_prompt="create divider.py and verify it works",
        task_signature="repo/general",
    )
    assert "Repeat the following tool-workflow steps" in prompt
    assert "Write file using write_file" in prompt
    assert "Execute script using `python3 <file>.py`" in prompt
    assert "Do NOT repeat the following failure patterns" in prompt
    assert "Using heredoc for file creation" in prompt


# ---------------------------------------------------------------------------
# O2 — style-gap detector
# ---------------------------------------------------------------------------


@dataclass
class _StubContext:
    student_notes: list[dict] = field(default_factory=list)


def _make_agent():
    """Shim an AgentCore instance for bound-method calls without full wiring.

    We only exercise `_retrospective_style_gaps` which uses `self` solely to
    access class-level regex constants — no network, no tools, no provider.
    """
    from rocky.core.agent import AgentCore

    class _ShimAgent:
        _RETRO_SHELL_CMD_RE = AgentCore._RETRO_SHELL_CMD_RE
        _retrospective_style_gaps = AgentCore._retrospective_style_gaps

    return _ShimAgent()


def _retro(title: str, text: str) -> dict:
    return {"id": "r", "kind": "retrospective", "title": title, "text": text}


def test_gap_fires_when_shell_retro_applies_but_answer_has_no_shell_literal() -> None:
    """gemma4:26b answer had `if __name__ == "__main__"` + prints — no shell
    invocation. Gap must fire so the repair path re-invokes."""
    agent = _make_agent()
    context = _StubContext(
        student_notes=[
            _retro(
                "Verification loop for file creation and execution",
                "## Repeat next time\n- Execute via shell `python3 file.py`",
            )
        ]
    )
    answer = (
        "I have created `divider.py` with the requested `divide` function.\n\n"
        "```python\ndef divide(a, b): return a/b\n\n"
        'if __name__ == "__main__":\n    print(divide(10, 2))\n```'
    )
    gaps = agent._retrospective_style_gaps(answer, context)
    assert len(gaps) == 1
    assert gaps[0]["family"] == "shell"
    assert "shell-based verification" in gaps[0]["rationale"]


def test_no_gap_when_answer_already_contains_shell_invocation() -> None:
    agent = _make_agent()
    context = _StubContext(
        student_notes=[
            _retro(
                "Shell verification",
                "Use `python3 X.py` to verify",
            )
        ]
    )
    answer = (
        "I wrote divider.py. Verified with:\n\n"
        "```bash\npython3 divider.py\n```\n\n"
        "Output: 5.0"
    )
    assert agent._retrospective_style_gaps(answer, context) == []


def test_no_gap_when_python_dash_c_form_used() -> None:
    agent = _make_agent()
    context = _StubContext(student_notes=[_retro("shell", "`python3 -c`")])
    answer = 'Verified: `python3 -c "from divider import divide; print(divide(10, 2))"` → 5.0'
    assert agent._retrospective_style_gaps(answer, context) == []


def test_no_gap_when_no_retrospective_present() -> None:
    agent = _make_agent()
    context = _StubContext(student_notes=[])
    answer = "plain answer, no verification"
    assert agent._retrospective_style_gaps(answer, context) == []


def test_no_gap_when_retrospective_has_no_shell_family() -> None:
    """A retrospective about JSON formatting (format family, not shell) must
    not trigger the shell-gap check."""
    agent = _make_agent()
    context = _StubContext(
        student_notes=[_retro("Prefer JSON arrays", "Return responses as json arrays")]
    )
    answer = '{"result": "ok"}'
    # format family alone doesn't carry a hard textual pattern today.
    assert agent._retrospective_style_gaps(answer, context) == []


def test_shell_pattern_accepts_diverse_interpreters() -> None:
    """The gap detector isn't python-specific — node / bash / uv run all count."""
    agent = _make_agent()
    ctx = _StubContext(student_notes=[_retro("shell workflow", "use shell")])
    for ok_answer in (
        "Ran `node server.js` to verify",
        "Verified via `bash setup.sh`",
        "Executed `uv run pytest` → passed",
        "Ran `npx playwright test` → 3 passed",
    ):
        assert agent._retrospective_style_gaps(ok_answer, ctx) == [], (
            f"Expected no gap for answer containing diverse interpreter: {ok_answer!r}"
        )


def test_style_cue_compact_shell_family() -> None:
    """Guardrail: the per-retrospective cue stays compact. Imperative
    directives are emitted once at the block level by the packer, not per-cue.
    """
    cue = _style_cue_from_retrospective(
        {"title": "Verification loop", "text": "Use `python3 -c` for checks"}
    )
    assert cue is not None
    assert "(style: shell)" in cue
    # No imperative sentence appended per-cue.
    assert "must include" not in cue
    assert "Rewrite" not in cue
