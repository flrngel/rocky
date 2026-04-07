from __future__ import annotations

from rocky.config.models import AppConfig, ProviderConfig, ProviderStyle
from rocky.providers.litellm_chat import LiteLLMChatProvider
from rocky.providers.openai_chat import OpenAIChatProvider
from rocky.providers.openai_responses import OpenAIResponsesProvider


class ProviderRegistry:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def _make(self, cfg: ProviderConfig):
        if cfg.style == ProviderStyle.OPENAI_RESPONSES:
            return OpenAIResponsesProvider(cfg)
        if cfg.style == ProviderStyle.LITELLM_CHAT:
            return LiteLLMChatProvider(cfg)
        return OpenAIChatProvider(cfg)

    def primary(self):
        return self._make(self.config.provider())

    def provider_for_task(self, needs_tools: bool = False):
        cfg = self.config.provider()
        if needs_tools and cfg.style == ProviderStyle.OPENAI_RESPONSES:
            return OpenAIChatProvider(cfg)
        return self._make(cfg)

    def healthcheck(self) -> tuple[bool, str]:
        return self.primary().healthcheck()
