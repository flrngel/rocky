from __future__ import annotations

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document


class RockyCompleter(Completer):
    def __init__(self, runtime) -> None:
        self.runtime = runtime

    def _command_names(self) -> list[str]:
        names = getattr(getattr(self.runtime, "commands", None), "names", []) or []
        return sorted({f"/{name}" for name in names})

    def _session_ids(self) -> list[str]:
        sessions = getattr(getattr(self.runtime, "sessions", None), "list", None)
        if not callable(sessions):
            return []
        try:
            rows = sessions()
        except Exception:
            return []
        ids: list[str] = []
        for row in rows or []:
            if isinstance(row, dict) and row.get("id"):
                ids.append(str(row["id"]))
        return ids

    def _memory_names(self) -> list[str]:
        fn = getattr(type(self.runtime), "memory_inventory", None)
        if fn is None:
            return []
        try:
            rows = self.runtime.memory_inventory()
        except Exception:
            return []
        names: list[str] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            scope = str(row.get("scope") or "")
            name = str(row.get("name") or "")
            if scope and name:
                names.append(f"{scope}:{name}")
            elif name:
                names.append(name)
        return sorted(set(names))

    def _student_entry_ids(self) -> list[str]:
        fn = getattr(type(self.runtime), "student_inventory", None)
        if fn is None:
            return []
        try:
            payload = self.runtime.student_inventory()
        except Exception:
            return []
        notes = payload.get("notes") if isinstance(payload, dict) else []
        values: list[str] = []
        for item in notes or []:
            if isinstance(item, dict) and item.get("id"):
                values.append(str(item["id"]))
        return sorted(set(values))

    def _thread_ids(self) -> list[str]:
        fn = getattr(type(self.runtime), "thread_inventory", None)
        if fn is None:
            return []
        try:
            payload = self.runtime.thread_inventory()
        except Exception:
            return []
        rows = payload.get("threads") if isinstance(payload, dict) else []
        values: list[str] = []
        for item in rows or []:
            if isinstance(item, dict) and item.get("thread_id"):
                values.append(str(item["thread_id"]))
        return sorted(set(values))

    def _yield_matches(self, token: str, options: list[str]):
        lowered = token.lower()
        for word in options:
            if word.lower().startswith(lowered):
                yield Completion(word, start_position=-len(token))

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor
        stripped = text.lstrip()
        if not stripped.startswith("/"):
            return
        parts = stripped.split()
        if len(parts) <= 1 and not stripped.endswith(" "):
            yield from self._yield_matches(stripped, self._command_names())
            return
        command = parts[0]
        args = parts[1:]
        if stripped.endswith(" "):
            current = ""
        else:
            current = parts[-1]
        if command == "/resume":
            yield from self._yield_matches(current, self._session_ids())
            return
        if command == "/threads":
            yield from self._yield_matches(current, self._thread_ids())
            return
        if command == "/freeze":
            yield from self._yield_matches(current, ["on", "off", "status"])
            return
        if command == "/plan":
            yield from self._yield_matches(current, ["on", "off"])
            return
        if command == "/memory":
            if len(args) <= 1:
                yield from self._yield_matches(current, ["list", "show", "add", "set", "remove"])
                return
            action = args[0]
            if action in {"show", "remove"}:
                yield from self._yield_matches(current, self._memory_names())
                return
        if command == "/student":
            if len(args) <= 1:
                yield from self._yield_matches(current, ["status", "list", "show", "add"])
                return
            action = args[0]
            if action == "list":
                yield from self._yield_matches(current, ["lesson", "knowledge", "pattern", "example", "profile"])
                return
            if action == "show":
                yield from self._yield_matches(current, self._student_entry_ids())
                return
            if action == "add" and len(args) <= 2:
                yield from self._yield_matches(current, ["knowledge", "pattern", "example", "profile"])
                return


def build_completer(runtime) -> RockyCompleter:
    return RockyCompleter(runtime)
