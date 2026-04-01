from __future__ import annotations

import fnmatch
import re
import shutil
from pathlib import Path
from typing import Any

from rocky.tools.base import Tool, ToolContext, ToolResult
from rocky.util.paths import depth
from rocky.util.text import sha256_bytes, truncate


def _iter_files(root: Path, max_depth: int) -> list[Path]:
    if root.is_file():
        return [root]
    return [
        path
        for path in root.rglob("*")
        if path.is_file() and depth(path, root) <= max_depth
    ]


def list_files(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    ctx.require("filesystem", "list files", args.get("path"))
    root = ctx.resolve_path(args.get("path", "."))
    if not root.exists():
        return ToolResult(False, [], f"Path does not exist: {root}")
    pattern = args.get("glob", "*")
    max_items = int(args.get("max_items", 200))
    max_depth = int(args.get("max_depth", 4))
    results: list[str] = []
    if root.is_dir():
        candidates = [path for path in root.rglob("*") if depth(path, root) <= max_depth]
    else:
        candidates = [root]
    for path in candidates:
        rel = path.relative_to(ctx.workspace_root)
        if fnmatch.fnmatch(str(rel), pattern) or fnmatch.fnmatch(path.name, pattern):
            results.append(str(rel))
        if len(results) >= max_items:
            break
    return ToolResult(True, results, f"Listed {len(results)} path(s)")


def stat_path(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    ctx.require("filesystem", "stat path", args.get("path"))
    path = ctx.resolve_path(args["path"])
    if not path.exists():
        return ToolResult(False, {}, f"Path does not exist: {path}")
    stat = path.stat()
    return ToolResult(
        True,
        {
            "path": str(path.relative_to(ctx.workspace_root)),
            "exists": True,
            "is_file": path.is_file(),
            "is_dir": path.is_dir(),
            "size": stat.st_size,
            "mtime": stat.st_mtime,
        },
        f"Stat for {path.name}",
    )


def read_file(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    ctx.require("filesystem", "read file", args.get("path"))
    path = ctx.resolve_path(args["path"])
    if not path.exists() or not path.is_file():
        return ToolResult(False, "", f"File not found: {path}")
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    start = max(1, int(args.get("start_line", 1)))
    end = int(args["end_line"]) if args.get("end_line") else None
    sliced = lines[start - 1 : end]
    numbered = "\n".join(f"{idx + start}: {line}" for idx, line in enumerate(sliced))
    return ToolResult(
        True,
        truncate(numbered, ctx.config.tools.max_read_chars),
        f"Read {path.name}",
        {
            "sha256": sha256_bytes(path.read_bytes()),
            "path": str(path.relative_to(ctx.workspace_root)),
            "line_count": len(lines),
        },
    )


def write_file(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    ctx.require("filesystem", "write file", args.get("path"), writes=True)
    path = ctx.resolve_path(args["path"])
    expected = args.get("expected_sha256")
    if path.exists() and expected and sha256_bytes(path.read_bytes()) != expected:
        return ToolResult(False, {}, f"Hash mismatch for {path}")
    backup = ctx.backup_if_exists(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(args["content"], encoding="utf-8")
    return ToolResult(
        True,
        {
            "path": str(path.relative_to(ctx.workspace_root)),
            "sha256": sha256_bytes(path.read_bytes()),
            "backup": str(backup) if backup else None,
        },
        f"Wrote {path.name}",
    )


def replace_in_file(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    ctx.require("filesystem", "edit file", args.get("path"), writes=True)
    path = ctx.resolve_path(args["path"])
    if not path.exists():
        return ToolResult(False, {}, f"File not found: {path}")
    expected = args.get("expected_sha256")
    if expected and sha256_bytes(path.read_bytes()) != expected:
        return ToolResult(False, {}, f"Hash mismatch for {path}")
    text = path.read_text(encoding="utf-8", errors="replace")
    old = args["old"]
    new = args["new"]
    count = int(args.get("count", 1))
    occurrences = text.count(old)
    if occurrences == 0:
        return ToolResult(False, {}, "Original text not found")
    backup = ctx.backup_if_exists(path)
    updated = text.replace(old, new, count)
    path.write_text(updated, encoding="utf-8")
    return ToolResult(
        True,
        {
            "path": str(path.relative_to(ctx.workspace_root)),
            "replacements": min(count, occurrences),
            "backup": str(backup) if backup else None,
            "sha256": sha256_bytes(path.read_bytes()),
        },
        f"Edited {path.name}",
    )


def grep_files(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    ctx.require("filesystem", "grep files", args.get("pattern"))
    root = ctx.resolve_path(args.get("path", "."))
    regex = re.compile(args["pattern"], flags=re.I if args.get("ignore_case", True) else 0)
    glob_pattern = args.get("glob", "*")
    hits: list[dict[str, Any]] = []
    for path in _iter_files(root, int(args.get("max_depth", 4))):
        rel = path.relative_to(ctx.workspace_root)
        if not fnmatch.fnmatch(str(rel), glob_pattern) and not fnmatch.fnmatch(
            path.name, glob_pattern
        ):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                hits.append({"path": str(rel), "line": line_no, "text": line[:400]})
                if len(hits) >= int(args.get("max_hits", 100)):
                    return ToolResult(True, hits, f"Found {len(hits)} hit(s)")
    return ToolResult(True, hits, f"Found {len(hits)} hit(s)")


def glob_paths(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    ctx.require("filesystem", "glob paths", args.get("pattern"))
    pattern = args["pattern"]
    results = [
        str(path.relative_to(ctx.workspace_root))
        for path in ctx.workspace_root.rglob("*")
        if fnmatch.fnmatch(str(path.relative_to(ctx.workspace_root)), pattern)
    ]
    max_items = int(args.get("max_items", 200))
    return ToolResult(True, results[:max_items], f"Glob matched {len(results)} path(s)")


def move_path(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    ctx.require(
        "filesystem",
        "move path",
        f"{args.get('src')} -> {args.get('dst')}",
        writes=True,
    )
    src = ctx.resolve_path(args["src"])
    dst = ctx.resolve_path(args["dst"])
    if not src.exists():
        return ToolResult(False, {}, f"Source not found: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return ToolResult(
        True,
        {
            "src": str(src.relative_to(ctx.workspace_root)),
            "dst": str(dst.relative_to(ctx.workspace_root)),
        },
        f"Moved {src.name}",
    )


def copy_path(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    ctx.require(
        "filesystem",
        "copy path",
        f"{args.get('src')} -> {args.get('dst')}",
        writes=True,
    )
    src = ctx.resolve_path(args["src"])
    dst = ctx.resolve_path(args["dst"])
    if not src.exists():
        return ToolResult(False, {}, f"Source not found: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=bool(args.get("overwrite", True)))
    else:
        shutil.copy2(src, dst)
    return ToolResult(
        True,
        {
            "src": str(src.relative_to(ctx.workspace_root)),
            "dst": str(dst.relative_to(ctx.workspace_root)),
        },
        f"Copied {src.name}",
    )


def delete_path(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    ctx.require(
        "filesystem",
        "delete path",
        args.get("path"),
        writes=True,
        risky=True,
    )
    path = ctx.resolve_path(args["path"])
    if not path.exists():
        return ToolResult(False, {}, f"Path not found: {path}")
    backup = ctx.backup_if_exists(path) if path.is_file() else None
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    return ToolResult(
        True,
        {
            "path": str(path.relative_to(ctx.workspace_root)),
            "backup": str(backup) if backup else None,
        },
        f"Deleted {path.name}",
    )


def tools() -> list[Tool]:
    return [
        Tool(
            "list_files",
            "List files under a workspace path",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "glob": {"type": "string"},
                    "max_items": {"type": "integer"},
                    "max_depth": {"type": "integer"},
                },
                "required": [],
            },
            "filesystem",
            list_files,
        ),
        Tool(
            "stat_path",
            "Inspect a file or directory path",
            {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            "filesystem",
            stat_path,
        ),
        Tool(
            "glob_paths",
            "Find workspace paths by glob pattern",
            {
                "type": "object",
                "properties": {"pattern": {"type": "string"}, "max_items": {"type": "integer"}},
                "required": ["pattern"],
            },
            "filesystem",
            glob_paths,
        ),
        Tool(
            "read_file",
            "Read a text file with line numbers",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                },
                "required": ["path"],
            },
            "filesystem",
            read_file,
        ),
        Tool(
            "write_file",
            "Write a text file",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "expected_sha256": {"type": "string"},
                },
                "required": ["path", "content"],
            },
            "filesystem",
            write_file,
        ),
        Tool(
            "replace_in_file",
            "Replace text in a file with conflict detection",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old": {"type": "string"},
                    "new": {"type": "string"},
                    "count": {"type": "integer"},
                    "expected_sha256": {"type": "string"},
                },
                "required": ["path", "old", "new"],
            },
            "filesystem",
            replace_in_file,
        ),
        Tool(
            "grep_files",
            "Search files by regex pattern",
            {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                    "glob": {"type": "string"},
                    "ignore_case": {"type": "boolean"},
                    "max_hits": {"type": "integer"},
                    "max_depth": {"type": "integer"},
                },
                "required": ["pattern"],
            },
            "filesystem",
            grep_files,
        ),
        Tool(
            "move_path",
            "Move or rename a workspace path",
            {
                "type": "object",
                "properties": {"src": {"type": "string"}, "dst": {"type": "string"}},
                "required": ["src", "dst"],
            },
            "filesystem",
            move_path,
        ),
        Tool(
            "copy_path",
            "Copy a file or directory inside the workspace",
            {
                "type": "object",
                "properties": {
                    "src": {"type": "string"},
                    "dst": {"type": "string"},
                    "overwrite": {"type": "boolean"},
                },
                "required": ["src", "dst"],
            },
            "filesystem",
            copy_path,
        ),
        Tool(
            "delete_path",
            "Delete a file or directory inside the workspace",
            {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            "filesystem",
            delete_path,
        ),
    ]
