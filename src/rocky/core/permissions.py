from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from rocky.config.models import PermissionConfig


class PermissionDenied(RuntimeError):
    pass


@dataclass(slots=True)
class PermissionRequest:
    family: str
    action: str
    detail: str | None = None
    writes: bool = False
    risky: bool = False


@dataclass(slots=True)
class PermissionManager:
    config: PermissionConfig
    workspace_root: Path
    ask_callback: Callable[[PermissionRequest], bool] | None = None
    decision_log: list[dict] = field(default_factory=list)

    def _record(self, decision: str, request: PermissionRequest) -> None:
        self.decision_log.append({"decision": decision, **asdict(request)})

    def check(self, request: PermissionRequest) -> None:
        self._record("allow", request)

    def explain(self) -> dict:
        return {
            "enforced": False,
            "mode": "disabled",
            "legacy_mode": self.config.mode,
            "message": "Tool-call permission enforcement is disabled. Rocky will not block tools based on permission policy.",
            "recent_decisions": self.decision_log[-20:],
        }
