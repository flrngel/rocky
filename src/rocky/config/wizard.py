from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Callable

from rich.console import Console

from rocky.config.loader import DEFAULT_CONFIG_DICT
from rocky.config.models import merge_dict
from rocky.util.io import read_yaml, write_yaml
from rocky.util.yamlx import dump_yaml

InputFunc = Callable[[str], str]


def _prompt_text(input_func: InputFunc, label: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    raw = input_func(f"{label}{suffix}: ").strip()
    if raw:
        return raw
    return default or ""


def _prompt_choice(
    input_func: InputFunc,
    label: str,
    options: list[tuple[str, str]],
    default: str,
) -> str:
    option_map = {key: value for key, value in options}
    prompt_bits = ", ".join(f"{key}) {value}" for key, value in options)
    while True:
        raw = input_func(f"{label} ({prompt_bits}) [{default}]: ").strip().lower()
        selected = raw or default
        if selected in option_map:
            return selected


def _provider_defaults(config: dict, name: str, fallback: dict) -> dict:
    provider = ((config.get("providers") or {}).get(name) or {})
    return {**fallback, **provider}


def build_global_config(existing: dict | None, answers: dict[str, str]) -> dict:
    base = merge_dict(deepcopy(DEFAULT_CONFIG_DICT), existing or {})
    providers = deepcopy(base.get("providers") or {})
    active_provider = answers["active_provider"]

    if active_provider == "compatible":
        providers["compatible"] = {
            "style": answers["compatible_style"],
            "base_url": answers["base_url"],
            "api_key_env": answers["api_key_env"] or None,
            "model": answers["model"],
            "store": False,
        }
    else:
        provider = deepcopy(providers.get(active_provider) or {})
        provider["base_url"] = answers["base_url"]
        provider["model"] = answers["model"]
        provider["api_key_env"] = answers["api_key_env"] or None
        providers[active_provider] = provider

    base["active_provider"] = active_provider
    base["providers"] = providers
    base["permissions"] = {
        **(base.get("permissions") or {}),
        "mode": answers["permission_mode"],
    }
    return base


def config_summary(config: dict) -> str:
    active_provider = str(config.get("active_provider", "unknown"))
    provider = ((config.get("providers") or {}).get(active_provider) or {})
    summary = {
        "active_provider": active_provider,
        "base_url": provider.get("base_url"),
        "model": provider.get("model"),
        "api_key_env": provider.get("api_key_env"),
        "permission_mode": ((config.get("permissions") or {}).get("mode")),
    }
    return dump_yaml(summary)


def run_config_wizard(
    config_path: Path,
    console: Console | None = None,
    input_func: InputFunc = input,
) -> dict:
    console = console or Console()
    existing = read_yaml(config_path)
    existing = existing if isinstance(existing, dict) else {}
    merged = merge_dict(deepcopy(DEFAULT_CONFIG_DICT), existing)

    console.print("[bold green]Rocky configuration[/]")
    console.print(f"Config path: {config_path}")
    console.print("This runs on first launch and anytime you use `rocky configure` or `/configure`.\n")

    active_default = str(merged.get("active_provider", "ollama"))
    active_choice = _prompt_choice(
        input_func,
        "Provider",
        [("1", "ollama"), ("2", "openai"), ("3", "compatible")],
        {"ollama": "1", "openai": "2", "compatible": "3"}.get(active_default, "1"),
    )
    active_provider = {"1": "ollama", "2": "openai", "3": "compatible"}[active_choice]

    if active_provider == "ollama":
        provider_defaults = _provider_defaults(merged, "ollama", DEFAULT_CONFIG_DICT["providers"]["ollama"])
        style = provider_defaults["style"]
        base_url = _prompt_text(input_func, "Ollama base URL", str(provider_defaults["base_url"]))
        model = _prompt_text(input_func, "Ollama model", str(provider_defaults["model"]))
        api_key_env = _prompt_text(input_func, "API key env var", str(provider_defaults.get("api_key_env") or "OLLAMA_API_KEY"))
    elif active_provider == "openai":
        provider_defaults = _provider_defaults(merged, "openai", DEFAULT_CONFIG_DICT["providers"]["openai"])
        style = provider_defaults["style"]
        base_url = _prompt_text(input_func, "OpenAI base URL", str(provider_defaults["base_url"]))
        model = _prompt_text(input_func, "OpenAI model", str(provider_defaults["model"]))
        api_key_env = _prompt_text(input_func, "API key env var", str(provider_defaults.get("api_key_env") or "OPENAI_API_KEY"))
    else:
        compatible_defaults = _provider_defaults(
            merged,
            "compatible",
            {
                "style": "openai_chat",
                "base_url": "http://localhost:11434/v1",
                "api_key_env": "COMPATIBLE_API_KEY",
                "model": "llama3.2",
            },
        )
        style_choice = _prompt_choice(
            input_func,
            "Compatible API style",
            [("1", "openai_chat"), ("2", "openai_responses")],
            "2" if compatible_defaults.get("style") == "openai_responses" else "1",
        )
        style = "openai_chat" if style_choice == "1" else "openai_responses"
        base_url = _prompt_text(input_func, "Compatible base URL", str(compatible_defaults["base_url"]))
        model = _prompt_text(input_func, "Compatible model", str(compatible_defaults["model"]))
        api_key_env = _prompt_text(
            input_func,
            "API key env var (leave blank if none)",
            str(compatible_defaults.get("api_key_env") or ""),
        )

    permission_defaults = str(((merged.get("permissions") or {}).get("mode")) or "supervised")
    permission_choice = _prompt_choice(
        input_func,
        "Permission mode",
        [("1", "supervised"), ("2", "auto"), ("3", "bypass")],
        {"supervised": "1", "auto": "2", "bypass": "3"}.get(permission_defaults, "1"),
    )
    permission_mode = {"1": "supervised", "2": "auto", "3": "bypass"}[permission_choice]

    config = build_global_config(
        existing,
        {
            "active_provider": active_provider,
            "compatible_style": style,
            "base_url": base_url,
            "model": model,
            "api_key_env": api_key_env,
            "permission_mode": permission_mode,
        },
    )
    write_yaml(config_path, config)
    console.print("\n[bold green]Saved global config[/]")
    console.print(config_summary(config))
    return config
