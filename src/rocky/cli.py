from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

from rich.console import Console

from rocky.app import RockyRuntime
from rocky.core.permissions import PermissionRequest
from rocky.ui.repl import EventPrinter, RockyRepl


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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cli_overrides: dict[str, object] = {}
    if args.provider:
        cli_overrides["active_provider"] = args.provider
    if args.permission_mode:
        cli_overrides.setdefault("permissions", {})["mode"] = args.permission_mode

    runtime = RockyRuntime.load_from(args.cwd or Path.cwd(), cli_overrides=cli_overrides)
    provider_name = args.provider or runtime.config.active_provider
    provider_cfg = runtime.config.provider(provider_name)
    runtime.config.active_provider = provider_name
    if args.model:
        provider_cfg.model = args.model
    if args.base_url:
        provider_cfg.base_url = args.base_url.rstrip("/")

    console = Console()
    text = _task_text(args)
    if text is not None:
        if args.yes:
            runtime.permissions.ask_callback = lambda request: True
        elif sys.stdin.isatty():
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
    try:
        if text.startswith("/"):
            result = runtime.commands.handle(text)
            if args.json:
                print(json.dumps({"name": result.name, "text": result.text, "data": result.data}, ensure_ascii=False))
            else:
                console.print(result.text)
            return 0

        printer = None if args.json else EventPrinter(console)
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
                console.print(response.text)
            if response.verification.get("status") != "pass":
                console.print(f"[yellow]Verification:[/] {response.verification.get('message')}")
        return 0
    except Exception as exc:
        if args.json:
            print(json.dumps({"error": exc.__class__.__name__, "message": str(exc)}, ensure_ascii=False))
        else:
            console.print(f"[red]Rocky failed:[/] {exc}")
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
