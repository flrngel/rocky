from __future__ import annotations

from pathlib import Path

from rocky.app import RockyRuntime


def test_runtime_meta_and_init(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    workspace = tmp_path / 'project'
    workspace.mkdir()
    runtime = RockyRuntime.load_from(workspace)
    init = runtime.init_scaffold()
    assert init['initialized'] is True
    status = runtime.status()
    assert status["version"]
    assert status['workspace_root'] == str(workspace)
    assert status["runtime"]["active_provider"] == runtime.config.active_provider
    assert status["runtime"]["verbose_mode"] is False
    assert status["global_settings"]["path"].endswith("config.yaml")
    assert status["project_settings"]["project"]["path"].endswith(".rocky/config.yaml")
    assert status["effective_settings"]["active_provider"] == runtime.config.active_provider
    text = runtime.meta_answer('what tools do you have?')
    assert 'tools:' in text
    provider_text = runtime.meta_answer('what provider am i using right now?')
    assert 'active_provider:' in provider_text
    assert 'model:' in provider_text


def test_runtime_status_reports_freeze_mode_without_creating_session(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "project"
    workspace.mkdir()

    runtime = RockyRuntime.load_from(workspace, freeze=True)
    status = runtime.status()

    assert status["runtime"]["freeze_mode"] is True
    assert status["session_id"] is None
    assert status["global_settings"]["exists"] is False
    assert status["project_settings"]["project"]["exists"] is False
    assert not runtime.workspace.sessions_dir.exists()


def test_freeze_command_toggles_process_local_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "project"
    workspace.mkdir()
    runtime = RockyRuntime.load_from(workspace)

    on = runtime.commands.handle("/freeze on")
    status = runtime.commands.handle("/freeze status")
    off = runtime.commands.handle("/freeze off")

    assert on.data["freeze_mode"] is True
    assert status.data["freeze_mode"] is True
    assert off.data["freeze_mode"] is False
