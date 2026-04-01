from __future__ import annotations

from rocky.core.router import Lane, Router, TaskClass


def test_router_meta_and_data() -> None:
    router = Router()
    meta = router.route('what tools do you have?')
    assert meta.lane == Lane.META
    data = router.route('analyze this spreadsheet and tell me the key columns')
    assert data.task_class == TaskClass.DATA
    assert 'data' in data.tool_families
