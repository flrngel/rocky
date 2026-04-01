from __future__ import annotations

from pathlib import Path

from rocky.config.loader import ConfigLoader
from rocky.util.io import write_text


def test_config_precedence(tmp_path: Path) -> None:
    global_root = tmp_path / "global"
    workspace = tmp_path / "workspace"
    (workspace / ".rocky").mkdir(parents=True)
    loader = ConfigLoader(global_root, workspace)
    loader.ensure_defaults()
    write_text(
        global_root / "config.yaml",
        "active_provider: ollama\npermissions:\n  mode: supervised\n",
    )
    write_text(workspace / ".rocky" / "config.yaml", "permissions:\n  mode: auto\n")
    write_text(workspace / ".rocky" / "config.local.yaml", "permissions:\n  mode: plan\n")
    config = loader.load({"permissions": {"mode": "bypass"}})
    assert config.permissions.mode == "bypass"
