from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

from rich.console import Console

from rocky.app import RockyRuntime
from rocky.config.loader import ConfigLoader
from rocky.config.wizard import run_config_wizard
from rocky.core.permissions import PermissionRequest
from rocky.ui.repl import EventPrinter, RockyRepl, make_live_console, render_console_text
from rocky.util.paths import discover_workspace, ensure_global_layout


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rocky", description="Rocky general agent")
    parser.add_argument("task", nargs="*", help="Task string or command")
    parser.add_argument("--cwd", type=Path, help="Working directory")
    parser.add_argument("--provider", help="Provider name override")
    parser.add_argument("--model", help="Model override for the selected provider")
    parser.add_argument("--base-url", help="Base URL override for the selected provider")
    parser.add_argument("--permission-mode", choices=["plan", "supervised", "accept-edits", "auto", "bypass"])
    parser.add_argument("-y", "--yes", action="store_true", help="Auto-approve permission prompts")
    parser.add_argument("--json", action="store_true", help="Print machine-readable output for one-shot tasks")
    return parser


def _ask_cli(console: Console, request: PermissionRequest) -> bool:
    answer = input(f"Allow {request.family}:{request.action}? {request.detail or ''} [y/N] ")
    return answer.strip().lower() in {"y", "yes"}


def _task_text(args) -> str | None:
    if args.task:
        return " ".join(args.task).strip()
    if not sys.stdin.isatty():
        piped = sys.stdin.read().strip()
        if piped:
            return piped
    return None


def _interactive_terminal() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _config_loader(cwd: Path) -> ConfigLoader:
    workspace = discover_workspace(cwd.resolve())
    global_root = ensure_global_layout()
    return ConfigLoader(global_root, workspace.root)


def _maybe_run_first_launch_wizard(cwd: Path, console: Console, allow_wizard: bool) -> None:
    loader = _config_loader(cwd)
    if loader.global_config.exists():
        return
    if allow_wizard and _interactive_terminal():
        run_config_wizard(loader.global_config, console=console)
        return
    loader.ensure_defaults()


def _run_configure_flow(cwd: Path, console: Console) -> dict:
    loader = _config_loader(cwd)
    if not loader.global_config.exists():
        loader.ensure_defaults()
    return run_config_wizard(loader.global_config, console=console)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cwd = args.cwd or Path.cwd()
    console = Console()
    requested_text = " ".join(args.task).strip() if args.task else None
    configure_requested = requested_text in {"configure", "/configure"}

    _maybe_run_first_launch_wizard(cwd, console, allow_wizard=not args.json and not configure_requested)
    if configure_requested and not args.json and _interactive_terminal():
        _run_configure_flow(cwd, console)
        return 0

    cli_overrides: dict[str, object] = {}
    if args.provider:
        cli_overrides["active_provider"] = args.provider
    if args.permission_mode:
        cli_overrides.setdefault("permissions", {})["mode"] = args.permission_mode

    runtime = RockyRuntime.load_from(cwd, cli_overrides=cli_overrides)
    provider_name = args.provider or runtime.config.active_provider
    provider_cfg = runtime.config.provider(provider_name)
    runtime.config.active_provider = provider_name
    if args.model:
        provider_cfg.model = args.model
    if args.base_url:
        provider_cfg.base_url = args.base_url.rstrip("/")

    text = _task_text(args)
    if text is not None:
        if args.yes:
            runtime.permissions.ask_callback = lambda request: True
        elif _interactive_terminal():
            runtime.permissions.ask_callback = lambda request: _ask_cli(console, request)
        else:
            runtime.permissions.ask_callback = lambda request: False
    elif args.yes:
        runtime.permissions.ask_callback = lambda request: True

    if text is None:
        repl = RockyRepl(runtime)
        return repl.run()

    if text in runtime.commands.names:
        text = "/" + text
    if text == "/configure" and (args.json or not _interactive_terminal()):
        text = "/config"
    try:
        if text.startswith("/"):
            result = runtime.commands.handle(text)
            if args.json:
                print(json.dumps({"name": result.name, "text": result.text, "data": result.data}, ensure_ascii=False))
            else:
                render_console_text(console, result.text)
            return 0

        printer = None if args.json else EventPrinter(make_live_console(console))
        response = runtime.run_prompt(text, stream=not args.json, event_handler=printer)
        if args.json:
            print(
                json.dumps(
                    {
                        "text": response.text,
                        "route": asdict(response.route),
                        "verification": response.verification,
                        "usage": response.usage,
                        "trace": response.trace,
                    },
                    ensure_ascii=False,
                )
            )
        else:
            if printer and printer.streamed_text:
                printer.finish()
            else:
                render_console_text(console, response.text)
            if response.verification.get("status") != "pass":
                console.print(f"[yellow]Verification:[/] {response.verification.get('message')}", markup=False)
        return 0
    except Exception as exc:
        if args.json:
            print(json.dumps({"error": exc.__class__.__name__, "message": str(exc)}, ensure_ascii=False))
        else:
            console.print(f"Rocky failed: {exc}", markup=False, style="red")
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
