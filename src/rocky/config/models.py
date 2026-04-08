from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ProviderStyle(str, Enum):
    OPENAI_CHAT = 'openai_chat'
    OPENAI_RESPONSES = 'openai_responses'
    LITELLM_CHAT = 'litellm_chat'


@dataclass(slots=True)
class ProviderConfig:
    name: str
    style: ProviderStyle = ProviderStyle.OPENAI_CHAT
    base_url: str = 'http://localhost:11434/v1'
    api_key_env: str | None = 'OLLAMA_API_KEY'
    api_key: str | None = None
    model: str = 'llama3.2'
    thinking: bool = True
    temperature: float = 0.2
    timeout_s: int = 120
    store: bool = False
    extra_headers: dict[str, str] = field(default_factory=dict)
    reasoning_effort: str | None = None
    tool_choice: str | None = None
    extra_body: dict[str, Any] = field(default_factory=dict)

    def resolve_api_key(self) -> str | None:
        return self.api_key or (os.getenv(self.api_key_env) if self.api_key_env else None)


@dataclass(slots=True)
class PermissionConfig:
    mode: str = 'bypass'
    allow: dict[str, list[str]] = field(default_factory=dict)
    deny: dict[str, list[str]] = field(default_factory=dict)


@dataclass(slots=True)
class ToolConfig:
    max_read_chars: int = 12000
    max_tool_output_chars: int = 12000
    shell_timeout_s: int = 60
    python_timeout_s: int = 60


@dataclass(slots=True)
class LearningConfig:
    enabled: bool = True
    auto_publish_project_skills: bool = True
    slow_learner_enabled: bool = True


@dataclass(slots=True)
class AppConfig:
    active_provider: str = 'litellm_local'
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    permissions: PermissionConfig = field(default_factory=PermissionConfig)
    tools: ToolConfig = field(default_factory=ToolConfig)
    learning: LearningConfig = field(default_factory=LearningConfig)

    def provider(self, name: str | None = None) -> ProviderConfig:
        selected = name or self.active_provider
        if selected not in self.providers:
            raise KeyError(f'Unknown provider: {selected}')
        return self.providers[selected]

    @staticmethod
    def default() -> 'AppConfig':
        return AppConfig(
            active_provider='litellm_local',
            providers={
                'litellm_local': ProviderConfig(
                    name='litellm_local',
                    style=ProviderStyle.LITELLM_CHAT,
                    base_url='http://localhost:4000',
                    api_key_env='LITELLM_API_KEY',
                    model='ollama_chat/qwen3.5:4b',
                    thinking=True,
                    reasoning_effort='medium',
                    store=False,
                ),
                'ollama': ProviderConfig(
                    name='ollama',
                    style=ProviderStyle.OPENAI_CHAT,
                    base_url='http://localhost:11434/v1',
                    api_key_env='OLLAMA_API_KEY',
                    model='llama3.2',
                    thinking=True,
                    store=False,
                ),
                'openai': ProviderConfig(
                    name='openai',
                    style=ProviderStyle.OPENAI_RESPONSES,
                    base_url='https://api.openai.com/v1',
                    api_key_env='OPENAI_API_KEY',
                    model='gpt-5.2',
                    thinking=True,
                    reasoning_effort='medium',
                    store=False,
                ),
            },
        )


def merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_dict(result[key], value)
        else:
            result[key] = value
    return result
