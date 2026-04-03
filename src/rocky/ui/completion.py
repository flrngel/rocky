from __future__ import annotations

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document


class SlashCommandCompleter(Completer):
    def __init__(self, command_names: list[str]) -> None:
        self.words = sorted({f"/{name}" for name in command_names})

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor
        stripped = text.lstrip()
        if not stripped.startswith("/"):
            return
        if " " in stripped:
            return
        lowered = stripped.lower()
        for word in self.words:
            if not word.lower().startswith(lowered):
                continue
            yield Completion(word, start_position=-len(stripped))


def build_completer(command_names: list[str]) -> SlashCommandCompleter:
    return SlashCommandCompleter(command_names)
