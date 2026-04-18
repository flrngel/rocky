from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from rocky.tool_events import tool_event_artifacts, tool_event_summary_text
from rocky.util.text import tokenize_keywords, truncate
from rocky.util.time import utc_iso


STATUS_READY = "ready"
STATUS_DOING = "doing"
STATUS_CHECKING = "checking"
STATUS_DONE = "done"
STATUS_BLOCKED = "blocked"

LIVE_PAGE_TOOL_NAMES = {"fetch_url", "agent_browser", "browser_render_page"}


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return cleaned[:40] or "run"


def _append_unique(items: list[str], value: str, *, limit: int) -> None:
    cleaned = " ".join(str(value or "").split()).strip()
    if not cleaned:
        return
    if cleaned in items:
        return
    items.append(cleaned)
    if len(items) > limit:
        del items[:-limit]


def _render_checkbox(text: str, checked: bool) -> str:
    box = "x" if checked else " "
    return f"- [{box}] {text}"


def _extract_urls(text: str) -> list[str]:
    return [match.rstrip(").,;:!?]") for match in re.findall(r"https?://[^\s)]+", text or "")]


def _looks_like_build_task(prompt: str) -> bool:
    lowered = prompt.lower()
    return any(
        token in lowered
        for token in (
            "build ",
            "create ",
            "write ",
            "make ",
            "fix ",
            "update ",
            "edit ",
            "scaffold ",
            "implement ",
            "automate ",
        )
    )


def _looks_like_exact_output_task(prompt: str) -> bool:
    lowered = prompt.lower()
    return any(
        token in lowered
        for token in (
            "exact json",
            "exact output",
            "valid json",
            "return json",
            "show me the exact",
            "tell me the exact",
        )
    )


def _lead_excerpt(text: str, *, limit: int = 180) -> str:
    cleaned = " ".join(str(text or "").split()).strip()
    if not cleaned:
        return ""
    return cleaned if len(cleaned) <= limit else cleaned[: max(0, limit - 3)] + "..."


def _fact_rank(text: str) -> tuple[int, int]:
    lowered = text.lower()
    if text.startswith("Link item:"):
        return (0, -len(text))
    if lowered.startswith("lead:"):
        return (1, -len(text))
    if text.startswith("Observed"):
        return (2, -len(text))
    if text.startswith("Title:"):
        return (3, -len(text))
    if text.startswith("Headers:"):
        return (4, -len(text))
    if text.startswith("Excerpt:"):
        return (6, -len(text))
    if text.startswith("Fetched ") or text.startswith("Search returned "):
        return (7, -len(text))
    return (5, -len(text))


def _event_fact_lines(event: dict[str, Any], *, limit: int = 6) -> list[str]:
    lines: list[str] = []
    for item in list(event.get("facts") or []):
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text") or "").split()).strip()
        if not text or text in lines:
            continue
        lines.append(text)
    ranked = sorted(lines, key=_fact_rank)
    return ranked[:limit]


def _compact_fact_for_import(text: str, *, limit: int = 180) -> str:
    cleaned = " ".join(str(text or "").split()).strip()
    if not cleaned:
        return ""
    urls = _extract_urls(cleaned)
    if not urls:
        return _lead_excerpt(cleaned, limit=limit)
    url = urls[0]
    url_start = cleaned.find(url)
    prefix = cleaned[:url_start].rstrip(" (")
    prefix_limit = max(40, limit - len(url) - 4)
    compact_prefix = _lead_excerpt(prefix, limit=prefix_limit).rstrip()
    if compact_prefix:
        return f"{compact_prefix} ({url})"
    return url


def _lead_score(url: str, title: str, prompt: str) -> int:
    haystack = f"{url} {title}".lower()
    prompt_tokens = tokenize_keywords(prompt)
    score = 0
    for token in prompt_tokens:
        if len(token) >= 4 and token in haystack:
            score += 3
    parsed = urlparse(url)
    path = parsed.path.lower()
    host = parsed.netloc.lower()
    if any(segment in path for segment in ("/models", "/model", "/leaderboard", "/list")):
        score += 4
    if "trending" in haystack or "leaderboard" in haystack:
        score += 3
    if "text" in prompt.lower() and "text-generation" in haystack:
        score += 4
    if "under" in prompt.lower() and "num_parameters" in haystack:
        score += 4
    if any(segment in host for segment in ("linkedin.", "pinterest.", "youtube.", "reddit.")):
        score -= 4
    if any(segment in path for segment in ("/blog", "/news", "/pulse/")):
        score -= 2
    return score


def _under_b_limit(prompt: str) -> str:
    match = re.search(r"\b(?:under|below|less than)\s+(\d+(?:\.\d+)?)\s*b\b", prompt, flags=re.I)
    return str(match.group(1)) if match else ""


def _under_b_limit_number(prompt: str) -> float | None:
    limit = _under_b_limit(prompt)
    if not limit:
        return None
    try:
        return float(limit)
    except ValueError:
        return None


def _numeric_search_terms(prompt: str) -> list[str]:
    limit = _under_b_limit_number(prompt)
    if limit is None or limit <= 1:
        return []
    terms: list[str] = []
    seen: set[int] = set()
    for value in (
        int(limit * 0.67),
        int(limit * 0.75),
        int(limit * 0.58),
        int(limit * 0.33),
        max(1, int(limit * 0.17)),
        int(limit) - 1,
    ):
        if value <= 0 or value >= limit or value in seen:
            continue
        seen.add(value)
        terms.append(f"{value}B")
    return terms


def _refine_lead_url_for_prompt(url: str, prompt: str) -> str:
    limit = _under_b_limit(prompt)
    if not limit or "num_parameters=" not in url:
        return url
    parsed = urlparse(url)
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    changed = False
    refined_pairs: list[tuple[str, str]] = []
    for key, value in pairs:
        if re.fullmatch(r"min:[^,]+,max:[^,]+", value, flags=re.I):
            value = f"min:0,max:{limit}B"
            changed = True
        refined_pairs.append((key, value))
    if not changed:
        return url
    return urlunparse(parsed._replace(query=urlencode(refined_pairs, safe=":,")))


def _numeric_search_urls_for_prompt(url: str, prompt: str) -> list[str]:
    terms = _numeric_search_terms(prompt)
    if not terms:
        return []
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return []
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    keys = [key for key, _value in pairs]
    key_set = set(keys)
    if "search" in key_set:
        search_key = "search"
    elif "q" in key_set:
        search_key = "q"
    elif "query" in key_set:
        search_key = "query"
    elif any(key == "sort" or key.startswith("task") for key in key_set):
        search_key = "search"
    else:
        return []

    urls: list[str] = []
    for term in terms:
        next_pairs: list[tuple[str, str]] = []
        replaced = False
        for key, value in pairs:
            if key == search_key:
                next_pairs.append((key, term))
                replaced = True
            elif key.lower().startswith("cache"):
                continue
            else:
                next_pairs.append((key, value))
        if not replaced:
            next_pairs.append((search_key, term))
        urls.append(urlunparse(parsed._replace(query=urlencode(next_pairs, safe=":,"))))
    return urls


def _urls_from_texts(items: list[str]) -> list[str]:
    urls: list[str] = []
    for item in items:
        urls.extend(_extract_urls(item))
    return urls


def _extract_candidate_pool(text: str) -> list[str]:
    """Extract a candidate name list from free text.

    Looks for a numbered or bulleted list with at least 10 entries that looks
    like product / item names (not full sentences).  Returns the names in order,
    or an empty list if no qualifying list is found.
    """
    lines = str(text or "").splitlines()
    names: list[str] = []
    for line in lines:
        stripped = line.strip()
        # Match "1. Name", "1) Name", "- Name", "* Name" patterns
        m = re.match(r"^(?:\d+[.)]\s+|[-*]\s+)(.+)$", stripped)
        if not m:
            continue
        name = m.group(1).strip()
        # Skip long sentences — we want product/item names, not prose
        if len(name) > 120 or len(name) < 3:
            continue
        if name.endswith((".", "?", "!")):
            continue
        names.append(name)
    if len(names) >= 10:
        return names
    return []


def _reflection_excerpt(text: str, *, limit: int = 180) -> str:
    candidates: list[str] = []
    for piece in re.split(r"[\n\r]+|(?<=[.!?])\s+", str(text or "")):
        cleaned = " ".join(piece.split()).strip()
        if not cleaned:
            continue
        lowered = cleaned.lower()
        if re.match(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)", cleaned):
            continue
        if cleaned.startswith("#") or cleaned.startswith("```"):
            continue
        if lowered.startswith(("based on ", "here is ", "here are ", "sources:", "source:")):
            continue
        if any(
            phrase in lowered
            for phrase in (
                "need to ",
                "needs to ",
                "next ",
                "still need ",
                "missing ",
                "verify ",
                "inspect ",
                "open ",
                "filter ",
                "continue ",
                "gather ",
                "check ",
            )
        ):
            candidates.append(cleaned)
    if not candidates:
        return ""
    return _lead_excerpt(candidates[0], limit=limit)


@dataclass(slots=True)
class FlowTask:
    task_id: str
    title: str
    goal: str
    next_move: str
    done_when: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    rollup: str = ""
    status: str = STATUS_READY
    kind: str = "work"
    parent_id: str = "ROOT"

    def short_line(self) -> str:
        return f"{self.task_id} [{self.status}] {self.title}"

    def compact_rollup(self) -> str:
        if self.rollup:
            return self.rollup
        if self.facts:
            return self.facts[-1]
        return f"{self.title} still in progress"

    def render_markdown(self) -> str:
        lines = [
            f"# Task {self.task_id}",
            "",
            "## Identity",
            f"- Parent: {self.parent_id}",
            f"- Status: {self.status}",
            f"- Title: {self.title}",
            "",
            "## Goal",
            f"- {self.goal}",
            "",
            "## Done When",
        ]
        if self.done_when:
            lines.extend(f"- {item}" for item in self.done_when)
        else:
            lines.append("- Finish the local goal with grounded evidence.")
        if self.imports:
            lines.extend(["", "## Imports", *[f"- {item}" for item in self.imports]])
        if self.notes:
            lines.extend(["", "## Notes", *[f"- {item}" for item in self.notes]])
        if self.facts:
            lines.extend(["", "## Facts", *[f"- {item}" for item in self.facts]])
        if self.artifacts:
            lines.extend(["", "## Artifacts", *[f"- {item}" for item in self.artifacts]])
        lines.extend(
            [
                "",
                "## Next Move",
                f"- {self.next_move or 'Pending'}",
                "",
                "## Rollup",
                f"- {self.rollup or 'pending'}",
                "",
            ]
        )
        return "\n".join(lines)

    def capsule_text(self) -> str:
        lines = [
            f"Active task: {self.task_id} [{self.status}] {self.title}",
            f"Goal: {self.goal}",
        ]
        if self.imports:
            lines.append("Imports:")
            lines.extend(f"- {item}" for item in self.imports[-10:])
        if self.notes:
            lines.append("Notes:")
            lines.extend(f"- {item}" for item in self.notes[-3:])
        if self.facts:
            lines.append("Facts:")
            lines.extend(f"- {item}" for item in sorted(self.facts, key=_fact_rank)[:10])
        if self.artifacts:
            lines.append("Artifacts:")
            lines.extend(f"- {item}" for item in self.artifacts[-8:])
        if self.done_when:
            lines.append("Done when:")
            lines.extend(f"- {item}" for item in self.done_when[:3])
        lines.append(f"Next move: {self.next_move or 'Pending'}")
        return "\n".join(lines)


@dataclass(slots=True)
class FlowRun:
    run_id: str
    mission: str
    task_signature: str
    task_class: str
    execution_cwd: str
    prompt: str
    success_criteria: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    global_facts: list[str] = field(default_factory=list)
    tasks: list[FlowTask] = field(default_factory=list)
    active_task_id: str = ""
    status: str = STATUS_READY
    created_at: str = field(default_factory=utc_iso)
    updated_at: str = field(default_factory=utc_iso)

    def task_map(self) -> dict[str, FlowTask]:
        return {task.task_id: task for task in self.tasks}

    def active_task(self) -> FlowTask:
        task_by_id = self.task_map()
        if self.active_task_id and self.active_task_id in task_by_id:
            return task_by_id[self.active_task_id]
        for task in self.tasks:
            if task.status in {STATUS_READY, STATUS_DOING, STATUS_CHECKING}:
                self.active_task_id = task.task_id
                return task
        self.active_task_id = self.tasks[-1].task_id
        return self.tasks[-1]

    def done_count(self) -> int:
        return sum(1 for task in self.tasks if task.status == STATUS_DONE)

    def render_markdown(self) -> str:
        lines = [
            "# Flow",
            "",
            "## Mission",
            f"- Goal: {self.mission}",
            f"- Status: {self.status}",
            f"- Task signature: {self.task_signature}",
            f"- Updated: {self.updated_at}",
            "",
            "## Success Criteria",
        ]
        success_done = self.status == STATUS_DONE
        if self.success_criteria:
            lines.extend(_render_checkbox(item, success_done) for item in self.success_criteria)
        else:
            lines.append("- [ ] Complete the user's request with grounded evidence.")
        if self.constraints:
            lines.extend(["", "## Global Constraints", *[f"- {item}" for item in self.constraints]])
        lines.extend(
            [
                "",
                "## Task Tree",
                "| ID | Parent | Title | Status |",
                "| --- | --- | --- | --- |",
            ]
        )
        for task in self.tasks:
            lines.append(f"| {task.task_id} | {task.parent_id} | {task.title} | {task.status} |")
        lines.extend(
            [
                "",
                "## Active Path",
                f"- ROOT -> {self.active_task_id or self.active_task().task_id}",
            ]
        )
        if self.global_facts:
            lines.extend(["", "## Global Facts", *[f"- {item}" for item in self.global_facts[-6:]]])
        lines.extend(
            [
                "",
                "## Next Transition",
                f"- Activate `{self.active_task().task_id}` and follow its local note.",
                "",
            ]
        )
        return "\n".join(lines)

    def flow_capsule(self) -> str:
        lines = [
            f"Mission: {self.mission}",
            f"Run status: {self.status}",
            "Success criteria:",
        ]
        lines.extend(f"- {item}" for item in self.success_criteria[:4])
        if self.constraints:
            lines.append("Global constraints:")
            lines.extend(f"- {item}" for item in self.constraints[:8])
        lines.append("Task tree:")
        lines.extend(f"- {task.short_line()}" for task in self.tasks[:6])
        if self.global_facts:
            lines.append("Global facts:")
            lines.extend(f"- {item}" for item in self.global_facts[-4:])
        lines.append(f"Active path: ROOT -> {self.active_task().task_id}")
        return "\n".join(lines)


class RunFlowManager:
    def __init__(
        self,
        runs_root: Path,
        *,
        prompt: str,
        task_signature: str,
        task_class: str,
        execution_cwd: str,
        minimum_list_items: int = 0,
    ) -> None:
        self.runs_root = runs_root
        self.prompt = prompt
        self.task_signature = task_signature
        self.task_class = task_class
        self.execution_cwd = execution_cwd
        self.minimum_list_items = minimum_list_items
        seed = f"{utc_iso().replace(':', '').replace('-', '')}_{_slug(prompt)}"
        self.run_dir = self.runs_root / seed
        self.tasks_dir = self.run_dir / "tasks"
        self.artifacts_dir = self.run_dir / "artifacts"
        self.flow_path = self.run_dir / "flow.md"
        self.run = self._bootstrap_run(seed)
        self._write()

    def _bootstrap_run(self, run_id: str) -> FlowRun:
        mission = _lead_excerpt(self.prompt, limit=220)
        success = self._success_criteria()
        constraints = self._constraints()
        tasks = self._initial_tasks()
        if tasks:
            tasks[0].status = STATUS_DOING
        return FlowRun(
            run_id=run_id,
            mission=mission,
            task_signature=self.task_signature,
            task_class=self.task_class,
            execution_cwd=self.execution_cwd,
            prompt=self.prompt,
            success_criteria=success,
            constraints=constraints,
            tasks=tasks,
            active_task_id=tasks[0].task_id if tasks else "",
            status=STATUS_DOING if tasks else STATUS_READY,
        )

    def _success_criteria(self) -> list[str]:
        if self.task_signature.startswith(("research/", "site/")):
            criteria = [
                "Map the most relevant live sources before concluding.",
                "Gather observed evidence from opened live pages, not just search snippets.",
            ]
            if self.minimum_list_items > 0:
                criteria.append(f"Produce a grounded list with at least {self.minimum_list_items} supported items, or clearly state that not enough verified items were found.")
            else:
                criteria.append("Produce the final answer from supported live evidence.")
            return criteria
        if self.task_signature in {"repo/shell_execution", "automation/general"} or _looks_like_build_task(self.prompt):
            return [
                "Create or update the requested workspace artifacts.",
                "Verify the result with an observed follow-up step before reporting success.",
                "Answer with the exact observed result when the user asked for it.",
            ]
        if self.task_signature.startswith(("extract/", "data/")) or _looks_like_exact_output_task(self.prompt):
            return [
                "Inspect the real input first.",
                "Produce the requested structured result.",
                "Return the final output in the requested format only after verification.",
            ]
        return [
            "Inspect the relevant workspace or live state first.",
            "Answer directly from supported evidence.",
        ]

    def _constraints(self) -> list[str]:
        constraints: list[str] = []
        prompt_lower = self.prompt.lower()
        for url in _extract_urls(self.prompt)[:3]:
            constraints.append(f"Start from the user-provided URL when relevant: {url}")
        if "show me as a list" in prompt_lower or "list" in prompt_lower:
            constraints.append("Use list formatting in the final answer when requested.")
        if self.minimum_list_items > 0:
            constraints.append(f"Do not stop before either supporting at least {self.minimum_list_items} items or clearly saying the evidence was insufficient.")
        for phrase in re.findall(r"\b(?:under|below|less than|over|above|more than|at least)\s+[0-9][a-z0-9. -]*", self.prompt, flags=re.I)[:3]:
            constraints.append(f"Respect the numeric filter from the request: {phrase.strip()}.")
        if any(token in prompt_lower for token in ("right now", "current", "currently", "trending", "latest", "recent")):
            constraints.append("Preserve the recency/currentness requirement from the request.")
        if any(token in prompt_lower for token in ("openweight", "open-weight", "open weight")):
            constraints.append("Preserve the open-weight/openweight filter from the request.")
        if any(token in prompt_lower for token in ("text model", "text models", "llm", "language model")):
            constraints.append("Preserve the requested model/task type filter.")
        if self.task_signature.startswith(("research/", "site/")):
            constraints.append("Treat search results as leads; final claims must come from opened pages.")
        if self.task_signature in {"repo/shell_execution", "automation/general"} or _looks_like_build_task(self.prompt):
            constraints.append("Verify created or edited outputs before declaring success.")
        return constraints[:8]

    def _initial_tasks(self) -> list[FlowTask]:
        if self.task_signature.startswith(("research/", "site/")):
            target = self.minimum_list_items or 4
            return [
                FlowTask(
                    task_id="T1",
                    title="Map the source surface",
                    goal="Find the strongest live pages or listings to inspect for this request.",
                    done_when=[
                        "At least one promising live page has been opened or fetched.",
                        "The next page to inspect is explicit.",
                    ],
                    notes=[
                        "Use the user-provided URL first when one exists.",
                        "Search results are leads. Open pages before trusting claims.",
                    ],
                    next_move="Open the best starting page and record what it exposes.",
                    kind="discover",
                ),
                FlowTask(
                    task_id="T2",
                    title="Gather candidate evidence",
                    goal=f"Collect concrete candidate items from live pages until there is evidence for roughly {target} items or the source surface is exhausted.",
                    done_when=[
                        f"Observed item evidence exists for about {target} candidates.",
                        "Key candidate pages or model/detail pages are named explicitly.",
                    ],
                    notes=[
                        "Stay close to the source site when possible.",
                        "Record item names and URLs instead of summarizing families too early.",
                    ],
                    next_move="Inspect listing or detail pages and collect grounded candidate items.",
                    kind="gather",
                ),
                FlowTask(
                    task_id="T3",
                    title="Verify constraints and answer",
                    goal="Turn the gathered evidence into the final answer without unsupported claims.",
                    done_when=[
                        "Every final item is supported by opened-page evidence.",
                        "The final answer satisfies the requested format and count.",
                    ],
                    notes=[
                        "Do not rely on search snippets for final claims.",
                        "If support is still missing, continue gathering evidence instead of guessing.",
                    ],
                    next_move="Verify the requested filters item by item, then draft the final answer with sources.",
                    kind="finalize",
                ),
            ]
        if self.task_signature in {"repo/shell_execution", "automation/general"} or _looks_like_build_task(self.prompt):
            return [
                FlowTask(
                    task_id="T1",
                    title="Shape the requested artifact",
                    goal="Inspect just enough state to create or update the requested workspace artifact.",
                    done_when=["The intended file or command target is clear."],
                    notes=["Avoid broad exploration. Move quickly to the requested artifact."],
                    next_move="Create or edit the main workspace artifact.",
                    kind="build",
                ),
                FlowTask(
                    task_id="T2",
                    title="Verify the observed result",
                    goal="Run a follow-up check that proves the requested result actually works.",
                    done_when=["A real verification step has succeeded."],
                    notes=["Prefer exact observed output over paraphrase."],
                    next_move="Run the verification step and record the result.",
                    kind="verify",
                ),
                FlowTask(
                    task_id="T3",
                    title="Report the exact outcome",
                    goal="Answer from the observed result and mention the concrete file or command used.",
                    done_when=["Final answer is grounded in the observed verification output."],
                    notes=["If the user asked for exact JSON or exact output, return it directly."],
                    next_move="Use the verified result to produce the final answer.",
                    kind="finalize",
                ),
            ]
        if self.task_signature.startswith(("extract/", "data/")) or _looks_like_exact_output_task(self.prompt):
            return [
                FlowTask(
                    task_id="T1",
                    title="Inspect the input surface",
                    goal="Read the exact input source that the extraction depends on.",
                    done_when=["The source shape is clear from observed input."],
                    notes=["Use the named file or source first instead of searching broadly."],
                    next_move="Inspect the input and capture the relevant structure.",
                    kind="inspect",
                ),
                FlowTask(
                    task_id="T2",
                    title="Produce the requested structure",
                    goal="Transform the observed input into the requested output format.",
                    done_when=["A concrete candidate output exists."],
                    notes=["Keep the output faithful to the observed input."],
                    next_move="Generate the candidate structured result.",
                    kind="produce",
                ),
                FlowTask(
                    task_id="T3",
                    title="Verify and return the output",
                    goal="Verify the candidate structure and return only the requested final format.",
                    done_when=["The final output matches the requested format and observed source."],
                    notes=["Do not wrap exact JSON answers in extra prose."],
                    next_move="Verify the output shape and produce the final answer.",
                    kind="finalize",
                ),
            ]
        return [
            FlowTask(
                task_id="T1",
                title="Inspect the relevant state",
                goal="Gather the most relevant evidence for the request.",
                done_when=["There is enough direct evidence to answer."],
                notes=["Prefer narrow inspection over broad exploration."],
                next_move="Inspect the most relevant file, command, or page first.",
                kind="inspect",
            ),
            FlowTask(
                task_id="T2",
                title="Answer from evidence",
                goal="Use the gathered evidence to produce the final answer.",
                done_when=["The final answer is grounded and direct."],
                notes=["Do not restate exploration. Answer from the evidence."],
                next_move="Turn the observed evidence into the final answer.",
                kind="finalize",
            ),
        ]

    def _write(self) -> None:
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.flow_path.write_text(self.run.render_markdown(), encoding="utf-8")
        for task in self.run.tasks:
            task_dir = self.artifacts_dir / task.task_id
            task_dir.mkdir(parents=True, exist_ok=True)
            (self.tasks_dir / f"{task.task_id}.md").write_text(task.render_markdown(), encoding="utf-8")

    @property
    def run_summary(self) -> dict[str, Any]:
        active = self.run.active_task()
        return {
            "run_id": self.run.run_id,
            "run_dir": str(self.run_dir),
            "flow_path": str(self.flow_path),
            "task_signature": self.run.task_signature,
            "active_task_id": active.task_id,
            "active_task_path": str(self.tasks_dir / f"{active.task_id}.md"),
            "status": self.run.status,
            "done_tasks": self.run.done_count(),
            "total_tasks": len(self.run.tasks),
        }

    def flow_prompt_block(self) -> str:
        return self.run.flow_capsule()

    def active_task_prompt_block(self) -> str:
        return self.run.active_task().capsule_text()

    def _is_candidate_draft_burst(self) -> bool:
        """Return True when this is the first burst of a research-flavored flow.

        Burst-0 is identified by:
        - the active task has kind == "discover" (research/* + site/* only)
        - task_signature starts with "research/" or "site/"
        - the active task has no ingested facts or artifacts yet (no tool events processed)
        - no candidate_pool entry exists in global_facts (suppresses re-injection if state
          persists across runs or the pool was already recorded)
        """
        task = self.run.active_task()
        # Widened 2026-04-17 after T4 live-calibration found that gemma4:26b
        # frequently routes recommendation tasks into `site/understanding/*`
        # rather than `research/live_compare/*`, defeating a prefix-only gate.
        # The honest invariant for a "research-flavored first burst" is
        # task.kind == "discover" — that kind only fires for research/* + site/*
        # flows per current-state.md's flow-loop-task-kinds table, and only the
        # FIRST burst of those flows has it. Keep the no-facts/no-artifacts gate
        # as belt-and-suspenders in case a future flow reuses the kind.
        if task.kind != "discover":
            return False
        if not self.task_signature.startswith(("research/", "site/")):
            return False
        if task.facts or task.artifacts:
            return False
        if any(f.startswith("candidate_pool:") for f in self.run.global_facts):
            return False
        return True

    def record_candidate_pool(self, names: list[str]) -> None:
        """Persist a candidate name list as a global flow fact.

        Stores a single ``candidate_pool: <comma-separated names>`` entry in
        ``self.run.global_facts`` so that subsequent bursts can see it and T4's
        trace assertion (``candidate_pool_present``) can find it.
        """
        if not names:
            return
        if any(f.startswith("candidate_pool:") for f in self.run.global_facts):
            return
        pool_text = "candidate_pool: " + ", ".join(names[:30])
        _append_unique(self.run.global_facts, pool_text, limit=12)
        self.run.updated_at = utc_iso()
        self._write()

    def task_instruction(self) -> str:
        task = self.run.active_task()
        if task.kind == "finalize":
            return (
                "Work only on the active task. Use the gathered evidence to produce the final answer. "
                "If any requested filter is still unsupported for the final items, make one focused evidence-gathering tool call instead of guessing."
            )
        if task.kind == "gather":
            return (
                "Work only on the active task. Gather concrete item-level evidence, preserving names, URLs, and filter clues. "
                "Do not draft the final answer yet unless the verifier can already support the requested output."
            )
        base = (
            "Work only on the active task. Do not try to complete the whole mission yet. "
            "Use tools to make progress on this local goal, then stop once the next move is clearer."
        )
        if self._is_candidate_draft_burst():
            candidate_block = (
                "\n\nBefore making any tool call, enumerate 15 to 25 specific candidate "
                "names from your own knowledge that are relevant to this request. "
                "List each candidate on its own line (numbered list). "
                "This candidate list will guide breadth-first exploration — "
                "do not restrict yourself to fewer than 15 candidates."
            )
            return base + candidate_block
        return base

    def user_prompt_for_burst(self) -> str:
        task = self.run.active_task()
        return (
            f"Original request: {self.prompt}\n\n"
            f"Active task {task.task_id}: {task.title}\n"
            f"Local goal: {task.goal}\n"
            f"Done when: {'; '.join(task.done_when[:3])}\n"
            f"Next move: {task.next_move}\n\n"
            f"{self.task_instruction()}"
        )

    def _active_task_mut(self) -> FlowTask:
        return self.run.active_task()

    def ingest_tool_event(self, event: dict[str, Any]) -> None:
        task = self._active_task_mut()
        summary = tool_event_summary_text(event)
        success = bool(event.get("success", True))
        best_next_url = ""
        if summary:
            if success:
                _append_unique(task.facts, summary, limit=20)
            else:
                _append_unique(task.notes, summary, limit=6)
        for fact_line in _event_fact_lines(event, limit=12 if self.task_signature.startswith(("research/", "site/")) else 4):
            _append_unique(task.facts, fact_line, limit=20)
        for artifact in tool_event_artifacts(event):
            ref = str(artifact.get("ref") or "").strip()
            if ref:
                _append_unique(task.artifacts, ref, limit=14)
        name = str(event.get("name") or "")
        if name in LIVE_PAGE_TOOL_NAMES:
            for artifact in tool_event_artifacts(event):
                if str(artifact.get("kind") or "") == "url":
                    _append_unique(self.run.global_facts, f"Opened live page: {artifact.get('ref')}", limit=8)
                    break
        elif name == "search_web":
            url_titles: list[tuple[str, str]] = []
            for fact in list(event.get("facts") or []):
                if not isinstance(fact, dict):
                    continue
                url = str(fact.get("url") or "").strip()
                if not url:
                    continue
                url_titles.append((url, str(fact.get("title") or "").strip()))
            urls = [url for url, _title in url_titles]
            if urls:
                _append_unique(
                    task.notes,
                    f"Search surfaced leads such as {', '.join(urls[:3])}. Open a page before trusting the results.",
                    limit=6,
                )
                ranked = sorted(url_titles, key=lambda item: _lead_score(item[0], item[1], self.prompt), reverse=True)
                if ranked and _lead_score(ranked[0][0], ranked[0][1], self.prompt) > 0:
                    best_next_url = _refine_lead_url_for_prompt(ranked[0][0], self.prompt)
        self.run.updated_at = utc_iso()
        self._refresh_next_move()
        if best_next_url:
            task.next_move = f"Open the strongest search lead with fetch_url: {best_next_url}"
        self._write()

    def note_burst_output(self, text: str) -> None:
        task = self._active_task_mut()
        if task.kind == "finalize":
            return
        # For research-flavored discover-phase bursts, extract and persist a candidate pool
        # from the model's free-text response before the pool-persistence guard advances.
        # Gate mirrors `_is_candidate_draft_burst` exactly — same four-condition invariant —
        # so a future burst ordering cannot silently diverge between inject-time and
        # extract-time. (Widened 2026-04-17: research/* + site/* because gemma4:26b
        # routes recommendation tasks into site/understanding/* as often as into
        # research/live_compare/*.)
        if (
            task.kind == "discover"
            and self.task_signature.startswith(("research/", "site/"))
            and not (task.facts or task.artifacts)
            and not any(f.startswith("candidate_pool:") for f in self.run.global_facts)
        ):
            pool_names = _extract_candidate_pool(text)
            if pool_names:
                self.record_candidate_pool(pool_names)
        excerpt = _reflection_excerpt(text, limit=180)
        if not excerpt:
            return
        _append_unique(task.notes, excerpt, limit=6)
        self.run.updated_at = utc_iso()
        self._write()

    def note_verification_failure(self, failure: Any) -> None:
        message = str(getattr(failure, "message", failure) or "").strip()
        failure_class = str(getattr(failure, "failure_class", "") or "").strip()
        task = self._active_task_mut()
        _append_unique(task.notes, f"Verification failed: {_lead_excerpt(message, limit=220)}", limit=6)
        task.next_move = self._failure_next_move(message, task, failure_class=failure_class)
        if self._should_return_to_gather(task, message, failure_class):
            gather_task = self._research_recovery_task(message, failure_class)
            if gather_task is not None and gather_task.task_id != task.task_id:
                task.status = STATUS_READY
                gather_task.status = STATUS_DOING
                gather_task.next_move = self._failure_next_move(message, gather_task, failure_class=failure_class)
                _append_unique(
                    gather_task.imports,
                    f"{task.task_id} verification gap: {_lead_excerpt(message, limit=160)}",
                    limit=6,
                )
                _append_unique(
                    gather_task.notes,
                    "The previous draft was not supported enough. Gather or filter evidence before returning to the answer task.",
                    limit=6,
                )
                self.run.active_task_id = gather_task.task_id
            else:
                task.status = STATUS_DOING
        else:
            task.status = STATUS_DOING
            self.run.active_task_id = task.task_id
        self.run.status = STATUS_DOING
        self.run.updated_at = utc_iso()
        self._write()

    def _failure_next_move(self, message: str, task: FlowTask, *, failure_class: str = "") -> str:
        lowered = message.lower()
        if failure_class in {
            "answer_claimed_knowledge_without_reference",
            "minimum_list_count_not_met",
            "counted_list_missing_live_evidence",
            "counted_list_live_pages_too_shallow",
            "counted_list_search_stopped_too_early",
            "unsupported_claim_introduced",
            "research_list_item_url_unverified",
            "research_list_parameter_filter_unverified",
            "research_list_item_not_grounded",
            "research_list_item_markup_leak",
            "research_list_task_type_unverified",
        }:
            if self.task_signature.startswith(("research/", "site/")):
                return "Gather one more opened-page evidence slice and keep only items that satisfy the request before answering."
            return "Gather one more focused evidence slice, then retry the answer."
        if "unsupported" in lowered or "missing evidence" in lowered:
            if self.task_signature.startswith(("research/", "site/")):
                return "Open more source pages and verify the missing claims before trying to answer again."
            return "Gather the missing evidence with one more focused tool step, then retry."
        if "list" in lowered:
            return "Keep gathering grounded items until the requested list is satisfied."
        if "json" in lowered or "format" in lowered:
            return "Repair the output format and verify it against the observed source."
        return task.next_move

    def _should_return_to_gather(self, task: FlowTask, message: str, failure_class: str) -> bool:
        if not self.task_signature.startswith(("research/", "site/")):
            return False
        if task.kind != "finalize":
            return False
        if failure_class in {
            "answer_claimed_knowledge_without_reference",
            "minimum_list_count_not_met",
            "counted_list_missing_live_evidence",
            "counted_list_live_pages_too_shallow",
            "counted_list_search_stopped_too_early",
            "unsupported_claim_introduced",
            "research_list_item_url_unverified",
            "research_list_parameter_filter_unverified",
            "research_list_item_not_grounded",
            "research_list_item_markup_leak",
            "research_list_task_type_unverified",
        }:
            return True
        lowered = message.lower()
        return any(
            phrase in lowered
            for phrase in (
                "missing evidence",
                "unsupported",
                "gather more",
                "open more",
                "live item evidence",
                "stopped the counted",
                "too early",
                "tool failures observed",
            )
        )

    def _research_recovery_task(self, message: str, failure_class: str) -> FlowTask | None:
        lowered = message.lower()
        preferred = "discover" if "live page" in lowered or "source" in lowered else "gather"
        if failure_class in {
            "minimum_list_count_not_met",
            "counted_list_missing_live_evidence",
            "research_list_item_url_unverified",
            "research_list_parameter_filter_unverified",
            "research_list_item_not_grounded",
            "research_list_item_markup_leak",
            "research_list_task_type_unverified",
        }:
            preferred = "gather"
        for task in self.run.tasks:
            if task.kind == preferred:
                return task
        for task in self.run.tasks:
            if task.kind in {"gather", "discover"}:
                return task
        return None

    def _refresh_next_move(self) -> None:
        task = self._active_task_mut()
        refined_url = self._best_refined_known_url(task)
        if refined_url and task.kind in {"discover", "gather"}:
            task.next_move = f"Open the numeric-filtered source lead with fetch_url: {refined_url}"
            return
        if task.kind == "discover":
            task.next_move = "Open the strongest live page and record what it exposes."
        elif task.kind == "gather":
            target = self.minimum_list_items or 4
            task.next_move = f"Keep inspecting listing or detail pages until there is evidence for about {target} candidate items."
        elif task.kind == "verify":
            task.next_move = "Run the concrete verification step and record the observed result."
        elif task.kind == "finalize":
            task.next_move = "Use the gathered evidence to produce the final answer without unsupported claims."

    def _best_refined_known_url(self, task: FlowTask) -> str:
        if not _under_b_limit(self.prompt):
            return ""
        urls = [
            *task.artifacts,
            *_urls_from_texts(task.facts),
            *_urls_from_texts(task.imports),
            *_urls_from_texts(task.notes),
        ]
        candidates: list[tuple[int, int, str]] = []
        known_text = "\n".join([*task.facts, *task.notes])
        for url in urls:
            refined = _refine_lead_url_for_prompt(url, self.prompt)
            if refined == url:
                refined_urls = _numeric_search_urls_for_prompt(url, self.prompt)
            else:
                refined_urls = [refined]
            for candidate_url in refined_urls:
                if candidate_url == url:
                    continue
                if f"Fetched {candidate_url}" in known_text or f"already succeeded for {candidate_url}" in known_text:
                    continue
                candidates.append((_lead_score(candidate_url, "", self.prompt), len(candidates), candidate_url))
        if not candidates:
            return ""
        candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
        return candidates[0][2]

    def suggested_fetch_url(self) -> str:
        task = self._active_task_mut()
        if task.kind not in {"discover", "gather"}:
            return ""
        self._refresh_next_move()
        if "fetch_url" not in task.next_move:
            return ""
        urls = _extract_urls(task.next_move)
        return urls[0] if urls else ""

    def decorate_tool_result_event(self, event: dict[str, Any]) -> dict[str, Any]:
        task = self.run.active_task()
        lines = [
            tool_event_summary_text(event) or f"{event.get('name', 'tool')} completed",
            "",
            f"Task state: {task.task_id} [{task.status}] {task.title}",
        ]
        if task.facts:
            lines.extend(f"- {item}" for item in task.facts[-2:])
        if task.next_move:
            lines.append(f"Next move: {task.next_move}")
        decorated = dict(event)
        decorated["summary_text"] = tool_event_summary_text(event) or str(event.get("summary_text") or "")
        decorated["model_text"] = truncate("\n".join(item for item in lines if item), limit=900)
        decorated["text"] = decorated["model_text"]
        return decorated

    def _successful_live_page_count(self, tool_events: list[dict[str, Any]]) -> int:
        refs: set[str] = set()
        for event in tool_events:
            if event.get("type") != "tool_result" or not event.get("success", True):
                continue
            if str(event.get("name") or "") not in LIVE_PAGE_TOOL_NAMES:
                continue
            for artifact in tool_event_artifacts(event):
                if str(artifact.get("kind") or "") == "url":
                    ref = str(artifact.get("ref") or "").strip()
                    if ref:
                        refs.add(ref)
        return len(refs)

    def _successful_tool_names(self, tool_events: list[dict[str, Any]]) -> set[str]:
        return {
            str(event.get("name") or "")
            for event in tool_events
            if event.get("type") == "tool_result" and event.get("success", True)
        }

    def _observed_live_item_count(self, evidence_graph: Any) -> int:
        if evidence_graph is None:
            return 0
        values = {
            str(item.get("value") or "").strip()
            for item in getattr(evidence_graph, "entities", [])
            if str(item.get("kind") or "") == "live_item" and str(item.get("value") or "").strip()
        }
        return len(values)

    def advance(self, *, evidence_graph: Any, tool_events: list[dict[str, Any]], final_output_ready: bool = False) -> bool:
        task = self._active_task_mut()
        successful_names = self._successful_tool_names(tool_events)
        live_pages = self._successful_live_page_count(tool_events)
        live_items = self._observed_live_item_count(evidence_graph)

        should_advance = False
        if task.kind == "discover":
            should_advance = live_pages >= 1 or ("read_file" in successful_names and self.task_signature.startswith("repo/"))
        elif task.kind == "gather":
            target = self.minimum_list_items or 4
            if self.minimum_list_items > 0:
                should_advance = live_items >= target or (live_pages >= 2 and live_items >= min(4, target))
            else:
                should_advance = live_pages >= 1 or live_items >= target
        elif task.kind in {"build", "inspect", "produce"}:
            should_advance = bool(successful_names)
        elif task.kind == "verify":
            should_advance = "run_shell_command" in successful_names or "read_file" in successful_names
        elif task.kind == "finalize":
            should_advance = final_output_ready

        if not should_advance:
            task.status = STATUS_DOING
            self.run.status = STATUS_DOING
            self.run.updated_at = utc_iso()
            self._write()
            return False

        task.status = STATUS_DONE
        if not task.rollup:
            task.rollup = self._task_rollup(task)
        next_task = self._next_task(task.task_id)
        if next_task is None:
            self.run.status = STATUS_DONE
            self.run.updated_at = utc_iso()
            self._write()
            return True

        next_task.status = STATUS_DOING
        self._carry_task_context(task, next_task)
        _append_unique(self.run.global_facts, f"{task.task_id} done: {task.rollup}", limit=8)
        self.run.active_task_id = next_task.task_id
        self.run.status = STATUS_DOING
        self.run.updated_at = utc_iso()
        self._write()
        return True

    def _task_rollup(self, task: FlowTask) -> str:
        salient = self._salient_task_facts(task, limit=3)
        if salient:
            if task.kind == "gather":
                return "Candidate evidence: " + "; ".join(salient[:2])
            return "; ".join(salient[:2])
        if task.notes:
            return _lead_excerpt(task.notes[-1], limit=180)
        return f"{task.title} complete."

    def _salient_task_facts(self, task: FlowTask, *, limit: int) -> list[str]:
        ranked = sorted((fact for fact in task.facts if fact.strip()), key=_fact_rank)
        seen: set[str] = set()
        chosen: list[str] = []
        for fact in ranked:
            compact = _compact_fact_for_import(fact, limit=180)
            if not compact or compact in seen:
                continue
            seen.add(compact)
            chosen.append(compact)
            if len(chosen) >= limit:
                break
        return chosen

    def _carry_task_context(self, source: FlowTask, target: FlowTask) -> None:
        _append_unique(target.imports, f"{source.task_id}: {source.rollup}", limit=16)
        for fact in self._salient_task_facts(source, limit=10):
            _append_unique(target.imports, f"{source.task_id} fact: {fact}", limit=16)
        for ref in source.artifacts[-8:]:
            _append_unique(target.artifacts, ref, limit=14)

    def _next_task(self, task_id: str) -> FlowTask | None:
        ids = [task.task_id for task in self.run.tasks]
        try:
            index = ids.index(task_id)
        except ValueError:
            return None
        if index + 1 >= len(self.run.tasks):
            return None
        return self.run.tasks[index + 1]
