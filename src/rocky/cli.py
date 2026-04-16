from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

from rich.console import Console
from rich.text import Text

from rocky import __version__
from rocky.app import RockyRuntime
from rocky.config.loader import ConfigLoader
from rocky.config.wizard import run_config_wizard
from rocky.ui.repl import EventPrinter, RockyRepl, make_console, make_live_console, render_console_text
from rocky.util.paths import discover_workspace, ensure_global_layout


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rocky", description="Rocky general agent")
    parser.add_argument("task", nargs="*", help="Task string or command")
    parser.add_argument("--cwd", type=Path, help="Working directory")
    parser.add_argument("--provider", help="Provider name override")
    parser.add_argument("--model", help="Model override for the selected provider")
    parser.add_argument("--base-url", help="Base URL override for the selected provider")
    parser.add_argument("--permission-mode", choices=["plan", "supervised", "accept-edits", "auto", "bypass"], help=argparse.SUPPRESS)
    parser.add_argument("--continue", dest="continue_session", action="store_true", help="Reuse current session history for one-shot tasks")
    parser.add_argument("--continue-session", dest="continue_session", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--freeze", action="store_true", help="Read existing Rocky state but do not persist new Rocky state")
    parser.add_argument("--verbose", action="store_true", help="Show full tool call and tool result logs")
    parser.add_argument("-y", "--yes", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--json", action="store_true", help="Print machine-readable output for one-shot tasks")
    parser.add_argument("-V", "--version", action="store_true", help="Print Rocky version and exit")
    return parser
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
    console = make_console()
    if args.version:
        print(f"rocky {__version__}")
        return 0
    requested_text = " ".join(args.task).strip() if args.task else None
    configure_requested = requested_text in {"configure", "/configure"}

    if not args.freeze:
        _maybe_run_first_launch_wizard(cwd, console, allow_wizard=not args.json and not configure_requested)
    elif configure_requested:
        if args.json:
            print(json.dumps({"error": "FreezeMode", "message": "Freeze mode blocks configure because it writes persistent config."}, ensure_ascii=False))
        else:
            console.print(
                "Freeze mode blocks configure because it writes persistent config.",
                markup=False,
                style="yellow",
            )
        return 1
    if configure_requested and not args.json and _interactive_terminal():
        _run_configure_flow(cwd, console)
        return 0

    cli_overrides: dict[str, object] = {}
    if args.provider:
        cli_overrides["active_provider"] = args.provider
    runtime = RockyRuntime.load_from(cwd, cli_overrides=cli_overrides, freeze=args.freeze, verbose=args.verbose)
    provider_name = args.provider or runtime.config.active_provider
    provider_cfg = runtime.config.provider(provider_name)
    runtime.config.active_provider = provider_name
    if args.model:
        provider_cfg.model = args.model
    if args.base_url:
        provider_cfg.base_url = args.base_url.rstrip("/")

    text = _task_text(args)
    if text is None:
        repl = RockyRepl(runtime)
        return repl.run()

    if args.task and args.task[0] in runtime.commands.names:
        text = "/" + " ".join(args.task).strip()
    elif text in runtime.commands.names:
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

        printer = None if args.json else EventPrinter(make_live_console(console), verbose=args.verbose)
        response = runtime.run_prompt(
            text,
            stream=not args.json,
            event_handler=printer,
            continue_session=args.continue_session,
            freeze=args.freeze,
        )
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
                vline = Text()
                vline.append(" verification  ", style="bold yellow")
                vline.append(str(response.verification.get("message", "")))
                console.print(vline)
        return 0
    except Exception as exc:
        if args.json:
            print(json.dumps({"error": exc.__class__.__name__, "message": str(exc)}, ensure_ascii=False))
        else:
            console.print(f"Rocky failed: {exc}", markup=False, style="red")
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
