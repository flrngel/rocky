from __future__ import annotations

import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
from typing import Any

from rocky.tools.base import Tool, ToolContext, ToolResult
from rocky.util.text import truncate


WRITE_MARKERS = [' rm ', ' mv ', ' cp ', ' >', '>>', ' touch ', ' mkdir ', ' rmdir ', ' sed -i', ' git add', ' git commit', ' git apply', ' npm install', ' pip install']


def _declared_shell() -> Path | None:
    shell = os.environ.get("SHELL") or ""
    if not shell:
        return None
    return Path(shell)


def _shell_name() -> str:
    declared = _declared_shell()
    return declared.name if declared else ""


def _shell_program() -> str:
    declared = _declared_shell()
    if declared is not None and declared.exists():
        return str(declared)
    for fallback in ("/bin/bash", "/bin/sh"):
        if Path(fallback).exists():
            return fallback
    return "/bin/sh"


def _shell_prefix(shell_program: str) -> str:
    preferred = _shell_name() or Path(shell_program).name
    if preferred == "zsh":
        return "test -f ~/.zshrc && source ~/.zshrc >/dev/null 2>&1; "
    if preferred == "bash":
        return (
            "if [ -f ~/.bashrc ]; then source ~/.bashrc >/dev/null 2>&1; "
            "elif [ -f ~/.bash_profile ]; then source ~/.bash_profile >/dev/null 2>&1; fi; "
        )
    if preferred == "fish":
        return "test -f ~/.config/fish/config.fish && source ~/.config/fish/config.fish >/dev/null 2>&1; "
    return ""


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


def _runtime_name_pattern(target: str) -> re.Pattern[str]:
    escaped = re.escape(target)
    if target and target[-1].isdigit():
        suffix = r"(?:\.\d+)*"
    else:
        suffix = r"(?:\d+(?:\.\d+)*)?"
    return re.compile(rf"^{escaped}{suffix}$")


def _path_directories() -> list[Path]:
    ordered: list[Path] = []
    seen: set[Path] = set()
    for chunk in os.environ.get("PATH", "").split(os.pathsep):
        if not chunk:
            continue
        path = Path(chunk).expanduser()
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in seen or not resolved.exists() or not resolved.is_dir():
            continue
        ordered.append(resolved)
        seen.add(resolved)
    return ordered


def _command_sort_key(target: str, name: str) -> tuple[int, tuple[int, ...], str]:
    if name == target:
        return (0, (), name)
    suffix = name[len(target):].lstrip(".")
    numbers = tuple(int(part) for part in suffix.split(".") if part.isdigit())
    return (1, numbers, name)


def _discover_runtime_commands(target: str, max_variants: int = 12) -> list[tuple[str, Path]]:
    pattern = _runtime_name_pattern(target)
    candidates: dict[str, Path] = {}
    if resolved := shutil.which(target):
        candidates[target] = Path(resolved).resolve()
    for directory in _path_directories():
        try:
            for entry in directory.iterdir():
                name = entry.name
                if name in candidates or not pattern.fullmatch(name):
                    continue
                if not entry.is_file() or not os.access(entry, os.X_OK):
                    continue
                candidates[name] = entry.resolve()
        except OSError:
            continue
    ordered = sorted(candidates.items(), key=lambda item: _command_sort_key(target, item[0]))
    return ordered[:max_variants]


def _capture_version(command_path: Path) -> str | None:
    for flag in ("--version", "-V"):
        try:
            proc = subprocess.run(
                [str(command_path), flag],
                capture_output=True,
                text=True,
                timeout=3,
            )
        except Exception:
            continue
        output = (proc.stdout or proc.stderr).strip()
        if output:
            return output.splitlines()[0].strip()
    return None


def run_shell_command(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    command = str(args['command'])
    timeout_s = int(args.get('timeout_s', ctx.config.tools.shell_timeout_s))
    writes = any(marker in f' {command} ' for marker in WRITE_MARKERS)
    cwd, requested_cwd = ctx.resolve_execution_cwd(
        args.get('cwd', '.'),
        fallback_to_workspace=not writes,
    )
    ctx.require('shell', 'run command', command, writes=writes, risky=True)
    shell_program = _shell_program()
    shell_command = f"{_shell_prefix(shell_program)}{command}"
    metadata: dict[str, Any] = {}
    if requested_cwd:
        metadata = {
            'cwd_fallback': True,
            'requested_cwd': requested_cwd,
        }
    try:
        proc = subprocess.run(
            [shell_program, '-c', shell_command],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        data = {
            'command': command,
            'cwd': str(cwd.relative_to(ctx.workspace_root)),
            'returncode': proc.returncode,
            'stdout': truncate(proc.stdout, ctx.config.tools.max_tool_output_chars),
            'stderr': truncate(proc.stderr, ctx.config.tools.max_tool_output_chars),
            'shell': shell_program,
        }
        return ToolResult(proc.returncode == 0, data, f'Command exited with {proc.returncode}', metadata)
    except subprocess.TimeoutExpired as exc:
        return ToolResult(False, {
            'command': command,
            'cwd': str(cwd.relative_to(ctx.workspace_root)),
            'timeout_s': timeout_s,
            'stdout': truncate(exc.stdout or '', ctx.config.tools.max_tool_output_chars),
            'stderr': truncate(exc.stderr or '', ctx.config.tools.max_tool_output_chars),
            'shell': shell_program,
        }, f'Command timed out after {timeout_s}s', metadata)


def inspect_shell_environment(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    ctx.require('shell', 'inspect environment', 'shell/runtime facts', risky=True)
    shell = os.environ.get("SHELL") or ""
    cwd = ctx.execution_root.resolve()
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


def inspect_runtime_versions(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    raw_targets = args.get("targets") or []
    targets = [str(item).strip() for item in raw_targets if str(item).strip()]
    max_variants = max(1, min(int(args.get("max_variants", 12)), 20))
    ctx.require('shell', 'inspect runtime versions', ", ".join(targets) or "runtime inspection", risky=True)

    inspected: list[dict[str, Any]] = []
    for target in targets:
        matches = _discover_runtime_commands(target, max_variants=max_variants)
        exact_path = shutil.which(target)
        rows = []
        for name, path in matches:
            rows.append(
                {
                    "command": name,
                    "path": str(path),
                    "version": _capture_version(path),
                    "exact": name == target,
                }
            )
        inspected.append(
            {
                "target": target,
                "exact_available": bool(exact_path),
                "exact_path": str(Path(exact_path).resolve()) if exact_path else None,
                "matches": rows,
            }
        )

    found = any(item["matches"] for item in inspected)
    summary_targets = ", ".join(targets) if targets else "runtime targets"
    return ToolResult(
        found,
        {"targets": inspected},
        f"Inspected local runtime targets: {summary_targets}",
    )


def tools() -> list[Tool]:
    return [
        Tool(
            'run_shell_command',
            'Run a shell command in the active workspace; omit `cwd` unless you need a workspace subdirectory',
            {'type': 'object', 'properties': {'command': {'type': 'string'}, 'cwd': {'type': 'string'}, 'timeout_s': {'type': 'integer'}}, 'required': ['command']},
            'shell',
            run_shell_command,
        ),
    ]
