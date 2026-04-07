from __future__ import annotations

from pathlib import Path

from rocky.config.wizard import build_global_config, run_config_wizard
from rocky.util.io import read_yaml


def test_build_global_config_for_compatible_provider() -> None:
    config = build_global_config(
        None,
        {
            "active_provider": "compatible",
            "compatible_style": "openai_chat",
            "base_url": "http://example.test/v1",
            "model": "qwen3.5:4b",
            "thinking": False,
            "api_key_env": "EXAMPLE_API_KEY",
            "permission_mode": "auto",
        },
    )

    assert config["active_provider"] == "compatible"
    assert config["providers"]["compatible"]["base_url"] == "http://example.test/v1"
    assert config["providers"]["compatible"]["model"] == "qwen3.5:4b"
    assert config["providers"]["compatible"]["thinking"] is False
    assert config["permissions"]["mode"] == "auto"


def test_run_config_wizard_writes_selected_provider(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    answers = iter(
        [
            "2",
            "http://localhost:11434/v1",
            "qwen3.5:4b",
            "false",
            "",
            "OLLAMA_API_KEY",
            "1",
        ]
    )

    run_config_wizard(config_path, input_func=lambda prompt: next(answers))

    config = read_yaml(config_path)
    assert config["active_provider"] == "ollama"
    assert config["providers"]["ollama"]["model"] == "qwen3.5:4b"
    assert config["providers"]["ollama"]["thinking"] is False
    assert config["permissions"]["mode"] == "supervised"
