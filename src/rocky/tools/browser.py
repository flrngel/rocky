from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from typing import Any

from rocky.tools.base import Tool, ToolContext, ToolResult
from rocky.util.text import truncate


AGENT_BROWSER_DESCRIPTION = (
    "Drive Vercel's `agent-browser` CLI. Headless is the default. "
    "Pass only the part after `agent-browser` as `command`. "
    "Minimum useful workflow: `open <url>` -> `snapshot -i --json` -> "
    "interact with refs such as `click @e1`, `fill @e2 \"text\"`, or `press Enter` -> "
    "run `snapshot -i --json` again after navigation or DOM changes. "
    "Useful reads: `get text @e1`, `get title`, `get url`, `wait 1000`. "
    "Set `headed=true` only when headless mode is insufficient."
)


def _build_agent_browser_command(
    command: str,
    *,
    session: str | None = None,
    headed: bool = False,
) -> list[str]:
    argv = ["agent-browser"]
    if session:
        argv.extend(["--session", session])
    if headed:
        argv.append("--headed")
    argv.extend(shlex.split(command))
    return argv


def _extract_open_url(command: str) -> str:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return ""
    if len(tokens) >= 2 and tokens[0] == "open" and tokens[1].startswith(("http://", "https://")):
        return tokens[1]
    return ""


def _extract_items(refs: Any) -> list[dict[str, str]]:
    if not isinstance(refs, dict):
        return []
    items: list[dict[str, str]] = []
    for ref, payload in list(refs.items())[:16]:
        if not isinstance(payload, dict):
            continue
        name = " ".join(str(payload.get("name") or "").split()).strip()
        role = " ".join(str(payload.get("role") or "").split()).strip()
        if not name and not role:
            continue
        item: dict[str, str] = {"ref": str(ref).strip()}
        if name:
            item["name"] = name[:180]
        if role:
            item["role"] = role[:80]
        items.append(item)
    return items


def _extract_browser_observations(command: str, stdout: str) -> tuple[dict[str, Any], bool]:
    data: dict[str, Any] = {}
    stripped = stdout.strip()
    json_success = True
    if stripped:
        try:
            payload = json.loads(stripped)
        except Exception:
            payload = None
        if isinstance(payload, dict):
            json_success = bool(payload.get("success", True))
            raw_data = payload.get("data")
            if isinstance(raw_data, dict):
                url = str(raw_data.get("url") or raw_data.get("final_url") or "").strip()
                title = " ".join(str(raw_data.get("title") or "").split()).strip()
                snapshot = str(raw_data.get("snapshot") or raw_data.get("text") or "").strip()
                items = _extract_items(raw_data.get("refs"))
                if url:
                    data["url"] = url
                if title:
                    data["title"] = title[:240]
                if snapshot:
                    data["snapshot"] = truncate(snapshot, 3000)
                if items:
                    data["items"] = items
    open_url = _extract_open_url(command)
    if open_url and not data.get("url"):
        data["url"] = open_url
    lowered = command.lower().strip()
    if lowered.startswith("get url") and stripped and stripped.startswith(("http://", "https://")):
        data["url"] = stripped
    if lowered.startswith("get title") and stripped and not data.get("title"):
        data["title"] = stripped[:240]
    if "snapshot" in lowered and stripped and not data.get("snapshot"):
        data["snapshot"] = truncate(stripped, 3000)
    return data, json_success


def agent_browser(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    command = str(args.get("command") or "").strip()
    if not command:
        return ToolResult(False, {}, "Missing required `command`")
    session = str(args.get("session") or "").strip() or None
    headed = bool(args.get("headed", False))
    timeout_s = int(args.get("timeout_s", 60))
    cwd, requested_cwd = ctx.resolve_execution_cwd(
        args.get("cwd", "."),
        fallback_to_workspace=True,
    )
    metadata = {
        "cwd_fallback": True,
        "requested_cwd": requested_cwd,
    } if requested_cwd else {}
    writes = any(term in command.lower() for term in ("screenshot", " pdf ", "pdf "))
    ctx.require("browser", "run agent-browser", detail=command[:160], writes=writes, risky=True)
    if shutil.which("agent-browser") is None:
        return ToolResult(
            False,
            {"command": command},
            "agent-browser CLI is not installed or not on PATH",
            metadata,
        )
    try:
        argv = _build_agent_browser_command(command, session=session, headed=headed)
    except ValueError as exc:
        return ToolResult(False, {"command": command}, f"Invalid agent-browser command: {exc}", metadata)
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        return ToolResult(
            False,
            {
                "command": command,
                "cwd": str(cwd.relative_to(ctx.workspace_root)),
                "timeout_s": timeout_s,
                "stdout": truncate(exc.stdout or "", ctx.config.tools.max_tool_output_chars),
                "stderr": truncate(exc.stderr or "", ctx.config.tools.max_tool_output_chars),
            },
            f"agent-browser timed out after {timeout_s}s",
            metadata,
        )

    stdout = truncate(proc.stdout, ctx.config.tools.max_tool_output_chars)
    stderr = truncate(proc.stderr, ctx.config.tools.max_tool_output_chars)
    observations, json_success = _extract_browser_observations(command, proc.stdout)
    data: dict[str, Any] = {
        "command": command,
        "cwd": str(cwd.relative_to(ctx.workspace_root)),
        "returncode": proc.returncode,
        "stdout": stdout,
        "stderr": stderr,
        **observations,
    }
    success = proc.returncode == 0 and json_success
    summary = f"agent-browser `{command}` exited with {proc.returncode}"
    if success and observations.get("url"):
        summary = f"agent-browser `{command}` succeeded for {observations['url']}"
    elif not success and stderr.strip():
        summary = truncate(stderr.strip().splitlines()[0], 180)
    return ToolResult(success, data, summary, metadata)


def tools() -> list[Tool]:
    return [
        Tool(
            "agent_browser",
            AGENT_BROWSER_DESCRIPTION,
            {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "session": {"type": "string"},
                    "headed": {"type": "boolean"},
                    "cwd": {"type": "string"},
                    "timeout_s": {"type": "integer"},
                },
                "required": ["command"],
            },
            "browser",
            agent_browser,
        )
    ]
