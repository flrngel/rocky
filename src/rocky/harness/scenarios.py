from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import random
import shutil
import subprocess
from typing import Any

import openpyxl

from rocky.core.router import TaskClass
from rocky.harness.models import MiniProjectScenario, PhaseExpectations, Scenario, WorkspaceContinuityScenario
from rocky.harness.phases import DEFAULT_PHASES


_ADJECTIVES = (
    "atlas",
    "cedar",
    "ember",
    "lumen",
    "sable",
    "spruce",
    "tango",
    "vivid",
)
_NOUNS = (
    "anchor",
    "beacon",
    "compass",
    "harbor",
    "ledger",
    "signal",
    "switch",
    "vector",
)
_PRODUCT_HEADS = (
    "Atlas",
    "Cinder",
    "Harbor",
    "Juniper",
    "Ming River",
    "Northwind",
    "Sable",
    "Vantage",
)
_PRODUCT_TAILS = (
    "Bottle",
    "Journal",
    "Notebook",
    "Original",
    "Reserve",
    "Tonic",
    "Tea",
    "Vermouth",
)
_MONTHS = ("jan", "feb", "mar")
_COUNTRIES = ("US", "CA", "JP", "DE")
_NAMES = ("Ada", "Bryn", "Cam", "Dina", "Eli", "Faye", "Gio", "Hana")
_ROLES = ("dev", "ops", "qa", "pm")
_PHASE_SEEDS = (11, 17, 29, 41)
_MINI_PROJECT_SEEDS = (101, 131, 151, 181)


@dataclass(frozen=True, slots=True)
class WorkspaceBundle:
    seed: int
    token: str
    docs_path: str
    cli_path: str
    shell_tools_path: str
    wizard_path: str
    provider_path: str
    registry_path: str
    permissions_path: str
    session_path: str
    test_rendering_path: str
    catalog_script: str
    catalog_output_json: str
    catalog_products: tuple[dict[str, Any], ...]
    sales_csv: str
    sales_rows: tuple[tuple[str, int, str], ...]
    users_csv: str
    user_rows: tuple[tuple[int, str, str], ...]
    inventory_csv: str
    inventory_rows: tuple[tuple[str, str, int], ...]
    metrics_xlsx: str
    summary_sheet: str
    regions_sheet: str
    notes_txt: str
    note_emails: tuple[str, ...]
    tickets_txt: str
    ticket_rows: tuple[str, ...]
    people_jsonl: str
    people_rows: tuple[dict[str, str], ...]
    todos_txt: str
    todo_items: tuple[str, ...]
    log_path: str
    log_lines: tuple[str, ...]
    report_script_path: str
    env_script_path: str
    history_commands: tuple[str, ...]
    runtime_outputs: dict[str, str]


def _token(seed: int) -> str:
    return f"{_ADJECTIVES[seed % len(_ADJECTIVES)]}_{_NOUNS[(seed * 3) % len(_NOUNS)]}_{seed}"


def _make_id(prefix: str, seed: int, offset: int) -> str:
    return f"{prefix}{seed:02d}{offset:02d}"


def _catalog_products(seed: int) -> tuple[dict[str, Any], ...]:
    rng = random.Random(seed)
    products: list[dict[str, Any]] = []
    for index in range(2):
        head = _PRODUCT_HEADS[(seed + index * 2) % len(_PRODUCT_HEADS)]
        tail = _PRODUCT_TAILS[(seed + index * 3) % len(_PRODUCT_TAILS)]
        size = ("Small", "Original", "Reserve", "500ml")[index % 4]
        name = f"{head} {tail} {size}".strip()
        sku = f"{head[:3].upper()}-{tail[:3].upper()}-{seed + index:03d}"
        product_id = _make_id("P", seed, index + 1)
        match_id = _make_id("C", seed, index * 3 + 1)
        mismatch_one_id = _make_id("C", seed, index * 3 + 2)
        mismatch_two_id = _make_id("C", seed, index * 3 + 3)
        mismatch_name = f"{head} {_PRODUCT_TAILS[(seed + index * 5 + 1) % len(_PRODUCT_TAILS)]} {size}"
        mismatch_sku = f"{head[:3].upper()}-{_PRODUCT_TAILS[(seed + index * 5 + 1) % len(_PRODUCT_TAILS)][:3].upper()}-{seed + index + 7:03d}"
        products.append(
            {
                "product_id": product_id,
                "name": name,
                "sku": sku,
                "candidates": [
                    {"candidate_id": match_id, "name": name, "sku": sku},
                    {"candidate_id": mismatch_one_id, "name": mismatch_name, "sku": mismatch_sku},
                    {"candidate_id": mismatch_two_id, "name": f"{head} {tail} Alt", "sku": f"{head[:3].upper()}-{tail[:3].upper()}-{seed + index + 19:03d}"},
                ],
            }
        )
    rng.shuffle(products)
    return tuple(products)


def _workspace_bundle(seed: int) -> WorkspaceBundle:
    token = _token(seed)
    sales_rows = tuple(
        (month, 90 + seed + index * 17, _COUNTRIES[(seed + index) % len(_COUNTRIES)])
        for index, month in enumerate(_MONTHS)
    )
    user_rows = tuple(
        (
            index + 1,
            _NAMES[(seed + index) % len(_NAMES)],
            f"{_NAMES[(seed + index) % len(_NAMES)].lower()}.{token}@example.com",
        )
        for index in range(3)
    )
    inventory_rows = tuple(
        (
            f"{token[:3].upper()}{index + 1}",
            f"{_PRODUCT_HEADS[(seed + index) % len(_PRODUCT_HEADS)]} {_PRODUCT_TAILS[(seed + index) % len(_PRODUCT_TAILS)]}",
            4 + ((seed + index * 3) % 12),
        )
        for index in range(3)
    )
    people_rows = tuple(
        {
            "name": _NAMES[(seed + index) % len(_NAMES)],
            "role": _ROLES[(seed + index) % len(_ROLES)],
        }
        for index in range(2)
    )
    note_emails = tuple(row[2] for row in user_rows[:2])
    ticket_rows = (
        f"bug: {token} shell output drifts",
        f"feature: add {token} config wizard",
        f"bug: {token} repl colors break",
    )
    todo_items = (
        f"ship {token} cli",
        f"fix {token} repl",
        f"verify {token} tools",
    )
    log_lines = (
        "INFO startup ok",
        "WARN cache warming slow",
        "ERROR disk almost full",
        f"INFO support+{token}@example.com notified",
    )
    history_commands = (
        "pwd",
        "git status --short",
        f"rocky --status {token}",
    )
    runtime_outputs = {
        "python3": "Python 3.14.3",
        "python3.13": "Python 3.13.5",
        "node": "v22.14.0",
        "node18": "v18.20.8",
        "ruby": "ruby 3.2.2p1",
        "ruby3.2": "ruby 3.2.2p1",
        "bun": "1.1.30",
        "whoami": "rockytester",
    }
    return WorkspaceBundle(
        seed=seed,
        token=token,
        docs_path=f"docs/{token}_tui_research.md",
        cli_path=f"src/{token}_cli.py",
        shell_tools_path=f"src/tools/{token}_shell_tools.py",
        wizard_path=f"src/{token}_config_wizard.py",
        provider_path=f"src/providers/{token}_chat_provider.py",
        registry_path=f"src/{token}_tool_registry.py",
        permissions_path=f"src/{token}_permissions.py",
        session_path=f"src/{token}_session_store.py",
        test_rendering_path=f"tests/test_{token}_repl_rendering.py",
        catalog_script=f"{token}_pending_catalog.sh",
        catalog_output_json=f"{token}_merge_decisions.json",
        catalog_products=_catalog_products(seed),
        sales_csv=f"data/{token}_sales.csv",
        sales_rows=sales_rows,
        users_csv=f"data/{token}_users.csv",
        user_rows=user_rows,
        inventory_csv=f"data/{token}_inventory.csv",
        inventory_rows=inventory_rows,
        metrics_xlsx=f"data/{token}_metrics.xlsx",
        summary_sheet="Summary",
        regions_sheet="Regions",
        notes_txt=f"{token}_notes.txt",
        note_emails=note_emails,
        tickets_txt=f"{token}_tickets.txt",
        ticket_rows=ticket_rows,
        people_jsonl=f"data/{token}_people.jsonl",
        people_rows=people_rows,
        todos_txt=f"{token}_todos.txt",
        todo_items=todo_items,
        log_path=f"logs/{token}_app.log",
        log_lines=log_lines,
        report_script_path=f"scripts/{token}_report.sh",
        env_script_path=f"scripts/{token}_env_snapshot.sh",
        history_commands=history_commands,
        runtime_outputs=runtime_outputs,
    )


def _catalog_decisions(bundle: WorkspaceBundle) -> dict[str, Any]:
    products: list[dict[str, Any]] = []
    for product in bundle.catalog_products:
        merge = [
            candidate["candidate_id"]
            for candidate in product["candidates"]
            if candidate["name"] == product["name"] and candidate["sku"] == product["sku"]
        ]
        skip = [
            candidate["candidate_id"]
            for candidate in product["candidates"]
            if candidate["candidate_id"] not in merge
        ]
        products.append(
            {
                "product_id": product["product_id"],
                "merge": merge,
                "skip": skip,
            }
        )
    return {"products": products}


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _run(cmd: list[str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=str(cwd), check=True, capture_output=True, text=True)


def _render_catalog_script(bundle: WorkspaceBundle) -> str:
    payload = {"products": list(bundle.catalog_products)}
    return "#!/bin/sh\ncat <<'JSON'\n" + json.dumps(payload, separators=(",", ":")) + "\nJSON\n"


def _render_csv(headers: tuple[str, ...], rows: tuple[tuple[Any, ...], ...]) -> str:
    lines = [",".join(headers)]
    for row in rows:
        lines.append(",".join(str(item) for item in row))
    return "\n".join(lines) + "\n"


def materialize_scenario_workspace(workspace: Path, home: Path, scenario: Scenario) -> Path:
    bundle = _workspace_bundle(scenario.fixture_seed)
    home.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    bin_dir = workspace / ".harness_bin"
    bin_dir.mkdir(parents=True, exist_ok=True)

    _write(
        home / ".zsh_history",
        "".join(f": 171200000{index}:0;{command}\n" for index, command in enumerate(bundle.history_commands)),
    )
    _write(
        workspace / "README.md",
        (
            f"# Sample {bundle.token} Repo\n\n"
            f"Rocky uses prompt_toolkit and Rich for the TUI.\n"
            f"The CLI parser lives in {bundle.cli_path}.\n"
        ),
    )
    _write(
        workspace / bundle.docs_path,
        f"# TUI Research\n\n{bundle.token} prefers prompt_toolkit for editing and Rich for rendering.\n",
    )
    _write(
        workspace / bundle.cli_path,
        "from argparse import ArgumentParser\n\n"
        "ALIASES = ['configure', 'setup', 'set-up']\n\n"
        "def build_parser():\n"
        "    parser = ArgumentParser(prog='rocky')\n"
        "    return parser\n",
    )
    _write(
        workspace / bundle.shell_tools_path,
        "def read_shell_history(limit=10):\n"
        "    return []\n\n"
        "def inspect_runtime_versions(targets=None):\n"
        "    return []\n",
    )
    _write(
        workspace / bundle.wizard_path,
        "def run_config_wizard(path):\n"
        "    return {'path': path}\n",
    )
    _write(
        workspace / bundle.provider_path,
        "class ChatProvider:\n"
        "    \"\"\"Uses chat completions.\"\"\"\n",
    )
    _write(
        workspace / bundle.registry_path,
        "SUPPORTED_FAMILIES = ['filesystem', 'shell', 'python', 'data', 'git']\n",
    )
    _write(
        workspace / bundle.permissions_path,
        "class PermissionManager:\n"
        "    def check(self, request):\n"
        "        return True\n",
    )
    _write(
        workspace / bundle.session_path,
        "def recent_messages(limit=12):\n"
        "    return []\n\n"
        "# continue_session support lives here\n",
    )
    _write(workspace / "src" / "__init__.py", "__version__ = '0.9.0'\n")
    _write(
        workspace / bundle.test_rendering_path,
        "def test_stream_rendering():\n"
        "    assert 'bracket' != 'broken'\n",
    )
    _write(
        workspace / "pyproject.toml",
        "[project]\nname = 'sample-rocky'\nversion = '0.9.0'\n",
    )
    _write(workspace / bundle.log_path, "\n".join(bundle.log_lines) + "\n")
    _write(workspace / bundle.tickets_txt, "\n".join(bundle.ticket_rows) + "\n")
    _write(workspace / bundle.notes_txt, "Reach " + " and ".join(bundle.note_emails) + " for follow-up.\n")
    _write(workspace / bundle.catalog_script, _render_catalog_script(bundle))
    _write(workspace / bundle.todos_txt, "\n".join(f"- {item}" for item in bundle.todo_items) + "\n")
    _write(workspace / bundle.sales_csv, _render_csv(("month", "revenue", "country"), bundle.sales_rows))
    _write(workspace / bundle.users_csv, _render_csv(("id", "name", "email"), bundle.user_rows))
    _write(workspace / bundle.inventory_csv, _render_csv(("sku", "name", "stock"), bundle.inventory_rows))
    _write(
        workspace / bundle.people_jsonl,
        "".join(json.dumps(row) + "\n" for row in bundle.people_rows),
    )

    workbook = openpyxl.Workbook()
    summary = workbook.active
    summary.title = bundle.summary_sheet
    summary.append(["metric", "value"])
    summary.append(["users", len(bundle.user_rows)])
    summary.append(["sales_total", sum(row[1] for row in bundle.sales_rows)])
    regions = workbook.create_sheet(bundle.regions_sheet)
    regions.append(["region", "revenue"])
    regions.append(["NA", bundle.sales_rows[0][1]])
    regions.append(["EU", bundle.sales_rows[1][1]])
    workbook.save(workspace / bundle.metrics_xlsx)

    for command_name, output in bundle.runtime_outputs.items():
        script = bin_dir / command_name
        _write(script, f"#!/bin/sh\necho {output}\n")
        script.chmod(0o755)

    _run(["git", "init"], workspace)
    _run(["git", "config", "user.email", "rocky@example.com"], workspace)
    _run(["git", "config", "user.name", "Rocky Tests"], workspace)
    _run(["git", "add", "."], workspace)
    _run(["git", "commit", "-m", "initial"], workspace)
    with (workspace / "README.md").open("a", encoding="utf-8") as handle:
        handle.write(f"Local modification for {bundle.token}.\n")

    return bin_dir


def materialize_mini_project_workspace(workspace: Path, scenario: MiniProjectScenario) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    oracle = scenario.oracle
    family = str(oracle.get("family", ""))
    if family == "catalog_review":
        _write(workspace / str(oracle["script_name"]), str(oracle["script_text"]))
        return
    if family in {"wordcount", "sales_report", "email_extract"}:
        return
    raise ValueError(f"unknown mini-project family: {family}")


def _shell_execution_prompt(bundle: WorkspaceBundle, variant: int) -> str:
    templates = (
        "Act as duplicate product reviewer. Execute `{script}` and explore the response. Decide which candidates should merge when `name` and `sku` match exactly.",
        "Run `{script}`, inspect the payload it returns, and determine for each product which candidate ids are true merges versus skips using exact `name` + `sku` matching.",
        "Use tools properly: execute `{script}`, study the returned products, and sort each candidate into merge or not-merge based on exact `name` and `sku` equality.",
        "Execute `{script}` from this workspace, analyze the response, and figure out which candidates are the same product and which are not using exact `name` and `sku` matches.",
    )
    return templates[variant % len(templates)].format(script=bundle.catalog_script)


def _shell_inspection_prompt(bundle: WorkspaceBundle, variant: int) -> str:
    count = 2 + variant
    templates = (
        "Show my current shell, current directory, and the last {count} history entries.",
        "What shell am I using, where am I, and what were my last {count} shell commands?",
        "Tell me the shell environment, current directory, and recent history with the last {count} commands.",
        "Use shell tools to tell me my shell, working directory, and the latest {count} history rows.",
    )
    return templates[variant % len(templates)].format(count=count)


def _runtime_prompt(bundle: WorkspaceBundle, target: str, variant: int) -> str:
    templates = (
        "What {target} versions do I have, what command paths do they use, and confirm one with a shell command?",
        "Which local {target} installs do I have, what command paths do they use, and can you confirm one via shell?",
        "Which {target} runtimes are installed on this machine, where do they live, and confirm one with a command?",
        "Use tools to list installed {target} versions, the command paths they use, and one shell confirmation.",
    )
    return templates[variant % len(templates)].format(target=target)


def _repo_prompt(bundle: WorkspaceBundle, focus: str, variant: int) -> tuple[str, tuple[str, ...]]:
    if focus == "parser":
        return (
            (
                "In this repo, find which file defines the CLI parser and where aliases are declared, "
                "then read the implementation file."
            ),
            (bundle.cli_path,),
        )
    if focus == "shell_tools":
        return (
            (
                "In this repo, find where shell history and runtime inspection are implemented, "
                "then read the implementation file."
            ),
            (bundle.shell_tools_path,),
        )
    if focus == "wizard":
        return (
            (
                "In this repo, locate the config wizard entrypoint, list nearby modules, and read the wizard file."
            ),
            (bundle.wizard_path,),
        )
    return (
        (
            "In this repo, find the provider that handles chat completions, list provider modules, and read its source file."
        ),
        (bundle.provider_path,),
    )


def _data_prompt(bundle: WorkspaceBundle, focus: str, variant: int) -> tuple[str, tuple[str, ...]]:
    if focus == "sales":
        return (
            f"Analyze `{bundle.sales_csv}` and summarize the headers, sample rows, and total revenue.",
            (bundle.sales_csv,),
        )
    if focus == "users":
        return (
            f"Analyze `{bundle.users_csv}` and summarize the headers, sample rows, row count, and email list.",
            (bundle.users_csv,),
        )
    if focus == "inventory":
        return (
            f"Analyze `{bundle.inventory_csv}` and summarize the headers, sample rows, and total stock.",
            (bundle.inventory_csv,),
        )
    return (
        (
            f"Inspect `{bundle.metrics_xlsx}`, compare the `{bundle.summary_sheet}` and `{bundle.regions_sheet}` sample rows, "
            "and count the sheets."
        ),
        (bundle.metrics_xlsx,),
    )


def _extraction_prompt(bundle: WorkspaceBundle, focus: str) -> tuple[str, tuple[str, ...]]:
    if focus == "tickets":
        return (
            f"Classify `{bundle.tickets_txt}` into valid JSON with category counts.",
            (bundle.tickets_txt,),
        )
    if focus == "emails":
        return (
            f"Extract the email addresses from `{bundle.notes_txt}` into valid JSON.",
            (bundle.notes_txt,),
        )
    if focus == "people":
        return (
            f"Normalize `{bundle.people_jsonl}` into valid JSON with row count and fields.",
            (bundle.people_jsonl,),
        )
    return (
        f"Turn `{bundle.todos_txt}` into a clean JSON array of todo items.",
        (bundle.todos_txt,),
    )


def _automation_prompt(bundle: WorkspaceBundle, focus: str) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    if focus == "report":
        return (
            (
                f"From scratch in this workspace, create exactly one shell script at `{bundle.report_script_path}` "
                f"that totals the revenue in `{bundle.sales_csv}`, then run it and tell me the exact output."
            ),
            (bundle.sales_csv,),
            (bundle.report_script_path,),
        )
    return (
        (
            f"From scratch in this workspace, create exactly one environment snapshot script at `{bundle.env_script_path}` "
            "that prints HOME and SHELL, then run it and tell me the exact output."
        ),
        (),
        (bundle.env_script_path,),
    )


def _build_default_scenarios() -> tuple[Scenario, ...]:
    scenarios: list[Scenario] = []
    repo_focuses = ("parser", "shell_tools", "wizard", "provider")
    data_focuses = ("sales", "users", "inventory", "workbook")
    extraction_focuses = ("tickets", "emails", "people", "todos")
    runtime_targets = ("python", "node", "ruby", "bun")
    automation_focuses = ("report", "env")

    for variant, seed in enumerate(_PHASE_SEEDS):
        bundle = _workspace_bundle(seed)
        catalog_decisions = _catalog_decisions(bundle)
        scenarios.append(
            Scenario(
                name=f"shell_execution_{bundle.token}",
                prompt=_shell_execution_prompt(bundle, variant),
                task_class=TaskClass.REPO,
                task_signature="repo/shell_execution",
                tool_families=("filesystem", "shell", "python", "git"),
                fixture_seed=seed,
                phase_expectations=PhaseExpectations(
                    anchor_tools=("run_shell_command",),
                    min_successful_tools=2,
                    phase2_required_tools=("run_shell_command",),
                    phase2_required_any=("read_file", "run_python", "write_file", "stat_path"),
                    required_tools=("run_shell_command",),
                    requires_non_shell_follow_up=True,
                    response_markers=tuple(item["product_id"] for item in catalog_decisions["products"]),
                ),
                tags=("repo", "shell_execution", "generated"),
                oracle={
                    "family": "catalog_review",
                    "script_name": bundle.catalog_script,
                    "referenced_paths": (bundle.catalog_script,),
                    "expected_decisions": catalog_decisions,
                },
            )
        )
        scenarios.append(
            Scenario(
                name=f"shell_inspection_{bundle.token}",
                prompt=_shell_inspection_prompt(bundle, variant),
                task_class=TaskClass.REPO,
                task_signature="repo/shell_inspection",
                tool_families=("shell", "filesystem"),
                fixture_seed=seed,
                phase_expectations=PhaseExpectations(
                    anchor_tools=("inspect_shell_environment", "read_shell_history", "run_shell_command"),
                    min_successful_tools=2,
                    phase2_required_any=("inspect_shell_environment", "read_shell_history", "run_shell_command"),
                    response_markers=("rockytester",),
                ),
                tags=("repo", "shell_inspection", "generated"),
                oracle={"family": "shell_inspection", "referenced_paths": ()},
            )
        )
        target = runtime_targets[variant % len(runtime_targets)]
        scenarios.append(
            Scenario(
                name=f"runtime_{target}_{bundle.token}",
                prompt=_runtime_prompt(bundle, target, variant),
                task_class=TaskClass.REPO,
                task_signature="local/runtime_inspection",
                tool_families=("shell",),
                fixture_seed=seed,
                phase_expectations=PhaseExpectations(
                    anchor_tools=("inspect_runtime_versions",),
                    min_successful_tools=2,
                    phase2_required_tools=("inspect_runtime_versions",),
                    phase2_required_any=("run_shell_command", "inspect_shell_environment"),
                    required_tools=("inspect_runtime_versions",),
                    response_markers=(target,),
                ),
                tags=("runtime", "generated"),
                oracle={"family": "runtime", "target": target, "referenced_paths": ()},
            )
        )
        repo_focus = repo_focuses[variant % len(repo_focuses)]
        repo_prompt, repo_paths = _repo_prompt(bundle, repo_focus, variant)
        scenarios.append(
            Scenario(
                name=f"repo_lookup_{repo_focus}_{bundle.token}",
                prompt=repo_prompt,
                task_class=TaskClass.REPO,
                task_signature="repo/general",
                tool_families=("filesystem", "shell", "git", "python"),
                fixture_seed=seed,
                phase_expectations=PhaseExpectations(
                    anchor_tools=("grep_files", "list_files", "read_file", "git_status", "git_recent_commits", "git_diff"),
                    min_successful_tools=2,
                    phase2_required_any=("read_file", "grep_files", "git_recent_commits", "git_diff", "list_files"),
                    response_markers=tuple(Path(path).name for path in repo_paths),
                ),
                tags=("repo", "general", "generated"),
                oracle={"family": "repo_lookup", "focus": repo_focus, "referenced_paths": repo_paths},
            )
        )
        data_focus = data_focuses[variant % len(data_focuses)]
        data_prompt, data_paths = _data_prompt(bundle, data_focus, variant)
        scenarios.append(
            Scenario(
                name=f"data_{data_focus}_{bundle.token}",
                prompt=data_prompt,
                task_class=TaskClass.DATA,
                task_signature="data/spreadsheet/analysis",
                tool_families=("filesystem", "data", "python"),
                fixture_seed=seed,
                phase_expectations=PhaseExpectations(
                    anchor_tools=("inspect_spreadsheet",),
                    min_successful_tools=2,
                    phase2_required_tools=("inspect_spreadsheet",),
                    phase2_required_any=("read_sheet_range", "run_python"),
                    required_tools=("inspect_spreadsheet",),
                    forbidden_tools=("write_file",),
                    response_markers=tuple(Path(path).name for path in data_paths),
                ),
                tags=("data", "generated"),
                oracle={"family": "data", "focus": data_focus, "referenced_paths": data_paths},
            )
        )
        extraction_focus = extraction_focuses[variant % len(extraction_focuses)]
        extraction_prompt, extraction_paths = _extraction_prompt(bundle, extraction_focus)
        scenarios.append(
            Scenario(
                name=f"extract_{extraction_focus}_{bundle.token}",
                prompt=extraction_prompt,
                task_class=TaskClass.EXTRACTION,
                task_signature="extract/general",
                tool_families=("filesystem", "python", "data"),
                output_kind="json",
                fixture_seed=seed,
                phase_expectations=PhaseExpectations(
                    anchor_tools=("read_file", "stat_path", "glob_paths", "list_files"),
                    min_successful_tools=2,
                    phase2_required_any=("run_python", "read_file", "stat_path"),
                    forbidden_tools=("write_file",),
                    requires_json_output=True,
                    response_markers=tuple(Path(path).name for path in extraction_paths),
                ),
                tags=("extract", "generated"),
                oracle={"family": "extract", "focus": extraction_focus, "referenced_paths": extraction_paths},
            )
        )
        automation_focus = automation_focuses[variant % len(automation_focuses)]
        automation_prompt, referenced_paths, created_paths = _automation_prompt(bundle, automation_focus)
        scenarios.append(
            Scenario(
                name=f"automation_{automation_focus}_{bundle.token}",
                prompt=automation_prompt,
                task_class=TaskClass.AUTOMATION,
                task_signature="automation/general",
                tool_families=("filesystem", "shell", "python"),
                fixture_seed=seed,
                phase_expectations=PhaseExpectations(
                    anchor_tools=("write_file", "list_files", "read_file", "run_shell_command"),
                    min_successful_tools=3,
                    phase2_required_tools=("write_file",),
                    phase2_required_any=("run_shell_command",),
                    required_tools=("write_file", "run_shell_command"),
                    response_markers=tuple(Path(path).name for path in created_paths),
                ),
                tags=("automation", "generated"),
                oracle={
                    "family": "automation",
                    "focus": automation_focus,
                    "referenced_paths": referenced_paths,
                    "created_paths": created_paths,
                },
            )
        )

    return tuple(scenarios)


def _build_mini_project_scenarios() -> tuple[MiniProjectScenario, ...]:
    scenarios: list[MiniProjectScenario] = []

    for seed in _MINI_PROJECT_SEEDS:
        bundle = _workspace_bundle(seed)
        if len(scenarios) == 0:
            lines = (
                f"{bundle.token} builds tools",
                f"tools build trust {seed}",
            )
            input_file = f"input_{bundle.token}.txt"
            script_name = f"count_{bundle.token}.py"
            readme_name = f"README_{bundle.token}.md"
            expected_output = {"line_count": 2, "word_count": sum(len(line.split()) for line in lines)}
            scenarios.append(
                MiniProjectScenario(
                    name=f"wordcount_{bundle.token}",
                    prompt=(
                        "Build a tiny Python script project in this empty workspace. "
                        f"Create exactly these files: `{input_file}` with exactly two lines `{lines[0]}` and `{lines[1]}`, "
                        f"`{script_name}` that reads `{input_file}` and prints valid JSON with keys `line_count` and `word_count`, "
                        f"and `{readme_name}` with one short usage example. Then run `python3 {script_name}` to verify it works and tell me the exact JSON output."
                    ),
                    expected_files=(input_file, script_name, readme_name),
                    verify_command=("python3", script_name),
                    expected_output=expected_output,
                    output_kind="json",
                    response_snippets=(script_name, str(expected_output["word_count"]), "line_count"),
                    fixture_seed=seed,
                    phase_expectations=PhaseExpectations(
                        anchor_tools=("write_file", "list_files", "read_file"),
                        min_successful_tools=3,
                        required_tools=("write_file", "run_shell_command"),
                    ),
                    oracle={
                        "family": "wordcount",
                        "input_file": input_file,
                        "input_text": "\n".join(lines) + "\n",
                    },
                )
            )
            continue
        if len(scenarios) == 1:
            sales_file = f"sales_{bundle.token}.csv"
            report_script = f"report_{bundle.token}.sh"
            readme_name = f"README_{bundle.token}.md"
            expected_total = sum(row[1] for row in bundle.sales_rows)
            row_text = ", ".join(
                f"`{month},{revenue},{country}`"
                for month, revenue, country in bundle.sales_rows
            )
            scenarios.append(
                MiniProjectScenario(
                    name=f"sales_report_{bundle.token}",
                    prompt=(
                        "Build a tiny shell script project in this empty workspace. "
                        f"Create exactly these files: `{sales_file}` with header `month,revenue,country` and rows {row_text}, "
                        f"`{report_script}` that prints only the total revenue from `{sales_file}`, "
                        f"and `{readme_name}` with one short usage example. Then run `sh {report_script}` to verify it works and tell me the exact output."
                    ),
                    expected_files=(sales_file, report_script, readme_name),
                    verify_command=("sh", report_script),
                    expected_output=str(expected_total),
                    response_snippets=(report_script, str(expected_total)),
                    fixture_seed=seed,
                    phase_expectations=PhaseExpectations(
                        anchor_tools=("write_file", "list_files", "read_file"),
                        min_successful_tools=3,
                        required_tools=("write_file", "run_shell_command"),
                    ),
                    oracle={
                        "family": "sales_report",
                        "sales_file": sales_file,
                        "sales_csv": _render_csv(("month", "revenue", "country"), bundle.sales_rows),
                    },
                )
            )
            continue
        if len(scenarios) == 2:
            notes_file = f"notes_{bundle.token}.txt"
            script_name = f"extract_{bundle.token}.py"
            readme_name = f"README_{bundle.token}.md"
            expected_output = {"emails": sorted(bundle.note_emails)}
            scenarios.append(
                MiniProjectScenario(
                    name=f"email_extract_{bundle.token}",
                    prompt=(
                        "Build a tiny Python script project in this empty workspace. "
                        f"Create exactly these files: `{notes_file}` with exactly two lines containing `{bundle.note_emails[0]}` and `{bundle.note_emails[1]}`, "
                        f"`{script_name}` that reads `{notes_file}` and prints valid JSON with a single key `emails` whose value is a sorted array, "
                        f"and `{readme_name}` with one short usage example. Then run `python3 {script_name}` to verify it works and tell me the exact JSON output."
                    ),
                    expected_files=(notes_file, script_name, readme_name),
                    verify_command=("python3", script_name),
                    expected_output=expected_output,
                    output_kind="json",
                    response_snippets=(script_name, bundle.note_emails[0], bundle.note_emails[1]),
                    fixture_seed=seed,
                    phase_expectations=PhaseExpectations(
                        anchor_tools=("write_file", "list_files", "read_file"),
                        min_successful_tools=3,
                        required_tools=("write_file", "run_shell_command"),
                    ),
                    oracle={
                        "family": "email_extract",
                        "notes_file": notes_file,
                        "notes_text": f"{bundle.note_emails[0]}\n{bundle.note_emails[1]}\n",
                    },
                )
            )
            continue
        expected_output = _catalog_decisions(bundle)
        scenarios.append(
            MiniProjectScenario(
                name=f"catalog_review_{bundle.token}",
                prompt=(
                    "You are now product catalog manager. "
                    f"Execute `{bundle.catalog_script}` and explore the response. "
                    "Those are pending products to be managed, and each product has candidates to merge. "
                    "Treat a candidate as the same product only when both `name` and `sku` match exactly. "
                    f"Write valid JSON to `{bundle.catalog_output_json}` with a top-level key `products`, where each item contains "
                    "`product_id`, `merge`, and `skip` arrays of candidate ids. Then read the file and tell me the exact JSON."
                ),
                expected_files=(bundle.catalog_script, bundle.catalog_output_json),
                verify_command=(
                    "python3",
                    "-c",
                    f"import json, pathlib; print(json.dumps(json.loads(pathlib.Path('{bundle.catalog_output_json}').read_text()), sort_keys=True))",
                ),
                expected_output=expected_output,
                output_kind="json",
                response_snippets=(bundle.catalog_output_json, expected_output["products"][0]["merge"][0]),
                fixture_seed=seed,
                task_class=TaskClass.REPO,
                task_signature="repo/shell_execution",
                phase_expectations=PhaseExpectations(
                    anchor_tools=("run_shell_command", "read_file"),
                    min_successful_tools=3,
                    phase2_required_tools=("run_shell_command",),
                    phase2_required_any=("run_python", "read_file", "write_file"),
                    required_tools=("run_shell_command", "read_file"),
                    requires_non_shell_follow_up=True,
                ),
                oracle={
                    "family": "catalog_review",
                    "script_name": bundle.catalog_script,
                    "output_file": bundle.catalog_output_json,
                    "script_text": _render_catalog_script(bundle),
                    "expected_output": expected_output,
                },
            )
        )

    return tuple(scenarios)


def _build_continuity_scenarios() -> tuple[WorkspaceContinuityScenario, ...]:
    bundles = tuple(_workspace_bundle(seed) for seed in _PHASE_SEEDS[:3])
    return (
        WorkspaceContinuityScenario(
            name=f"resume_parser_{bundles[0].token}",
            seed_prompt=f"Implement parser work in {bundles[0].cli_path} and keep focusing on aliases.",
            seed_answer=f"Updated {bundles[0].cli_path} and kept focus on aliases plus parser wiring.",
            follow_up_prompt="continue the parser work in this project",
            expected_markers=(bundles[0].cli_path, "aliases"),
            fixture_seed=bundles[0].seed,
        ),
        WorkspaceContinuityScenario(
            name=f"resume_shell_tools_{bundles[1].token}",
            seed_prompt=f"Improve shell history support in {bundles[1].shell_tools_path} and keep runtime inspection nearby.",
            seed_answer=f"Edited {bundles[1].shell_tools_path} and kept the shell history/runtime inspection focus.",
            follow_up_prompt="pick up the shell tooling work",
            expected_markers=(bundles[1].shell_tools_path, "runtime inspection"),
            fixture_seed=bundles[1].seed,
        ),
        WorkspaceContinuityScenario(
            name=f"resume_report_script_{bundles[2].token}",
            seed_prompt=f"Build the report helper at {bundles[2].report_script_path} and remember the sales file path.",
            seed_answer=f"Created {bundles[2].report_script_path} and kept the sales file path in view.",
            follow_up_prompt="continue the reporting helper work",
            expected_markers=(bundles[2].report_script_path, bundles[2].sales_csv),
            fixture_seed=bundles[2].seed,
        ),
    )


def default_scenarios() -> tuple[Scenario, ...]:
    return _build_default_scenarios()


def phase4_mini_projects() -> tuple[MiniProjectScenario, ...]:
    return _build_mini_project_scenarios()


def workspace_continuity_scenarios() -> tuple[WorkspaceContinuityScenario, ...]:
    return _build_continuity_scenarios()


def _first_scenario(task_signature: str) -> Scenario:
    return next(scenario for scenario in default_scenarios() if scenario.task_signature == task_signature)


def _first_project(family: str) -> MiniProjectScenario:
    return next(
        scenario
        for scenario in phase4_mini_projects()
        if str(scenario.oracle.get("family") or "") == family
    )


def _learning_case_from_project(project: MiniProjectScenario) -> dict[str, object]:
    family = str(project.oracle.get("family") or "")
    if family == "catalog_review":
        script_name = str(project.oracle["script_name"])
        output_path = f"retry_{Path(str(project.oracle['output_file'])).name}"
        return {
            "name": f"learning_{family}_{project.fixture_seed}",
            "summary": "Teach Rocky to resume existing catalog-review work in a fresh process and reuse the learned correction on the next turn.",
            "workspace_profile": "mini_project",
            "fixture_seed": project.fixture_seed,
            "baseline_prompt": project.prompt,
            "teach_feedback": (
                f"When continuing catalog-review work in this workspace, keep `{script_name}` in focus. "
                "Execute the existing workspace script first. "
                "If it returns structured data, parse it with `run_python` before making merge decisions. "
                "When the user asks for a result file, write the exact JSON there and reread it before answering."
            ),
            "retry_prompt": (
                "continue the catalog review work in this project. "
                f"Re-run the existing workspace script, write the final exact JSON merge decisions to `{output_path}` "
                "with a top-level key `products`, where each item contains `product_id`, `merge`, and `skip` arrays of candidate ids, "
                "then read that file back and tell me the exact JSON."
            ),
            "expected_task_signature": "repo/shell_execution",
            "expected_tools": ["run_shell_command", "run_python", "write_file", "read_file"],
            "expected_artifacts": [output_path],
            "phases": ["prepare_workspace", "install_and_baseline", "teach", "retry_with_learning", "grade_results"],
        }
    if family == "sales_report":
        report_script = str(project.verify_command[1])
        sales_file = str(project.oracle["sales_file"])
        return {
            "name": f"learning_{family}_{project.fixture_seed}",
            "summary": "Teach Rocky to keep existing project files in focus and verify them again in a fresh process.",
            "workspace_profile": "mini_project",
            "fixture_seed": project.fixture_seed,
            "baseline_prompt": project.prompt,
            "teach_feedback": (
                f"When continuing report-helper work in this workspace, keep `{report_script}` and `{sales_file}` in view. "
                "Use the existing project files, reread the script before verifying it, then run the exact command and name the exact observed output in the final answer."
            ),
            "retry_prompt": (
                "continue the reporting helper work in this project. "
                "Re-read the existing script if needed, then tell me the exact verified output."
            ),
            "expected_task_signature": "automation/general",
            "expected_tools": ["read_file", "run_shell_command"],
            "expected_artifacts": [report_script],
            "phases": ["prepare_workspace", "install_and_baseline", "teach", "retry_with_learning", "grade_results"],
        }
    raise ValueError(f"unsupported learning project family: {family}")


def agentic_playbook() -> dict[str, object]:
    repo_lookup = _first_scenario("repo/general")
    exact_output = _first_project("sales_report")
    learning_project = _first_project("catalog_review")
    learning_case = _learning_case_from_project(learning_project)
    phases = [
        {
            "slug": phase.slug,
            "title": phase.title,
            "description": phase.description,
            "success_signals": list(phase.success_signals),
        }
        for phase in DEFAULT_PHASES
    ]
    return {
        "strategy": "installed_cli_agentic_scenarios",
        "provider": "current ollama via installed rocky CLI",
        "notes": [
            "Use generated workspaces instead of fixed example repositories or hard-coded case literals.",
            "Judge both agentic behavior and task result from the real installed `rocky` process.",
            "For learning scenarios, verify that `/learn` persists reusable guidance and that a fresh Rocky process loads it on the retry.",
        ],
        "phases": phases,
        "scenarios": [
            {
                "name": f"agentic_repo_lookup_{repo_lookup.fixture_seed}",
                "summary": "Run a repo-inspection task and confirm multi-step tool use plus a grounded answer.",
                "workspace_profile": repo_lookup.workspace_profile,
                "fixture_seed": repo_lookup.fixture_seed,
                "baseline_prompt": repo_lookup.prompt,
                "expected_task_signature": repo_lookup.task_signature,
                "expected_tools": list(repo_lookup.phase_expectations.anchor_tools),
                "manual_steps": [
                    "mkdir -p /tmp/rocky/<tmpid>",
                    "pipx install --force <repo_root>",
                    "rocky --provider ollama --cwd /tmp/rocky/<tmpid> --json '<baseline_prompt>'",
                ],
            },
            {
                "name": f"agentic_exact_output_{exact_output.fixture_seed}",
                "summary": "Run a build-and-verify task and compare the observed output from the created project files.",
                "workspace_profile": exact_output.workspace_profile,
                "fixture_seed": exact_output.fixture_seed,
                "baseline_prompt": exact_output.prompt,
                "expected_task_signature": exact_output.task_signature,
                "expected_files": list(exact_output.expected_files),
                "manual_steps": [
                    "mkdir -p /tmp/rocky/<tmpid>",
                    "pipx install --force <repo_root>",
                    "rocky --provider ollama --cwd /tmp/rocky/<tmpid> --json '<baseline_prompt>'",
                    "run the verify command from the scenario and compare it with Rocky's answer",
                ],
            },
            {
                **learning_case,
                "manual_steps": [
                    "mkdir -p /tmp/rocky/<tmpid>",
                    "pipx install --force <repo_root>",
                    "rocky --provider ollama --cwd /tmp/rocky/<tmpid> --json '<baseline_prompt>'",
                    "rocky --provider ollama --cwd /tmp/rocky/<tmpid> --json learn '<teach_feedback>'",
                    "rocky --provider ollama --cwd /tmp/rocky/<tmpid> --json '<retry_prompt>'",
                ],
            },
        ],
    }


def scenarios_by_phase(phase_slug: str) -> tuple[object, ...]:
    playbook = agentic_playbook()
    return tuple(
        scenario
        for scenario in playbook["scenarios"]
        if phase_slug in tuple(scenario.get("phases") or ())
    )


def harness_inventory() -> dict[str, object]:
    return agentic_playbook()
