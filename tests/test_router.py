from __future__ import annotations

from rocky.core.router import Lane, Router, TaskClass


def test_router_meta_and_data() -> None:
    router = Router()
    meta = router.route('what tools do you have?')
    assert meta.lane == Lane.META
    data = router.route('analyze this spreadsheet and tell me the key columns')
    assert data.task_class == TaskClass.DATA
    assert data.tool_families == ['filesystem', 'shell']


def test_router_detects_shell_execution_requests() -> None:
    router = Router()
    route = router.route(
        'execute command and find information about me\n```bash\nwhoami && id && pwd\n```'
    )
    assert route.task_class == TaskClass.REPO
    assert route.task_signature == 'repo/shell_execution'
    assert 'shell' in route.tool_families


def test_router_detects_shell_inspection_requests() -> None:
    router = Router()

    route = router.route('show me 10 last history of current shell')

    assert route.task_class == TaskClass.REPO
    assert route.task_signature == 'repo/shell_inspection'
    assert route.tool_families == ['shell', 'filesystem']


def test_router_prefers_repo_route_for_git_status_question() -> None:
    router = Router()

    route = router.route('in this repo, show current git status and last commit message')

    assert route.task_class == TaskClass.REPO
    assert route.task_signature == 'repo/general'
    assert route.tool_families == ['filesystem', 'shell']


def test_router_prefers_repo_route_for_shell_history_code_lookup() -> None:
    router = Router()

    route = router.route('find where shell history is implemented in this repo and tell me the file and function name')

    assert route.task_class == TaskClass.REPO
    assert route.task_signature == 'repo/general'


def test_router_detects_provider_question_as_meta() -> None:
    router = Router()

    route = router.route('what provider am i using right now?')

    assert route.task_class == TaskClass.META
    assert route.task_signature == 'meta/runtime'


def test_router_detects_runtime_version_questions() -> None:
    router = Router()

    route = router.route('what python versions do i have')

    assert route.task_class == TaskClass.REPO
    assert route.task_signature == 'local/runtime_inspection'
    assert route.tool_families == ['shell']


def test_router_detects_runtime_version_questions_with_system_wording() -> None:
    router = Router()

    node_route = router.route('what node versions in my system do i have')
    ruby_route = router.route('what are ruby versions in my system list it')

    assert node_route.task_signature == 'local/runtime_inspection'
    assert ruby_route.task_signature == 'local/runtime_inspection'


def test_router_prefers_explicit_commands_over_runtime_inference() -> None:
    router = Router()

    route = router.route('run python3 --version and which python3, then inspect python3 runtime variants')

    assert route.task_signature == 'repo/shell_execution'


def test_router_treats_use_cli_current_fact_prompt_as_shell_execution() -> None:
    router = Router()

    route = router.route("what's the date today? use cli to get exact date and check the nike price of today")

    assert route.task_class == TaskClass.REPO
    assert route.task_signature == 'repo/shell_execution'
    assert 'shell' in route.tool_families


def test_router_treats_use_command_current_fact_prompt_as_shell_execution() -> None:
    router = Router()

    route = router.route("use command to get exact date. and then check the nike stock's price of today")

    assert route.task_class == TaskClass.REPO
    assert route.task_signature == 'repo/shell_execution'
    assert 'shell' in route.tool_families


def test_router_treats_inline_workspace_script_execution_as_shell_execution() -> None:
    router = Router()

    route = router.route(
        "You are now product catalog manager. Execute `x.sh` and explore the response. "
        "Those are pending products to be managed, and each product has candidates to merge. "
        "Use tools properly."
    )

    assert route.task_class == TaskClass.REPO
    assert route.task_signature == 'repo/shell_execution'
    assert 'shell' in route.tool_families


def test_router_treats_existing_workspace_script_resume_as_shell_execution() -> None:
    router = Router()

    route = router.route(
        "continue the catalog review work in this project. "
        "Re-run the existing workspace script, write the final exact JSON merge decisions to `phase5_merge_decisions.json`, "
        "then read that file back and tell me the exact JSON."
    )

    assert route.task_class == TaskClass.REPO
    assert route.task_signature == 'repo/shell_execution'
    assert 'shell' in route.tool_families


def test_router_does_not_treat_latest_shell_command_as_research() -> None:
    router = Router()

    route = router.route('what environment values do USER HOME SHELL have and what was my latest shell command')

    assert route.task_signature == 'repo/shell_inspection'


def test_router_does_not_treat_report_as_repo_keyword() -> None:
    router = Router()

    route = router.route('build a repeatable sales report script and run it')

    assert route.task_class == TaskClass.AUTOMATION
    assert route.task_signature == 'automation/general'


def test_router_treats_explicit_people_search_as_research() -> None:
    router = Router()

    route = router.route('search for all QUEEN BEE members and find out who is the leader and tell me about their biography')

    assert route.task_class == TaskClass.RESEARCH
    assert route.task_signature == 'research/live_compare/general'
    assert 'web' in route.tool_families


def test_router_treats_current_trending_query_as_research() -> None:
    router = Router()

    route = router.route('what is current github trending repos')

    assert route.task_class == TaskClass.RESEARCH
    assert route.task_signature == 'research/live_compare/general'
    assert 'web' in route.tool_families


def test_router_treats_person_profile_investigation_as_research() -> None:
    router = Router()

    route = router.route('investigate on a person "Kiha Lee" who is vc partner. write a nice table about his profile, what should I know about.')

    assert route.task_class == TaskClass.RESEARCH
    assert route.task_signature == 'research/live_compare/general'
    assert 'web' in route.tool_families


def test_router_treats_trending_openweight_model_query_as_research() -> None:
    router = Router()

    route = router.route(
        "find huggingface openweight llm models that are trending right now. "
        "filter models that have parameters under 12B. you should find at least 10 models and show me as a list."
    )

    assert route.task_class == TaskClass.RESEARCH
    assert route.task_signature == "research/live_compare/general"
    assert "web" in route.tool_families


def test_router_prefers_automation_for_empty_workspace_python_project() -> None:
    router = Router()

    route = router.route(
        'Build a tiny Python script project in this empty workspace. '
        'Create exactly these files: input.txt, main.py, and README.md. '
        'Then run python3 main.py to verify it works.'
    )

    assert route.task_class == TaskClass.AUTOMATION
    assert route.task_signature == 'automation/general'


def test_router_prefers_automation_for_empty_workspace_csv_project() -> None:
    router = Router()

    route = router.route(
        'Build a tiny shell script project in this empty workspace. '
        'Create exactly these files: sales.csv, report.sh, and README.md. '
        'Then run sh report.sh to verify it works and tell me the exact output.'
    )

    assert route.task_class == TaskClass.AUTOMATION
    assert route.task_signature == 'automation/general'


# --------------------------------------------------------------------------
# O13 — Heredoc / multi-line shell router robustness.
# --------------------------------------------------------------------------


import pytest


@pytest.mark.parametrize(
    "prompt",
    [
        # Lexically distinct from the follow-ups §6.1 example (which used <<EOF).
        "cat <<'BOUNDARY'\nThe deployment pipeline stages artifacts through\nthe build cache before promotion.\nBOUNDARY",
        "ssh deploy@host <<-MARKER\n  pushd /opt/app\n  ./rollout.sh\n  popd\nMARKER",
        "mysql -u admin <<\"QUERY\"\nSELECT id FROM customers WHERE region='EU';\nQUERY",
    ],
)
def test_router_classifies_heredoc_as_shell(prompt: str) -> None:
    router = Router()
    route = router.route(prompt)
    assert route.task_class == TaskClass.REPO
    assert 'shell' in route.tool_families


# --------------------------------------------------------------------------
# O14 — Non-Python/Markdown file extensions (.rs, .go, .kt, .swift).
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt",
    [
        # New extensions (previously unrecognized).
        "Refactor the coroutine dispatcher in src/main/kotlin/App.kt",
        "Fix the memory leak in Sources/Modules/Auth.swift",
        # Regression guards for existing extensions (confirm we did not break them).
        "Add error handling to src/bin/server.rs",
        "Optimize the goroutine pool in pkg/worker/pool.go",
    ],
)
def test_router_repo_task_recognises_language_file_extensions(prompt: str) -> None:
    router = Router()
    assert router._looks_like_repo_task(prompt.lower()) is True
