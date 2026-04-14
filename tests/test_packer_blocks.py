"""T5 + T6 — canonical 6-block packer + retrospective style extraction (Phase 2.3).

Verifies the PRD §12.1 canonical block structure:
  1. Hard constraints (promoted only — CF-14 two-site gate preservation)
  2. Workspace brief (elevated from project_brief memory)
  3. Verification / Style conventions (extracted from retrospectives)
  4. Procedural brief (compact learned-policy summaries)
  5. Curated skills (compact form)
  6. Retrieved memory + student notebook (non-brief, non-retrospective)

Deterministic. No live LLM dependency.
"""

from __future__ import annotations

from rocky.core.context import ContextPackage
from rocky.core.system_prompt import build_system_prompt, _style_cue_from_retrospective


def _pack(
    *,
    memories=None,
    skills=None,
    learned_policies=None,
    student_notes=None,
    tool_families=None,
) -> str:
    return build_system_prompt(
        ContextPackage(
            instructions=[],
            memories=memories or [],
            skills=skills or [],
            learned_policies=learned_policies or [],
            tool_families=tool_families or [],
            student_notes=student_notes or [],
        ),
        mode="bypass",
        user_prompt="task",
        task_signature="",
    )


def test_candidate_policy_only_produces_procedural_brief_not_hard_constraints() -> None:
    """CF-14 two-site gate at packer site — candidates never hard."""
    candidate = {
        "name": "candidate-policy",
        "scope": "project",
        "origin": "learned",
        "generation": 1,
        "text": "candidate body",
        "description": "Candidate description",
        "promotion_state": "candidate",
        "required_behavior": ["Candidate says: do X"],
        "prohibited_behavior": ["Candidate says: do not Y"],
    }
    prompt = _pack(learned_policies=[candidate])
    assert "## Hard constraints" not in prompt
    assert "## Procedural brief" in prompt
    assert "candidate-policy" in prompt
    # Candidate must NOT contribute Do/Do-not lines.
    assert "Do: Candidate says: do X" not in prompt
    assert "Do not: Candidate says: do not Y" not in prompt


def test_promoted_policy_produces_hard_constraints() -> None:
    promoted = {
        "name": "promoted-policy",
        "scope": "project",
        "origin": "learned",
        "generation": 1,
        "text": "promoted body",
        "description": "Promoted description",
        "promotion_state": "promoted",
        "required_behavior": ["Use pnpm"],
        "prohibited_behavior": ["Do not use npm install"],
        "feedback_excerpt": "This project uses pnpm.",
    }
    prompt = _pack(learned_policies=[promoted])
    assert "## Hard constraints" in prompt
    assert "Do: Use pnpm" in prompt
    assert "Do not: Do not use npm install" in prompt
    assert "Teacher correction: This project uses pnpm." in prompt
    # Procedural brief still present for context.
    assert "## Procedural brief" in prompt
    assert "promoted-policy" in prompt


def test_workspace_brief_elevated_to_dedicated_block() -> None:
    brief_memory = {
        "id": "project_brief",
        "name": "project_brief",
        "title": "Project brief",
        "scope": "project_auto",
        "kind": "project_brief",
        "path": "/tmp/project_brief.md",
        "text": "The team prefers pnpm and TypeScript. Main branch is `trunk`.",
        "provenance_type": "learned_rule",
        "contradiction_state": "active",
    }
    prompt = _pack(memories=[brief_memory])
    assert "## Workspace brief" in prompt
    assert "The team prefers pnpm and TypeScript" in prompt
    # Non-brief memories go to the retrieved memory block; brief should NOT
    # also appear there (deduplication).
    assert prompt.count("The team prefers pnpm and TypeScript") == 1


def test_retrospective_produces_style_block_and_compact_body() -> None:
    """T6 style extraction — retrospective surfaces title as a style cue + preserves body."""
    retro = {
        "id": "retro_1",
        "kind": "retrospective",
        "title": "Python functional verification via shell one-liners",
        "text": (
            "# Self retrospective\n\n## Learned\n\n"
            "When verifying Python functions, prefer `python3 -c` invocations "
            "over writing an entire test file. This keeps the verification "
            "step tight and observable."
        ),
    }
    prompt = _pack(student_notes=[retro])
    assert "## Verification / Style conventions" in prompt
    # Style cue with detected style family.
    assert "Python functional verification via shell one-liners" in prompt
    assert "(style: shell)" in prompt
    # Full retrospective body preserved (truncated to 800 chars, not 4000).
    assert "python3 -c" in prompt


def test_style_cue_helper_detects_style_families() -> None:
    """`_style_cue_from_retrospective` classifies style families from content."""
    shell_retro = {"title": "Shell-first verification", "text": "use bash"}
    cue = _style_cue_from_retrospective(shell_retro)
    assert cue is not None
    assert "shell" in cue

    format_retro = {"title": "Prefer JSON output", "text": "render responses as json arrays"}
    cue = _style_cue_from_retrospective(format_retro)
    assert cue is not None
    assert "format" in cue

    empty_retro = {"title": "", "text": ""}
    assert _style_cue_from_retrospective(empty_retro) is None


def test_canonical_pack_reduces_chars_vs_legacy_for_policy_heavy_context() -> None:
    """T5 objective — packer produces shorter output than the verbose legacy
    `## Learned policies` dump for a realistic policy-heavy context.
    """
    promoted_policies = [
        {
            "name": f"policy-{i}",
            "scope": "project",
            "origin": "learned",
            "generation": 1,
            "text": "Verbose policy body. " * 80,  # ~1.6 KB per policy
            "description": f"Policy {i} handles case {i}",
            "promotion_state": "promoted",
            "required_behavior": [f"Do thing {i}"],
            "prohibited_behavior": [f"Do not do wrong thing {i}"],
            "feedback_excerpt": f"Teacher feedback {i}",
        }
        for i in range(4)
    ]
    prompt = _pack(learned_policies=promoted_policies)
    # Heuristic: verbose per-policy text dumps would multiply ~1.6 KB × 4 policies;
    # canonical packer emits compact 1-line procedural-brief entries plus one
    # deduped hard-constraints block, so total learning pack length is bounded
    # well below the 6.4 KB a verbose dump would have produced.
    learning_section = (
        prompt.split("## Hard constraints", 1)[1]
        if "## Hard constraints" in prompt
        else prompt
    )
    # Bound: each verbose policy body was 1.6 KB; 4 policies verbose = ~6.4 KB.
    # Canonical pack should be under 3 KB for this fixture.
    assert len(learning_section) < 3000, (
        f"Learning section {len(learning_section)} chars exceeds 3 KB budget — "
        f"verbose policy bodies appear to have leaked through the procedural brief."
    )
