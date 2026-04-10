from __future__ import annotations

from typing import Any, Callable

from rocky.core.messages import Message
from rocky.providers.base import ProviderResponse, sanitize_assistant_text
from rocky.providers.openai_chat import OpenAIChatProvider
from rocky.tool_events import MODEL_TEXT_TOTAL_LIMIT, tool_event_model_text, truncate_model_text


class LiteLLMChatProvider(OpenAIChatProvider):
    """LiteLLM-backed chat provider.

    Falls back to the existing OpenAI-compatible chat implementation when LiteLLM
    is not installed so test environments can still exercise the rest of Rocky.
    """

    def _litellm(self):
        try:
            import litellm  # type: ignore
        except Exception:
            return None
        return litellm

    def _extract_message(self, response: Any) -> dict[str, Any]:
        payload = self._coerce_dict(response)
        choices = payload.get("choices") or []
        if not choices:
            return {}
        choice = choices[0]
        if not isinstance(choice, dict):
            choice = self._coerce_dict(choice)
        message = choice.get("message") or {}
        if not isinstance(message, dict):
            message = self._coerce_dict(message)
        tool_calls = self._normalize_tool_calls(message)
        if tool_calls:
            message["tool_calls"] = tool_calls
        return message

    def _extract_stream_delta(self, chunk: Any) -> tuple[str, dict[str, Any], dict[str, Any]]:
        payload = self._coerce_dict(chunk)
        usage = payload.get("usage") or {}
        choices = payload.get("choices") or []
        if not choices:
            return "", {}, usage
        choice = choices[0]
        if not isinstance(choice, dict):
            choice = self._coerce_dict(choice)
        delta = choice.get("delta") or {}
        if not isinstance(delta, dict):
            delta = self._coerce_dict(delta)
        text = self._extract_content({"content": delta.get("content")})
        tool_calls = self._normalize_tool_calls(delta)
        if tool_calls:
            delta["tool_calls"] = tool_calls
        return text, delta, usage

    def _litellm_kwargs(
        self,
        *,
        messages: list[dict[str, Any]],
        stream: bool,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "stream": stream,
            "drop_params": True,
            "timeout": self.config.timeout_s,
            "api_base": self.config.base_url,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        api_key = self.config.api_key or self.config.resolve_api_key()
        if api_key:
            kwargs["api_key"] = api_key
        elif "localhost" in self.config.base_url or "127.0.0.1" in self.config.base_url:
            kwargs["api_key"] = "ollama"
        if self.config.extra_headers:
            kwargs["extra_headers"] = self.config.extra_headers
        extra_body = dict(self.config.extra_body or {})
        if self.config.model.startswith("ollama_chat/"):
            if not self.config.thinking:
                extra_body.setdefault("think", False)
            else:
                extra_body.setdefault("think", self.config.reasoning_effort or True)
        if extra_body:
            kwargs["extra_body"] = extra_body
        if self.config.reasoning_effort:
            kwargs["reasoning_effort"] = self.config.reasoning_effort
        if tools is not None:
            kwargs["tools"] = tools
        if self.config.tool_choice and tools is not None:
            kwargs["tool_choice"] = self.config.tool_choice
        return kwargs

    def complete(
        self,
        system_prompt: str,
        messages: list[Message],
        stream: bool = False,
        event_handler: Callable[[dict[str, Any]], None] | None = None,
    ) -> ProviderResponse:
        litellm = self._litellm()
        if litellm is None:
            return super().complete(system_prompt, messages, stream=stream, event_handler=event_handler)
        payload_messages = self._convert_messages(system_prompt, messages)
        kwargs = self._litellm_kwargs(
            messages=payload_messages,
            stream=bool(stream and event_handler),
            temperature=self.config.temperature,
        )
        if stream and event_handler:
            full_text = ""
            usage: dict[str, Any] = {}
            raw_chunks: list[dict[str, Any]] = []
            for chunk in litellm.completion(**kwargs):
                raw_chunks.append(self._coerce_dict(chunk))
                delta_text, _, delta_usage = self._extract_stream_delta(chunk)
                if delta_usage:
                    usage = delta_usage
                clean = sanitize_assistant_text(delta_text, strip=False)
                if clean:
                    full_text += clean
                    event_handler({"type": "assistant_chunk", "text": clean})
            return ProviderResponse(text=full_text, usage=usage, raw={"streamed": True, "chunks": raw_chunks})
        response = litellm.completion(**{**kwargs, "stream": False})
        raw = self._coerce_dict(response)
        message = self._extract_message(response)
        text = sanitize_assistant_text(self._extract_content(message))
        if stream and event_handler and text:
            event_handler({"type": "assistant_chunk", "text": text})
        return ProviderResponse(text=text, usage=raw.get("usage") or {}, raw=raw)

    def _forced_final_response_litellm(
        self,
        conversation: list[dict[str, Any]],
        usage: dict[str, Any],
        raw_rounds: list[dict[str, Any]],
        tool_events: list[dict[str, Any]],
        event_handler: Callable[[dict[str, Any]], None] | None = None,
    ) -> ProviderResponse:
        litellm = self._litellm()
        if litellm is None:
            return ProviderResponse(
                text=self._tool_summary_fallback(tool_events) or "Tool loop ended without a final assistant response.",
                usage=usage,
                raw={"rounds": raw_rounds, "forced_final": True},
                tool_events=tool_events,
            )
        prompts = [
            "Use the tool results already collected and answer the original request now. Do not call any more tools.",
            (
                "Your previous reply was empty or incomplete. Produce the final answer now using only the tool "
                "results already in the conversation. Do not call any more tools. If the user requested JSON or "
                "structured output, return valid JSON directly with no prose or markdown."
            ),
        ]
        text = ""
        for prompt in prompts:
            followup = [*conversation, {"role": "user", "content": prompt}]
            response = litellm.completion(
                **self._litellm_kwargs(
                    messages=followup,
                    stream=False,
                    temperature=0,
                )
            )
            raw = self._coerce_dict(response)
            raw_rounds.append(raw)
            usage = raw.get("usage") or usage
            message = self._extract_message(response)
            text = sanitize_assistant_text(self._extract_content(message))
            if text:
                break
            conversation = followup
        text = text or self._tool_summary_fallback(tool_events) or "Tool loop ended without a final assistant response."
        if event_handler and text:
            event_handler({"type": "assistant_chunk", "text": text})
        return ProviderResponse(
            text=text,
            usage=usage,
            raw={"rounds": raw_rounds, "forced_final": True},
            tool_events=tool_events,
        )

    def run_with_tools(
        self,
        system_prompt: str,
        messages: list[Message],
        tools: list[dict[str, Any]],
        execute_tool,
        max_rounds: int = 8,
        event_handler: Callable[[dict[str, Any]], None] | None = None,
    ) -> ProviderResponse:
        litellm = self._litellm()
        if litellm is None:
            return super().run_with_tools(
                system_prompt,
                messages,
                tools,
                execute_tool,
                max_rounds=max_rounds,
                event_handler=event_handler,
            )
        conversation = self._convert_messages(system_prompt, messages)
        tool_events: list[dict[str, Any]] = []
        usage: dict[str, Any] = {}
        raw_rounds: list[dict[str, Any]] = []
        remaining_model_chars = MODEL_TEXT_TOTAL_LIMIT
        for _ in range(max_rounds):
            response = litellm.completion(
                **self._litellm_kwargs(
                    messages=conversation,
                    tools=tools,
                    stream=False,
                    temperature=self.config.temperature,
                )
            )
            raw = self._coerce_dict(response)
            raw_rounds.append(raw)
            usage = raw.get("usage") or usage
            message = self._extract_message(response)
            tool_calls = self._normalize_tool_calls(message)
            assistant_record: dict[str, Any] = {
                "role": "assistant",
                "content": self._assistant_history_content(message, tool_calls),
            }
            if tool_calls:
                assistant_record["tool_calls"] = tool_calls
            conversation.append(assistant_record)
            if not tool_calls:
                text = sanitize_assistant_text(self._extract_content(message))
                if text:
                    if event_handler:
                        event_handler({"type": "assistant_chunk", "text": text})
                    return ProviderResponse(text=text, usage=usage, raw={"rounds": raw_rounds}, tool_events=tool_events)
                return self._forced_final_response_litellm(conversation, usage, raw_rounds, tool_events, event_handler)

            for call in tool_calls:
                function_payload = call.get("function") or {}
                tool_name = str(function_payload.get("name") or call.get("name") or "")
                raw_arguments = function_payload.get("arguments")
                arguments = self._parse_json_args(raw_arguments)
                tool_call_id = str(call.get("id") or "")
                call_event = {
                    "type": "tool_call",
                    "id": tool_call_id,
                    "tool_call_id": tool_call_id,
                    "name": tool_name,
                    "arguments": arguments,
                }
                tool_events.append(call_event)
                if event_handler:
                    event_handler(call_event)
                result_event = self._coerce_tool_result_event(
                    name=tool_name,
                    arguments=arguments,
                    output=execute_tool(tool_name, arguments),
                    tool_call_id=tool_call_id,
                )
                output = truncate_model_text(
                    tool_event_model_text(result_event),
                    remaining_model_chars,
                )
                remaining_model_chars = max(0, remaining_model_chars - len(output))
                tool_events.append(result_event)
                if event_handler:
                    event_handler(result_event)
                conversation.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "name": tool_name,
                        "content": output,
                    }
                )
        return self._forced_final_response_litellm(conversation, usage, raw_rounds, tool_events, event_handler)

    def healthcheck(self) -> tuple[bool, str]:
        litellm = self._litellm()
        if litellm is None:
            return super().healthcheck()
        try:
            response = litellm.completion(
                **self._litellm_kwargs(
                    messages=[{"role": "user", "content": "ping"}],
                    stream=False,
                    temperature=0,
                )
            )
            raw = self._coerce_dict(response)
            if raw.get("choices") or raw.get("id"):
                return True, "LiteLLM provider reachable"
            return True, "LiteLLM completion returned without obvious error"
        except Exception as exc:  # pragma: no cover - network dependent
            return False, str(exc)
