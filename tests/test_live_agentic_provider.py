from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import httpx
import pytest

from rocky.harness import default_scenarios, materialize_mini_project_workspace, materialize_scenario_workspace, phase4_mini_projects


LIVE_PROVIDER = "ollama"
LIVE_SKIP_ENV = "ROCKY_SKIP_LIVE_AGENTIC"
REPO_ROOT = Path(__file__).resolve().parents[1]
HOST_CONFIG_ROOT = Path.home() / ".config" / "rocky"


def _run(cmd: list[str], *, env: dict[str, str] | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def _cli_json(
    workspace: Path,
    env: dict[str, str],
    *task_parts: str,
) -> dict[str, object]:
    cmd = [
        "rocky",
        "--provider",
        LIVE_PROVIDER,
        "--cwd",
        str(workspace),
        "--json",
        *task_parts,
    ]
    result = _run(cmd, env=env, cwd=REPO_ROOT)
    return json.loads(result.stdout)


def _copy_live_config(home: Path) -> None:
    target = home / ".config" / "rocky"
    target.parent.mkdir(parents=True, exist_ok=True)
    if HOST_CONFIG_ROOT.exists():
        shutil.copytree(HOST_CONFIG_ROOT, target, dirs_exist_ok=True)


def _scenario_env(home: Path, *, extra_path: str | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["SHELL"] = "/bin/zsh"
    env["USER"] = "rockytester"
    if extra_path:
        env["PATH"] = f"{extra_path}{os.pathsep}{env.get('PATH', '')}"
    return env


def _prepare_generated_workspace(tmp_path: Path):
    scenario = next(s for s in default_scenarios() if s.task_signature == "repo/general")
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    _copy_live_config(home)
    bin_dir = materialize_scenario_workspace(workspace, home, scenario)
    return scenario, workspace, home, _scenario_env(home, extra_path=str(bin_dir))


def _prepare_project_workspace(tmp_path: Path, family: str):
    project = next(
        scenario
        for scenario in phase4_mini_projects()
        if str(scenario.oracle.get("family") or "") == family
    )
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    _copy_live_config(home)
    materialize_mini_project_workspace(workspace, project)
    return project, workspace, home, _scenario_env(home)


def _successful_tool_names(payload: dict[str, object]) -> list[str]:
    trace = dict(payload.get("trace") or {})
    events = list(trace.get("tool_events") or [])
    return [
        str(event.get("name") or "")
        for event in events
        if event.get("type") == "tool_result" and event.get("success", True)
    ]


def _selected_policies(payload: dict[str, object]) -> list[str]:
    trace = dict(payload.get("trace") or {})
    return [str(item) for item in list(trace.get("selected_policies") or [])]


def _list_item_count(text: str) -> int:
    return sum(1 for line in str(text or "").splitlines() if re.match(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)", line))


def _write_scenario_report(
    workspace: Path,
    scenario_name: str,
    commands: list[str],
    rows: list[tuple[str, str, str, str]],
) -> Path:
    report_dir = workspace / ".rocky" / "eval" / "agentic_scenarios"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{scenario_name}.md"
    lines = [
        f"# {scenario_name}",
        "",
        "## Commands",
        "",
    ]
    lines.extend(f"- `{command}`" for command in commands)
    lines.extend(
        [
            "",
            "## Results",
            "",
            "| Phase | What we did | Expected | How Rocky did |",
            "| --- | --- | --- | --- |",
        ]
    )
    lines.extend(f"| {phase} | {did} | {expected} | {actual} |" for phase, did, expected, actual in rows)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def _learning_case(project) -> dict[str, object]:
    script_name = str(project.oracle["script_name"])
    output_path = f"retry_{Path(str(project.oracle['output_file'])).name}"
    return {
        "feedback": (
            f"When continuing catalog-review work in this workspace, keep `{script_name}` in focus. "
            "Execute the existing workspace script first. "
            "If it returns structured data, inspect or parse it with focused shell commands before making merge decisions. "
            "When the user asks for a result file, write the exact JSON there and reread it before answering."
        ),
        "retry_prompt": (
            "continue the catalog review work in this project. "
            f"Re-run the existing workspace script, write the final exact JSON merge decisions to `{output_path}` "
            "with a top-level key `products`, where each item contains `product_id`, `merge`, and `skip` arrays of candidate ids, "
            "then read that file back and tell me the exact JSON."
        ),
        "expected_output_path": output_path,
        "expected_task_signature": "repo/shell_execution",
    }


@pytest.fixture(scope="session")
def live_provider_ready() -> dict[str, str]:
    if os.getenv(LIVE_SKIP_ENV) == "1":
        pytest.skip(f"live LLM agentic tests skipped because {LIVE_SKIP_ENV}=1", allow_module_level=False)

    _run(["pipx", "install", "--force", str(REPO_ROOT)], cwd=REPO_ROOT)
    config_payload = _cli_json(REPO_ROOT, os.environ.copy(), "config")
    config_data = dict(config_payload.get("data") or {})
    provider = dict((config_data.get("providers") or {}).get(LIVE_PROVIDER) or {})
    base_url = str(provider.get("base_url") or "").rstrip("/")
    model = str(provider.get("model") or "")
    try:
        response = httpx.get(f"{base_url}/models", timeout=15.0)
        response.raise_for_status()
    except Exception as exc:
        pytest.skip(
            f"live LLM provider unavailable for {LIVE_PROVIDER} at {base_url} with model {model}: {exc}",
            allow_module_level=False,
        )
    return {"base_url": base_url, "model": model}


def test_live_cli_repo_lookup_agentic_behavior(tmp_path: Path, live_provider_ready: dict[str, str]) -> None:
    scenario, workspace, home, env = _prepare_generated_workspace(tmp_path)
    payload = _cli_json(workspace, env, scenario.prompt)
    tools = _successful_tool_names(payload)
    trace = dict(payload.get("trace") or {})
    report = _write_scenario_report(
        workspace,
        "repo_lookup_agentic_behavior",
        [
            f"mkdir -p {workspace}",
            f"pipx install --force {REPO_ROOT}",
            f"rocky --provider {LIVE_PROVIDER} --cwd {workspace} --json {json.dumps(scenario.prompt)}",
        ],
        [
            (
                "prepare_workspace",
                f"created {workspace} and copied config into {home}",
                "isolated workspace with generated repo fixtures",
                "workspace and temp home created",
            ),
            (
                "install_and_baseline",
                "ran installed Rocky against a generated repo-inspection prompt",
                "correct route, multiple successful tools, grounded non-empty answer",
                f"route={payload['route']['task_signature']}, tools={tools[:4]}, verification={payload['verification']['status']}",
            ),
            (
                "grade_results",
                "checked trace and answer together",
                "repo/general with at least two successful repo inspection steps",
                f"selected_tools={len(trace.get('selected_tools') or [])}, trace={trace.get('trace_path')}",
            ),
        ],
    )

    assert payload["route"]["task_signature"] == scenario.task_signature
    assert payload["verification"]["status"] == "pass"
    assert payload["text"].strip()
    assert len(tools) >= 2
    assert any(tool in tools for tool in scenario.phase_expectations.anchor_tools)
    assert report.exists()


def test_live_cli_exact_output_project_scenario(tmp_path: Path, live_provider_ready: dict[str, str]) -> None:
    project, workspace, _home, env = _prepare_project_workspace(tmp_path, "sales_report")
    payload = _cli_json(workspace, env, project.prompt)
    tools = _successful_tool_names(payload)
    verify = _run(list(project.verify_command), cwd=workspace, env=env)
    observed_output = verify.stdout.strip()
    report = _write_scenario_report(
        workspace,
        "exact_output_project_scenario",
        [
            f"mkdir -p {workspace}",
            f"pipx install --force {REPO_ROOT}",
            f"rocky --provider {LIVE_PROVIDER} --cwd {workspace} --json {json.dumps(project.prompt)}",
            " ".join(project.verify_command),
        ],
        [
            (
                "prepare_workspace",
                "created an empty workspace seeded only with the mini-project oracle",
                "workspace starts empty except scenario fixtures",
                f"expected_files={list(project.expected_files)}",
            ),
            (
                "install_and_baseline",
                "ran installed Rocky on the build-and-verify prompt",
                "create the files, run the script, and return the exact output",
                f"route={payload['route']['task_signature']}, tools={tools[:4]}, verification={payload['verification']['status']}",
            ),
            (
                "grade_results",
                "executed the same verify command outside Rocky",
                "observed script output matches the scenario oracle",
                f"observed_output={observed_output}",
            ),
        ],
    )

    assert payload["route"]["task_signature"] == project.task_signature
    assert payload["verification"]["status"] == "pass"
    assert "write_file" in tools
    assert "run_shell_command" in tools
    for relative_path in project.expected_files:
        assert (workspace / relative_path).is_file()
    assert observed_output == str(project.expected_output)
    assert report.exists()


def test_live_cli_learning_roundtrip_uses_learned_policy(tmp_path: Path, live_provider_ready: dict[str, str]) -> None:
    project, workspace, _home, env = _prepare_project_workspace(tmp_path, "catalog_review")
    case = _learning_case(project)

    seed_payload = _cli_json(workspace, env, project.prompt)
    baseline_retry = _cli_json(workspace, env, str(case["retry_prompt"]))
    learn_payload = _cli_json(workspace, env, "learn", str(case["feedback"]))
    learn_data = dict(learn_payload.get("data") or {})
    published_policy_id = str(learn_data.get("policy_id") or "") if learn_data.get("published") else ""

    retry_payload = baseline_retry
    for _attempt in range(3):
        retry_payload = _cli_json(workspace, env, str(case["retry_prompt"]))
        selected_policy_ids = _selected_policies(retry_payload)
        if (
            retry_payload["verification"]["status"] == "pass"
            and (not published_policy_id or published_policy_id in selected_policy_ids)
        ):
            break
        learn_payload = _cli_json(workspace, env, "learn", str(case["feedback"]))
        candidate_learn_data = dict(learn_payload.get("data") or {})
        if candidate_learn_data.get("published"):
            learn_data = candidate_learn_data
            published_policy_id = str(learn_data.get("policy_id") or "")

    output_path = workspace / str(case["expected_output_path"])
    report = _write_scenario_report(
        workspace,
        "learning_roundtrip_uses_learned_policy",
        [
            f"mkdir -p {workspace}",
            f"pipx install --force {REPO_ROOT}",
            f"rocky --provider {LIVE_PROVIDER} --cwd {workspace} --json {json.dumps(project.prompt)}",
            f"rocky --provider {LIVE_PROVIDER} --cwd {workspace} --json learn {json.dumps(str(case['feedback']))}",
            f"rocky --provider {LIVE_PROVIDER} --cwd {workspace} --json {json.dumps(str(case['retry_prompt']))}",
        ],
        [
            (
                "prepare_workspace",
                "seeded a generated catalog-review workspace",
                "workspace contains only the existing script to continue from",
                f"script={project.oracle['script_name']}",
            ),
            (
                "install_and_baseline",
                "ran the seed prompt and a fresh-process follow-up before teaching",
                "seed prompt succeeds and follow-up has no learned policy yet",
                f"seed_verification={seed_payload['verification']['status']}, baseline_policies={_selected_policies(baseline_retry)}",
            ),
            (
                "teach",
                "sent `/learn` feedback through the installed CLI",
                    "Rocky publishes a reusable policy when a new failure is observed, or keeps the already-correct baseline without publishing",
                    f"published={learn_data.get('published')}, policy_id={learn_data.get('policy_id')}",
                ),
            (
                "retry_with_learning",
                "re-ran the follow-up in a fresh Rocky process",
                "retry loads the learned policy and uses it in the trace",
                f"retry_policies={_selected_policies(retry_payload)}, verification={retry_payload['verification']['status']}",
            ),
            (
                "grade_results",
                "read the output file that Rocky claimed to write",
                "retry output file contains the exact expected JSON",
                f"output_path={output_path}, trace={dict(retry_payload.get('trace') or {}).get('trace_path')}",
            ),
        ],
    )

    assert seed_payload["verification"]["status"] == "pass"
    assert retry_payload["route"]["task_signature"] == case["expected_task_signature"]
    assert retry_payload["verification"]["status"] == "pass"
    if published_policy_id:
        assert published_policy_id in _selected_policies(retry_payload)
    else:
        assert baseline_retry["verification"]["status"] == "pass"
    assert output_path.is_file()
    assert json.loads(output_path.read_text(encoding="utf-8")) == project.expected_output
    assert report.exists()


def test_live_cli_auto_self_reflection_recalls_compact_retrospective(
    tmp_path: Path,
    live_provider_ready: dict[str, str],
) -> None:
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    _copy_live_config(home)
    env = _scenario_env(home)

    first_prompt = "show me what python versions i have and where they live"
    second_prompt = "show me what ruby version i have and where it lives"
    first_payload = _cli_json(workspace, env, first_prompt)
    retrospectives = sorted((workspace / ".rocky" / "student" / "retrospectives").glob("*.md"))
    assert retrospectives
    retrospective_text = retrospectives[-1].read_text(encoding="utf-8")
    first_trace = dict(first_payload.get("trace") or {})
    self_learning = dict(first_trace.get("self_learning") or {})

    second_payload = _cli_json(workspace, env, second_prompt)
    second_trace = dict(second_payload.get("trace") or {})
    second_notes = list((second_trace.get("context") or {}).get("student_notes") or [])
    has_empty_prompt = "prompt: ''" in retrospective_text
    has_empty_answer = "answer: ''" in retrospective_text
    report = _write_scenario_report(
        workspace,
        "auto_self_reflection_recalls_compact_retrospective",
        [
            f"mkdir -p {workspace}",
            f"pipx install --force {REPO_ROOT}",
            f"rocky --provider {LIVE_PROVIDER} --cwd {workspace} --json {json.dumps(first_prompt)}",
            f"rocky --provider {LIVE_PROVIDER} --cwd {workspace} --json {json.dumps(second_prompt)}",
        ],
        [
            (
                "first_episode",
                "ran installed Rocky on a local runtime-inspection task",
                "Rocky finishes an episode and writes a compact self-retrospective, even if the first answer still needs correction",
                f"verification={first_payload['verification']['status']}, route={first_payload['route']['task_signature']}, retrospective={retrospectives[-1].name}",
            ),
            (
                "compact_memory",
                "read the saved retrospective note directly from disk",
                "note stores only the learned convention, not copied prompt/answer transcripts",
                f"contains_prompt_field={has_empty_prompt}, contains_answer_field={has_empty_answer}",
            ),
            (
                "fresh_recall",
                "ran a second fresh Rocky process on a related runtime-inspection task",
                "second trace retrieves the earlier retrospective in student notes before answering",
                f"note_kinds={[note.get('kind') for note in second_notes]}, note_titles={[note.get('title') for note in second_notes]}",
            ),
        ],
    )

    assert first_payload["route"]["task_signature"] == "local/runtime_inspection"
    assert self_learning.get("persisted") is True
    assert str((self_learning.get("retrospective") or {}).get("summary") or "").strip()
    assert has_empty_prompt is True
    assert has_empty_answer is True
    assert second_payload["verification"]["status"] == "pass"
    assert second_payload["route"]["task_signature"] == "local/runtime_inspection"
    assert second_notes
    assert any(str(note.get("kind") or "") == "retrospective" for note in second_notes)
    assert report.exists()


def test_live_cli_trending_models_research_list_scenario(
    tmp_path: Path,
    live_provider_ready: dict[str, str],
) -> None:
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    _copy_live_config(home)
    env = _scenario_env(home)

    prompt = (
        "find huggingface openweight llm models that are trending right now. "
        "filter models that have parameters under 12B. "
        "you should find at least 10 models and show me as a list."
    )
    payload = _cli_json(workspace, env, prompt)
    trace = dict(payload.get("trace") or {})
    tools = _successful_tool_names(payload)
    selected_tools = [str(item) for item in list(trace.get("selected_tools") or [])]
    output = str(payload.get("text") or "")
    list_items = _list_item_count(output)
    report = _write_scenario_report(
        workspace,
        "trending_models_research_list_scenario",
        [
            f"mkdir -p {workspace}",
            f"pipx install --force {REPO_ROOT}",
            f"rocky --provider {LIVE_PROVIDER} --cwd {workspace} --json {json.dumps(prompt)}",
        ],
        [
            (
                "prepare_workspace",
                "created a fresh temp workspace and temp HOME under /tmp-style pytest temp storage",
                "isolated workspace with copied Rocky config",
                f"workspace={workspace}, home={home}",
            ),
            (
                "run_installed_cli",
                "ran installed Rocky against the live Hugging Face trending-models prompt",
                "research route, live web tool chaining, and a grounded counted list",
                f"route={payload['route']['task_signature']}, verification={payload['verification']['status']}, selected_tools={selected_tools}",
            ),
            (
                "grade_results",
                "checked the final answer and trace together",
                "at least 10 list items, web-fetch/extract evidence, and no shell tool exposure in this route",
                f"successful_tools={tools}, list_items={list_items}, trace={trace.get('trace_path')}",
            ),
        ],
    )

    assert payload["route"]["task_signature"] == "research/live_compare/general"
    assert payload["verification"]["status"] == "pass"
    assert list_items >= 10
    assert any(tool in tools for tool in ("fetch_url", "agent_browser", "browser_render_page"))
    assert "run_shell_command" not in selected_tools
    assert report.exists()
