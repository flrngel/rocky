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
