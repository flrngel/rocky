"""Graceful-unknown-key shim (run-20260414-205412).

Covers SC-LOADER-UNKNOWN-KEY (A5).

When a retired knob like `slow_learner_enabled` is still present in a user's
on-disk `.rocky/config.yaml` (or a forward-looking knob predates schema merge),
`ConfigLoader.load` must silently drop the unknown keys rather than raising
`TypeError: __init__() got an unexpected keyword argument ...` at boot.

Sensitivity: removing `_filter_known_fields` from `loader.py` makes these
tests fail with the TypeError they were written to prevent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rocky.config.loader import ConfigLoader
from rocky.config.models import LearningConfig, PermissionConfig, ToolConfig
from rocky.util.io import write_yaml


def _write_yaml_config(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_yaml(path, data)


def test_learning_unknown_key_retired_slow_learner(tmp_path: Path) -> None:
    """Retired `slow_learner_enabled` in user YAML must not crash boot."""
    global_root = tmp_path / "global"
    workspace_root = tmp_path / "ws"
    _write_yaml_config(
        workspace_root / ".rocky" / "config.yaml",
        {
            "learning": {
                "enabled": True,
                "slow_learner_enabled": False,  # retired field; must be tolerated
            }
        },
    )
    loader = ConfigLoader(global_root, workspace_root)
    config = loader.load(create_defaults=True)
    assert isinstance(config.learning, LearningConfig)
    assert config.learning.enabled is True
    assert not hasattr(config.learning, "slow_learner_enabled")


def test_learning_unknown_key_forward_looking(tmp_path: Path) -> None:
    """Future-looking unknown knob in user YAML must not crash boot."""
    global_root = tmp_path / "global"
    workspace_root = tmp_path / "ws"
    _write_yaml_config(
        workspace_root / ".rocky" / "config.yaml",
        {"learning": {"enabled": True, "future_knob": 42}},
    )
    config = ConfigLoader(global_root, workspace_root).load(create_defaults=True)
    assert config.learning.enabled is True


def test_permissions_unknown_key_tolerated(tmp_path: Path) -> None:
    global_root = tmp_path / "global"
    workspace_root = tmp_path / "ws"
    _write_yaml_config(
        workspace_root / ".rocky" / "config.yaml",
        {"permissions": {"mode": "bypass", "experimental_guard": "reject"}},
    )
    config = ConfigLoader(global_root, workspace_root).load(create_defaults=True)
    assert isinstance(config.permissions, PermissionConfig)
    assert config.permissions.mode == "bypass"


def test_tools_unknown_key_tolerated(tmp_path: Path) -> None:
    global_root = tmp_path / "global"
    workspace_root = tmp_path / "ws"
    _write_yaml_config(
        workspace_root / ".rocky" / "config.yaml",
        {"tools": {"max_read_chars": 6000, "new_internal_knob": True}},
    )
    config = ConfigLoader(global_root, workspace_root).load(create_defaults=True)
    assert isinstance(config.tools, ToolConfig)
    assert config.tools.max_read_chars == 6000


def test_known_keys_still_apply(tmp_path: Path) -> None:
    """Sanity: the shim must not accidentally discard known keys too."""
    global_root = tmp_path / "global"
    workspace_root = tmp_path / "ws"
    _write_yaml_config(
        workspace_root / ".rocky" / "config.yaml",
        {
            "learning": {"enabled": False, "auto_publish_project_skills": False},
            "permissions": {"mode": "supervised"},
        },
    )
    config = ConfigLoader(global_root, workspace_root).load(create_defaults=True)
    assert config.learning.enabled is False
    assert config.learning.auto_publish_project_skills is False
    assert config.permissions.mode == "supervised"
