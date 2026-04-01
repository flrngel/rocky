from __future__ import annotations

import os
from pathlib import Path
import pwd
import re
import subprocess
from typing import Any

from rocky.tools.base import Tool, ToolContext, ToolResult
from rocky.util.text import truncate


WRITE_MARKERS = [' rm ', ' mv ', ' cp ', ' >', '>>', ' touch ', ' mkdir ', ' rmdir ', ' sed -i', ' git add', ' git commit', ' git apply', ' npm install', ' pip install']


def _shell_name() -> str:
    shell = os.environ.get("SHELL") or ""
    return Path(shell).name if shell else ""


def _history_candidates() -> list[Path]:
    home = Path.home()
    candidates: list[Path] = []
    if histfile := os.environ.get("HISTFILE"):
        candidates.append(Path(histfile).expanduser())

    shell_name = _shell_name()
    if shell_name == "zsh":
        candidates.append(home / ".zsh_history")
    if shell_name == "bash":
        candidates.append(home / ".bash_history")
    if shell_name == "fish":
        candidates.append(home / ".local" / "share" / "fish" / "fish_history")

    candidates.extend(
        [
            home / ".zsh_history",
            home / ".bash_history",
            home / ".local" / "share" / "fish" / "fish_history",
        ]
    )
    seen: set[Path] = set()
    ordered: list[Path] = []
    for candidate in candidates:
        resolved = candidate.expanduser()
        if resolved not in seen:
            ordered.append(resolved)
            seen.add(resolved)
    return ordered


def _parse_history_lines(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    commands: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if path.name == "fish_history":
            match = re.search(r"- cmd: (.*)", stripped)
            if match:
                commands.append(match.group(1))
            continue
        if stripped.startswith(": ") and ";" in stripped:
            commands.append(stripped.split(";", 1)[1])
            continue
        commands.append(stripped)
    return commands


def run_shell_command(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    command = str(args['command'])
    cwd = ctx.resolve_path(args.get('cwd', '.'))
    timeout_s = int(args.get('timeout_s', ctx.config.tools.shell_timeout_s))
    writes = any(marker in f' {command} ' for marker in WRITE_MARKERS)
    ctx.require('shell', 'run command', command, writes=writes, risky=True)
    try:
        proc = subprocess.run(['bash', '-lc', command], cwd=str(cwd), capture_output=True, text=True, timeout=timeout_s)
        data = {
            'command': command,
            'cwd': str(cwd.relative_to(ctx.workspace_root)),
            'returncode': proc.returncode,
            'stdout': truncate(proc.stdout, ctx.config.tools.max_tool_output_chars),
            'stderr': truncate(proc.stderr, ctx.config.tools.max_tool_output_chars),
        }
        return ToolResult(proc.returncode == 0, data, f'Command exited with {proc.returncode}')
    except subprocess.TimeoutExpired as exc:
        return ToolResult(False, {
            'command': command,
            'cwd': str(cwd.relative_to(ctx.workspace_root)),
            'timeout_s': timeout_s,
            'stdout': truncate(exc.stdout or '', ctx.config.tools.max_tool_output_chars),
            'stderr': truncate(exc.stderr or '', ctx.config.tools.max_tool_output_chars),
        }, f'Command timed out after {timeout_s}s')


def inspect_shell_environment(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    ctx.require('shell', 'inspect environment', 'shell/runtime facts', risky=True)
    shell = os.environ.get("SHELL") or ""
    cwd = Path.cwd().resolve()
    user = os.environ.get("USER") or pwd.getpwuid(os.getuid()).pw_name
    home = str(Path.home())
    history_file = next((path for path in _history_candidates() if path.exists()), None)
    data = {
        'shell': shell,
        'shell_name': Path(shell).name if shell else '',
        'user': user,
        'home': home,
        'cwd': str(cwd),
        'history_file': str(history_file) if history_file else None,
    }
    return ToolResult(True, data, 'Inspected shell environment')


def read_shell_history(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    limit = max(1, min(int(args.get('limit', 10)), 200))
    ctx.require('shell', 'read history', f'last {limit} history entries', risky=True)
    for candidate in _history_candidates():
        if not candidate.exists() or not candidate.is_file():
            continue
        commands = _parse_history_lines(candidate)
        return ToolResult(
            True,
            {
                'shell': os.environ.get("SHELL") or '',
                'history_file': str(candidate),
                'entries': commands[-limit:],
                'count': min(limit, len(commands)),
            },
            f'Read last {min(limit, len(commands))} history entries',
        )
    return ToolResult(
        False,
        {
            'shell': os.environ.get("SHELL") or '',
            'history_file': None,
            'entries': [],
            'count': 0,
        },
        'No readable shell history file found',
    )


def tools() -> list[Tool]:
    return [
        Tool(
            'run_shell_command',
            'Run a shell command inside the workspace',
            {'type': 'object', 'properties': {'command': {'type': 'string'}, 'cwd': {'type': 'string'}, 'timeout_s': {'type': 'integer'}}, 'required': ['command']},
            'shell',
            run_shell_command,
        ),
        Tool(
            'inspect_shell_environment',
            'Inspect the active shell, user, home directory, cwd, and history file path',
            {'type': 'object', 'properties': {}},
            'shell',
            inspect_shell_environment,
        ),
        Tool(
            'read_shell_history',
            'Read recent commands from the current shell history file',
            {'type': 'object', 'properties': {'limit': {'type': 'integer'}}},
            'shell',
            read_shell_history,
        ),
    ]
