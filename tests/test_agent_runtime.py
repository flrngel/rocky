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
                    "name": "inspect_runtime_versions",
                    "arguments": {"targets": ["python"]},
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
                    "name": "run_python",
                    "arguments": {"code": "print(...)"},
                    "text": '{"success": true, "data": {"stdout": "{\\"rows\\": 2, \\"fields\\": [\\"name\\", \\"role\\"]}"}}',
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
        grep_result = execute_tool("grep_files", {"pattern": "PermissionDenied", "path": "src"})
        env_result = execute_tool("inspect_shell_environment", {})
        grep_payload = json.loads(grep_result)
        env_payload = json.loads(env_result)
        grep_count = len(grep_payload.get("data") or [])
        shell_name = str((env_payload.get("data") or {}).get("shell_name") or "")
        return ProviderResponse(
            text=(
                f"Current shell is {shell_name or 'unknown'}. "
                f"Searching the repo for PermissionDenied found {grep_count} hit(s)."
            ),
            raw={"rounds": []},
            tool_events=[
                {
                    "type": "tool_result",
                    "name": "grep_files",
                    "arguments": {"pattern": "PermissionDenied", "path": "src"},
                    "text": grep_result,
                    "success": True,
                },
                {
                    "type": "tool_result",
                    "name": "inspect_shell_environment",
                    "arguments": {},
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
        blocked_shell = execute_tool("run_shell_command", {"command": "printf '{\"products\": []}\\n' | jq .", "timeout_s": 5})
        parsed = execute_tool("run_python", {"code": "print('parsed response')"})
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
                    "text": blocked_shell,
                    "success": False,
                },
                {
                    "type": "tool_result",
                    "name": "run_python",
                    "arguments": {"code": "print('parsed response')"},
                    "text": parsed,
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
    skill_dir = workspace / ".rocky" / "skills" / "learned" / "plain-product-query-variant-isolation"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_dir.joinpath("SKILL.md").write_text(
        """---
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

# Learned corrective workflow

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
    assert len(provider.tool_calls) == 1
    assert len(provider.complete_calls) == 3
    assert "learned constraints" in provider.complete_calls[0][0].content.lower()
    assert "plain-product-query-variant-isolation" in response.trace["selected_skills"]


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
    assert "inspect_runtime_versions" in tool_names


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


def test_learned_tool_refusal_skill_can_upgrade_conversation_route_to_research(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    skill_dir = workspace / ".rocky" / "skills" / "learned" / "tool-use-refusal-conversation-general"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_dir.joinpath("SKILL.md").write_text(
        """---
name: tool-use-refusal-conversation-general
description: Avoid false refusals regarding live web search availability.
scope: project
task_signatures:
  - conversation/general
generation: 1
origin: learned
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
    assert "grep_files" in selected
    assert "list_files" in selected
    assert "glob_paths" in selected
    assert "git_status" in selected
    assert "git_diff" in selected


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
    assert "stat_path" in selected
    assert "list_files" in selected
    assert "glob_paths" in selected
    assert "run_python" in selected


def test_spreadsheet_route_exposes_file_inspection_tools(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)

    selected = {
        tool.name
        for tool in runtime.tool_registry.select_for_task(
            ["filesystem", "data", "python"],
            "data/spreadsheet/analysis",
            "analyze sales.csv and summarize the sheet",
        )
    }

    assert "inspect_spreadsheet" in selected
    assert "read_sheet_range" in selected
    assert "stat_path" in selected
    assert "read_file" in selected
    assert "glob_paths" in selected


def test_runtime_allows_cross_route_tools_from_provider(tmp_path: Path) -> None:
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
    assert "\"tool_not_exposed\"" not in tool_result["text"]
    assert (tmp_path / "oops.txt").exists()


def test_runtime_allows_safe_hidden_inspection_tool_within_route_family(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    runtime.permissions.config.mode = "bypass"

    provider = _ShellInspectionHiddenFilesystemProvider()
    registry = _ProviderRegistry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    response = runtime.run_prompt("show me 10 last history of current shell", continue_session=False)

    assert response.trace["tool_events"][0]["name"] == "grep_files"
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
    retried_messages = provider.tool_calls[1]["messages"]
    assert retried_messages[-1].role == "user"
    assert "did not pass verification" in str(retried_messages[-1].content)


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
    retried_messages = provider.tool_calls[1]["messages"]
    assert retried_messages[-1].role == "user"
    assert "360" in str(retried_messages[-1].content)


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

    assert json.loads(response.text) == {
        "output_path": "decisions.json",
        "verified_output": {"products": [{"product_id": "P1", "merge": ["C1"], "skip": ["C2"]}]},
    }
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


def test_runtime_blocks_second_shell_step_when_repo_execution_needs_non_shell_follow_up(tmp_path: Path) -> None:
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
    assert response.trace["tool_events"][1]["success"] is False
    assert "use_non_shell_follow_up" in response.trace["tool_events"][1]["text"]
    assert response.trace["tool_events"][2]["name"] == "run_python"
