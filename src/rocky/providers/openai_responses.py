from __future__ import annotations

from typing import Any, Callable

import httpx

from rocky.config.models import ProviderConfig
from rocky.core.messages import Message
from rocky.providers.base import ProviderResponse


class OpenAIResponsesProvider:
    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", **self.config.extra_headers}
        api_key = self.config.api_key or self.config.resolve_api_key()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        elif "localhost" in self.config.base_url or "127.0.0.1" in self.config.base_url:
            headers["Authorization"] = "Bearer ollama"
        return headers

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self.config.timeout_s, headers=self._headers())

    def _input_string(self, messages: list[Message]) -> str:
        return "\n\n".join(f"{message.role.upper()}: {message.content}" for message in messages)

    def complete(
        self,
        system_prompt: str,
        messages: list[Message],
        stream: bool = False,
        event_handler: Callable[[dict[str, Any]], None] | None = None,
    ) -> ProviderResponse:
        payload = {
            "model": self.config.model,
            "instructions": system_prompt,
            "input": self._input_string(messages),
            "stream": False,
            "store": self.config.store,
        }
        with self._client() as client:
            response = client.post(f"{self.config.base_url}/responses", json=payload)
            response.raise_for_status()
            data = response.json()
        text = data.get("output_text") or ""
        if not text:
            output = data.get("output") or []
            parts: list[str] = []
            for item in output:
                if item.get("type") != "message":
                    continue
                for content in item.get("content", []):
                    if content.get("type") in {"output_text", "text"}:
                        parts.append(content.get("text", ""))
            text = "".join(parts)
        if stream and event_handler and text:
            event_handler({"type": "assistant_chunk", "text": text})
        return ProviderResponse(text=text, usage=data.get("usage") or {}, raw=data)

    def run_with_tools(
        self,
        system_prompt: str,
        messages: list[Message],
        tools: list[dict[str, Any]],
        execute_tool,
        max_rounds: int = 8,
        event_handler: Callable[[dict[str, Any]], None] | None = None,
    ) -> ProviderResponse:
        raise NotImplementedError("Use chat-completions fallback for tool loops")

    def healthcheck(self) -> tuple[bool, str]:
        try:
            with self._client() as client:
                response = client.post(
                    f"{self.config.base_url}/responses",
                    json={"model": self.config.model, "input": "ping", "store": False},
                )
                if response.status_code < 400:
                    return True, "Provider reachable"
                return False, f"Provider returned {response.status_code}"
        except Exception as exc:  # pragma: no cover - network dependent
            return False, str(exc)
