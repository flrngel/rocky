from __future__ import annotations

from pathlib import Path
from typing import Any

from rocky.config.models import AppConfig, LearningConfig, PermissionConfig, ProviderConfig, ProviderStyle, ToolConfig, merge_dict
from rocky.util.io import read_yaml, write_yaml


DEFAULT_CONFIG_DICT = {
    'active_provider': 'ollama',
    'providers': {
        'ollama': {
            'style': 'openai_chat',
            'base_url': 'http://localhost:11434/v1',
            'api_key_env': 'OLLAMA_API_KEY',
            'model': 'llama3.2',
            'store': False,
        },
        'openai': {
            'style': 'openai_responses',
            'base_url': 'https://api.openai.com/v1',
            'api_key_env': 'OPENAI_API_KEY',
            'model': 'gpt-5.2',
            'store': False,
        },
    },
    'permissions': {'mode': 'supervised'},
    'tools': {
        'max_read_chars': 12000,
        'max_tool_output_chars': 12000,
        'shell_timeout_s': 60,
        'python_timeout_s': 60,
    },
    'learning': {
        'enabled': True,
        'auto_publish_project_skills': True,
        'slow_learner_enabled': True,
    },
}


class ConfigLoader:
    def __init__(self, global_root: Path, workspace_root: Path) -> None:
        self.global_root = global_root
        self.workspace_root = workspace_root
        self.global_config = global_root / 'config.yaml'
        self.project_config = workspace_root / '.rocky' / 'config.yaml'
        self.local_config = workspace_root / '.rocky' / 'config.local.yaml'

    def ensure_defaults(self) -> None:
        if not self.global_config.exists():
            write_yaml(self.global_config, DEFAULT_CONFIG_DICT)

    def load(self, cli_overrides: dict[str, Any] | None = None) -> AppConfig:
        self.ensure_defaults()
        merged = dict(DEFAULT_CONFIG_DICT)
        for path in [self.global_config, self.project_config, self.local_config]:
            data = read_yaml(path)
            if isinstance(data, dict):
                merged = merge_dict(merged, data)
        if cli_overrides:
            merged = merge_dict(merged, cli_overrides)
        providers = {
            name: ProviderConfig(
                name=name,
                style=ProviderStyle(data.get('style', 'openai_chat')),
                base_url=data.get('base_url', DEFAULT_CONFIG_DICT['providers']['ollama']['base_url']),
                api_key_env=data.get('api_key_env'),
                api_key=data.get('api_key'),
                model=data.get('model', 'llama3.2'),
                temperature=float(data.get('temperature', 0.2)),
                timeout_s=int(data.get('timeout_s', 120)),
                store=bool(data.get('store', False)),
                extra_headers=data.get('extra_headers', {}) or {},
            )
            for name, data in (merged.get('providers') or {}).items()
        }
        if not providers:
            providers = AppConfig.default().providers
        return AppConfig(
            active_provider=merged.get('active_provider', 'ollama'),
            providers=providers,
            permissions=PermissionConfig(**(merged.get('permissions') or {})),
            tools=ToolConfig(**(merged.get('tools') or {})),
            learning=LearningConfig(**(merged.get('learning') or {})),
        )
