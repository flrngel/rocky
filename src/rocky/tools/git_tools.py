from __future__ import annotations

import subprocess
from typing import Any

from rocky.tools.base import Tool, ToolContext, ToolResult
from rocky.util.text import truncate


def _git(ctx: ToolContext, args: list[str], cwd: str | None = None) -> ToolResult:
    path, requested_cwd = ctx.resolve_execution_cwd(cwd or '.', fallback_to_workspace=True)
    ctx.require('git', 'read git state', ' '.join(args))
    proc = subprocess.run(['git', '-C', str(path), *args], capture_output=True, text=True)
    metadata = {
        'cwd_fallback': True,
        'requested_cwd': requested_cwd,
    } if requested_cwd else {}
    return ToolResult(proc.returncode == 0, {
        'cwd': str(path.relative_to(ctx.workspace_root)),
        'returncode': proc.returncode,
        'stdout': truncate(proc.stdout, ctx.config.tools.max_tool_output_chars),
        'stderr': truncate(proc.stderr, ctx.config.tools.max_tool_output_chars),
    }, f'git {' '.join(args)} -> {proc.returncode}', metadata)


def git_status(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    return _git(ctx, ['status', '--short', '--branch'], args.get('cwd'))


def git_diff(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    git_args = ['diff']
    if args.get('staged'):
        git_args.append('--staged')
    if args.get('path'):
        git_args.extend(['--', args['path']])
    return _git(ctx, git_args, args.get('cwd'))


def git_recent_commits(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    count = int(args.get('count', 10))
    return _git(ctx, ['log', '--oneline', '-n', str(count)], args.get('cwd'))


def tools() -> list[Tool]:
    return [
        Tool('git_status', 'Show git status for the workspace', {'type': 'object', 'properties': {'cwd': {'type': 'string'}}, 'required': []}, 'git', git_status),
        Tool('git_diff', 'Show git diff for the workspace', {'type': 'object', 'properties': {'cwd': {'type': 'string'}, 'path': {'type': 'string'}, 'staged': {'type': 'boolean'}}, 'required': []}, 'git', git_diff),
        Tool('git_recent_commits', 'Show recent git commits', {'type': 'object', 'properties': {'cwd': {'type': 'string'}, 'count': {'type': 'integer'}}, 'required': []}, 'git', git_recent_commits),
    ]
