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
    artifacts_dir: Path
    permissions: PermissionManager
    config: AppConfig

    def resolve_path(self, value: str | Path) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = (self.workspace_root / path).resolve()
        else:
            path = path.resolve()
        artifacts_root = self.artifacts_dir.resolve()
        if (
            self.workspace_root not in path.parents
            and path != self.workspace_root
            and not str(path).startswith(str(artifacts_root))
        ):
            raise ValueError(f"Path escapes workspace: {path}")
        return path

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
