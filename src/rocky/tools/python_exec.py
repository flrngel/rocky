from __future__ import annotations

import subprocess
import sys
import uuid
from typing import Any

from rocky.tools.base import Tool, ToolContext, ToolResult
from rocky.util.text import truncate


def run_python(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    code = str(args['code'])
    cwd, requested_cwd = ctx.resolve_execution_cwd(
        args.get('cwd', '.'),
        fallback_to_workspace=True,
    )
    timeout_s = int(args.get('timeout_s', ctx.config.tools.python_timeout_s))
    ctx.require('python', 'run python', detail=code[:120], risky=True)
    run_dir = ctx.artifacts_dir / 'python_runs'
    run_dir.mkdir(parents=True, exist_ok=True)
    script_path = run_dir / f'snippet_{uuid.uuid4().hex[:8]}.py'
    script_path.write_text(code, encoding='utf-8')
    metadata = {
        'cwd_fallback': True,
        'requested_cwd': requested_cwd,
    } if requested_cwd else {}
    try:
        proc = subprocess.run([sys.executable, str(script_path)], cwd=str(cwd), capture_output=True, text=True, timeout=timeout_s)
        data = {
            'script_path': str(script_path),
            'cwd': str(cwd.relative_to(ctx.workspace_root)),
            'returncode': proc.returncode,
            'stdout': truncate(proc.stdout, ctx.config.tools.max_tool_output_chars),
            'stderr': truncate(proc.stderr, ctx.config.tools.max_tool_output_chars),
        }
        return ToolResult(proc.returncode == 0, data, f'Python exited with {proc.returncode}', metadata)
    except subprocess.TimeoutExpired as exc:
        return ToolResult(False, {
            'script_path': str(script_path),
            'timeout_s': timeout_s,
            'stdout': truncate(exc.stdout or '', ctx.config.tools.max_tool_output_chars),
            'stderr': truncate(exc.stderr or '', ctx.config.tools.max_tool_output_chars),
        }, f'Python timed out after {timeout_s}s', metadata)


def tools() -> list[Tool]:
    return [Tool('run_python', 'Run a Python snippet inside the workspace', {'type': 'object', 'properties': {'code': {'type': 'string'}, 'cwd': {'type': 'string'}, 'timeout_s': {'type': 'integer'}}, 'required': ['code']}, 'python', run_python)]
