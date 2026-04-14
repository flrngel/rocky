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
    context_window: int | None = None

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
    auto_self_reflection_enabled: bool = True
    slow_learner_enabled: bool = False


@dataclass(slots=True)
class RetrievalConfig:
    """Tunable knobs for `LedgerRetriever` ranking.

    Defaults match the pre-Phase-3 hard-coded module constants so a
    default-constructed `RetrievalConfig` produces bit-identical behavior
    to the legacy retriever. Meta-variants overlay these knobs at runtime
    without mutating module state (see `rocky.meta.overlay`).
    """

    top_k_limit: int = 8
    authority_weight: dict[str, int] = field(
        default_factory=lambda: {
            'teacher': 4,
            'evidence_backed': 3,
            'self_generated': 2,
        }
    )
    promotion_weight: dict[str, int] = field(
        default_factory=lambda: {
            'promoted': 3,
            'validated': 2,
            'candidate': 1,
            'stale': -1,
            'rejected': -3,
        }
    )
    ts_exact_score: float = 6.0
    ts_prefix_score: float = 3.0
    tf_score: float = 2.0
    thread_relevance_cap: int = 4
    prompt_overlap_cap: int = 4
    prompt_overlap_multiplier: float = 1.5
    trigger_literal_score: float = 6.0
    fc_score: float = 3.0
    evidence_quality_cap: int = 4
    recency_score: float = 1.0
    conflict_status_score: float = 0.0
    prior_success_cap: int = 4
    require_signal: bool = True


@dataclass(slots=True)
class PackingConfig:
    """Tunable knobs for `build_system_prompt` learning-pack blocks.

    Defaults match the pre-Phase-3 hard-coded char budgets and caps so a
    default-constructed `PackingConfig` produces bit-identical packer
    output. Meta-variants overlay these knobs at runtime.
    """

    workspace_brief_budget: int = 2000
    retrospective_body_budget: int = 400
    student_profile_budget: int = 4000
    legacy_note_budget: int = 4000
    hard_lines_cap: int = 12
    procedural_cap: int = 6
    retro_cap: int = 3
    style_cue_cap: int = 3
    repeat_step_cap: int = 6
    avoid_step_cap: int = 6
    workflow_step_body_budget: int = 240
    user_prompt_budget: int = 2000


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
                    context_window=32768,
                ),
                'ollama': ProviderConfig(
                    name='ollama',
                    style=ProviderStyle.OPENAI_CHAT,
                    base_url='http://localhost:11434/v1',
                    api_key_env='OLLAMA_API_KEY',
                    model='llama3.2',
                    thinking=True,
                    store=False,
                    context_window=131072,
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
                    context_window=128000,
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
