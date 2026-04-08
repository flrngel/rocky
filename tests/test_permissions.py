from __future__ import annotations

from pathlib import Path

from rocky.app import RockyRuntime
from rocky.config.models import PermissionConfig
from rocky.core.permissions import PermissionManager, PermissionRequest


def test_permission_manager_is_non_enforcing_even_with_legacy_deny_rules(tmp_path: Path) -> None:
    manager = PermissionManager(
        PermissionConfig(mode="supervised", deny={"shell": ["run"]}),
        tmp_path,
    )

    manager.check(PermissionRequest(family="shell", action="run", detail="rm -rf nope", writes=True, risky=True))

    state = manager.explain()
    assert state["enforced"] is False
    assert state["mode"] == "disabled"
    assert state["legacy_mode"] == "supervised"
    assert state["recent_decisions"][-1]["decision"] == "allow"


def test_permissions_command_reports_disabled_enforcement(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "project"
    workspace.mkdir()

    runtime = RockyRuntime.load_from(workspace)
    result = runtime.commands.handle("/permissions")

    assert result.data["enforced"] is False
    assert result.data["mode"] == "disabled"
    assert "will not block tools" in result.data["message"]
