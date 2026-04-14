from __future__ import annotations

from pathlib import Path
from typing import Any

from rocky.config.models import AppConfig, LearningConfig, PermissionConfig, ProviderConfig, ProviderStyle, ToolConfig, merge_dict
from rocky.util.io import read_yaml, write_yaml


DEFAULT_CONFIG_DICT = {
    'active_provider': 'litellm_local',
    'providers': {
        'litellm_local': {
            'style': 'litellm_chat',
            'base_url': 'http://localhost:4000',
            'api_key_env': 'LITELLM_API_KEY',
            'model': 'ollama_chat/qwen3.5:4b',
            'thinking': True,
            'reasoning_effort': 'medium',
            'store': False,
            'context_window': 32768,
        },
        'ollama': {
            'style': 'openai_chat',
            'base_url': 'http://localhost:11434/v1',
            'api_key_env': 'OLLAMA_API_KEY',
            'model': 'llama3.2',
            'thinking': True,
            'store': False,
            'context_window': 131072,
        },
        'openai': {
            'style': 'openai_responses',
            'base_url': 'https://api.openai.com/v1',
            'api_key_env': 'OPENAI_API_KEY',
            'model': 'gpt-5.2',
            'thinking': True,
            'reasoning_effort': 'medium',
            'store': False,
            'context_window': 128000,
        },
    },
    'permissions': {'mode': 'bypass'},
    'tools': {
        'max_read_chars': 12000,
        'max_tool_output_chars': 12000,
        'shell_timeout_s': 60,
        'python_timeout_s': 60,
    },
    'learning': {
        'enabled': True,
        'auto_publish_project_skills': True,
        'auto_self_reflection_enabled': True,
    },
}


def _filter_known_fields(dataclass_cls, payload: dict[str, Any]) -> dict[str, Any]:
    """Drop unknown keys so stale or forward-looking config YAML doesn't crash boot.

    Dataclass-slotted configs (`LearningConfig`, `PermissionConfig`, `ToolConfig`)
    raise `TypeError` on unknown kwargs. When an operator's on-disk `.rocky/config.yaml`
    still carries a retired knob (e.g. `slow_learner_enabled` after PRD §18 removal),
    or carries a forward-looking knob authored in a newer Rocky, we want Rocky to
    ignore the unknown key rather than hard-crash at boot. This filter preserves
    only the keys the dataclass knows about; logging is intentionally skipped
    since the normal case is benign (operator YAML that predates a schema change).
    """
    known = set(getattr(dataclass_cls, '__dataclass_fields__', {}).keys())
    return {k: v for k, v in payload.items() if k in known}


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

    def load(
        self,
        cli_overrides: dict[str, Any] | None = None,
        *,
        create_defaults: bool = True,
    ) -> AppConfig:
        if create_defaults:
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
                thinking=bool(data.get('thinking', True)),
                temperature=float(data.get('temperature', 0.2)),
                timeout_s=int(data.get('timeout_s', 120)),
                store=bool(data.get('store', False)),
                extra_headers=data.get('extra_headers', {}) or {},
                reasoning_effort=(str(data.get('reasoning_effort')).strip() if data.get('reasoning_effort') not in {None, ''} else None),
                tool_choice=(str(data.get('tool_choice')).strip() if data.get('tool_choice') not in {None, ''} else None),
                extra_body=data.get('extra_body', {}) or {},
                context_window=int(data['context_window']) if data.get('context_window') else None,
            )
            for name, data in (merged.get('providers') or {}).items()
        }
        if not providers:
            providers = AppConfig.default().providers
        return AppConfig(
            active_provider=merged.get('active_provider', 'litellm_local'),
            providers=providers,
            permissions=PermissionConfig(**_filter_known_fields(PermissionConfig, merged.get('permissions') or {})),
            tools=ToolConfig(**_filter_known_fields(ToolConfig, merged.get('tools') or {})),
            learning=LearningConfig(**_filter_known_fields(LearningConfig, merged.get('learning') or {})),
        )
