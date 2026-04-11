from __future__ import annotations

from pathlib import Path

from rocky.config.loader import ConfigLoader
from rocky.config.models import AppConfig, ProviderConfig, ProviderStyle
from rocky.util.io import write_text
from rocky.util.yamlx import dump_yaml


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


def test_config_defaults_enable_thinking_for_providers(tmp_path: Path) -> None:
    global_root = tmp_path / "global"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    config = ConfigLoader(global_root, workspace).load()

    assert config.providers["ollama"].thinking is True
    assert config.providers["openai"].thinking is True


def test_dump_yaml_serializes_enum_values() -> None:
    rendered = dump_yaml({"style": ProviderStyle.OPENAI_CHAT})

    assert "openai_chat" in rendered


def test_provider_config_context_window_defaults_to_none() -> None:
    cfg = ProviderConfig(name="test")

    assert hasattr(cfg, "context_window")
    assert cfg.context_window is None


def test_provider_config_context_window_accepts_explicit_value() -> None:
    cfg = ProviderConfig(name="test", context_window=128000)

    assert cfg.context_window == 128000


def test_app_config_default_sets_context_windows() -> None:
    config = AppConfig.default()

    assert config.providers["litellm_local"].context_window == 32768
    assert config.providers["ollama"].context_window == 131072
    assert config.providers["openai"].context_window == 128000


def test_config_loader_reads_context_window_from_yaml(tmp_path: Path) -> None:
    global_root = tmp_path / "global"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_text(
        global_root / "config.yaml",
        "providers:\n  custom:\n    style: openai_chat\n    model: gpt-4\n    context_window: 32768\n",
    )

    config = ConfigLoader(global_root, workspace).load(create_defaults=False)

    assert config.providers["custom"].context_window == 32768


def test_config_loader_context_window_none_when_omitted(tmp_path: Path) -> None:
    global_root = tmp_path / "global"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_text(
        global_root / "config.yaml",
        "providers:\n  custom:\n    style: openai_chat\n    model: gpt-4\n",
    )

    config = ConfigLoader(global_root, workspace).load(create_defaults=False)

    assert config.providers["custom"].context_window is None
