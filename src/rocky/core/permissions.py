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

    def _match_rules(self, rules: dict[str, list[str]], family: str, action: str) -> bool:
        patterns = rules.get(family, []) + rules.get("*", [])
        for pattern in patterns:
            if pattern == "*" or pattern == action:
                return True
        return False

    def check(self, request: PermissionRequest) -> None:
        mode = self.config.mode
        if self._match_rules(self.config.deny, request.family, request.action):
            self._record("deny", request)
            raise PermissionDenied(f"Denied by rule: {request.family}:{request.action}")
        if self._match_rules(self.config.allow, request.family, request.action):
            self._record("allow", request)
            return
        if mode == "plan" and (request.writes or request.risky or request.family in {"shell", "browser", "web", "python"}):
            self._record("deny", request)
            raise PermissionDenied("Plan mode allows read-only inspection only")
        if mode == "bypass":
            self._record("allow", request)
            return
        if mode == "accept-edits" and request.family == "filesystem" and request.writes:
            self._record("allow", request)
            return
        if mode == "auto" and not request.risky:
            self._record("allow", request)
            return
        if mode == "supervised" or request.risky or request.writes:
            if self.ask_callback and self.ask_callback(request):
                self._record("allow", request)
                return
            self._record("deny", request)
            raise PermissionDenied(f"Permission denied for {request.family}:{request.action}")
        self._record("allow", request)

    def explain(self) -> dict:
        return {
            "mode": self.config.mode,
            "allow_rules": self.config.allow,
            "deny_rules": self.config.deny,
            "recent_decisions": self.decision_log[-20:],
        }
