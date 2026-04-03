from __future__ import annotations

import json
import time
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from rocky.config.models import ProviderConfig
from rocky.core.messages import Message
from rocky.providers.base import ProviderResponse, sanitize_assistant_text


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

    def _message_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") in {"text", "output_text"}:
                        parts.append(str(item.get("text", "")))
                    elif "content" in item:
                        parts.append(str(item.get("content", "")))
                else:
                    parts.append(str(item))
            return "\n".join(parts)
        return str(content)

    def _convert_messages(self, system_prompt: str, messages: list[Message]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        for message in messages:
            payload: dict[str, Any] = {
                "role": message.role,
                "content": self._message_text(message.content),
            }
            if message.name:
                payload["name"] = message.name
            if message.tool_call_id:
                payload["tool_call_id"] = message.tool_call_id
            converted.append(payload)
        return converted

    def _extract_content(self, message: dict[str, Any]) -> str:
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    parts.append(str(item))
                    continue
                if item.get("type") in {"text", "output_text"}:
                    parts.append(str(item.get("text", "")))
                elif item.get("type") == "refusal":
                    parts.append(str(item.get("refusal", "")))
            return "".join(parts)
        return str(content)

    def _parse_json_args(self, raw: str | None) -> dict[str, Any]:
        if not raw:
            return {}
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {"value": data}
        except Exception:
            return {"_raw": raw}

    def _tool_success(self, output: str) -> bool:
        try:
            payload = json.loads(output)
            return bool(payload.get("success", True))
        except Exception:
            return '"success": false' not in output.lower()

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
            event
            for event in tool_events
            if event.get("type") == "tool_result" and event.get("success", True)
        ]
        if not successful_results:
            return ""

        created_paths: list[str] = []
        for event in successful_results:
            if event.get("name") != "write_file":
                continue
            try:
                payload = json.loads(str(event.get("text", "")))
            except Exception:
                continue
            data = payload.get("data") or {}
            path = str(data.get("path", "")).strip()
            if path and path not in created_paths:
                created_paths.append(path)

        last = successful_results[-1]
        try:
            payload = json.loads(str(last.get("text", "")))
        except Exception:
            payload = {}
        data = payload.get("data") if isinstance(payload, dict) else {}
        lines: list[str] = []
        if created_paths:
            joined = ", ".join(f"`{path}`" for path in created_paths[:5])
            lines.append(f"Completed the requested file changes: {joined}.")
        if last.get("name") == "run_shell_command" and isinstance(data, dict):
            command = str(data.get("command", "")).strip()
            stdout = str(data.get("stdout", "")).strip()
            stderr = str(data.get("stderr", "")).strip()
            if command:
                lines.append(f"Verified with `{command}`.")
            if stdout:
                lines.append(f"Output:\n```text\n{stdout[:2000]}\n```")
            elif stderr:
                lines.append(f"Command stderr:\n```text\n{stderr[:2000]}\n```")
        elif last.get("name") == "read_file" and isinstance(data, dict):
            path = str(data.get("path", "")).strip()
            if path:
                lines.append(f"Verified the resulting file `{path}`.")
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
            message = ((data.get("choices") or [{}])[0]).get("message") or {}
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
        message = ((data.get("choices") or [{}])[0]).get("message") or {}
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
                message = ((data.get("choices") or [{}])[0]).get("message") or {}
                assistant_record: dict[str, Any] = {
                    "role": "assistant",
                    "content": self._extract_content(message),
                }
                if message.get("tool_calls"):
                    assistant_record["tool_calls"] = message["tool_calls"]
                conversation.append(assistant_record)
                tool_calls = message.get("tool_calls") or []
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
                    if event_handler:
                        event_handler({"type": "tool_call", "name": name, "arguments": arguments})
                    tool_events.append(
                        {
                            "type": "tool_call",
                            "id": call.get("id"),
                            "name": name,
                            "arguments": arguments,
                        }
                    )
                    tool_output = execute_tool(name, arguments)
                    conversation.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id"),
                            "name": name,
                            "content": tool_output,
                        }
                    )
                    tool_events.append(
                        {
                            "type": "tool_result",
                            "id": call.get("id"),
                            "name": name,
                            "arguments": arguments,
                            "text": tool_output,
                            "success": self._tool_success(tool_output),
                        }
                    )
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
