from __future__ import annotations

from prompt_toolkit.completion import WordCompleter


def build_completer(command_names: list[str]) -> WordCompleter:
    words = [f'/{name}' for name in command_names]
    return WordCompleter(words, ignore_case=True, sentence=True)
