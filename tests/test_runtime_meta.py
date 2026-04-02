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
    assert status['workspace_root'] == str(workspace)
    text = runtime.meta_answer('what tools do you have?')
    assert 'tools:' in text
    provider_text = runtime.meta_answer('what provider am i using right now?')
    assert 'active_provider:' in provider_text
    assert 'model:' in provider_text
