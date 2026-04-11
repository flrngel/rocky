from __future__ import annotations

import json
import os
from pathlib import Path
import shutil

import httpx

from rocky.app import RockyRuntime
from rocky.core.messages import Message
from rocky.core.router import Lane, RouteDecision, TaskClass
from rocky.providers.base import ProviderResponse
from rocky.tools.base import ToolResult


class _OkProvider:
    def __init__(self) -> None:
        self.calls: list[list[Message]] = []
        self.tool_calls: list[dict] = []

    def complete(self, system_prompt, messages, stream=False, event_handler=None) -> ProviderResponse:
        self.calls.append(messages)
        return ProviderResponse(text="ok")

    def run_with_tools(self, system_prompt, messages, tools, execute_tool, max_rounds=8, event_handler=None) -> ProviderResponse:
        self.tool_calls.append({"messages": messages, "tools": tools, "max_rounds": max_rounds})
        return ProviderResponse(
            text="runtime inspected",
            raw={"rounds": []},
            tool_events=[
                {
                    "type": "tool_result",
                    "name": "run_shell_command",
                    "arguments": {"command": "which -a python python3 && python3 --version", "timeout_s": 5},
                    "text": "{}",
                    "success": True,
                }
            ],
        )


class _ConversationalToolProvider:
    def __init__(self) -> None:
        self.calls: list[list[Message]] = []
        self.tool_calls: list[dict] = []

    def complete(self, system_prompt, messages, stream=False, event_handler=None) -> ProviderResponse:
        self.calls.append(messages)
        return ProviderResponse(text="ok")

    def run_with_tools(self, system_prompt, messages, tools, execute_tool, max_rounds=8, event_handler=None) -> ProviderResponse:
        self.tool_calls.append({"messages": messages, "tools": tools, "max_rounds": max_rounds})
        return ProviderResponse(text="ok", raw={"rounds": []}, tool_events=[])


class _StreamingToolEventProvider:
    def __init__(self) -> None:
        self.tool_calls: list[dict] = []

    def complete(self, system_prompt, messages, stream=False, event_handler=None) -> ProviderResponse:
        return ProviderResponse(text="ok")

    def run_with_tools(self, system_prompt, messages, tools, execute_tool, max_rounds=8, event_handler=None) -> ProviderResponse:
        self.tool_calls.append({"messages": messages, "tools": tools, "max_rounds": max_rounds})
        call_event = {
            "type": "tool_call",
            "id": "call_1",
            "tool_call_id": "call_1",
            "name": "run_shell_command",
            "arguments": {"command": "python3 --version", "timeout_s": 5},
        }
        if event_handler:
            event_handler(call_event)
        result = execute_tool("run_shell_command", {"command": "python3 --version", "timeout_s": 5})
        result_event = {
            "type": "tool_result",
            "name": "run_shell_command",
            "tool_call_id": "call_1",
            "arguments": {"command": "python3 --version", "timeout_s": 5},
            "text": result,
            "success": True,
        }
        if event_handler:
            event_handler(result_event)
        return ProviderResponse(text="runtime inspected", raw={"rounds": []}, tool_events=[call_event, result_event])


class _ShellJsonProvider:
    def __init__(self) -> None:
        self.complete_calls: list[list[Message]] = []
        self.tool_calls: list[dict] = []

    def complete(self, system_prompt, messages, stream=False, event_handler=None) -> ProviderResponse:
        self.complete_calls.append(messages)
        return ProviderResponse(text="chat")

    def run_with_tools(self, system_prompt, messages, tools, execute_tool, max_rounds=8, event_handler=None) -> ProviderResponse:
        self.tool_calls.append({"messages": messages, "tools": tools, "max_rounds": max_rounds})
        shell_result = execute_tool("run_shell_command", {"command": "printf '[]\\n'", "timeout_s": 5})
        return ProviderResponse(
            text="[]",
            raw={"rounds": []},
            tool_events=[
                {
                    "type": "tool_result",
                    "name": "run_shell_command",
                    "arguments": {"command": "printf '[]\\n'", "timeout_s": 5},
                    "text": shell_result,
                    "success": True,
                }
            ],
        )


class _FencedShellJsonProvider(_ShellJsonProvider):
    def run_with_tools(self, system_prompt, messages, tools, execute_tool, max_rounds=8, event_handler=None) -> ProviderResponse:
        self.tool_calls.append({"messages": messages, "tools": tools, "max_rounds": max_rounds})
        shell_result = execute_tool("run_shell_command", {"command": "printf '[]\\n'", "timeout_s": 5})
        return ProviderResponse(
            text="```json\n[]\n```",
            raw={"rounds": []},
            tool_events=[
                {
                    "type": "tool_result",
                    "name": "run_shell_command",
                    "arguments": {"command": "printf '[]\\n'", "timeout_s": 5},
                    "text": shell_result,
                    "success": True,
                }
            ],
        )


class _ExtractionProvider:
    def __init__(self) -> None:
        self.tool_calls: list[dict] = []

    def run_with_tools(self, system_prompt, messages, tools, execute_tool, max_rounds=8, event_handler=None) -> ProviderResponse:
        self.tool_calls.append({"messages": messages, "tools": tools, "max_rounds": max_rounds})
        return ProviderResponse(
            text='Done.\n```json\n{"rows": 2, "fields": ["name"]}\n```',
            raw={"rounds": []},
            tool_events=[
                {
                    "type": "tool_result",
                    "name": "read_file",
                    "arguments": {"path": "data/people.jsonl"},
                    "text": "{}",
                    "success": True,
                }
            ],
        )


class _RepairingExtractionProvider:
    def __init__(self) -> None:
        self.tool_calls: list[dict] = []
        self.complete_calls: list[list[Message]] = []

    def run_with_tools(self, system_prompt, messages, tools, execute_tool, max_rounds=8, event_handler=None) -> ProviderResponse:
        self.tool_calls.append({"messages": messages, "tools": tools, "max_rounds": max_rounds})
        return ProviderResponse(
            text="I found 2 rows and the fields are name and role.",
            raw={"rounds": []},
            tool_events=[
                {
                    "type": "tool_result",
                    "name": "run_shell_command",
                    "arguments": {"command": "printf '{\"rows\": 2, \"fields\": [\"name\", \"role\"]}\\n'", "timeout_s": 5},
                    "text": '{"success": true, "data": {"command": "printf", "stdout": "{\\"rows\\": 2, \\"fields\\": [\\"name\\", \\"role\\"]}"}}',
                    "success": True,
                }
            ],
        )

    def complete(self, system_prompt, messages, stream=False, event_handler=None) -> ProviderResponse:
        self.complete_calls.append(messages)
        return ProviderResponse(text='{"rows": 2, "fields": ["name", "role"]}')


class _RepairingProjectShellJsonProvider:
    def __init__(self) -> None:
        self.tool_calls: list[dict] = []
        self.complete_calls: list[list[Message]] = []

    def run_with_tools(self, system_prompt, messages, tools, execute_tool, max_rounds=8, event_handler=None) -> ProviderResponse:
        self.tool_calls.append({"messages": messages, "tools": tools, "max_rounds": max_rounds})
        shell_result = execute_tool("run_shell_command", {"command": "printf 'search results\\n'", "timeout_s": 5})
        return ProviderResponse(
            text=(
                "```json\n[\n"
                '  {"id":"1","product_name":"Oban Port Cask 15 Years","confidence":"confirmed"},\n'
                '  {"id":"2",% "product_name":"Oban Cask Strength 15 Years","confidence":"uncertain"}\n'
                "]\n```"
            ),
            raw={"rounds": []},
            tool_events=[
                {
                    "type": "tool_result",
                    "name": "run_shell_command",
                    "arguments": {"command": "printf 'search results\\n'", "timeout_s": 5},
                    "text": shell_result,
                    "success": True,
                }
            ],
        )

    def complete(self, system_prompt, messages, stream=False, event_handler=None) -> ProviderResponse:
        self.complete_calls.append(messages)
        return ProviderResponse(
            text='[{"id":"1","product_name":"Oban Port Cask 15 Years","confidence":"confirmed"}]'
        )


class _LearnedConstraintRepairProvider:
    def __init__(self) -> None:
        self.tool_calls: list[dict] = []
        self.complete_calls: list[list[Message]] = []

    def run_with_tools(self, system_prompt, messages, tools, execute_tool, max_rounds=8, event_handler=None) -> ProviderResponse:
        self.tool_calls.append({"messages": messages, "tools": tools, "max_rounds": max_rounds})
        shell_result = execute_tool("run_shell_command", {"command": "printf 'search results\\n'", "timeout_s": 5})
        if len(self.tool_calls) == 1:
            text = (
                "["
                '{"id":"1","product_name":"Oban Port Cask 15 Years","confidence":"confirmed"},'
                '{"id":"2","product_name":"Oban Cask Strength 15 Years","confidence":"uncertain"}'
                "]"
            )
            raw = {"rounds": ["initial"]}
        else:
            text = '[{"id":"1","product_name":"Oban Port Cask 15 Years","confidence":"confirmed"}]'
            raw = {"rounds": ["retry"]}
        return ProviderResponse(
            text=text,
            raw=raw,
            tool_events=[
                {
                    "type": "tool_result",
                    "name": "run_shell_command",
                    "arguments": {"command": "printf 'search results\\n'", "timeout_s": 5},
                    "text": shell_result,
                    "success": True,
                }
            ],
        )

    def complete(self, system_prompt, messages, stream=False, event_handler=None) -> ProviderResponse:
        self.complete_calls.append(messages)
        if len(self.complete_calls) == 1:
            return ProviderResponse(
                text=(
                    '{"status":"fail","reason":"Learned constraint violation: the final deliverable still includes a '
                    'candidate that the learned rule says to exclude. Remove excluded candidates instead of '
                    'keeping them as uncertain placeholders.","violated_rules":["Include cask-strength variants '
                    'in the final deliverable for a plain product query."]}'
                )
            )
        if len(self.complete_calls) == 2:
            return ProviderResponse(
                text='[{"id":"1","product_name":"Oban Port Cask 15 Years","confidence":"confirmed"}]'
            )
        return ProviderResponse(
            text='{"status":"pass","reason":"The final deliverable obeys the retrieved learned constraints.","violated_rules":[]}'
        )


class _HiddenToolProvider:
    def __init__(self) -> None:
        self.tool_calls: list[dict] = []

    def run_with_tools(self, system_prompt, messages, tools, execute_tool, max_rounds=8, event_handler=None) -> ProviderResponse:
        self.tool_calls.append({"messages": messages, "tools": tools, "max_rounds": max_rounds})
        hidden = execute_tool("write_file", {"path": "oops.txt", "content": "nope"})
        return ProviderResponse(
            text="checked hidden tool",
            raw={"rounds": []},
            tool_events=[
                {
                    "type": "tool_result",
                    "name": "write_file",
                    "arguments": {"path": "oops.txt", "content": "nope"},
                    "text": hidden,
                    "success": False,
                }
            ],
        )


class _ShellInspectionHiddenFilesystemProvider:
    def __init__(self) -> None:
        self.tool_calls: list[dict] = []

    def run_with_tools(self, system_prompt, messages, tools, execute_tool, max_rounds=8, event_handler=None) -> ProviderResponse:
        self.tool_calls.append({"messages": messages, "tools": tools, "max_rounds": max_rounds})
        history_result = execute_tool("run_shell_command", {"command": "tail -n 10 ~/.zsh_history", "timeout_s": 5})
        env_result = execute_tool("run_shell_command", {"command": "printf '%s\\n' \"$SHELL\"", "timeout_s": 5})
        history_payload = json.loads(history_result)
        env_payload = json.loads(env_result)
        history_lines = str((history_payload.get("data") or {}).get("stdout") or "").strip().splitlines()
        shell_name = str((env_payload.get("data") or {}).get("stdout") or "").strip()
        return ProviderResponse(
            text=(
                f"Current shell is {shell_name or 'unknown'}. "
                f"Recent shell history returned {len(history_lines)} line(s)."
            ),
            raw={"rounds": []},
            tool_events=[
                {
                    "type": "tool_result",
                    "name": "run_shell_command",
                    "arguments": {"command": "tail -n 10 ~/.zsh_history", "timeout_s": 5},
                    "text": history_result,
                    "success": True,
                },
                {
                    "type": "tool_result",
                    "name": "run_shell_command",
                    "arguments": {"command": "printf '%s\\n' \"$SHELL\"", "timeout_s": 5},
                    "text": env_result,
                    "success": True,
                },
            ],
        )


class _RepairingToolExpectationProvider:
    def __init__(self) -> None:
        self.tool_calls: list[dict] = []

    def run_with_tools(self, system_prompt, messages, tools, execute_tool, max_rounds=8, event_handler=None) -> ProviderResponse:
        self.tool_calls.append({"messages": messages, "tools": tools, "max_rounds": max_rounds})
        if len(self.tool_calls) == 1:
            return ProviderResponse(
                text="Created note.txt.",
                raw={"rounds": ["initial"]},
                tool_events=[
                    {
                        "type": "tool_result",
                        "name": "run_shell_command",
                        "arguments": {"command": "printf 'hello\\n' > note.txt"},
                        "text": "{}",
                        "success": True,
                    }
                ],
            )
        return ProviderResponse(
            text="Created note.txt, read it, and confirmed the file exists.",
            raw={"rounds": ["retry"]},
            tool_events=[
                {
                    "type": "tool_result",
                    "name": "run_shell_command",
                    "arguments": {"command": "printf 'hello\\n' > note.txt"},
                    "text": "{}",
                    "success": True,
                },
                {
                    "type": "tool_result",
                    "name": "read_file",
                    "arguments": {"path": "note.txt"},
                    "text": "{}",
                    "success": True,
                },
                {
                    "type": "tool_result",
                    "name": "stat_path",
                    "arguments": {"path": "note.txt"},
                    "text": "{}",
                    "success": True,
                },
            ],
        )


class _ResearchRetryProvider:
    def __init__(self) -> None:
        self.tool_calls: list[dict] = []

    def run_with_tools(self, system_prompt, messages, tools, execute_tool, max_rounds=8, event_handler=None) -> ProviderResponse:
        self.tool_calls.append({"messages": messages, "tools": tools, "max_rounds": max_rounds})
        if len(self.tool_calls) == 1:
            return ProviderResponse(
                text="",
                raw={"rounds": ["initial"]},
                tool_events=[],
            )
        return ProviderResponse(
            text=(
                "Queen Bee members include Avu-chan, Yashi-chan, and Hibari-kun. "
                "Avu-chan is the frontperson and leader.\n\n"
                "Sources:\n"
                "https://www.queenbee-ztf.jp/profile\n"
                "https://en.wikipedia.org/wiki/Queen_Bee_(band)"
            ),
            raw={"rounds": ["retry"]},
            tool_events=[
                {
                    "type": "tool_result",
                    "name": "search_web",
                    "arguments": {"query": "QUEEN BEE members leader biography"},
                    "text": json.dumps(
                        {
                            "success": True,
                            "data": [
                                {
                                    "title": "QUEEN BEE official profile",
                                    "url": "https://www.queenbee-ztf.jp/profile",
                                    "snippet": "Avu-chan, Yashi-chan, and Hibari-kun are the current members of Queen Bee.",
                                },
                                {
                                    "title": "Queen Bee (band) - Wikipedia",
                                    "url": "https://en.wikipedia.org/wiki/Queen_Bee_(band)",
                                    "snippet": "Queen Bee is a Japanese band led by Avu-chan.",
                                },
                            ],
                            "summary": "Search returned 2 result(s)",
                        }
                    ),
                    "success": True,
                },
                {
                    "type": "tool_result",
                    "name": "fetch_url",
                    "arguments": {"url": "https://www.queenbee-ztf.jp/profile"},
                    "text": json.dumps(
                        {
                            "success": True,
                            "data": {
                                "url": "https://www.queenbee-ztf.jp/profile",
                                "status_code": 200,
                                "title": "QUEEN BEE Profile",
                                "text_excerpt": "Avu-chan is the vocalist and bassist. Yashi-chan is the guitarist. Hibari-kun is the drummer.",
                                "links": [],
                                "content_type": "text/html",
                            },
                            "summary": "Fetched https://www.queenbee-ztf.jp/profile",
                        }
                    ),
                    "success": True,
                },
                {
                    "type": "tool_result",
                    "name": "fetch_url",
                    "arguments": {"url": "https://en.wikipedia.org/wiki/Queen_Bee_(band)"},
                    "text": json.dumps(
                        {
                            "success": True,
                            "data": {
                                "url": "https://en.wikipedia.org/wiki/Queen_Bee_(band)",
                                "status_code": 200,
                                "title": "Queen Bee (band) - Wikipedia",
                                "text_excerpt": "Queen Bee is a Japanese band formed in Kobe in 2009 and led by Avu-chan.",
                                "links": [],
                                "content_type": "text/html",
                            },
                            "summary": "Fetched https://en.wikipedia.org/wiki/Queen_Bee_(band)",
                        }
                    ),
                    "success": True,
                },
            ],
        )


class _GitHubResearchRetryProvider:
    def __init__(self) -> None:
        self.tool_calls: list[dict] = []

    def run_with_tools(self, system_prompt, messages, tools, execute_tool, max_rounds=8, event_handler=None) -> ProviderResponse:
        self.tool_calls.append({"messages": messages, "tools": tools, "max_rounds": max_rounds})
        if len(self.tool_calls) == 1:
            return ProviderResponse(
                text="",
                raw={"rounds": ["initial"]},
                tool_events=[],
            )
        return ProviderResponse(
            text=(
                "Current GitHub trending repositories include microsoft/typescript, rust-lang/rust, and "
                "vercel/next.js.\n\n"
                "Sources:\n"
                "https://github.com/trending\n"
                "https://github.com/microsoft/TypeScript"
            ),
            raw={"rounds": ["retry"]},
            tool_events=[
                {
                    "type": "tool_result",
                    "name": "search_web",
                    "arguments": {"query": "github trending repositories right now"},
                    "text": json.dumps(
                        {
                            "success": True,
                            "data": [
                                {
                                    "title": "GitHub Trending",
                                    "url": "https://github.com/trending",
                                    "snippet": "Trending repositories on GitHub right now.",
                                },
                                {
                                    "title": "TypeScript - GitHub",
                                    "url": "https://github.com/microsoft/TypeScript",
                                    "snippet": "TypeScript is a language for application-scale JavaScript.",
                                },
                            ],
                            "summary": "Search returned 2 result(s)",
                        }
                    ),
                    "success": True,
                },
                {
                    "type": "tool_result",
                    "name": "fetch_url",
                    "arguments": {"url": "https://github.com/trending"},
                    "text": json.dumps(
                        {
                            "success": True,
                            "data": {
                                "url": "https://github.com/trending",
                                "status_code": 200,
                                "title": "GitHub Trending",
                                "text_excerpt": "Trending repositories include microsoft/typescript, rust-lang/rust, and vercel/next.js.",
                                "links": [],
                                "content_type": "text/html",
                            },
                            "summary": "Fetched https://github.com/trending",
                        }
                    ),
                    "success": True,
                },
            ],
        )


class _BrowserUnavailableRetryProvider:
    def __init__(self) -> None:
        self.tool_calls: list[dict] = []

    def run_with_tools(self, system_prompt, messages, tools, execute_tool, max_rounds=8, event_handler=None) -> ProviderResponse:
        self.tool_calls.append({"messages": messages, "tools": tools, "max_rounds": max_rounds})
        fetch_text = execute_tool("fetch_url", {"url": "https://example.com"})
        first_browser_text = execute_tool("agent_browser", {"command": "open https://example.com"})
        second_browser_text = execute_tool("agent_browser", {"command": "snapshot -i --json"})
        fetch_payload = json.loads(fetch_text)
        first_browser_payload = json.loads(first_browser_text)
        second_browser_payload = json.loads(second_browser_text)
        return ProviderResponse(
            text="- Example result from the page\n\nSources:\nhttps://example.com",
            raw={"rounds": []},
            tool_events=[
                {
                    "type": "tool_result",
                    "name": "fetch_url",
                    "arguments": {"url": "https://example.com"},
                    "text": fetch_text,
                    "success": bool(fetch_payload.get("success", False)),
                },
                {
                    "type": "tool_result",
                    "name": "agent_browser",
                    "arguments": {"command": "open https://example.com"},
                    "text": first_browser_text,
                    "success": bool(first_browser_payload.get("success", False)),
                },
                {
                    "type": "tool_result",
                    "name": "agent_browser",
                    "arguments": {"command": "snapshot -i --json"},
                    "text": second_browser_text,
                    "success": bool(second_browser_payload.get("success", False)),
                },
            ],
        )


class _AutomationOutputRepairProvider:
    def __init__(self) -> None:
        self.tool_calls: list[dict] = []
        self.complete_calls: list[list[Message]] = []

    def run_with_tools(self, system_prompt, messages, tools, execute_tool, max_rounds=8, event_handler=None) -> ProviderResponse:
        self.tool_calls.append({"messages": messages, "tools": tools, "max_rounds": max_rounds})
        if len(self.tool_calls) == 1:
            return ProviderResponse(
                text="Done. Created the files and `sh report.sh` printed `220`.",
                raw={"rounds": ["initial"]},
                tool_events=[
                    {
                        "type": "tool_result",
                        "name": "write_file",
                        "arguments": {"path": "sales.csv"},
                        "text": "{}",
                        "success": True,
                    },
                    {
                        "type": "tool_result",
                        "name": "write_file",
                        "arguments": {"path": "report.sh"},
                        "text": "{}",
                        "success": True,
                    },
                    {
                        "type": "tool_result",
                        "name": "read_file",
                        "arguments": {"path": "report.sh"},
                        "text": "{}",
                        "success": True,
                    },
                    {
                        "type": "tool_result",
                        "name": "run_shell_command",
                        "arguments": {"command": "sh report.sh"},
                        "text": '{"success": true, "data": {"stdout": "220\\n", "stderr": "", "returncode": 0}}',
                        "success": True,
                    },
                ],
            )
        return ProviderResponse(
            text="Done. Created the files and `sh report.sh` printed `360`.",
            raw={"rounds": ["retry"]},
            tool_events=[
                {
                    "type": "tool_result",
                    "name": "write_file",
                    "arguments": {"path": "report.sh"},
                    "text": "{}",
                    "success": True,
                },
                {
                    "type": "tool_result",
                    "name": "read_file",
                    "arguments": {"path": "report.sh"},
                    "text": "{}",
                    "success": True,
                },
                {
                    "type": "tool_result",
                    "name": "run_shell_command",
                    "arguments": {"command": "sh report.sh"},
                    "text": '{"success": true, "data": {"stdout": "360\\n", "stderr": "", "returncode": 0}}',
                    "success": True,
                },
            ],
        )

    def complete(self, system_prompt, messages, stream=False, event_handler=None) -> ProviderResponse:
        self.complete_calls.append(messages)
        if len(self.complete_calls) == 1:
            return ProviderResponse(
                text='{"status":"fail","reason":"The observed total should be 360, not 220."}'
            )
        return ProviderResponse(
            text='{"status":"pass","reason":"The observed total matches the requested output."}'
        )


class _AutomationJsonNormalizationProvider:
    def run_with_tools(self, system_prompt, messages, tools, execute_tool, max_rounds=8, event_handler=None) -> ProviderResponse:
        return ProviderResponse(
            text='Done.\n```json\n{"line_count": 2, "word_count": 7}\n```',
            raw={"rounds": []},
            tool_events=[
                {
                    "type": "tool_result",
                    "name": "write_file",
                    "arguments": {"path": "count.py"},
                    "text": "{}",
                    "success": True,
                },
                {
                    "type": "tool_result",
                    "name": "read_file",
                    "arguments": {"path": "count.py"},
                    "text": "{}",
                    "success": True,
                },
                {
                    "type": "tool_result",
                    "name": "run_shell_command",
                    "arguments": {"command": "python3 count.py"},
                    "text": '{"success": true, "data": {"stdout": "{\\"line_count\\": 2, \\"word_count\\": 7}\\n", "stderr": "", "returncode": 0}}',
                    "success": True,
                },
            ],
        )


class _RepoJsonFileNormalizationProvider:
    def run_with_tools(self, system_prompt, messages, tools, execute_tool, max_rounds=8, event_handler=None) -> ProviderResponse:
        return ProviderResponse(
            text='{"products":[{"product_id":"P1","merge":["C1"],"skip":["C2"]}]}',
            raw={"rounds": []},
            tool_events=[
                {
                    "type": "tool_result",
                    "name": "run_shell_command",
                    "arguments": {"command": "sh catalog.sh"},
                    "text": '{"success": true, "data": {"command": "sh catalog.sh", "stdout": "{\\"products\\":[{\\"product_id\\":\\"P1\\",\\"merge\\":[\\"C1\\"],\\"skip\\":[\\"C2\\"]}]}", "stderr": "", "returncode": 0}}',
                    "success": True,
                },
                {
                    "type": "tool_result",
                    "name": "write_file",
                    "arguments": {"path": "decisions.json"},
                    "text": "{}",
                    "success": True,
                },
                {
                    "type": "tool_result",
                    "name": "read_file",
                    "arguments": {"path": "decisions.json"},
                    "text": '{"success": true, "metadata": {"path": "decisions.json"}, "data": "{\\"products\\":[{\\"product_id\\":\\"P1\\",\\"merge\\":[\\"C1\\"],\\"skip\\":[\\"C2\\"]}]}", "summary": "Read decisions.json"}',
                    "success": True,
                },
            ],
        )


class _AutomationShellWriteGuardProvider:
    def run_with_tools(self, system_prompt, messages, tools, execute_tool, max_rounds=8, event_handler=None) -> ProviderResponse:
        blocked = execute_tool(
            "run_shell_command",
            {"command": "cat > scripts/report.sh <<'EOF'\necho hi\nEOF", "timeout_s": 5},
        )
        wrote = execute_tool(
            "write_file",
            {"path": "scripts/report.sh", "content": "#!/bin/sh\necho hi\n"},
        )
        reread = execute_tool("read_file", {"path": "scripts/report.sh"})
        verified = execute_tool("run_shell_command", {"command": "sh scripts/report.sh", "timeout_s": 5})
        return ProviderResponse(
            text="Ran `sh scripts/report.sh` and it printed `hi`.",
            raw={"rounds": []},
            tool_events=[
                {
                    "type": "tool_result",
                    "name": "run_shell_command",
                    "arguments": {"command": "cat > scripts/report.sh <<'EOF'\necho hi\nEOF", "timeout_s": 5},
                    "text": blocked,
                    "success": False,
                },
                {
                    "type": "tool_result",
                    "name": "write_file",
                    "arguments": {"path": "scripts/report.sh", "content": "#!/bin/sh\necho hi\n"},
                    "text": wrote,
                    "success": True,
                },
                {
                    "type": "tool_result",
                    "name": "read_file",
                    "arguments": {"path": "scripts/report.sh"},
                    "text": reread,
                    "success": True,
                },
                {
                    "type": "tool_result",
                    "name": "run_shell_command",
                    "arguments": {"command": "sh scripts/report.sh", "timeout_s": 5},
                    "text": verified,
                    "success": True,
                },
            ],
        )


class _AutomationPreWriteShellLoopProvider:
    def run_with_tools(self, system_prompt, messages, tools, execute_tool, max_rounds=8, event_handler=None) -> ProviderResponse:
        inspect = execute_tool("run_shell_command", {"command": "ls", "timeout_s": 5})
        blocked = execute_tool("run_shell_command", {"command": "mkdir -p scripts backups", "timeout_s": 5})
        wrote = execute_tool(
            "write_file",
            {"path": "scripts/backup_logs.sh", "content": "#!/bin/sh\nmkdir -p backups\ncp logs/app.log backups/app.log\n"},
        )
        reread = execute_tool("read_file", {"path": "scripts/backup_logs.sh"})
        verified = execute_tool(
            "run_shell_command",
            {"command": "sh scripts/backup_logs.sh && test -f backups/app.log", "timeout_s": 5},
        )
        return ProviderResponse(
            text="Ran `sh scripts/backup_logs.sh` and verified backups/app.log exists.",
            raw={"rounds": []},
            tool_events=[
                {
                    "type": "tool_result",
                    "name": "run_shell_command",
                    "arguments": {"command": "ls", "timeout_s": 5},
                    "text": inspect,
                    "success": True,
                },
                {
                    "type": "tool_result",
                    "name": "run_shell_command",
                    "arguments": {"command": "mkdir -p scripts backups", "timeout_s": 5},
                    "text": blocked,
                    "success": False,
                },
                {
                    "type": "tool_result",
                    "name": "write_file",
                    "arguments": {"path": "scripts/backup_logs.sh"},
                    "text": wrote,
                    "success": True,
                },
                {
                    "type": "tool_result",
                    "name": "read_file",
                    "arguments": {"path": "scripts/backup_logs.sh"},
                    "text": reread,
                    "success": True,
                },
                {
                    "type": "tool_result",
                    "name": "run_shell_command",
                    "arguments": {"command": "sh scripts/backup_logs.sh && test -f backups/app.log", "timeout_s": 5},
                    "text": verified,
                    "success": True,
                },
            ],
        )


class _ShellFollowUpGuardProvider:
    def run_with_tools(self, system_prompt, messages, tools, execute_tool, max_rounds=8, event_handler=None) -> ProviderResponse:
        first_shell = execute_tool("run_shell_command", {"command": "printf '{\"products\": []}\\n'", "timeout_s": 5})
        parsed_shell = execute_tool("run_shell_command", {"command": "printf '{\"products\": []}\\n' | jq .", "timeout_s": 5})
        return ProviderResponse(
            text="Executed the script and parsed the response.",
            raw={"rounds": []},
            tool_events=[
                {
                    "type": "tool_result",
                    "name": "run_shell_command",
                    "arguments": {"command": "printf '{\"products\": []}\\n'", "timeout_s": 5},
                    "text": first_shell,
                    "success": True,
                },
                {
                    "type": "tool_result",
                    "name": "run_shell_command",
                    "arguments": {"command": "printf '{\"products\": []}\\n' | jq .", "timeout_s": 5},
                    "text": parsed_shell,
                    "success": True,
                },
            ],
        )


class _FailingProvider:
    def complete(self, system_prompt, messages, stream=False, event_handler=None) -> ProviderResponse:
        raise RuntimeError("provider offline")

    def run_with_tools(self, system_prompt, messages, tools, execute_tool, max_rounds=8, event_handler=None) -> ProviderResponse:
        raise RuntimeError("provider offline")


class _LearningFailingProvider:
    def complete(self, system_prompt, messages, stream=False, event_handler=None) -> ProviderResponse:
        return ProviderResponse(text="ok")

    def run_with_tools(self, system_prompt, messages, tools, execute_tool, max_rounds=8, event_handler=None) -> ProviderResponse:
        return ProviderResponse(text="ok", raw={"rounds": []}, tool_events=[])


class _FlakyProvider:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, system_prompt, messages, stream=False, event_handler=None) -> ProviderResponse:
        self.calls += 1
        if self.calls == 1:
            request = httpx.Request("POST", "http://example.test/chat/completions")
            response = httpx.Response(500, request=request, json={"error": "temporary"})
            raise httpx.HTTPStatusError("temporary", request=request, response=response)
        return ProviderResponse(text="ok after retry")

    def run_with_tools(self, system_prompt, messages, tools, execute_tool, max_rounds=8, event_handler=None) -> ProviderResponse:
        self.calls += 1
        if self.calls == 1:
            request = httpx.Request("POST", "http://example.test/chat/completions")
            response = httpx.Response(500, request=request, json={"error": "temporary"})
            raise httpx.HTTPStatusError("temporary", request=request, response=response)
        return ProviderResponse(text="ok after retry", raw={"rounds": []}, tool_events=[])


class _ProviderRegistry:
    def __init__(self, provider) -> None:
        self.provider = provider

    def provider_for_task(self, needs_tools: bool = False):
        return self.provider


def _set_provider(runtime: RockyRuntime, provider) -> None:
    registry = _ProviderRegistry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry


def test_runtime_trace_uses_no_tools_for_direct_prompt(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    provider = _ConversationalToolProvider()
    _set_provider(runtime, provider)

    response = runtime.run_prompt("hello")

    assert response.text == "ok"
    assert response.trace["selected_tools"]
    assert provider.calls == []
    assert [message.content for message in provider.tool_calls[0]["messages"]] == ["hello"]


def test_runtime_streams_each_tool_result_once(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    provider = _StreamingToolEventProvider()
    _set_provider(runtime, provider)

    streamed_events: list[dict] = []
    response = runtime.run_prompt(
        "what python versions do i have",
        continue_session=False,
        stream=True,
        event_handler=streamed_events.append,
    )

    tool_result_events = [event for event in streamed_events if event.get("type") == "tool_result"]

    assert response.text == "runtime inspected"
    assert len(tool_result_events) == 1
    assert tool_result_events[0]["name"] == "run_shell_command"


def test_freeze_load_does_not_create_internal_layout(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    runtime = RockyRuntime.load_from(tmp_path, freeze=True)

    assert runtime.freeze_enabled is True
    assert not runtime.workspace.rocky_dir.exists()
    assert not runtime.global_root.exists()


def test_runtime_returns_failure_response_when_provider_errors(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    _set_provider(runtime, _FailingProvider())

    response = runtime.run_prompt("hello")

    assert response.verification["status"] == "fail"
    assert "provider offline" in response.text
    assert response.trace["error"]["type"] == "RuntimeError"


def test_runtime_retries_transient_provider_failures(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    provider = _FlakyProvider()
    _set_provider(runtime, provider)

    response = runtime.run_prompt("hello")

    assert response.text == "ok after retry"
    assert provider.calls == 2
    assert response.verification["status"] == "pass"


def test_runtime_ignores_learning_record_failures(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    _set_provider(runtime, _LearningFailingProvider())

    def _boom(*args, **kwargs):
        raise RuntimeError("learning write failed")

    runtime.learning_manager.record_query = _boom  # type: ignore[assignment]
    runtime.agent.learning_manager = runtime.learning_manager

    response = runtime.run_prompt("hello")

    assert response.text == "ok"
    assert response.verification["status"] == "pass"


def test_isolated_run_does_not_include_previous_session_messages(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    current = runtime.sessions.ensure_current()
    current.append("user", "old task")
    current.append("assistant", "old answer")
    runtime.sessions.save(current)

    provider = _ConversationalToolProvider()
    _set_provider(runtime, provider)

    response = runtime.run_prompt("new task", continue_session=False)

    assert response.text == "ok"
    assert provider.calls == []
    assert [message.content for message in provider.tool_calls[0]["messages"]] == ["new task"]
    assert runtime.sessions.ensure_current().id == current.id
    assert runtime.sessions.ensure_current().messages[-1]["content"] == "old answer"


def test_session_run_can_still_include_previous_messages(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    current = runtime.sessions.ensure_current()
    current.append("user", "old task")
    current.append("assistant", "old answer")
    runtime.sessions.save(current)

    provider = _ConversationalToolProvider()
    _set_provider(runtime, provider)

    runtime.run_prompt("new task", continue_session=True)

    assert provider.calls == []
    assert [message.content for message in provider.tool_calls[0]["messages"]] == ["old task", "old answer", "new task"]


def test_freeze_continue_session_reads_existing_messages_without_persisting(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    current = runtime.sessions.ensure_current()
    current.append("user", "old task")
    current.append("assistant", "old answer")
    runtime.sessions.save(current)

    frozen = RockyRuntime.load_from(tmp_path, freeze=True)
    provider = _ConversationalToolProvider()
    _set_provider(frozen, provider)

    sessions_before = list(runtime.workspace.sessions_dir.glob("ses_*.json"))
    traces_before = list(runtime.workspace.traces_dir.glob("trace_*.json"))
    query_before = list(runtime.workspace.episodes_query_dir.glob("qry_*.json"))

    response = frozen.run_prompt("new task", continue_session=True)

    assert response.text == "ok"
    assert provider.calls == []
    assert [message.content for message in provider.tool_calls[0]["messages"]] == ["old task", "old answer", "new task"]
    reloaded = runtime.sessions.load(current.id)
    assert [message["content"] for message in reloaded.messages[-2:]] == ["old task", "old answer"]
    assert list(runtime.workspace.sessions_dir.glob("ses_*.json")) == sessions_before
    assert list(runtime.workspace.traces_dir.glob("trace_*.json")) == traces_before
    assert list(runtime.workspace.episodes_query_dir.glob("qry_*.json")) == query_before
    assert "trace_path" not in response.trace


def test_isolated_run_refuses_to_invent_previous_turns(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    current = runtime.sessions.ensure_current()
    current.append("user", "old task")
    current.append("assistant", "old answer")
    runtime.sessions.save(current)

    provider = _OkProvider()
    _set_provider(runtime, provider)

    response = runtime.run_prompt("what was my previous question?", continue_session=False)

    assert "don't have any earlier turn context" in response.text
    assert provider.calls == []


def test_short_workspace_prompt_uses_project_skill_to_expose_shell_tools(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    skill_dir = workspace / ".rocky" / "skills" / "project" / "product-catalog"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_dir.joinpath("SKILL.md").write_text(
        """---
name: product-catalog-duplicate-check
description: Resolve duplicate product lookups for short catalog prompts.
task_signatures:
  - repo/shell_execution
  - conversation/general
retrieval:
  triggers:
    - macallan
    - laphroaig
    - sherry
  keywords:
    - product catalog
    - duplicate products
---

Interpret a short product name in this workspace as a duplicate-check request.

When shell tools are exposed:
1. Run `uv run python -m product_catalog_manager memory-load`.
2. Return a JSON array only.
""",
        encoding="utf-8",
    )
    runtime = RockyRuntime.load_from(workspace)
    provider = _ShellJsonProvider()
    _set_provider(runtime, provider)

    response = runtime.run_prompt("macallan 15 sherry", continue_session=False)

    assert response.route.task_signature == "repo/shell_execution"
    assert provider.complete_calls == []
    assert provider.tool_calls
    assert "run_shell_command" in response.trace["selected_tools"]


def test_project_context_shell_prompt_normalizes_fenced_json(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    skill_dir = workspace / ".rocky" / "skills" / "project" / "product-catalog"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_dir.joinpath("SKILL.md").write_text(
        """---
name: product-catalog-duplicate-check
description: Resolve duplicate product lookups for short catalog prompts.
task_signatures:
  - repo/shell_execution
  - conversation/general
retrieval:
  triggers:
    - macallan
---

Return a JSON array only.
""",
        encoding="utf-8",
    )
    runtime = RockyRuntime.load_from(workspace)
    provider = _FencedShellJsonProvider()
    _set_provider(runtime, provider)

    response = runtime.run_prompt("macallan 15 sherry", continue_session=False)

    assert response.route.task_signature == "repo/shell_execution"
    assert response.text == "[]"


def test_project_context_shell_prompt_repairs_invalid_json_like_output(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    skill_dir = workspace / ".rocky" / "skills" / "project" / "product-catalog"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_dir.joinpath("SKILL.md").write_text(
        """---
name: product-catalog-duplicate-check
description: Resolve duplicate product lookups for short catalog prompts.
task_signatures:
  - repo/shell_execution
  - conversation/general
retrieval:
  triggers:
    - oban
---

Return a JSON array only.
""",
        encoding="utf-8",
    )
    runtime = RockyRuntime.load_from(workspace)
    provider = _RepairingProjectShellJsonProvider()
    _set_provider(runtime, provider)

    response = runtime.run_prompt("oban 15", continue_session=False)

    assert response.route.task_signature == "repo/shell_execution"
    assert json.loads(response.text) == [
        {"id": "1", "product_name": "Oban Port Cask 15 Years", "confidence": "confirmed"}
    ]
    assert provider.complete_calls


def test_learned_constraints_retry_and_remove_excluded_deliverable_items(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    workspace.joinpath("AGENTS.md").write_text(
        """You are a product catalog manager agent. Your job is to find duplicate products for a given product name.

Your final deliverable is a JSON array printed to stdout.

## Available tools

```bash
uv run python -m product_catalog_manager search "<query>"
```
""",
        encoding="utf-8",
    )
    policy_dir = workspace / ".rocky" / "policies" / "learned" / "plain-product-query-variant-isolation"
    policy_dir.mkdir(parents=True, exist_ok=True)
    policy_dir.joinpath("POLICY.md").write_text(
        """---
policy_id: plain-product-query-variant-isolation
name: plain-product-query-variant-isolation
description: Exclude distinct modifiers from plain product queries.
scope: project
task_signatures:
  - repo/shell_execution
generation: 1
failure_class: over_inclusion_of_variants
promotion_state: promoted
retrieval:
  triggers:
    - oban
  keywords:
    - cask
required_behavior:
  - Keep only the plain product family unless the query explicitly asks for a modifier.
prohibited_behavior:
  - Include cask-strength variants in the final deliverable for a plain product query.
evidence_requirements:
  - Compare explicit modifiers in the query against explicit modifiers in the candidate names before including them.
---

# Learned corrective policy

Exclude distinct modifiers from plain product queries.
""",
        encoding="utf-8",
    )
    runtime = RockyRuntime.load_from(workspace)
    provider = _LearnedConstraintRepairProvider()
    _set_provider(runtime, provider)

    response = runtime.run_prompt("oban 15", continue_session=False)

    assert response.route.task_signature == "repo/shell_execution"
    assert response.verification["status"] == "pass"
    assert json.loads(response.text) == [
        {"id": "1", "product_name": "Oban Port Cask 15 Years", "confidence": "confirmed"}
    ]
    assert len(provider.tool_calls) == 2
    assert len(provider.complete_calls) == 2
    assert "learned constraints" in provider.complete_calls[0][0].content.lower()
    assert "plain-product-query-variant-isolation" in response.trace["selected_policies"]


def test_verification_repair_prompt_anchors_unsupported_claims_to_observed_strings(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    route = RouteDecision(
        lane=Lane.STANDARD,
        task_class=TaskClass.REPO,
        risk="medium",
        reasoning="test",
        tool_families=["filesystem", "shell", "python", "git"],
        task_signature="repo/shell_execution",
    )

    repair = runtime.agent._verification_repair_prompt(
        "oban 15",
        route,
        "Final answer includes unsupported deterministic claims that do not map cleanly to evidence-bearing claims.",
    )

    assert "exact observed strings" in repair
    assert "qualitative interpretations" in repair


def test_verification_repair_evidence_reuses_prior_live_research_results(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    route = RouteDecision(
        lane=Lane.STANDARD,
        task_class=TaskClass.RESEARCH,
        risk="medium",
        reasoning="test",
        tool_families=["web", "browser"],
        task_signature="research/live_compare/general",
    )

    evidence = runtime.agent._verification_repair_evidence(
        route,
        [
            {
                "type": "tool_result",
                "name": "search_web",
                "success": True,
                "text": '{"success": true, "summary": "Search returned 2 result(s)", "data": [{"title": "A", "url": "https://example.test/a"}]}',
            },
            {
                "type": "tool_result",
                "name": "agent_browser",
                "success": True,
                "text": (
                    '{"success": true, "summary": "agent-browser `snapshot -i --json` succeeded", "data": {'
                    '"url": "https://huggingface.co/models",'
                    '"items": ['
                    '{"name": "org/Model-One 7B", "role": "link", "ref": "e1"},'
                    '{"name": "org/Model-Two 8B", "role": "link", "ref": "e2"}'
                    ']}}'
                ),
            },
        ],
    )

    assert "Previously gathered" not in evidence
    assert "Tool `agent_browser` evidence:" in evidence
    assert "org/Model-One 7B" in evidence
    assert "org/Model-Two 8B" in evidence


def test_duplicate_live_page_guard_blocks_reopening_same_research_url(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    route = RouteDecision(
        lane=Lane.STANDARD,
        task_class=TaskClass.RESEARCH,
        risk="medium",
        reasoning="test",
        tool_families=["web", "browser"],
        task_signature="research/live_compare/general",
    )

    guarded = runtime.agent._duplicate_live_page_guard(
        route,
        "fetch_url",
        {"url": "https://huggingface.co/models?sort=trending"},
        {("fetch_url", "https://huggingface.co/models?sort=trending")},
    )

    assert guarded is not None
    assert "reuse_previous_live_page_evidence" in guarded
    assert "already succeeded" in guarded


def test_research_explicit_url_guard_requires_fetch_url_first(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    route = RouteDecision(
        lane=Lane.STANDARD,
        task_class=TaskClass.RESEARCH,
        risk="medium",
        reasoning="test",
        tool_families=["web", "browser"],
        task_signature="research/live_compare/general",
    )

    guarded = runtime.agent._research_explicit_url_guard(
        route,
        "find text models under 12B parameters that are trending right now. start from https://huggingface.co/models",
        "agent_browser",
        {"command": "open https://huggingface.co/models"},
        True,
    )

    assert guarded is not None
    assert "use_explicit_url_first" in guarded
    assert "Start with `fetch_url` on that exact URL" in guarded


def test_research_fetch_before_browser_guard_requires_fetch_on_new_research_url(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    route = RouteDecision(
        lane=Lane.STANDARD,
        task_class=TaskClass.RESEARCH,
        risk="medium",
        reasoning="test",
        tool_families=["web", "browser"],
        task_signature="research/live_compare/general",
    )

    guarded = runtime.agent._research_fetch_before_browser_guard(
        route,
        "agent_browser",
        {"command": "open https://huggingface.co/models?task_categories=Text+Generation&sort=trending"},
        set(),
    )

    assert guarded is not None
    assert "use_fetch_url_before_browser" in guarded
    assert "use `fetch_url` on https://huggingface.co/models?task_categories=Text+Generation&sort=trending before opening it" in guarded


def test_research_follow_up_suggestions_derive_tokens_from_observed_live_items(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)

    suggestion = runtime.agent._research_follow_up_suggestions(
        "find trending openweight llm models under 12B and show me as a list",
        [
            {
                "type": "tool_result",
                "name": "agent_browser",
                "success": True,
                "arguments": {"command": "open https://huggingface.co/models?sort=trending"},
                "text": (
                    '{"success": true, "data": {'
                    '"url": "https://huggingface.co/models?sort=trending",'
                    '"items": ['
                    '{"name": "HauhauCS/Qwen3.5-9B-Uncensored", "role": "link", "ref": "e1"},'
                    '{"name": "meta-llama/Llama-3.1-8B-Instruct", "role": "link", "ref": "e2"},'
                    '{"name": "google/gemma-4-E4B-it", "role": "link", "ref": "e3"}'
                    ']}}'
                ),
            }
        ],
    )

    assert "qwen3.5" in suggestion
    assert "llama" in suggestion
    assert "gemma" in suggestion
    assert "same-site filter/search urls" in suggestion.lower()


def test_runtime_blocks_repeat_agent_browser_after_runtime_unavailable(tmp_path: Path, monkeypatch) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    runtime.permissions.config.mode = "bypass"

    provider = _BrowserUnavailableRetryProvider()
    _set_provider(runtime, provider)

    calls: list[tuple[str, dict]] = []

    def fake_run(name: str, arguments: dict) -> ToolResult:
        calls.append((name, dict(arguments)))
        if name == "fetch_url":
            return ToolResult(
                True,
                {
                    "url": "https://example.com",
                    "status_code": 200,
                    "title": "Example",
                    "text_excerpt": "Example page with one result.",
                    "link_items": [],
                    "links": [],
                    "content_type": "text/html",
                },
                "Fetched https://example.com",
            )
        if name == "agent_browser":
            return ToolResult(
                False,
                {"command": str(arguments.get("command") or ""), "url": "https://example.com"},
                "agent-browser browser runtime is unavailable in this environment; use `fetch_url` instead.",
                {"error": "browser_runtime_unavailable"},
            )
        raise AssertionError(f"Unexpected tool: {name}")

    monkeypatch.setattr(runtime.tool_registry, "run", fake_run)

    response = runtime.run_prompt(
        "find text models under 12B parameters that are trending right now. start from https://example.com and show me as a list.",
        continue_session=False,
    )

    agent_browser_calls = [item for item in calls if item[0] == "agent_browser"]
    fetch_calls = [item for item in calls if item[0] == "fetch_url"]
    assert len(fetch_calls) == 1
    assert len(agent_browser_calls) == 1
    second_browser_event = [
        event
        for event in response.trace["tool_events"]
        if event.get("type") == "tool_result" and event.get("name") == "agent_browser"
    ][-1]
    assert second_browser_event["success"] is False
    assert "already known to be unavailable" in second_browser_event["text"]


def test_short_workspace_prompt_uses_project_instructions_to_expose_shell_tools(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    workspace.joinpath("AGENTS.md").write_text(
        """You are a product catalog manager agent. Your job is to find duplicate products for a given product name.

Your final deliverable is a JSON array printed to stdout.

## Available tools

```bash
uv run python -m product_catalog_manager memory-load
uv run python -m product_catalog_manager search "<query>"
```

## Workflow

1. Load memory.
2. Search at most 3 times.
3. Print JSON array output.
""",
        encoding="utf-8",
    )
    runtime = RockyRuntime.load_from(workspace)
    provider = _ShellJsonProvider()
    _set_provider(runtime, provider)

    response = runtime.run_prompt("laphroaig 15", continue_session=False)

    assert response.route.task_signature == "repo/shell_execution"
    assert provider.complete_calls == []
    assert provider.tool_calls


def test_shellish_project_instructions_do_not_upgrade_backchannel_prompt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    workspace.joinpath("AGENTS.md").write_text(
        """Your job is to handle workspace tasks with shell commands.

```bash
uv run python -m product_catalog_manager search "<query>"
```
""",
        encoding="utf-8",
    )
    runtime = RockyRuntime.load_from(workspace)
    provider = _ConversationalToolProvider()
    _set_provider(runtime, provider)

    response = runtime.run_prompt("thanks", continue_session=False)

    assert response.route.task_signature == "conversation/general"
    assert provider.calls == []
    assert provider.tool_calls


def test_runtime_inspection_prompt_uses_tool_capable_provider(tmp_path: Path, monkeypatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    python3 = bin_dir / "python3"
    python3.write_text("#!/bin/sh\necho Python 3.14.3\n", encoding="utf-8")
    python3.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    runtime = RockyRuntime.load_from(tmp_path)
    runtime.permissions.config.mode = "bypass"

    provider = _OkProvider()
    registry = _ProviderRegistry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    response = runtime.run_prompt("what python versions do i have", continue_session=False)

    assert response.text == "runtime inspected"
    assert response.trace["provider"] == "_OkProvider"
    assert provider.calls == []
    assert len(provider.tool_calls) == 1
    tool_names = {tool["function"]["name"] for tool in provider.tool_calls[0]["tools"]}
    assert "run_shell_command" in tool_names


def test_live_research_prompt_retries_after_no_op_and_uses_web_tools(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    runtime.permissions.config.mode = "bypass"

    provider = _ResearchRetryProvider()
    registry = _ProviderRegistry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    response = runtime.run_prompt(
        "search for all QUEEN BEE members and find out who's the leader, and tell me about their biography",
        continue_session=False,
    )

    assert response.route.task_signature == "research/live_compare/general"
    assert response.verification["status"] == "pass"
    assert len(provider.tool_calls) == 2
    assert "Avu-chan" in response.text
    assert "https://www.queenbee-ztf.jp/profile" in response.text
    assert "search_web" in response.trace["selected_tools"]
    assert any(event["name"] == "search_web" for event in response.trace["tool_events"])


def test_learned_tool_refusal_policy_can_upgrade_conversation_route_to_research(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    policy_dir = workspace / ".rocky" / "policies" / "learned" / "tool-use-refusal-conversation-general"
    policy_dir.mkdir(parents=True, exist_ok=True)
    policy_dir.joinpath("POLICY.md").write_text(
        """---
policy_id: tool-use-refusal-conversation-general
name: tool-use-refusal-conversation-general
description: Avoid false refusals regarding live web search availability.
scope: project
task_signatures:
  - conversation/general
generation: 1
failure_class: tool_use_refusal
promotion_state: candidate
feedback_excerpt: you must use web search and you do have search tools
required_behavior:
  - Attempt to use web search tools for real-time queries.
  - Verify tool availability through the environment before claiming inability.
prohibited_behavior:
  - Refuse live queries by claiming a lack of search tools.
retrieval:
  triggers:
    - github
    - repos
    - right now
  keywords:
    - web search
    - live data
---

Use web search tools for live queries.
""",
        encoding="utf-8",
    )
    runtime = RockyRuntime.load_from(workspace)
    runtime.permissions.config.mode = "bypass"

    provider = _GitHubResearchRetryProvider()
    _set_provider(runtime, provider)

    response = runtime.run_prompt("github repos right now", continue_session=False)

    assert response.route.task_signature == "research/live_compare/general"
    assert response.verification["status"] == "pass"
    assert provider.tool_calls
    assert "search_web" in response.trace["selected_tools"]
    assert any(event["name"] == "search_web" for event in response.trace["tool_events"])


def test_extraction_route_normalizes_json_fence_output(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    runtime.permissions.config.mode = "bypass"

    provider = _ExtractionProvider()
    registry = _ProviderRegistry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    response = runtime.run_prompt("normalize the people dataset into json", continue_session=False)

    assert response.text == '{"rows": 2, "fields": ["name"]}'
    assert response.verification["status"] == "pass"


def test_extraction_route_repairs_prose_into_json_with_provider(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    runtime.permissions.config.mode = "bypass"

    provider = _RepairingExtractionProvider()
    registry = _ProviderRegistry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    response = runtime.run_prompt("normalize the people dataset into json", continue_session=False)

    assert response.text == '{"rows": 2, "fields": ["name", "role"]}'
    assert response.verification["status"] == "pass"
    assert len(provider.tool_calls) == 2
    assert len(provider.complete_calls) == 2


def test_automation_route_gets_more_tool_rounds(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    runtime.permissions.config.mode = "bypass"

    provider = _OkProvider()
    registry = _ProviderRegistry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    runtime.run_prompt("create a repeatable cleanup script and verify it", continue_session=False)

    assert provider.tool_calls[0]["max_rounds"] == 12


def test_extraction_route_gets_extended_tool_rounds(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    runtime.permissions.config.mode = "bypass"

    provider = _ExtractionProvider()
    registry = _ProviderRegistry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    runtime.run_prompt("normalize the people dataset into json", continue_session=False)

    assert provider.tool_calls[0]["max_rounds"] == 8


def test_shell_execution_route_exposes_repo_inspection_tools(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)

    selected = {
        tool.name
        for tool in runtime.tool_registry.select_for_task(
            ["filesystem", "shell", "python", "git"],
            "repo/shell_execution",
            "run the workspace script and inspect the results",
        )
    }

    assert "run_shell_command" in selected
    assert "read_file" in selected
    assert "write_file" in selected


def test_automation_route_exposes_lightweight_inspection_tools(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)

    selected = {
        tool.name
        for tool in runtime.tool_registry.select_for_task(
            ["filesystem", "shell", "python"],
            "automation/general",
            "build a tiny shell script project and verify it works",
        )
    }

    assert "write_file" in selected
    assert "read_file" in selected
    assert "run_shell_command" in selected


def test_spreadsheet_route_exposes_file_inspection_tools(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)

    selected = {
        tool.name
        for tool in runtime.tool_registry.select_for_task(
            ["filesystem", "data", "python", "shell"],
            "data/spreadsheet/analysis",
            "analyze sales.csv and summarize the sheet",
        )
    }

    assert "run_shell_command" in selected
    assert "read_file" in selected


def test_runtime_blocks_cross_route_tools_from_provider(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    runtime.permissions.config.mode = "bypass"

    provider = _HiddenToolProvider()
    registry = _ProviderRegistry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    response = runtime.run_prompt("what python versions do i have", continue_session=False)

    assert response.verification["status"] == "fail"
    tool_result = response.trace["tool_events"][0]
    assert tool_result["name"] == "write_file"
    assert "tool_not_exposed" in tool_result["text"]
    assert not (tmp_path / "oops.txt").exists()


def test_runtime_allows_safe_hidden_inspection_tool_within_route_family(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    runtime.permissions.config.mode = "bypass"

    provider = _ShellInspectionHiddenFilesystemProvider()
    registry = _ProviderRegistry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    response = runtime.run_prompt("show me 10 last history of current shell", continue_session=False)

    assert response.trace["tool_events"][0]["name"] == "run_shell_command"
    assert response.trace["tool_events"][0]["success"] is True
    assert "\"tool_not_exposed\"" not in response.trace["tool_events"][0]["text"]


def test_runtime_recreates_internal_state_dirs_if_deleted_mid_run(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    provider = _ConversationalToolProvider()
    registry = _ProviderRegistry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    shutil.rmtree(runtime.sessions.sessions_dir)
    shutil.rmtree(runtime.agent.traces_dir)

    response = runtime.run_prompt("hello", continue_session=False)

    assert response.text == "ok"
    assert runtime.sessions.sessions_dir.exists()
    assert runtime.agent.traces_dir.exists()
    assert Path(response.trace["trace_path"]).exists()


def test_freeze_run_does_not_recreate_internal_state_dirs(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path, freeze=True)
    provider = _ConversationalToolProvider()
    registry = _ProviderRegistry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    response = runtime.run_prompt("hello", continue_session=False)

    assert response.text == "ok"
    assert not runtime.workspace.sessions_dir.exists()
    assert not runtime.workspace.traces_dir.exists()
    assert not runtime.workspace.episodes_query_dir.exists()
    assert "trace_path" not in response.trace


def test_runtime_retries_tool_loop_after_verification_failure(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    runtime.permissions.config.mode = "bypass"

    provider = _RepairingToolExpectationProvider()
    registry = _ProviderRegistry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    response = runtime.run_prompt(
        "run a command that creates note.txt, then read it and stat it",
        continue_session=False,
    )

    assert response.verification["status"] == "pass"
    assert len(provider.tool_calls) == 2


def test_runtime_retries_automation_when_judged_output_is_wrong(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    runtime.permissions.config.mode = "bypass"

    provider = _AutomationOutputRepairProvider()
    registry = _ProviderRegistry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    response = runtime.run_prompt(
        "Build a tiny shell script project in this empty workspace. "
        "Create exactly these files: sales.csv, report.sh, and README.md. "
        "Then run sh report.sh to verify it works and tell me the exact output.",
        continue_session=False,
    )

    assert response.verification["status"] == "pass"
    assert response.text == "Ran `sh report.sh` and it printed `360`."
    assert len(provider.tool_calls) == 2
    assert len(provider.complete_calls) == 2


def test_runtime_normalizes_exact_json_automation_answers(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    runtime.permissions.config.mode = "bypass"

    provider = _AutomationJsonNormalizationProvider()
    registry = _ProviderRegistry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    response = runtime.run_prompt(
        "Build a tiny Python script project and tell me the exact JSON output. The script prints valid JSON.",
        continue_session=False,
    )

    assert json.loads(response.text) == {
        "verified_command": "python3 count.py",
        "verified_output": {"line_count": 2, "word_count": 7},
    }
    assert response.verification["status"] == "pass"


def test_runtime_normalizes_repo_json_file_answers_with_output_path(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    runtime.permissions.config.mode = "bypass"

    provider = _RepoJsonFileNormalizationProvider()
    registry = _ProviderRegistry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    response = runtime.run_prompt(
        "Execute `catalog.sh`, write valid JSON to `decisions.json`, then read the file back and tell me the exact JSON.",
        continue_session=False,
    )

    assert json.loads(response.text) == {"products": [{"product_id": "P1", "merge": ["C1"], "skip": ["C2"]}]}
    assert response.verification["status"] == "pass"


def test_runtime_blocks_shell_file_creation_before_write_file_for_automation(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    runtime.permissions.config.mode = "bypass"

    provider = _AutomationShellWriteGuardProvider()
    registry = _ProviderRegistry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    response = runtime.run_prompt("build a repeatable report script and run it", continue_session=False)

    assert response.verification["status"] == "pass"
    first_event = response.trace["tool_events"][0]
    assert first_event["name"] == "run_shell_command"
    assert first_event["success"] is False
    assert "use_write_file_first" in first_event["text"]
    assert response.trace["tool_events"][1]["name"] == "write_file"


def test_runtime_allows_only_one_lightweight_shell_inspection_before_write_file(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    runtime.permissions.config.mode = "bypass"

    provider = _AutomationPreWriteShellLoopProvider()
    registry = _ProviderRegistry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    response = runtime.run_prompt("automate a backup log script and verify it runs", continue_session=False)

    assert response.verification["status"] == "pass"
    assert response.trace["tool_events"][0]["name"] == "run_shell_command"
    assert response.trace["tool_events"][0]["success"] is True
    assert response.trace["tool_events"][1]["name"] == "run_shell_command"
    assert response.trace["tool_events"][1]["success"] is False
    assert "use_write_file_first" in response.trace["tool_events"][1]["text"]
    assert response.trace["tool_events"][2]["name"] == "write_file"


def test_runtime_allows_second_shell_step_when_repo_execution_needs_follow_up_parsing(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    runtime.permissions.config.mode = "bypass"

    provider = _ShellFollowUpGuardProvider()
    registry = _ProviderRegistry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    response = runtime.run_prompt(
        "Execute `x.sh` and explore the response to decide which candidates should merge.",
        continue_session=False,
    )

    assert response.trace["tool_events"][0]["name"] == "run_shell_command"
    assert response.trace["tool_events"][0]["success"] is True
    assert response.trace["tool_events"][1]["name"] == "run_shell_command"
    assert response.trace["tool_events"][1]["success"] is True
