from __future__ import annotations

from rocky.core.context import ContextPackage
from rocky.core.system_prompt import build_system_prompt


def test_system_prompt_warns_against_inventing_prior_turns() -> None:
    prompt = build_system_prompt(
        ContextPackage(instructions=[], memories=[], skills=[], learned_policies=[], tool_families=[]),
        mode="bypass",
        user_prompt="what was my previous question?",
    )

    assert "Do not pretend to remember earlier turns" in prompt
    assert "Assume you know nothing until a fact is supported" in prompt
    assert "internal model memory is not evidence" in prompt
    assert "imagined permission limits" in prompt
    assert "keep created, copied, edited, and verified files inside the current workspace" in prompt


def test_system_prompt_pushes_multi_step_tool_use() -> None:
    prompt = build_system_prompt(
        ContextPackage(instructions=[], memories=[], skills=[], learned_policies=[], tool_families=["shell", "filesystem"]),
        mode="bypass",
        user_prompt="show me what python versions i have and where they live",
        task_signature="local/runtime_inspection",
    )

    assert "decompose the request into enough tool calls" in prompt
    assert "do not answer from parametric memory" in prompt
    assert "cannot determine the answer from evidence yet" in prompt
    assert "After each tool result, decide whether another tool is needed" in prompt
    assert "installed software, versions, or executable paths" in prompt
    assert "start with `run_shell_command`" in prompt


def test_system_prompt_guides_data_and_extraction_tasks() -> None:
    data_prompt = build_system_prompt(
        ContextPackage(instructions=[], memories=[], skills=[], learned_policies=[], tool_families=["filesystem", "data", "python", "shell"]),
        mode="bypass",
        user_prompt="analyze sales.csv",
        task_signature="data/spreadsheet/analysis",
    )
    extraction_prompt = build_system_prompt(
        ContextPackage(instructions=[], memories=[], skills=[], learned_policies=[], tool_families=["filesystem", "data", "python", "shell"]),
        mode="bypass",
        user_prompt="classify tickets.txt into json",
        task_signature="extract/general",
    )

    assert "inspect the named CSV/XLSX file with `run_shell_command` first" in data_prompt
    assert "use that exact path first instead of searching or guessing" in data_prompt
    assert "Do not stop after a single inspection command" in data_prompt
    assert "return the requested JSON directly" in extraction_prompt
    assert "Do not write output files unless the user explicitly asked" in extraction_prompt
    assert "Use `read_file` for quick inspection and `run_shell_command` for parsing" in extraction_prompt
    assert "discover it with shell commands such as `find`, `rg --files`, or `ls`" in extraction_prompt
    assert "Use at least two steps for extraction work" in extraction_prompt
    assert "line prefixes" in extraction_prompt
    assert "Never create or mention output files" in extraction_prompt


def test_system_prompt_guides_shell_and_automation_tasks() -> None:
    shell_prompt = build_system_prompt(
        ContextPackage(instructions=[], memories=[], skills=[], learned_policies=[], tool_families=["filesystem", "shell", "python", "git"]),
        mode="bypass",
        user_prompt="execute ls and count the entries",
        task_signature="repo/shell_execution",
    )
    automation_prompt = build_system_prompt(
        ContextPackage(instructions=[], memories=[], skills=[], learned_policies=[], tool_families=["filesystem", "shell", "python"]),
        mode="bypass",
        user_prompt="create a repeatable cleanup script and verify it",
        task_signature="automation/general",
    )

    assert "the first tool call should be `run_shell_command`" in shell_prompt
    assert "keep commands inside the workspace instead of using `/tmp` or a fake root" in shell_prompt
    assert "do not collapse that into one tool call" in shell_prompt
    assert "such as `x.sh`" in shell_prompt
    assert "execute that workspace file directly" in shell_prompt
    assert "permission denied" in shell_prompt
    assert "returns structured text such as JSON" in shell_prompt
    assert "current command output from this turn is the source of truth" in shell_prompt
    assert "Do not substitute previous traces, memories, or handoff summaries" in shell_prompt
    assert "auth, permission, network, or other error payload" in shell_prompt
    assert "did not ask for a result file" in shell_prompt
    assert "verify it with `run_shell_command` before answering" in automation_prompt
    assert "Keep the script path inside the workspace" in automation_prompt
    assert "Do not probe the environment or run verification commands before the file exists" in automation_prompt
    assert "first successful tool call should usually be `write_file`" in automation_prompt
    assert "do at most one lightweight inspection" in automation_prompt
    assert "Do not use shell redirection, heredocs, `tee`, or inline interpreter one-liners" in automation_prompt
    assert "mention the exact script or command you ran and the exact observed output" in automation_prompt
    assert "at least three successful tool steps" in automation_prompt
    assert "reread it with `read_file`" in automation_prompt
    assert "within your first five successful tool calls" in automation_prompt


def test_system_prompt_guides_repo_lookup_follow_up_reads() -> None:
    prompt = build_system_prompt(
        ContextPackage(instructions=[], memories=[], skills=[], learned_policies=[], tool_families=["filesystem", "git"]),
        mode="bypass",
        user_prompt="in this repo, find where shell history is implemented and tell me the file and function name",
        task_signature="repo/general",
    )

    assert "do not stop after directory listings or grep-style shell output alone" in prompt
    assert "After discovery, read the most likely file" in prompt


def test_system_prompt_prefers_exact_url_before_browser_for_live_research() -> None:
    prompt = build_system_prompt(
        ContextPackage(instructions=[], memories=[], skills=[], learned_policies=[], tool_families=["web", "browser"]),
        mode="bypass",
        user_prompt="find text models under 12B parameters that are trending right now. start from https://huggingface.co/models",
        task_signature="research/live_compare/general",
    )

    assert "start with `fetch_url` on that exact URL before searching elsewhere" in prompt
    assert "Use `agent_browser` only if the fetched page still leaves missing evidence" in prompt
    assert "send exactly one browser subcommand per tool call" in prompt


def test_system_prompt_makes_learned_policy_prohibitions_hard_constraints() -> None:
    prompt = build_system_prompt(
        ContextPackage(
            instructions=[],
            memories=[],
            skills=[],
            learned_policies=[
                {
                    "name": "product-expression-variant-misclassified",
                    "scope": "project",
                    "origin": "learned",
                    "generation": 2,
                    "promotion_state": "candidate",
                    "text": "- Do not include distinct expression variants as candidates for the base product.",
                    "required_behavior": ["Keep only the established item family once it is supported by the evidence."],
                    "prohibited_behavior": ["Include distinct variants once the established item family is known."],
                }
            ],
            tool_families=["shell", "filesystem"],
        ),
        mode="bypass",
        user_prompt="oban 15",
        task_signature="repo/shell_execution",
    )

    assert "prefer the newer corrective guidance" in prompt
    assert "Treat explicit 'Do not...' rules from retrieved student notes and learned policies as hard constraints" in prompt
    assert "even if the policy is still marked candidate" in prompt
    assert "## Learned constraints" in prompt
    assert "## Learned policies" in prompt
    assert "Do not: Include distinct variants once the established item family is known." in prompt
    assert "Do: Keep only the established item family once it is supported by the evidence." in prompt


def test_system_prompt_marks_self_retrospectives_as_soft_conventions() -> None:
    prompt = build_system_prompt(
        ContextPackage(
            instructions=[],
            memories=[],
            skills=[],
            learned_policies=[],
            tool_families=[],
            student_notes=[
                {
                    "id": "retrospective_1",
                    "kind": "retrospective",
                    "title": "Keep greeting turns compact",
                    "text": "# Self retrospective\n\n## Learned\n\nAnswer greeting-style turns briefly.\n",
                }
            ],
        ),
        mode="bypass",
        user_prompt="say hello again",
    )

    assert "## Student notebook" in prompt
    assert "Self retrospectives are Rocky's own compact lessons" in prompt
    assert "Use them as soft conventions" in prompt
    assert "Keep greeting turns compact [retrospective]" in prompt
