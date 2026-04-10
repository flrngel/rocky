from __future__ import annotations

import json
import time
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from rocky.config.models import ProviderConfig
from rocky.core.messages import Message
from rocky.providers.base import ProviderResponse, sanitize_assistant_text
from rocky.tool_events import (
    MODEL_TEXT_TOTAL_LIMIT,
    ensure_tool_result_event,
    normalize_tool_result_event,
    tool_event_model_text,
    tool_event_summary_text,
    truncate_model_text,
)
from rocky.util.text import extract_json_candidate, safe_json


class OpenAIChatProvider:
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

    def _coerce_dict(self, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            try:
                dumped = model_dump()
                if isinstance(dumped, dict):
                    return dumped
            except Exception:
                pass
        dict_method = getattr(value, "dict", None)
        if callable(dict_method):
            try:
                dumped = dict_method()
                if isinstance(dumped, dict):
                    return dumped
            except Exception:
                pass
        if hasattr(value, "__dict__"):
            return {k: v for k, v in vars(value).items() if not k.startswith("_")}
        return {}

    def _message_text(self, content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, dict):
            if content.get("type") in {"text", "output_text"}:
                return str(content.get("text", ""))
            if "content" in content:
                return self._message_text(content.get("content"))
            if "text" in content:
                return str(content.get("text", ""))
            return safe_json(content)
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") in {"text", "output_text"}:
                        parts.append(str(item.get("text", "")))
                    elif item.get("type") == "refusal":
                        parts.append(str(item.get("refusal", "")))
                    elif "content" in item:
                        parts.append(self._message_text(item.get("content")))
                    elif "text" in item:
                        parts.append(str(item.get("text", "")))
                elif item is not None:
                    parts.append(str(item))
            return "\n".join(part for part in parts if part)
        return str(content)

    def _convert_messages(self, system_prompt: str, messages: list[Message]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        for message in messages:
            payload: dict[str, Any] = {
                "role": message.role,
                "content": None if message.content is None else self._message_text(message.content),
            }
            if message.name:
                payload["name"] = message.name
            if message.tool_call_id:
                payload["tool_call_id"] = message.tool_call_id
            converted.append(payload)
        return converted

    def _extract_choice_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        choices = payload.get("choices") or []
        if not choices:
            return {}
        choice = choices[0]
        if not isinstance(choice, dict):
            choice = self._coerce_dict(choice)
        message = choice.get("message") or {}
        if not isinstance(message, dict):
            message = self._coerce_dict(message)
        return message

    def _extract_content(self, message: dict[str, Any]) -> str:
        content = message.get("content", "")
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, dict):
            return self._message_text(content)
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    if item is not None:
                        parts.append(str(item))
                    continue
                if item.get("type") in {"text", "output_text"}:
                    parts.append(str(item.get("text", "")))
                elif item.get("type") == "refusal":
                    parts.append(str(item.get("refusal", "")))
                elif "content" in item:
                    parts.append(self._message_text(item.get("content")))
                elif "text" in item:
                    parts.append(str(item.get("text", "")))
            return "".join(parts)
        return str(content)

    def _stringify_tool_arguments(self, raw: Any) -> str:
        if raw in (None, ""):
            return "{}"
        if isinstance(raw, (dict, list, tuple, bool, int, float)):
            return safe_json(raw)
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", errors="replace")
        if not isinstance(raw, str):
            dumped = self._coerce_dict(raw)
            if dumped:
                return safe_json(dumped)
            return str(raw)
        candidate = extract_json_candidate(raw)
        return candidate or raw.strip() or "{}"

    def _parse_json_args(self, raw: Any) -> dict[str, Any]:
        if raw in (None, ""):
            return {}
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, list):
            return {"value": raw}
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", errors="replace")
        if not isinstance(raw, str):
            dumped = self._coerce_dict(raw)
            if dumped:
                return dumped
            return {"value": raw}
        candidate = extract_json_candidate(raw) or raw
        try:
            data = json.loads(candidate)
            return data if isinstance(data, dict) else {"value": data}
        except Exception:
            return {"_raw": raw}

    def _normalize_tool_call(self, call: Any, index: int) -> dict[str, Any]:
        payload = self._coerce_dict(call)
        function_payload: Any = payload.get("function")
        if function_payload is None and payload.get("function_call") is not None:
            function_payload = payload.get("function_call")
        if function_payload is not None and not isinstance(function_payload, dict):
            function_payload = self._coerce_dict(function_payload)
        function_payload = function_payload or {}
        tool_name = str(function_payload.get("name") or payload.get("name") or "")
        raw_arguments = function_payload.get("arguments", payload.get("arguments"))
        tool_call_id = str(
            payload.get("id")
            or payload.get("call_id")
            or payload.get("tool_call_id")
            or f"call_{index + 1}"
        )
        return {
            "id": tool_call_id,
            "type": payload.get("type") or "function",
            "function": {
                "name": tool_name,
                "arguments": self._stringify_tool_arguments(raw_arguments),
            },
        }

    def _normalize_tool_calls(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        raw_tool_calls = message.get("tool_calls") or []
        if isinstance(raw_tool_calls, dict):
            raw_tool_calls = [raw_tool_calls]
        calls = [self._normalize_tool_call(call, index) for index, call in enumerate(raw_tool_calls)]
        if calls:
            return calls
        function_call = message.get("function_call")
        if function_call:
            return [self._normalize_tool_call({"function": function_call}, 0)]
        return []

    def _assistant_history_content(self, message: dict[str, Any], tool_calls: list[dict[str, Any]]) -> Any:
        raw_content = message.get("content", "")
        if raw_content is None:
            return None if tool_calls else ""
        text = self._extract_content({"content": raw_content})
        if not text and tool_calls:
            return None
        return text

    def _prepare_tool_output(self, output: Any) -> str:
        if isinstance(output, dict) and output.get("type") == "tool_result":
            return tool_event_model_text(output)
        return tool_event_model_text(normalize_tool_result_event("tool_result", {}, output))

    def _coerce_tool_result_event(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        output: Any,
        tool_call_id: str,
    ) -> dict[str, Any]:
        if isinstance(output, dict) and output.get("type") == "tool_result":
            event = ensure_tool_result_event(output)
            normalized = dict(event)
            normalized["name"] = name
            normalized["arguments"] = arguments
            normalized["id"] = tool_call_id
            normalized["tool_call_id"] = tool_call_id
            normalized["text"] = str(
                normalized.get("model_text") or normalized.get("summary_text") or ""
            )
            return normalized
        return normalize_tool_result_event(
            name,
            arguments,
            output,
            tool_call_id=tool_call_id,
        )

    def _tool_success(self, output: Any) -> bool:
        if isinstance(output, dict) and output.get("type") == "tool_result":
            return bool(output.get("success", True))
        return bool(normalize_tool_result_event("tool_result", {}, output).get("success", True))

    def _post_chat(self, client: httpx.Client, payload: dict[str, Any]) -> dict[str, Any]:
        attempts = 6
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                response = client.post(f"{self.config.base_url}/chat/completions", json=payload)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                last_error = exc
                if status < 500 or attempt == attempts - 1:
                    raise
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt == attempts - 1:
                    raise
            time.sleep(min(0.5 * (2**attempt), 4.0))
        assert last_error is not None
        raise last_error

    def _uses_ollama_compat_reasoning(self) -> bool:
        if self.config.name == "ollama":
            return True
        parsed = urlparse(self.config.base_url)
        return parsed.port == 11434

    def _reasoning_payload(self) -> dict[str, Any]:
        if not self._uses_ollama_compat_reasoning():
            return {}
        if not self.config.thinking:
            return {"think": False}
        if "gpt-oss" in self.config.model.lower():
            return {"think": "medium"}
        return {"think": True}

    def _chat_payload(
        self,
        *,
        messages: list[dict[str, Any]],
        temperature: float,
        stream: bool,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature,
            "stream": stream,
            **self._reasoning_payload(),
        }
        if tools is not None:
            payload["tools"] = tools
        return payload

    def _tool_summary_fallback(self, tool_events: list[dict[str, Any]]) -> str:
        successful_results = [
            ensure_tool_result_event(event)
            for event in tool_events
            if event.get("type") == "tool_result" and event.get("success", True)
        ]
        if not successful_results:
            return ""

        created_paths: list[str] = []
        for event in successful_results:
            if event.get("name") != "write_file":
                continue
            path = next(
                (
                    str(item.get("ref") or "").strip()
                    for item in (event.get("artifacts") or [])
                    if item.get("kind") == "path" and str(item.get("ref") or "").strip()
                ),
                "",
            )
            if path and path not in created_paths:
                created_paths.append(path)

        last = successful_results[-1]
        lines: list[str] = []
        if created_paths:
            joined = ", ".join(f"`{path}`" for path in created_paths[:5])
            lines.append(f"Completed the requested file changes: {joined}.")
        if last.get("name") == "run_shell_command":
            command = ""
            stdout = ""
            stderr = ""
            for fact in last.get("facts") or []:
                if fact.get("kind") == "command" and not command:
                    command = str(fact.get("command") or "").strip()
                elif fact.get("kind") == "stdout" and not stdout:
                    stdout = str(fact.get("stdout") or "").strip()
                elif fact.get("kind") == "stderr" and not stderr:
                    stderr = str(fact.get("stderr") or "").strip()
            if command:
                lines.append(f"Verified with `{command}`.")
            if stdout:
                lines.append(f"Output:\n```text\n{stdout[:2000]}\n```")
            elif stderr:
                lines.append(f"Command stderr:\n```text\n{stderr[:2000]}\n```")
        elif last.get("name") == "read_file":
            path = next(
                (
                    str(item.get("ref") or "").strip()
                    for item in (last.get("artifacts") or [])
                    if item.get("kind") == "path" and str(item.get("ref") or "").strip()
                ),
                "",
            )
            if path:
                lines.append(f"Verified the resulting file `{path}`.")
        elif summary := tool_event_summary_text(last):
            lines.append(summary)
        return "\n\n".join(lines).strip()

    def _forced_final_response(
        self,
        client: httpx.Client,
        conversation: list[dict[str, Any]],
        usage: dict[str, Any],
        raw_rounds: list[dict[str, Any]],
        tool_events: list[dict[str, Any]],
        event_handler: Callable[[dict[str, Any]], None] | None = None,
    ) -> ProviderResponse:
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
            followup = [
                *conversation,
                {
                    "role": "user",
                    "content": prompt,
                },
            ]
            data = self._post_chat(
                client,
                self._chat_payload(messages=followup, temperature=0, stream=False),
            )
            raw_rounds.append(data)
            usage = data.get("usage") or usage
            message = self._extract_choice_message(data)
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

    def complete(
        self,
        system_prompt: str,
        messages: list[Message],
        stream: bool = False,
        event_handler: Callable[[dict[str, Any]], None] | None = None,
    ) -> ProviderResponse:
        payload = self._chat_payload(
            messages=self._convert_messages(system_prompt, messages),
            temperature=self.config.temperature,
            stream=bool(stream and event_handler),
        )
        if stream and event_handler:
            try:
                return self._stream_complete(payload, event_handler)
            except Exception:
                pass
        with self._client() as client:
            response = client.post(
                f"{self.config.base_url}/chat/completions",
                json={**payload, "stream": False},
            )
            response.raise_for_status()
            data = response.json()
        message = self._extract_choice_message(data)
        text = sanitize_assistant_text(self._extract_content(message))
        if stream and event_handler and text:
            event_handler({"type": "assistant_chunk", "text": text})
        return ProviderResponse(text=text, usage=data.get("usage") or {}, raw=data)

    def _stream_complete(
        self,
        payload: dict[str, Any],
        event_handler: Callable[[dict[str, Any]], None],
    ) -> ProviderResponse:
        full_text = ""
        usage: dict[str, Any] = {}
        with self._client() as client:
            with client.stream(
                "POST",
                f"{self.config.base_url}/chat/completions",
                json=payload,
            ) as response:
                response.raise_for_status()
                for raw_line in response.iter_lines():
                    line = (raw_line or "").strip()
                    if not line.startswith("data:"):
                        continue
                    data_text = line[len("data:") :].strip()
                    if data_text == "[DONE]":
                        break
                    event = json.loads(data_text)
                    if event.get("usage"):
                        usage = event["usage"]
                    delta = ((event.get("choices") or [{}])[0].get("delta") or {}).get("content")
                    if isinstance(delta, str) and delta:
                        text = sanitize_assistant_text(delta, strip=False)
                        full_text += text
                        if text:
                            event_handler({"type": "assistant_chunk", "text": text})
                    elif isinstance(delta, list):
                        text = "".join(
                            str(item.get("text", ""))
                            for item in delta
                            if isinstance(item, dict)
                        )
                        text = sanitize_assistant_text(text, strip=False)
                        if text:
                            full_text += text
                            event_handler({"type": "assistant_chunk", "text": text})
        return ProviderResponse(text=full_text, usage=usage, raw={"streamed": True})

    def run_with_tools(
        self,
        system_prompt: str,
        messages: list[Message],
        tools: list[dict[str, Any]],
        execute_tool,
        max_rounds: int = 8,
        event_handler: Callable[[dict[str, Any]], None] | None = None,
    ) -> ProviderResponse:
        conversation = self._convert_messages(system_prompt, messages)
        tool_events: list[dict[str, Any]] = []
        usage: dict[str, Any] = {}
        raw_rounds: list[dict[str, Any]] = []
        remaining_model_chars = MODEL_TEXT_TOTAL_LIMIT
        with self._client() as client:
            for _ in range(max_rounds):
                payload = {
                    **self._chat_payload(
                        messages=conversation,
                        tools=tools,
                        temperature=self.config.temperature,
                        stream=False,
                    )
                }
                data = self._post_chat(client, payload)
                raw_rounds.append(data)
                usage = data.get("usage") or usage
                message = self._extract_choice_message(data)
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
                        return ProviderResponse(
                            text=text,
                            usage=usage,
                            raw={"rounds": raw_rounds},
                            tool_events=tool_events,
                        )
                    break
                for call in tool_calls:
                    function = call.get("function") or {}
                    name = str(function.get("name", ""))
                    arguments = self._parse_json_args(function.get("arguments"))
                    tool_call_id = str(call.get("id") or "")
                    call_event = {
                        "type": "tool_call",
                        "id": tool_call_id,
                        "tool_call_id": tool_call_id,
                        "name": name,
                        "arguments": arguments,
                    }
                    if event_handler:
                        event_handler(call_event)
                    tool_events.append(call_event)
                    result_event = self._coerce_tool_result_event(
                        name=name,
                        arguments=arguments,
                        output=execute_tool(name, arguments),
                        tool_call_id=tool_call_id,
                    )
                    tool_output = truncate_model_text(
                        tool_event_model_text(result_event),
                        remaining_model_chars,
                    )
                    remaining_model_chars = max(0, remaining_model_chars - len(tool_output))
                    conversation.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "name": name,
                            "content": tool_output,
                        }
                    )
                    tool_events.append(result_event)
                    if event_handler:
                        event_handler(result_event)
            return self._forced_final_response(
                client=client,
                conversation=conversation,
                usage=usage,
                raw_rounds=raw_rounds,
                tool_events=tool_events,
                event_handler=event_handler,
            )

    def healthcheck(self) -> tuple[bool, str]:
        try:
            with self._client() as client:
                response = client.post(
                    f"{self.config.base_url}/chat/completions",
                    json=self._chat_payload(
                        messages=[{"role": "user", "content": "ping"}],
                        temperature=self.config.temperature,
                        stream=False,
                    ),
                )
                if response.status_code < 400:
                    return True, "Provider reachable"
                return False, f"Provider returned {response.status_code}"
        except Exception as exc:  # pragma: no cover - network dependent
            return False, str(exc)
