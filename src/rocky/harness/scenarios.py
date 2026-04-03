from __future__ import annotations

from rocky.core.router import TaskClass
from rocky.harness.models import MiniProjectScenario, Scenario, ToolStep, WorkspaceContinuityScenario


def step(name: str, **arguments: object) -> ToolStep:
    return ToolStep(name=name, arguments=arguments)


def shell_execution(name: str, prompt: str, *plan: ToolStep, output_kind: str = "plain") -> Scenario:
    return Scenario(
        name=name,
        prompt=prompt,
        task_class=TaskClass.REPO,
        task_signature="repo/shell_execution",
        tool_families=("filesystem", "shell", "python", "git"),
        plan=plan,
        output_kind=output_kind,
        tags=("repo", "shell_execution"),
    )


def shell_inspection(name: str, prompt: str, *plan: ToolStep) -> Scenario:
    return Scenario(
        name=name,
        prompt=prompt,
        task_class=TaskClass.REPO,
        task_signature="repo/shell_inspection",
        tool_families=("shell", "filesystem"),
        plan=plan,
        tags=("repo", "shell_inspection"),
    )


def runtime_inspection(name: str, prompt: str, *plan: ToolStep) -> Scenario:
    return Scenario(
        name=name,
        prompt=prompt,
        task_class=TaskClass.REPO,
        task_signature="local/runtime_inspection",
        tool_families=("shell",),
        plan=plan,
        tags=("runtime",),
    )


def repo_general(name: str, prompt: str, *plan: ToolStep) -> Scenario:
    return Scenario(
        name=name,
        prompt=prompt,
        task_class=TaskClass.REPO,
        task_signature="repo/general",
        tool_families=("filesystem", "shell", "git", "python"),
        plan=plan,
        tags=("repo", "general"),
    )


def data_task(name: str, prompt: str, *plan: ToolStep) -> Scenario:
    return Scenario(
        name=name,
        prompt=prompt,
        task_class=TaskClass.DATA,
        task_signature="data/spreadsheet/analysis",
        tool_families=("filesystem", "data", "python"),
        plan=plan,
        tags=("data", "spreadsheet"),
    )


def extraction_task(name: str, prompt: str, *plan: ToolStep) -> Scenario:
    return Scenario(
        name=name,
        prompt=prompt,
        task_class=TaskClass.EXTRACTION,
        task_signature="extract/general",
        tool_families=("filesystem", "python", "data"),
        plan=plan,
        output_kind="json",
        tags=("extraction",),
    )


def automation_task(name: str, prompt: str, *plan: ToolStep) -> Scenario:
    return Scenario(
        name=name,
        prompt=prompt,
        task_class=TaskClass.AUTOMATION,
        task_signature="automation/general",
        tool_families=("filesystem", "shell", "python"),
        plan=plan,
        tags=("automation",),
    )


CATALOG_X_SH = """#!/bin/sh
cat <<'JSON'
{"products":[{"product_id":"P001","name":"Red T-Shirt","sku":"RTS-001","candidates":[{"candidate_id":"C001","name":"Red T-Shirt","sku":"RTS-001"},{"candidate_id":"C002","name":"Blue T-Shirt","sku":"BTS-002"},{"candidate_id":"C003","name":"Red Hoodie","sku":"RHD-003"}]},{"product_id":"P002","name":"Glass Bottle 500ml","sku":"BOT-500-CLR","candidates":[{"candidate_id":"C010","name":"Glass Bottle 500ml","sku":"BOT-500-CLR"},{"candidate_id":"C011","name":"Glass Bottle 750ml","sku":"BOT-750-CLR"},{"candidate_id":"C012","name":"Plastic Bottle 500ml","sku":"BOT-500-PL"}]}]}
JSON
"""


CATALOG_DECISIONS_CODE = (
    "import json, pathlib, subprocess; "
    "payload=json.loads(subprocess.check_output(['sh', 'x.sh'], text=True)); "
    "products=["
    "{'product_id': product['product_id'], "
    "'merge': [candidate['candidate_id'] for candidate in product['candidates'] if candidate['name']==product['name'] and candidate['sku']==product['sku']], "
    "'skip': [candidate['candidate_id'] for candidate in product['candidates'] if not (candidate['name']==product['name'] and candidate['sku']==product['sku'])]}"
    " for product in payload['products']]; "
    "decisions={'products': products}; "
    "pathlib.Path('merge_decisions.json').write_text(json.dumps(decisions, sort_keys=True), encoding='utf-8'); "
    "print(json.dumps(decisions, sort_keys=True))"
)


SCENARIOS: list[Scenario] = [

    shell_execution(
        "exec_pwd_whoami_env",
        "run pwd and whoami, then inspect the shell environment",
        step("run_shell_command", command="pwd", timeout_s=5),
        step("run_shell_command", command="whoami", timeout_s=5),
        step("inspect_shell_environment"),
    ),
    shell_execution(
        "exec_python_version_path_runtime",
        "run python3 --version and which python3, then inspect python3 runtime variants",
        step("run_shell_command", command="python3 --version", timeout_s=5),
        step("run_shell_command", command="which python3", timeout_s=5),
        step("inspect_runtime_versions", targets=["python3"], max_variants=10),
    ),
    shell_execution(
        "exec_list_and_count",
        "execute ls and count the entries, then inspect the readme path",
        step("run_shell_command", command="ls -1", timeout_s=5),
        step("run_shell_command", command="ls -1 | wc -l", timeout_s=5),
        step("stat_path", path="README.md"),
    ),
    shell_execution(
        "exec_readme_head_and_lines",
        "run head on README.md, count its lines, and then read it",
        step("run_shell_command", command="head -n 3 README.md", timeout_s=5),
        step("run_shell_command", command="wc -l README.md", timeout_s=5),
        step("read_file", path="README.md", start_line=1, end_line=6),
    ),
    shell_execution(
        "exec_git_branch_status_commit",
        "run git branch and git status, then inspect the most recent commit",
        step("run_shell_command", command="git branch --show-current", timeout_s=5),
        step("run_shell_command", command="git status --short", timeout_s=5),
        step("git_recent_commits", count=1),
    ),
    shell_execution(
        "exec_create_note_and_verify",
        "run a command that creates note.txt, then read it and stat it",
        step("run_shell_command", command="printf 'hello from rocky\\n' > note.txt", timeout_s=5),
        step("read_file", path="note.txt", start_line=1, end_line=4),
        step("stat_path", path="note.txt"),
    ),
    shell_execution(
        "exec_copy_log_and_verify",
        "run a command that copies the log, then read the copy and inspect its size",
        step("run_shell_command", command="cp logs/app.log logs/app_copy.log", timeout_s=5),
        step("read_file", path="logs/app_copy.log", start_line=1, end_line=5),
        step("stat_path", path="logs/app_copy.log"),
    ),
    shell_execution(
        "exec_home_shell_snapshot",
        "run commands to print HOME and SHELL, then inspect the shell environment",
        step("run_shell_command", command="printf '%s\\n' \"$HOME\"", timeout_s=5),
        step("run_shell_command", command="printf '%s\\n' \"$SHELL\"", timeout_s=5),
        step("inspect_shell_environment"),
    ),
    shell_execution(
        "exec_existing_script_catalog_review",
        "You are now product catalog manager. Execute `x.sh` and explore the response. "
        "Those are pending products to be managed, and each product has candidates to merge. "
        "Treat a candidate as the same product only when both `name` and `sku` match exactly. "
        "Write valid JSON to `merge_decisions.json`, then read it and tell me the exact JSON.",
        step("run_shell_command", command="sh x.sh", timeout_s=5),
        step("run_python", code=CATALOG_DECISIONS_CODE, timeout_s=5),
        step("read_file", path="merge_decisions.json", start_line=1, end_line=40),
    ),
    shell_inspection(
        "inspect_shell_cwd_history",
        "what shell am i using, where am i, and what are my recent shell history entries",
        step("inspect_shell_environment"),
        step("read_shell_history", limit=3),
        step("run_shell_command", command="pwd", timeout_s=5),
    ),
    shell_inspection(
        "inspect_user_home_shell",
        "who am i, what is my home directory, what shell am i using, and what was my latest shell command",
        step("inspect_shell_environment"),
        step("read_shell_history", limit=1),
        step("run_shell_command", command="whoami", timeout_s=5),
    ),
    shell_inspection(
        "inspect_history_and_shell_name",
        "show my recent command history, current shell, and current directory",
        step("read_shell_history", limit=4),
        step("inspect_shell_environment"),
        step("run_shell_command", command="printf '%s\\n' \"$SHELL\"", timeout_s=5),
    ),
    shell_inspection(
        "inspect_env_triplet_and_history",
        "what environment values do USER HOME SHELL have and what was my latest shell command",
        step("inspect_shell_environment"),
        step("run_shell_command", command="env | grep -E '^(USER|HOME|SHELL)='", timeout_s=5),
        step("read_shell_history", limit=1),
    ),
    shell_inspection(
        "inspect_working_directory_identity",
        "what is my working directory, who am i, and what shell history is available",
        step("inspect_shell_environment"),
        step("run_shell_command", command="whoami", timeout_s=5),
        step("read_shell_history", limit=2),
    ),
    shell_inspection(
        "inspect_history_source_and_home",
        "what shell history source is in use, what is my home directory, what shell am i in, and what are my last two shell commands",
        step("inspect_shell_environment"),
        step("read_shell_history", limit=2),
        step("run_shell_command", command="printf '%s:%s\\n' \"$HOME\" \"$SHELL\"", timeout_s=5),
    ),
    shell_inspection(
        "inspect_last_commands_and_identity",
        "show my last shell commands, my username, and my current directory",
        step("read_shell_history", limit=3),
        step("run_shell_command", command="whoami", timeout_s=5),
        step("inspect_shell_environment"),
    ),
    runtime_inspection(
        "runtime_python_versions",
        "what python versions do i have, what command paths do they use, and confirm one with a shell command",
        step("inspect_runtime_versions", targets=["python"], max_variants=10),
        step("run_shell_command", command="which -a python python3 python3.13 python3.14 2>/dev/null || true", timeout_s=5),
        step("run_shell_command", command="python3 --version", timeout_s=5),
    ),
    runtime_inspection(
        "runtime_python_versions_system",
        "what python versions in my system do i have, what command paths do they use, and confirm one with a shell command",
        step("inspect_runtime_versions", targets=["python"], max_variants=10),
        step("run_shell_command", command="which -a python python3 python3.13 python3.14 2>/dev/null || true", timeout_s=5),
        step("run_shell_command", command="python3.13 --version", timeout_s=5),
    ),
    runtime_inspection(
        "runtime_node_versions",
        "what node versions do i have, what command paths do they use, and confirm one with a shell command",
        step("inspect_runtime_versions", targets=["node"], max_variants=10),
        step("run_shell_command", command="which -a node node18 node22 2>/dev/null || true", timeout_s=5),
        step("run_shell_command", command="node --version", timeout_s=5),
    ),
    runtime_inspection(
        "runtime_node_versions_system",
        "what node versions in my system do i have, what command paths do they use, and confirm one with a shell command",
        step("inspect_runtime_versions", targets=["node"], max_variants=10),
        step("run_shell_command", command="which -a node node18 node22 2>/dev/null || true", timeout_s=5),
        step("run_shell_command", command="node18 --version", timeout_s=5),
    ),
    runtime_inspection(
        "runtime_ruby_versions",
        "what ruby versions do i have, what command paths do they use, and confirm one with a shell command",
        step("inspect_runtime_versions", targets=["ruby"], max_variants=10),
        step("run_shell_command", command="which -a ruby ruby3.2 2>/dev/null || true", timeout_s=5),
        step("run_shell_command", command="ruby --version", timeout_s=5),
    ),
    runtime_inspection(
        "runtime_ruby_versions_system",
        "what ruby versions are in my system, what command paths do they use, and confirm one with a shell command",
        step("inspect_runtime_versions", targets=["ruby"], max_variants=10),
        step("run_shell_command", command="which -a ruby ruby3.2 2>/dev/null || true", timeout_s=5),
        step("run_shell_command", command="ruby3.2 --version", timeout_s=5),
    ),
    runtime_inspection(
        "runtime_where_python3",
        "where is python3 on this machine, what command path does it use, and confirm it with a shell command",
        step("inspect_runtime_versions", targets=["python3"], max_variants=10),
        step("run_shell_command", command="which -a python3 2>/dev/null || true", timeout_s=5),
        step("run_shell_command", command="python3 --version", timeout_s=5),
    ),
    runtime_inspection(
        "runtime_bun_installed",
        "is bun installed here, what command path does it use, and confirm it with a shell command",
        step("inspect_runtime_versions", targets=["bun"], max_variants=10),
        step("run_shell_command", command="which -a bun 2>/dev/null || true", timeout_s=5),
        step("run_shell_command", command="bun --version", timeout_s=5),
    ),
    repo_general(
        "repo_git_status_commit_diff",
        "in this repo, show current git status, the last commit message, and the readme diff",
        step("git_status"),
        step("git_recent_commits", count=1),
        step("git_diff", path="README.md"),
    ),
    repo_general(
        "repo_modified_files_branch",
        "in this repo, what files are modified, what branch is active, and what does the README diff show",
        step("git_status"),
        step("run_shell_command", command="git branch --show-current", timeout_s=5),
        step("git_diff", path="README.md"),
    ),
    repo_general(
        "repo_cli_parser",
        "in this repo, which file defines the CLI parser, where are the aliases, and what does the parser entry look like",
        step("grep_files", pattern="def build_parser", path="src", glob="*.py", max_hits=20),
        step("grep_files", pattern="ALIASES|setup|set-up|configure", path="src", glob="*.py", max_hits=20),
        step("read_file", path="src/cli.py", start_line=1, end_line=40),
    ),
    repo_general(
        "repo_shell_history_impl",
        "in this repo, find where shell history is implemented, where runtime inspection lives, and read the implementation file",
        step("grep_files", pattern="read_shell_history", path="src", glob="*.py", max_hits=20),
        step("grep_files", pattern="inspect_runtime_versions", path="src", glob="*.py", max_hits=20),
        step("read_file", path="src/tools/shell_tools.py", start_line=1, end_line=40),
    ),
    repo_general(
        "repo_tui_choice",
        "in this repo, read the readme and tui research notes, then find the tui stack references",
        step("read_file", path="README.md", start_line=1, end_line=20),
        step("read_file", path="docs/TUI_RESEARCH.md", start_line=1, end_line=20),
        step("grep_files", pattern="prompt_toolkit|Rich", path=".", glob="*.md", max_hits=20),
    ),
    repo_general(
        "repo_command_aliases",
        "in this repo, find the command aliases, list the source files, and read the cli module",
        step("grep_files", pattern="ALIASES|setup|set-up|configure", path="src", glob="*.py", max_hits=20),
        step("list_files", path="src", glob="*.py", max_items=20, max_depth=3),
        step("read_file", path="src/cli.py", start_line=1, end_line=40),
    ),
    repo_general(
        "repo_config_wizard",
        "in this repo, locate the config wizard, list nearby modules, and read its entrypoint",
        step("grep_files", pattern="run_config_wizard", path="src", glob="*.py", max_hits=20),
        step("list_files", path="src", glob="*.py", max_items=20, max_depth=2),
        step("read_file", path="src/config_wizard.py", start_line=1, end_line=30),
    ),
    repo_general(
        "repo_chat_provider",
        "in this repo, find the provider that handles chat completions, list provider modules, and read it",
        step("grep_files", pattern="class ChatProvider|chat completions", path="src", glob="*.py", max_hits=20),
        step("list_files", path="src/providers", glob="*.py", max_items=20, max_depth=2),
        step("read_file", path="src/providers/chat_provider.py", start_line=1, end_line=30),
    ),
    repo_general(
        "repo_repl_render_tests",
        "in this repo, locate the repl rendering tests, search their assertions, and read the test file",
        step("grep_files", pattern="render|stream|bracket", path="tests", glob="*.py", max_hits=20),
        step("list_files", path="tests", glob="*.py", max_items=20, max_depth=2),
        step("read_file", path="tests/test_repl_rendering.py", start_line=1, end_line=30),
    ),
    repo_general(
        "repo_tool_registry",
        "in this repo, find the tool registry, search the supported families, and read the registry file",
        step("list_files", path="src", glob="*.py", max_items=30, max_depth=3),
        step("grep_files", pattern="filesystem|shell|python|data|git", path="src", glob="*.py", max_hits=20),
        step("read_file", path="src/tool_registry.py", start_line=1, end_line=30),
    ),
    repo_general(
        "repo_permission_behavior",
        "in this repo, find the permission manager, inspect its checks, and read the permission module",
        step("grep_files", pattern="PermissionManager|check", path="src", glob="*.py", max_hits=20),
        step("list_files", path="src", glob="*.py", max_items=20, max_depth=2),
        step("read_file", path="src/permissions.py", start_line=1, end_line=30),
    ),
    repo_general(
        "repo_session_continuation",
        "in this repo, locate the session continuation logic, search recent_messages, and read the session store",
        step("grep_files", pattern="continue_session|recent_messages", path="src", glob="*.py", max_hits=20),
        step("list_files", path="src", glob="*.py", max_items=20, max_depth=2),
        step("read_file", path="src/session_store.py", start_line=1, end_line=30),
    ),
    data_task(
        "data_sales_csv",
        "analyze data/sales.csv and summarize the headers, sample rows, and revenue total",
        step("inspect_spreadsheet", path="data/sales.csv"),
        step("read_sheet_range", path="data/sales.csv", start_row=1, max_rows=5),
        step(
            "run_python",
            code=(
                "import csv, json\n"
                "with open('data/sales.csv', newline='') as f:\n"
                "    rows = list(csv.DictReader(f))\n"
                "total = sum(int(row['revenue']) for row in rows)\n"
                "print(json.dumps({'rows': len(rows), 'total_revenue': total}))\n"
            ),
        ),
    ),
    data_task(
        "data_users_csv",
        "analyze data/users.csv and summarize the headers, sample rows, row count, and email list",
        step("inspect_spreadsheet", path="data/users.csv"),
        step("read_sheet_range", path="data/users.csv", start_row=1, max_rows=5),
        step(
            "run_python",
            code=(
                "import csv, json\n"
                "with open('data/users.csv', newline='') as f:\n"
                "    rows = list(csv.DictReader(f))\n"
                "print(json.dumps({'rows': len(rows), 'emails': [row['email'] for row in rows]}))\n"
            ),
        ),
    ),
    data_task(
        "data_metrics_xlsx",
        "inspect data/metrics.xlsx, compare the Summary and Regions sample rows, and count the sheets",
        step("inspect_spreadsheet", path="data/metrics.xlsx"),
        step("read_sheet_range", path="data/metrics.xlsx", sheet="Summary", start_row=1, max_rows=5),
        step("read_sheet_range", path="data/metrics.xlsx", sheet="Regions", start_row=1, max_rows=5),
    ),
    data_task(
        "data_inventory_csv",
        "analyze data/inventory.csv and summarize the headers, sample rows, and total stock",
        step("inspect_spreadsheet", path="data/inventory.csv"),
        step("read_sheet_range", path="data/inventory.csv", start_row=1, max_rows=5),
        step(
            "run_python",
            code=(
                "import csv, json\n"
                "with open('data/inventory.csv', newline='') as f:\n"
                "    rows = list(csv.DictReader(f))\n"
                "stock = sum(int(row['stock']) for row in rows)\n"
                "print(json.dumps({'rows': len(rows), 'total_stock': stock}))\n"
            ),
        ),
    ),
    data_task(
        "data_workbook_summary",
        "inspect the spreadsheet workbook data/metrics.xlsx, summarize its sheet names, show sample data from Summary, and count the sheets",
        step("inspect_spreadsheet", path="data/metrics.xlsx"),
        step("read_sheet_range", path="data/metrics.xlsx", sheet="Summary", start_row=1, max_rows=4),
        step(
            "run_python",
            code=(
                "import json, openpyxl\n"
                "wb = openpyxl.load_workbook('data/metrics.xlsx', read_only=True, data_only=True)\n"
                "print(json.dumps({'sheets': wb.sheetnames}))\n"
            ),
        ),
    ),
    extraction_task(
        "extract_tickets_json",
        "classify the support ticket backlog into json with categories and counts",
        step("read_file", path="tickets.txt", start_line=1, end_line=20),
        step("stat_path", path="tickets.txt"),
        step(
            "run_python",
            code=(
                "import json, pathlib\n"
                "counts = {}\n"
                "for line in pathlib.Path('tickets.txt').read_text().splitlines():\n"
                "    kind = line.split(':', 1)[0]\n"
                "    counts[kind] = counts.get(kind, 0) + 1\n"
                "print(json.dumps({'count': sum(counts.values()), 'categories': counts}, sort_keys=True))\n"
            ),
        ),
    ),
    extraction_task(
        "extract_people_json",
        "normalize the people dataset into json with row count and fields",
        step("read_file", path="data/people.jsonl", start_line=1, end_line=20),
        step("stat_path", path="data/people.jsonl"),
        step(
            "run_python",
            code=(
                "import json, pathlib\n"
                "rows = [json.loads(line) for line in pathlib.Path('data/people.jsonl').read_text().splitlines()]\n"
                "print(json.dumps({'rows': len(rows), 'fields': sorted(rows[0].keys())}))\n"
            ),
        ),
    ),
    extraction_task(
        "extract_emails_json",
        "extract the email addresses from the notes into json",
        step("read_file", path="notes.txt", start_line=1, end_line=20),
        step("stat_path", path="notes.txt"),
        step(
            "run_python",
            code=(
                "import json, pathlib, re\n"
                "text = pathlib.Path('notes.txt').read_text()\n"
                "emails = re.findall(r'[\\w.]+@[\\w.]+', text)\n"
                "print(json.dumps({'emails': emails}))\n"
            ),
        ),
    ),
    extraction_task(
        "extract_log_labels_json",
        "label the application log lines into json severity counts",
        step("read_file", path="logs/app.log", start_line=1, end_line=20),
        step("stat_path", path="logs/app.log"),
        step(
            "run_python",
            code=(
                "import json, pathlib\n"
                "counts = {'INFO': 0, 'WARN': 0, 'ERROR': 0}\n"
                "for line in pathlib.Path('logs/app.log').read_text().splitlines():\n"
                "    for key in counts:\n"
                "        if line.startswith(key):\n"
                "            counts[key] += 1\n"
                "print(json.dumps(counts, sort_keys=True))\n"
            ),
        ),
    ),
    extraction_task(
        "extract_todos_json",
        "turn the todo list into a json array with clean items",
        step("read_file", path="todos.txt", start_line=1, end_line=20),
        step("stat_path", path="todos.txt"),
        step(
            "run_python",
            code=(
                "import json, pathlib\n"
                "items = []\n"
                "for line in pathlib.Path('todos.txt').read_text().splitlines():\n"
                "    cleaned = line.strip().lstrip('-').strip()\n"
                "    if cleaned:\n"
                "        items.append(cleaned)\n"
                "print(json.dumps(items))\n"
            ),
        ),
    ),
    automation_task(
        "automation_backup_logs",
        "automate a backup log script and verify it runs",
        step(
            "write_file",
            path="scripts/backup_logs.sh",
            content="#!/bin/sh\nmkdir -p backups\ncp logs/app.log backups/app.log\n",
        ),
        step("read_file", path="scripts/backup_logs.sh", start_line=1, end_line=20),
        step("run_shell_command", command="sh scripts/backup_logs.sh && test -f backups/app.log", timeout_s=5),
    ),
    automation_task(
        "automation_cleanup_tmp",
        "create a repeatable cleanup script for tmp artifacts and verify it",
        step(
            "write_file",
            path="scripts/cleanup_tmp.sh",
            content="#!/bin/sh\nrm -f tmp/*.tmp\n",
        ),
        step("read_file", path="scripts/cleanup_tmp.sh", start_line=1, end_line=20),
        step("run_shell_command", command="sh scripts/cleanup_tmp.sh && test ! -f tmp/old.tmp", timeout_s=5),
    ),
    automation_task(
        "automation_sales_report",
        "build a repeatable sales report script and run it",
        step(
            "write_file",
            path="scripts/report.sh",
            content="#!/bin/sh\nawk -F, 'NR>1 {sum += $2} END {print sum}' data/sales.csv\n",
        ),
        step("read_file", path="scripts/report.sh", start_line=1, end_line=20),
        step("run_shell_command", command="sh scripts/report.sh", timeout_s=5),
    ),
    automation_task(
        "automation_env_snapshot",
        "create an environment snapshot script and execute it",
        step(
            "write_file",
            path="scripts/env_snapshot.sh",
            content="#!/bin/sh\nprintf '%s\\n' \"$HOME\" \"$SHELL\"\n",
        ),
        step("read_file", path="scripts/env_snapshot.sh", start_line=1, end_line=20),
        step("run_shell_command", command="sh scripts/env_snapshot.sh", timeout_s=5),
    ),
    automation_task(
        "automation_current_branch",
        "create a repeatable branch helper script and run it",
        step(
            "write_file",
            path="scripts/current_branch.sh",
            content="#!/bin/sh\ngit branch --show-current\n",
        ),
        step("read_file", path="scripts/current_branch.sh", start_line=1, end_line=20),
        step("run_shell_command", command="sh scripts/current_branch.sh", timeout_s=5),
    ),
]

DEFAULT_SCENARIOS: tuple[Scenario, ...] = tuple(SCENARIOS)

PHASE4_MINI_PROJECTS: tuple[MiniProjectScenario, ...] = (

    MiniProjectScenario(
        name="python_wordcount_cli",
        prompt=(
            "Build a tiny Python script project in this empty workspace. "
            "Create exactly these files: "
            "`input.txt` with exactly two lines `rocky builds tools` and `tools build trust`, "
            "`main.py` that reads `input.txt` and prints valid JSON with keys `line_count` and `word_count`, "
            "and `README.md` with one short usage example. "
            "Then run `python3 main.py` to verify it works and tell me the exact JSON output."
        ),
        expected_files=("input.txt", "main.py", "README.md"),
        verify_command=("python3", "main.py"),
        expected_output={"line_count": 2, "word_count": 6},
        output_kind="json",
        response_snippets=("line_count", "word_count", "6"),
    ),
    MiniProjectScenario(
        name="shell_sales_report",
        prompt=(
            "Build a tiny shell script project in this empty workspace. "
            "Create exactly these files: "
            "`sales.csv` with header `month,revenue` and rows `jan,100`, `feb,120`, and `mar,140`, "
            "`report.sh` that prints only the total revenue from `sales.csv`, "
            "and `README.md` with one short usage example. "
            "Then run `sh report.sh` to verify it works and tell me the exact output."
        ),
        expected_files=("sales.csv", "report.sh", "README.md"),
        verify_command=("sh", "report.sh"),
        expected_output="360",
        response_snippets=("360", "report.sh"),
    ),
    MiniProjectScenario(
        name="python_email_extract",
        prompt=(
            "Build a tiny Python script project in this empty workspace. "
            "Create exactly these files: "
            "`notes.txt` with exactly two lines containing `ada@example.com` and `bob@example.org`, "
            "`extract.py` that reads `notes.txt` and prints valid JSON with a single key `emails` whose value is a sorted array, "
            "and `README.md` with one short usage example. "
            "Then run `python3 extract.py` to verify it works and tell me the exact JSON output."
        ),
        expected_files=("notes.txt", "extract.py", "README.md"),
        verify_command=("python3", "extract.py"),
        expected_output={"emails": ["ada@example.com", "bob@example.org"]},
        output_kind="json",
        response_snippets=("ada@example.com", "bob@example.org", "emails"),
    ),
    MiniProjectScenario(
        name="workspace_script_catalog_review",
        prompt=(
            "You are now product catalog manager. Execute `x.sh` and explore the response. "
            "Those are pending products to be managed, and each product has candidates to merge. "
            "Treat a candidate as the same product only when both `name` and `sku` match exactly. "
            "Write valid JSON to `merge_decisions.json` with a top-level key `products`, where each item contains "
            "`product_id`, `merge`, and `skip` arrays of candidate ids. Then read the file and tell me the exact JSON."
        ),
        expected_files=("x.sh", "merge_decisions.json"),
        verify_command=(
            "python3",
            "-c",
            "import json, pathlib; print(json.dumps(json.loads(pathlib.Path('merge_decisions.json').read_text()), sort_keys=True))",
        ),
        expected_output={
            "products": [
                {"product_id": "P001", "merge": ["C001"], "skip": ["C002", "C003"]},
                {"product_id": "P002", "merge": ["C010"], "skip": ["C011", "C012"]},
            ]
        },
        output_kind="json",
        response_snippets=("merge_decisions.json", "C001", "C010"),
        seed_files=(("x.sh", CATALOG_X_SH),),
        task_class=TaskClass.REPO,
        task_signature="repo/shell_execution",
        min_successful_tools=3,
        required_successful_tools=("run_shell_command", "read_file"),
    ),
)

WORKSPACE_CONTINUITY_SCENARIOS: tuple[WorkspaceContinuityScenario, ...] = (
    WorkspaceContinuityScenario(
        name="resume_same_subdirectory",
        seed_prompt="Build the parser in src/parser.py and keep focusing on config loading.",
        seed_answer="Implemented parser flow in src/parser.py. Keep focusing on config loading and workspace memory.",
        follow_up_prompt="continue the work from the current project",
        expected_markers=("src/parser.py", "config loading", "Project handoff"),
    ),
    WorkspaceContinuityScenario(
        name="resume_ui_package_workspace",
        seed_prompt="In pkg/ui, wire the button state reducer and remember the active package path.",
        seed_answer="Updated pkg/ui/button.ts and kept the focus on reducer wiring for pkg/ui.",
        follow_up_prompt="continue inside the current package",
        expected_markers=("pkg/ui", "button.ts", "Workspace focus"),
    ),
    WorkspaceContinuityScenario(
        name="resume_test_fix_loop",
        seed_prompt="Fix failing shell tests and remember that login-shell PATH handling was the regression.",
        seed_answer="Patched shell execution to keep PATH stable and noted the regression cause in shell startup behavior.",
        follow_up_prompt="pick up the shell regression work",
        expected_markers=("PATH", "shell", "handoff"),
    ),
)


def scenarios_by_phase(phase_slug: str) -> tuple[object, ...]:
    if phase_slug == "phase4_exact_output_build":
        return PHASE4_MINI_PROJECTS
    if phase_slug == "phase5_workspace_continuity":
        return WORKSPACE_CONTINUITY_SCENARIOS
    if phase_slug in ('phase1_route_anchor', 'phase2_followup_evidence', 'phase3_end_to_end_contract'):
        return DEFAULT_SCENARIOS
    return ()


def harness_inventory() -> dict[str, object]:
    return {
        "phase1_3_scenarios": len(DEFAULT_SCENARIOS),
        "phase4_scenarios": len(PHASE4_MINI_PROJECTS),
        "phase5_scenarios": len(WORKSPACE_CONTINUITY_SCENARIOS),
        "scenario_names": [scenario.name for scenario in DEFAULT_SCENARIOS[:12]],
        "mini_project_names": [scenario.name for scenario in PHASE4_MINI_PROJECTS],
        "continuity_names": [scenario.name for scenario in WORKSPACE_CONTINUITY_SCENARIOS],
    }
