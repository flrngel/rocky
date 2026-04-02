from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from rocky.config.models import AppConfig
from rocky.core.permissions import PermissionManager, PermissionRequest
from rocky.util.text import safe_json


@dataclass(slots=True)
class ToolResult:
    success: bool
    data: Any
    summary: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_payload(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "summary": self.summary,
            "data": self.data,
            "metadata": self.metadata,
        }

    def as_text(self, limit: int = 12000) -> str:
        text = safe_json(self.as_payload())
        return text if len(text) <= limit else text[:limit] + "\n... [truncated]"


@dataclass(slots=True)
class ToolContext:
    workspace_root: Path
    execution_root: Path
    artifacts_dir: Path
    permissions: PermissionManager
    config: AppConfig

    def __post_init__(self) -> None:
        self.workspace_root = self.workspace_root.resolve()
        self.execution_root = self.execution_root.resolve()
        self.artifacts_dir = self.artifacts_dir.resolve()

    def _relative_candidates(self, value: str | Path) -> list[Path]:
        relative = Path(value).expanduser()
        candidates = [(self.execution_root / relative).resolve()]
        if self.execution_root != self.workspace_root:
            candidates.append((self.workspace_root / relative).resolve())
        seen: set[Path] = set()
        ordered: list[Path] = []
        for candidate in candidates:
            if candidate not in seen:
                ordered.append(candidate)
                seen.add(candidate)
        return ordered

    def _candidate_paths(self, value: str | Path) -> list[Path]:
        path = Path(value).expanduser()
        if path.is_absolute():
            return [path.resolve()]
        return self._relative_candidates(path)

    def _is_allowed(self, path: Path) -> bool:
        if path == self.workspace_root or self.workspace_root in path.parents:
            return True
        return str(path).startswith(str(self.artifacts_dir))

    def _coerce_relative(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.workspace_root))
        except ValueError:
            return str(path)

    @property
    def execution_relative(self) -> str:
        return self._coerce_relative(self.execution_root)

    def resolve_path(self, value: str | Path) -> Path:
        allowed = [candidate for candidate in self._candidate_paths(value) if self._is_allowed(candidate)]
        if not allowed:
            path = self._candidate_paths(value)[0]
            raise ValueError(f"Path escapes workspace: {path}")
        for candidate in allowed:
            if candidate.exists():
                return candidate
        return allowed[0]

    def resolve_execution_cwd(
        self,
        value: str | Path | None = None,
        *,
        fallback_to_workspace: bool = False,
    ) -> tuple[Path, str | None]:
        raw_value = value if value not in (None, "") else "."
        candidate = self._candidate_paths(raw_value)[0]
        try:
            return self.resolve_path(raw_value), None
        except ValueError:
            if fallback_to_workspace:
                return self.execution_root, str(candidate)
            raise

    def require(
        self,
        family: str,
        action: str,
        detail: str | None = None,
        writes: bool = False,
        risky: bool = False,
    ) -> None:
        self.permissions.check(
            PermissionRequest(
                family=family,
                action=action,
                detail=detail,
                writes=writes,
                risky=risky,
            )
        )

    def backup_if_exists(self, path: Path) -> Path | None:
        if not path.exists() or not path.is_file():
            return None
        backup_dir = self.artifacts_dir / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"{path.name}.bak"
        backup_path.write_bytes(path.read_bytes())
        return backup_path


@dataclass(slots=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    family: str
    handler: Callable[[ToolContext, dict[str, Any]], ToolResult]

    def openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }
