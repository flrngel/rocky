from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess

import httpx
import pytest

from rocky.app import RockyRuntime
from rocky.core.router import TaskClass
from rocky.harness import (
    MiniProjectScenario,
    Scenario,
    default_scenarios,
    materialize_mini_project_workspace,
    materialize_scenario_workspace,
    phase4_mini_projects,
)


LIVE_PROVIDER = "ollama"
LIVE_BASE_URL = "http://ainbr-research-fast:11434/v1"
LIVE_MODEL = "qwen3.5:4b"
LIVE_SKIP_ENV = "ROCKY_SKIP_LIVE_AGENTIC"


@dataclass(frozen=True)
class Phase5LearningCase:
    name: str
    seed_scenario: MiniProjectScenario
    follow_up_prompt: str
    feedback: str
    expected_task_signature: str
    expected_output_path: str | None = None


def _live_cli_overrides() -> dict[str, object]:
    return {
        "active_provider": LIVE_PROVIDER,
        "providers": {
            LIVE_PROVIDER: {
                "style": "openai_chat",
                "base_url": LIVE_BASE_URL,
                "model": LIVE_MODEL,
                "store": False,
            }
        },
        "permissions": {"mode": "bypass"},
    }


def _tool_result_events(response, *, successes_only: bool = False) -> list[dict]:
    events = [
        event
        for event in response.trace["tool_events"]
        if event.get("type") == "tool_result"
    ]
    if successes_only:
        return [event for event in events if event.get("success", True)]
    return events


def _tool_result_names(response, *, successes_only: bool = False) -> list[str]:
    return [event["name"] for event in _tool_result_events(response, successes_only=successes_only)]


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _prepare_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: Scenario,
) -> Path:
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    home.mkdir()

    monkeypatch.chdir(workspace)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setenv("USER", "rockytester")
    bin_dir = materialize_scenario_workspace(workspace, home, scenario)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return workspace


def _prepare_clean_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    home.mkdir()

    monkeypatch.chdir(workspace)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setenv("USER", "rockytester")
    return workspace


def _phase5_catalog_case(project: MiniProjectScenario) -> Phase5LearningCase:
    script_name = str(project.oracle["script_name"])
    output_path = f"phase5_{Path(str(project.oracle['output_file'])).name}"
    return Phase5LearningCase(
        name=f"learned_resume_{project.oracle['family']}",
        seed_scenario=project,
        follow_up_prompt=(
            "continue the catalog review work in this project. "
            f"Re-run the existing workspace script, write the final exact JSON merge decisions to `{output_path}`, "
            "then read that file back and tell me the exact JSON."
        ),
        feedback=(
            f"When continuing catalog-review work in this workspace, keep `{script_name}` in focus. "
            "Execute the existing workspace script first. "
            "If it returns structured data, parse it with `run_python` before making merge decisions. "
            "When the user asks for a result file, write the exact JSON there and reread it before answering."
        ),
        expected_task_signature="repo/shell_execution",
        expected_output_path=output_path,
    )


def _phase5_report_case(project: MiniProjectScenario) -> Phase5LearningCase:
    report_script = str(project.verify_command[1])
    sales_file = str(project.oracle["sales_file"])
    return Phase5LearningCase(
        name=f"learned_resume_{project.oracle['family']}",
        seed_scenario=project,
        follow_up_prompt=(
            "continue the reporting helper work in this project. "
            "Re-read the existing script if needed, then tell me the exact verified output."
        ),
        feedback=(
            f"When continuing report-helper work in this workspace, keep `{report_script}` and `{sales_file}` in view. "
            "Use the existing project files, reread the script before verifying it, then run the exact command and name the exact observed output in the final answer."
        ),
        expected_task_signature="automation/general",
    )


def _phase5_learning_cases() -> tuple[Phase5LearningCase, ...]:
    cases: list[Phase5LearningCase] = []
    for project in phase4_mini_projects():
        family = str(project.oracle.get("family") or "")
        if family == "catalog_review":
            cases.append(_phase5_catalog_case(project))
        elif family == "sales_report":
            cases.append(_phase5_report_case(project))
    return tuple(cases)


def _prepare_phase5_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: Phase5LearningCase,
) -> Path:
    workspace = _prepare_clean_workspace(tmp_path, monkeypatch)
    materialize_mini_project_workspace(workspace, case.seed_scenario)
    return workspace


def _runtime_for_workspace(workspace: Path) -> RockyRuntime:
    runtime = RockyRuntime.load_from(workspace, cli_overrides=_live_cli_overrides())
    runtime.permissions.config.mode = "bypass"
    return runtime


def _publish_learning(runtime: RockyRuntime, feedback: str) -> dict:
    result = runtime.commands.handle(f"/learn {feedback}").data
    assert result and result.get("published") is True
    return result


def _phase5_expected_json(case: Phase5LearningCase) -> object:
    return case.seed_scenario.expected_output


def _phase5_expected_text(case: Phase5LearningCase, workspace: Path) -> str:
    family = str(case.seed_scenario.oracle.get("family") or "")
    if family == "sales_report":
        result = subprocess.run(
            ["sh", str(case.seed_scenario.verify_command[1])],
            cwd=str(workspace),
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    raise AssertionError(f"no text expectation handler for phase5 family {family}")


def _assert_phase5_output(case: Phase5LearningCase, response, workspace: Path) -> None:
    trace_path = response.trace.get("trace_path", "unknown-trace")
    family = str(case.seed_scenario.oracle.get("family") or "")
    if family == "catalog_review":
        assert case.expected_output_path is not None, trace_path
        output_file = workspace / case.expected_output_path
        assert output_file.is_file(), trace_path
        assert json.loads(output_file.read_text(encoding="utf-8")) == _phase5_expected_json(case), trace_path
        assert case.expected_output_path in response.text, trace_path
        return
    if family == "sales_report":
        script_name = str(case.seed_scenario.verify_command[1])
        expected_output = _phase5_expected_text(case, workspace)
        assert expected_output in response.text, trace_path
        assert script_name in response.text, trace_path
        return
    raise AssertionError(f"unexpected phase5 family {family}")


@pytest.fixture(scope="session")
def live_provider_ready() -> None:
    if os.getenv(LIVE_SKIP_ENV) == "1":
        pytest.skip(
            f"live LLM agentic tests skipped because {LIVE_SKIP_ENV}=1",
            allow_module_level=False,
        )
    models_url = f"{LIVE_BASE_URL.rstrip('/')}/models"
    try:
        response = httpx.get(models_url, timeout=15.0)
        response.raise_for_status()
    except Exception as exc:
        pytest.skip(
            f"live LLM provider unavailable for {LIVE_PROVIDER} at {LIVE_BASE_URL} "
            f"with model {LIVE_MODEL}: {exc}",
            allow_module_level=False,
        )


def _assert_phase1(scenario: Scenario, response) -> None:
    trace_path = response.trace.get("trace_path", "unknown-trace")
    successful_names = _tool_result_names(response, successes_only=True)
    anchors = set(scenario.phase_expectations.anchor_tools)

    assert successful_names, f"phase1-step1: no successful tool results; trace={trace_path}"
    if anchors:
        assert successful_names[0] in anchors, (
            f"phase1-step1: first tool {successful_names[0]!r} not in {sorted(anchors)}; trace={trace_path}"
        )


def _assert_phase2(scenario: Scenario, response) -> None:
    trace_path = response.trace.get("trace_path", "unknown-trace")
    expectations = scenario.phase_expectations
    successful_names = _tool_result_names(response, successes_only=True)
    windows: list[list[str]] = [successful_names[:5]]
    raw_provider_keys = response.trace.get("raw_provider_keys") or []
    if len(raw_provider_keys) > 1 and len(successful_names) > 5:
        windows.append(successful_names[-5:])

    assert len(successful_names) >= min(2, expectations.min_successful_tools), (
        f"phase2-step2-3: too few successful tools {successful_names}; trace={trace_path}"
    )

    def _window_errors(window: list[str]) -> list[str]:
        errors: list[str] = []
        for tool_name in expectations.phase2_required_tools:
            if tool_name not in window:
                errors.append(f"missing required tool {tool_name!r} in window {window}")
        if expectations.phase2_required_any and not (set(window) & set(expectations.phase2_required_any)):
            errors.append(
                f"missing any of {sorted(expectations.phase2_required_any)} in window {window}"
            )
        if expectations.requires_non_shell_follow_up:
            if "run_shell_command" not in window:
                errors.append(f"missing shell anchor in window {window}")
            elif not any(name != "run_shell_command" for name in window):
                errors.append(f"missing non-shell follow-up in window {window}")
        return errors

    window_errors = [_window_errors(window) for window in windows]
    if any(not errors for errors in window_errors):
        return

    raise AssertionError(
        "phase2-step2-3: no valid evidence-gathering window satisfied expectations; "
        f"windows={windows}; errors={window_errors}; trace={trace_path}"
    )


def _assert_phase3_behavior(scenario: Scenario, response) -> None:
    trace_path = response.trace.get("trace_path", "unknown-trace")
    expectations = scenario.phase_expectations
    all_tool_names = _tool_result_names(response)
    successful_names = _tool_result_names(response, successes_only=True)

    assert response.route.task_class == scenario.task_class, trace_path
    assert response.route.task_signature == scenario.task_signature, trace_path
    assert response.trace["provider"] == "OpenAIChatProvider", trace_path
    assert response.trace["tool_events"], trace_path
    assert response.text.strip(), trace_path
    assert "Provider request failed:" not in response.text, trace_path
    assert "Tool loop ended without a final assistant response." not in response.text, trace_path
    assert response.verification["status"] != "fail", trace_path
    assert len(successful_names) >= expectations.min_successful_tools, (
        f"{scenario.name} only used {len(successful_names)} successful tools; trace={trace_path}"
    )

    for tool_name in expectations.required_tools:
        assert tool_name in successful_names, (
            f"{scenario.name} missing required tool {tool_name!r}; trace={trace_path}"
        )
    for tool_name in expectations.forbidden_tools:
        assert tool_name not in all_tool_names, (
            f"{scenario.name} used forbidden tool {tool_name!r}; trace={trace_path}"
        )
    if expectations.requires_non_shell_follow_up:
        assert any(name != "run_shell_command" for name in successful_names), trace_path
    if expectations.requires_json_output:
        payload = json.loads(_strip_fences(response.text))
        assert isinstance(payload, (dict, list)), trace_path


@pytest.fixture(scope="session", params=default_scenarios(), ids=[scenario.name for scenario in default_scenarios()])
def live_run(
    request: pytest.FixtureRequest,
    tmp_path_factory: pytest.TempPathFactory,
    live_provider_ready: None,
):
    scenario: Scenario = request.param
    tmp_path = tmp_path_factory.mktemp(f"live_{scenario.name}")
    monkeypatch = pytest.MonkeyPatch()
    try:
        workspace = _prepare_workspace(tmp_path, monkeypatch, scenario)
        runtime = RockyRuntime.load_from(workspace, cli_overrides=_live_cli_overrides())
        runtime.permissions.config.mode = "bypass"
        response = runtime.run_prompt(scenario.prompt, continue_session=False)
        return scenario, response
    finally:
        monkeypatch.undo()


def test_live_llm_phase1_step1_verification(live_run) -> None:
    scenario, response = live_run
    _assert_phase1(scenario, response)


def test_live_llm_phase2_step2_3_verification(live_run) -> None:
    scenario, response = live_run
    _assert_phase2(scenario, response)


def test_live_llm_phase3_multi_step_verification(live_run) -> None:
    scenario, response = live_run
    _assert_phase3_behavior(scenario, response)


@pytest.fixture(
    scope="session",
    params=phase4_mini_projects(),
    ids=[scenario.name for scenario in phase4_mini_projects()],
)
def live_phase4_run(
    request: pytest.FixtureRequest,
    tmp_path_factory: pytest.TempPathFactory,
    live_provider_ready: None,
):
    scenario: MiniProjectScenario = request.param
    tmp_path = tmp_path_factory.mktemp(f"live_phase4_{scenario.name}")
    monkeypatch = pytest.MonkeyPatch()
    try:
        workspace = _prepare_clean_workspace(tmp_path, monkeypatch)
        materialize_mini_project_workspace(workspace, scenario)
        runtime = RockyRuntime.load_from(workspace, cli_overrides=_live_cli_overrides())
        runtime.permissions.config.mode = "bypass"
        response = runtime.run_prompt(scenario.prompt, continue_session=False)
        return scenario, response, workspace
    finally:
        monkeypatch.undo()


def test_live_llm_phase4_mini_project_agentic_verification(live_phase4_run) -> None:
    scenario, response, workspace = live_phase4_run
    trace_path = response.trace.get("trace_path", "unknown-trace")
    successful_names = _tool_result_names(response, successes_only=True)
    expectations = scenario.phase_expectations

    assert response.route.task_class == scenario.task_class, trace_path
    assert response.route.task_signature == scenario.task_signature, trace_path
    assert response.trace["provider"] == "OpenAIChatProvider", trace_path
    assert response.verification["status"] == "pass", trace_path
    assert "Provider request failed:" not in response.text, trace_path
    assert "Tool loop ended without a final assistant response." not in response.text, trace_path
    assert len(successful_names) >= expectations.min_successful_tools, trace_path
    for tool_name in expectations.required_tools:
        assert tool_name in successful_names, trace_path

    for relative_path in scenario.expected_files:
        assert (workspace / relative_path).is_file(), f"missing {relative_path}; trace={trace_path}"


def test_live_llm_phase4_mini_project_outputs(live_phase4_run) -> None:
    scenario, response, workspace = live_phase4_run
    trace_path = response.trace.get("trace_path", "unknown-trace")

    result = subprocess.run(
        list(scenario.verify_command),
        cwd=str(workspace),
        check=True,
        capture_output=True,
        text=True,
    )
    stdout = result.stdout.strip()

    if scenario.output_kind == "json":
        assert json.loads(stdout) == scenario.expected_output, trace_path
    else:
        assert stdout == scenario.expected_output, trace_path

    for snippet in scenario.response_snippets:
        assert snippet in response.text, f"missing snippet {snippet!r}; trace={trace_path}"


def test_live_llm_phase1_cli_date_and_live_price_lookup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    live_provider_ready: None,
) -> None:
    workspace = _prepare_clean_workspace(tmp_path, monkeypatch)
    runtime = RockyRuntime.load_from(workspace, cli_overrides=_live_cli_overrides())
    runtime.permissions.config.mode = "bypass"

    response = runtime.run_prompt(
        "what's the date today? use cli to get exact date and check the nike price of today",
        continue_session=False,
    )

    trace_path = response.trace.get("trace_path", "unknown-trace")
    successful_names = _tool_result_names(response, successes_only=True)

    assert response.route.task_class == TaskClass.REPO, trace_path
    assert response.route.task_signature == "repo/shell_execution", trace_path
    assert response.trace["provider"] == "OpenAIChatProvider", trace_path
    assert response.verification["status"] == "pass", trace_path
    assert "run_shell_command" in successful_names, trace_path
    assert "Provider request failed:" not in response.text, trace_path
    assert "Tool loop ended without a final assistant response." not in response.text, trace_path
    assert "Nike" in response.text or "NKE" in response.text, trace_path


def _run_phase5_training_loop(
    workspace: Path,
    case: Phase5LearningCase,
    *,
    max_rounds: int = 3,
):
    seed_runtime = _runtime_for_workspace(workspace)
    seed_response = seed_runtime.run_prompt(case.seed_scenario.prompt, continue_session=False)

    baseline_runtime = _runtime_for_workspace(workspace)
    baseline_response = baseline_runtime.run_prompt(case.follow_up_prompt, continue_session=False)
    learn_result = _publish_learning(seed_runtime, case.feedback)

    last_response = baseline_response
    for round_index in range(max_rounds):
        runtime = _runtime_for_workspace(workspace)
        response = runtime.run_prompt(case.follow_up_prompt, continue_session=False)
        last_response = response
        if response.verification["status"] == "pass" and learn_result["skill_id"] in response.trace["selected_skills"]:
            return seed_response, baseline_response, learn_result, response, runtime
        if (
            round_index < max_rounds - 1
            and response.route.task_signature == case.expected_task_signature
        ):
            learn_result = _publish_learning(runtime, case.feedback)
    raise AssertionError(
        f"phase5 training loop did not converge for {case.name}; "
        f"seed_verification={seed_response.verification}; "
        f"baseline_verification={baseline_response.verification}; "
        f"last_verification={last_response.verification}; "
        f"selected_skills={last_response.trace.get('selected_skills')}"
    )


@pytest.mark.parametrize("case", _phase5_learning_cases(), ids=[case.name for case in _phase5_learning_cases()])
def test_live_llm_phase5_learning_and_workspace_continuity(
    case: Phase5LearningCase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    live_provider_ready: None,
) -> None:
    workspace = _prepare_phase5_workspace(tmp_path, monkeypatch, case)
    seed_response, baseline_response, learn_result, response, runtime = _run_phase5_training_loop(workspace, case)
    trace_path = response.trace.get("trace_path", "unknown-trace")
    context = runtime.current_context()

    assert seed_response.verification["status"] == "pass", trace_path
    assert baseline_response.trace["context"]["handoffs"], trace_path
    assert not any(
        skill.get("origin") == "learned"
        for skill in baseline_response.trace["context"]["skills"]
    ), trace_path
    assert response.route.task_signature == case.expected_task_signature, trace_path
    assert response.verification["status"] == "pass", trace_path
    assert response.trace["provider"] == "OpenAIChatProvider", trace_path
    assert learn_result["skill_id"] in response.trace["selected_skills"], trace_path
    assert context["handoffs"], trace_path
    assert any(skill.get("origin") == "learned" for skill in context["skills"]), trace_path
    _assert_phase5_output(case, response, workspace)
