from __future__ import annotations

from rocky.core.router import Lane, Router, TaskClass


def test_router_meta_and_data() -> None:
    router = Router()
    meta = router.route('what tools do you have?')
    assert meta.lane == Lane.META
    data = router.route('analyze this spreadsheet and tell me the key columns')
    assert data.task_class == TaskClass.DATA
    assert 'data' in data.tool_families


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
    assert 'git' in route.tool_families


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
