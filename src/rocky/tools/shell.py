from __future__ import annotations

import subprocess
from typing import Any

from rocky.tools.base import Tool, ToolContext, ToolResult
from rocky.util.text import truncate


WRITE_MARKERS = [' rm ', ' mv ', ' cp ', ' >', '>>', ' touch ', ' mkdir ', ' rmdir ', ' sed -i', ' git add', ' git commit', ' git apply', ' npm install', ' pip install']


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


def tools() -> list[Tool]:
    return [Tool('run_shell_command', 'Run a shell command inside the workspace', {'type': 'object', 'properties': {'command': {'type': 'string'}, 'cwd': {'type': 'string'}, 'timeout_s': {'type': 'integer'}}, 'required': ['command']}, 'shell', run_shell_command)]
