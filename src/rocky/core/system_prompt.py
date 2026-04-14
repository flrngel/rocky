from __future__ import annotations

import re

from rocky.core.context import ContextPackage
from rocky.core.runtime_state import prompt_requests_list_output, requested_minimum_list_items


_SHELL_STYLE_MARKERS = ("shell", "bash", "cli", "terminal", "one-liner", "python3 -c", "`")
_FORMAT_STYLE_MARKERS = ("json", "yaml", "markdown", "table", "bullet", "fenced", "quoted")
_TOOL_STYLE_MARKERS = ("read_file", "run_shell_command", "fetch_url", "search_web", "agent_browser")


_FAMILY_DIRECTIVES = {
    "shell": (
        "shell: answer must include the exact interpreter command "
        "(e.g. `python3 <file>.py` or `python3 -c \"...\"`) as a bash "
        "code block — not a paraphrase or 'Execution Output' alone."
    ),
    "format": "format: follow the retrospective's output-format pattern",
    "tool-use": "tool-use: prefer the retrospective's tool sequence",
}


def _detect_style_families(item: dict) -> list[str]:
    title = str(item.get("title") or "").strip()
    text = str(item.get("text") or "").strip()
    if not title and not text:
        return []
    haystack = f"{title} {text}".lower()
    families: list[str] = []
    if any(marker in haystack for marker in _SHELL_STYLE_MARKERS):
        families.append("shell")
    if any(marker in haystack for marker in _FORMAT_STYLE_MARKERS):
        families.append("format")
    if any(marker in haystack for marker in _TOOL_STYLE_MARKERS):
        families.append("tool-use")
    return families


_WORKFLOW_SECTION_RE = re.compile(
    r"##\s*(?P<header>Repeat next time|Avoid next time|Recall when)\s*\n(?P<body>.*?)(?=\n##\s|\Z)",
    re.IGNORECASE | re.DOTALL,
)
_WORKFLOW_BULLET_RE = re.compile(r"^[\s>]*[-*]\s+(?P<item>.+?)$", re.MULTILINE)


def _extract_retrospective_workflow(item: dict) -> dict[str, list[str]]:
    """Parse structured workflow sections out of a retrospective markdown body.

    Retrospective records persisted by `LearningManager.retrospect_episode`
    include `## Repeat next time`, `## Avoid next time`, and `## Recall when`
    sections when the synthesizer produces them. These are ground-truth
    workflow instructions that models should FOLLOW (repeat) or NOT DO
    (avoid) on the next similar turn.

    Returns a dict with keys `repeat`, `avoid`, `recall` each mapping to a
    list of bullet strings. Empty list if the section is absent or empty.
    """
    text = str(item.get("text") or "")
    if not text:
        return {"repeat": [], "avoid": [], "recall": []}
    out: dict[str, list[str]] = {"repeat": [], "avoid": [], "recall": []}
    for match in _WORKFLOW_SECTION_RE.finditer(text):
        header = (match.group("header") or "").lower()
        body = match.group("body") or ""
        bullets = [
            re.sub(r"\s+", " ", m.group("item")).strip()
            for m in _WORKFLOW_BULLET_RE.finditer(body)
        ]
        bullets = [b for b in bullets if b]
        if "repeat" in header:
            out["repeat"] = bullets
        elif "avoid" in header:
            out["avoid"] = bullets
        elif "recall" in header:
            out["recall"] = bullets
    return out


def _style_cue_from_retrospective(item: dict) -> str | None:
    """Extract a compact style cue from a retrospective note.

    Per-retrospective cue stays compact (`- title (style: <family>)`). The
    imperative directive per family is emitted once at the block level by
    `_append_learning_pack_blocks` so we don't inflate the prompt per
    retrospective — critical for retrospective-heavy workloads where the
    same directive would otherwise repeat.
    """
    title = str(item.get("title") or "").strip()
    text = str(item.get("text") or "").strip()
    if not title and not text:
        return None
    families = _detect_style_families(item)
    label = title or text[:120]
    label = re.sub(r"\s+", " ", label)[:140]
    if not label:
        return None
    if families:
        return f"- {label} (style: {', '.join(families)})"
    return f"- {label}"


def _append_learning_pack_blocks(parts: list[str], context: ContextPackage) -> None:
    """Canonical 6-block learning pack per PRD §12.1.

    Block order (all optional — emitted only when data is present):
      1. Hard constraints summary — deduped Do / Do not from PROMOTED records only.
      2. Workspace brief — the project_brief memory, elevated.
      3. Verification / Style conventions — extracted from retrospective records.
      4. Procedural brief — compact 1-line summaries of top learned policies.
      5. Curated skills — retained for manual/high-authority guidance.
      6. Retrieved memory + student notebook — compact form for transparency.

    CF-14 preservation: promoted-only filter is enforced at block 1 (this
    site) and at `core/agent.py::_learned_constraint_records` (the judge site).
    Both MUST stay aligned — see `tests/test_self_learn_scenarios.py`.
    """
    # Block 1 — Hard constraints (promoted only)
    hard_lines: list[str] = []
    seen_constraint: set[str] = set()
    for item in context.learned_policies:
        promotion_state = str(item.get("promotion_state") or "promoted").lower()
        if promotion_state != "promoted":
            continue
        feedback = str(item.get("feedback_excerpt") or "").strip()
        if feedback:
            key = f"tf:{feedback}"
            if key not in seen_constraint:
                seen_constraint.add(key)
                hard_lines.append(f"- Teacher correction: {feedback}")
        for rule in (item.get("prohibited_behavior") or [])[:3]:
            text = str(rule).strip()
            if not text:
                continue
            key = f"no:{text}"
            if key not in seen_constraint:
                seen_constraint.add(key)
                hard_lines.append(f"- Do not: {text}")
        for rule in (item.get("required_behavior") or [])[:3]:
            text = str(rule).strip()
            if not text:
                continue
            key = f"do:{text}"
            if key not in seen_constraint:
                seen_constraint.add(key)
                hard_lines.append(f"- Do: {text}")
    if hard_lines:
        parts.append("## Hard constraints")
        parts.append("Promoted policies — treat as hard constraints.")
        parts.extend(hard_lines[:12])

    # Block 2 — Workspace brief (elevated from retrieved memory)
    workspace_brief = None
    for item in context.memories:
        if str(item.get("kind") or "") == "project_brief":
            workspace_brief = item
            break
    if workspace_brief is not None:
        parts.append("## Workspace brief")
        parts.append(str(workspace_brief.get("text") or "")[:2000])

    # Block 3 — Verification / Style conventions (from retrospectives)
    retro_notes = [n for n in context.student_notes if str(n.get("kind") or "") == "retrospective"]
    style_cues: list[str] = []
    for item in retro_notes:
        cue = _style_cue_from_retrospective(item)
        if cue:
            style_cues.append(cue)
    if style_cues:
        parts.append("## Verification / Style conventions")
        parts.append(
            "Style guidance extracted from prior self-retrospectives. Apply "
            "unless an explicit teacher rule or hard constraint overrides."
        )
        parts.extend(style_cues[:3])
        # Structured workflow extraction (O1): when a retrospective includes
        # `## Repeat next time` / `## Avoid next time` sections in its body,
        # emit each bullet as an imperative workflow step. This surfaces the
        # actual tool-sequence the prior session used, not just a style tag.
        repeat_steps: list[str] = []
        avoid_steps: list[str] = []
        seen_repeat: set[str] = set()
        seen_avoid: set[str] = set()
        for item in retro_notes[:3]:
            workflow = _extract_retrospective_workflow(item)
            for step in workflow["repeat"][:4]:
                if step and step not in seen_repeat:
                    seen_repeat.add(step)
                    repeat_steps.append(step)
            for step in workflow["avoid"][:4]:
                if step and step not in seen_avoid:
                    seen_avoid.add(step)
                    avoid_steps.append(step)
        if repeat_steps:
            parts.append("Repeat the following tool-workflow steps when a similar task arises:")
            for step in repeat_steps[:6]:
                parts.append(f"  - do: {step[:240]}")
        if avoid_steps:
            parts.append("Do NOT repeat the following failure patterns:")
            for step in avoid_steps[:6]:
                parts.append(f"  - avoid: {step[:240]}")
        # Emit imperative directives ONCE per detected family across all
        # retrospectives, not per-cue.
        all_families: list[str] = []
        for item in retro_notes:
            for family in _detect_style_families(item):
                if family not in all_families:
                    all_families.append(family)
        for family in all_families:
            directive = _FAMILY_DIRECTIVES.get(family)
            if directive:
                parts.append(f"- {directive}")
        # Preserve retrospective text for model access, but tightened from the
        # pre-2.3 4000-char dump to 400 per retro (~90% reduction on long
        # retros; neutral on the short fixtures that already fit).
        for item in retro_notes[:3]:
            title = str(item.get("title") or item.get("id") or "note")
            body = str(item.get("text", ""))[:400]
            if body.strip():
                parts.append(f"### {title} [retrospective]\n{body}")

    # Block 4 — Procedural brief (compact policy summaries)
    # Candidates never produce HARD Do/Do-not lines (CF-14), but their teacher
    # correction + top behavioral cues MUST still be visible as soft guidance —
    # otherwise the model can't see the correction at all for unpromoted policies.
    if context.learned_policies:
        parts.append("## Procedural brief")
        parts.append(
            "Policies below shape this turn. Promoted items are hard constraints "
            "(see above). Candidate items are soft guidance — apply unless they "
            "conflict with promoted rules or explicit user intent."
        )
        for item in context.learned_policies[:6]:
            name = item.get("name", "policy")
            state = str(item.get("promotion_state") or "promoted")
            description = str(item.get("description") or "").strip()
            summary = description[:100] if description else "(no description)"
            parts.append(f"- {name} [{state}]: {summary}")
            feedback = str(item.get("feedback_excerpt") or "").strip()
            if feedback:
                parts.append(f"  correction: {feedback[:240]}")
            # Only surface Do/Do-not lines in the brief for CANDIDATES — promoted
            # policies already emitted them as hard constraints above. This keeps
            # the correction text visible without double-emitting hard rules.
            if state.lower() != "promoted":
                for rule in (item.get("required_behavior") or [])[:2]:
                    text = str(rule).strip()
                    if text:
                        parts.append(f"  suggest: {text[:160]}")
                for rule in (item.get("prohibited_behavior") or [])[:2]:
                    text = str(rule).strip()
                    if text:
                        parts.append(f"  avoid: {text[:160]}")

    # Block 5 — Curated skills (retained in compact form)
    if context.skills:
        parts.append("## Curated skills")
        for item in context.skills[:4]:
            name = item.get("name", "skill")
            description = str(item.get("description") or "").strip()
            summary = description[:140] if description else "(no description)"
            parts.append(f"- **{name}**: {summary}")

    # Block 6 — Retrieved memory (non-brief) + student notebook (non-retrospective)
    other_memories = [
        m for m in context.memories if str(m.get("kind") or "") != "project_brief"
    ]
    if other_memories:
        parts.append("## Retrieved memory")
        for item in other_memories[:4]:
            parts.append(f"### {item['name']} ({item['scope']})\n{str(item.get('text') or '')[:1200]}")
    non_retro_notes = [
        n for n in context.student_notes if str(n.get("kind") or "") != "retrospective"
    ]
    if non_retro_notes:
        parts.append("## Student notebook")
        for item in non_retro_notes[:4]:
            header = f"### {item.get('title', item.get('id', 'note'))} [{item.get('kind', 'note')}]"
            parts.append(header)
            parts.append(str(item.get("text", ""))[:1600])


def _append_framing_blocks(parts: list[str], context: ContextPackage) -> None:
    """Non-learning framing blocks. These live outside the learning pack and
    appear regardless of whether any learned policies/memories/retrospectives
    were retrieved."""
    if context.workspace_focus:
        parts.append("## Workspace focus")
        parts.append(context.workspace_focus.get("text", ""))
    if context.thread_summary:
        parts.append("## Active task thread")
        parts.append(context.thread_summary.get("text", ""))
        unresolved = context.thread_summary.get("unresolved_questions") or []
        if unresolved:
            parts.append("Unresolved questions: " + "; ".join(str(item) for item in unresolved[:6]))
        recent_tools = context.thread_summary.get("recent_tools") or []
        if recent_tools:
            parts.append("Recent tools: " + ", ".join(str(item) for item in recent_tools[:8]))
    if context.evidence_summary:
        parts.append("## Evidence summary")
        for claim in context.evidence_summary.get("claims", [])[:10]:
            parts.append(
                f"- [{claim.get('provenance_type', 'unknown')}] {claim.get('text', '')}"
            )
        artifacts = context.evidence_summary.get("artifacts") or []
        if artifacts:
            parts.append("Artifacts in scope: " + ", ".join(str(item.get("ref")) for item in artifacts[:8]))
    if context.contradictions:
        parts.append("## Contradictions")
        for item in context.contradictions[:6]:
            parts.append(f"- disputed: {item.get('text', '')}")
    if context.answer_target:
        parts.append("## Answer contract")
        target = context.answer_target
        parts.append(f"Current question: {target.get('current_question', '')}")
        if target.get("missing_evidence"):
            parts.append("Missing evidence: " + "; ".join(str(item) for item in target.get("missing_evidence", [])[:6]))
        if target.get("uncertainty_required"):
            parts.append("If the answer depends on missing support, say so explicitly instead of sounding certain.")
        if target.get("do_not_repeat_context"):
            parts.append("Delta-answering required: answer the current ask directly and do not replay prior setup unless strictly necessary.")
    if context.student_profile:
        parts.append("## Student profile")
        parts.append(str(context.student_profile.get("text", ""))[:4000])
    if context.handoffs:
        parts.append("## Project handoff")
        for item in context.handoffs:
            parts.append(
                f"### {item.get('session_id', 'session')} [{item.get('verification', 'unknown')}] @ {item.get('execution_cwd', '.')}\n{item.get('text', '')}"
            )
    if context.instructions:
        parts.append("## Project instructions")
        for item in context.instructions:
            parts.append(f"### {item['path']}\n{item['text']}")
    if context.tool_families:
        parts.append("## Tool exposure")
        parts.append("All tools are available. Prioritize these families first when relevant: " + ", ".join(context.tool_families))


def _append_context_blocks(parts: list[str], context: ContextPackage) -> None:
    """Compose framing blocks + canonical 6-block learning pack."""
    _append_framing_blocks(parts, context)
    _append_learning_pack_blocks(parts, context)


def _append_context_blocks_legacy(parts: list[str], context: ContextPackage) -> None:
    if context.workspace_focus:
        parts.append("## Workspace focus")
        parts.append(context.workspace_focus.get("text", ""))
    if context.thread_summary:
        parts.append("## Active task thread")
        parts.append(context.thread_summary.get("text", ""))
        unresolved = context.thread_summary.get("unresolved_questions") or []
        if unresolved:
            parts.append("Unresolved questions: " + "; ".join(str(item) for item in unresolved[:6]))
        recent_tools = context.thread_summary.get("recent_tools") or []
        if recent_tools:
            parts.append("Recent tools: " + ", ".join(str(item) for item in recent_tools[:8]))
    if context.evidence_summary:
        parts.append("## Evidence summary")
        for claim in context.evidence_summary.get("claims", [])[:10]:
            parts.append(
                f"- [{claim.get('provenance_type', 'unknown')}] {claim.get('text', '')}"
            )
        artifacts = context.evidence_summary.get("artifacts") or []
        if artifacts:
            parts.append("Artifacts in scope: " + ", ".join(str(item.get("ref")) for item in artifacts[:8]))
    if context.contradictions:
        parts.append("## Contradictions")
        for item in context.contradictions[:6]:
            parts.append(f"- disputed: {item.get('text', '')}")
    if context.answer_target:
        parts.append("## Answer contract")
        target = context.answer_target
        parts.append(f"Current question: {target.get('current_question', '')}")
        if target.get("missing_evidence"):
            parts.append("Missing evidence: " + "; ".join(str(item) for item in target.get("missing_evidence", [])[:6]))
        if target.get("uncertainty_required"):
            parts.append("If the answer depends on missing support, say so explicitly instead of sounding certain.")
        if target.get("do_not_repeat_context"):
            parts.append("Delta-answering required: answer the current ask directly and do not replay prior setup unless strictly necessary.")
    if context.student_profile:
        parts.append("## Student profile")
        parts.append(str(context.student_profile.get("text", ""))[:4000])
    if context.student_notes:
        parts.append("## Student notebook")
        if any(str(item.get("kind") or "") == "retrospective" for item in context.student_notes):
            parts.append(
                "Self retrospectives are Rocky's own compact lessons from earlier episodes. Use them as soft conventions for similar work, but let explicit teacher feedback and learned policies override them when they conflict."
            )
        for item in context.student_notes[:6]:
            header = f"### {item.get('title', item.get('id', 'note'))} [{item.get('kind', 'note')}]"
            parts.append(header)
            parts.append(str(item.get("text", ""))[:4000])
    if context.handoffs:
        parts.append("## Project handoff")
        for item in context.handoffs:
            parts.append(
                f"### {item.get('session_id', 'session')} [{item.get('verification', 'unknown')}] @ {item.get('execution_cwd', '.')}\n{item.get('text', '')}"
            )
    if context.instructions:
        parts.append("## Project instructions")
        for item in context.instructions:
            parts.append(f"### {item['path']}\n{item['text']}")
    if context.memories:
        parts.append("## Retrieved memory")
        for item in context.memories:
            parts.append(f"### {item['name']} ({item['scope']})\n{item['text']}")
    if context.learned_policies:
        parts.append(
            "Retrieved learned policies are corrective memories from earlier feedback in this workspace. When a learned policy applies, follow it before generic heuristics. Treat explicit prohibitions in promoted learned policies as hard constraints for this answer; candidate policies are visible for transparency but do not act as hard constraints until they have been promoted."
        )
        learned_constraints: list[str] = []
        for item in context.learned_policies:
            promotion_state = str(item.get("promotion_state") or "promoted").lower()
            if promotion_state != "promoted":
                continue
            feedback = str(item.get("feedback_excerpt") or "").strip()
            if feedback:
                learned_constraints.append(f"- Teacher correction: {feedback}")
            for rule in (item.get("prohibited_behavior") or [])[:3]:
                text = str(rule).strip()
                if text:
                    learned_constraints.append(f"- Do not: {text}")
            for rule in (item.get("required_behavior") or [])[:3]:
                text = str(rule).strip()
                if text:
                    learned_constraints.append(f"- Do: {text}")
        if learned_constraints:
            parts.append("## Learned constraints")
            parts.extend(learned_constraints[:12])
        parts.append("## Learned policies")
        for item in context.learned_policies:
            parts.append(
                f"### {item['name']} [{item['scope']} origin={item.get('origin', 'learned')} gen={item['generation']} state={item.get('promotion_state', 'promoted')}]\n{item['text']}"
            )
    if context.skills:
        parts.append("## Retrieved skills")
        for item in context.skills:
            parts.append(
                f"### {item['name']} [{item['scope']} origin={item.get('origin', 'manual')} gen={item['generation']}]\n{item['text']}"
            )
    if context.tool_families:
        parts.append("## Tool exposure")
        parts.append("All tools are available. Prioritize these families first when relevant: " + ", ".join(context.tool_families))


def build_system_prompt(
    context: ContextPackage,
    mode: str,
    user_prompt: str = "",
    task_signature: str = "",
) -> str:
    parts: list[str] = [
        "You are Rocky, a CLI-first, file-first, workspace-aware, local-model-first teachable student agent.",
        "Be concise, concrete, and operational.",
        "Assume you know nothing until a fact is supported by a user statement, retrieved workspace context, or tool evidence from this turn.",
        "Your internal model memory is not evidence. You cannot determine that you know a fact by introspection alone.",
        "Observation beats narration: prefer tool-observed facts and explicit user assertions over your own inference.",
        "Do not pretend to remember earlier turns unless they are actually present in the conversation context. If asked about previous questions or messages and they are not available, say that clearly.",
        "If tools are exposed and relevant, use them directly instead of self-censoring over imagined permission limits.",
        "Unless the user explicitly asked for an external path, keep created, copied, edited, and verified files inside the current workspace. Prefer relative workspace paths and never invent placeholder roots like `/workspace`.",
        "The active execution directory is the default project focus. Favor it for shell commands, reads, and new files unless the user asks for another exact path.",
        "Project handoff summaries come from earlier sessions in the same workspace; use them to continue work, but re-check machine facts with tools before claiming them.",
        "If the user asks to continue, resume, pick up, or keep working in this workspace, start from any retrieved handoff, student note, pattern, or learned policy before doing broad exploration. Treat those paths and constraints as the default working context until live tool results prove otherwise.",
        "Student notes are durable teacher feedback. Reuse them aggressively when they match the task, but verify environment facts live instead of assuming they still hold.",
        "When newer student feedback or learned policies conflict with older project instructions or fuzzy heuristics, prefer the newer corrective guidance.",
        "Treat explicit 'Do not...' rules from retrieved student notes and learned policies as hard constraints for the current answer, not soft suggestions.",
        "If a retrieved learned rule excludes a candidate, claim, file, or action from the current deliverable, omit it from the deliverable instead of keeping it with a warning label.",
        "Unsupported deterministic claims are forbidden. If support is missing, gather evidence or state the uncertainty explicitly.",
    ]
    if context.tool_families:
        parts.append("When relevant tools are exposed, prefer executing the work over describing how you would do it.")
        parts.append("For factual, comparative, or state-of-the-world questions, do not answer from parametric memory when tools could check the answer. Search, read, inspect, or execute first, then answer from the observed evidence.")
        parts.append("For multi-step tasks, decompose the request into enough tool calls to gather evidence for every requested claim. After each tool result, decide whether another tool is needed before answering.")
        parts.append("If you still lack evidence after the available tool steps, explicitly say you cannot determine the answer from evidence yet instead of guessing.")
    if any(family in context.tool_families for family in ("filesystem", "git")):
        parts.append("For repo, file, or git questions, inspect the workspace first with `read_file` or `run_shell_command` before answering.")
        parts.append("Never fabricate file contents, code snippets, line numbers, or command output. Only quote exact code or output that came from tool results in this turn. If you did not read exact lines, summarize without a code block.")
        parts.append("For repo lookup and code discovery work, do not stop after directory listings or grep-style shell output alone. After discovery, read the most likely file before claiming the answer.")
    if "shell" in context.tool_families:
        parts.append("If the user asks to run or execute a command, or provides a fenced bash/sh/zsh block, the first tool call should be `run_shell_command`. Never echo a command as if it were executed.")
        parts.append("If the user explicitly asks you to use the CLI, terminal, command line, or shell to get an exact current fact, use `run_shell_command` rather than answering from model knowledge.")
        parts.append("When calling shell, omit `cwd` unless you need a specific workspace subdirectory, and keep commands inside the workspace instead of using `/tmp` or a fake root.")
        parts.append("If the user asks about shell history, current shell, current directory, user identity, home directory, environment facts, installed software, versions, or executable paths, inspect them with `run_shell_command` first. Use concrete commands such as `echo $SHELL`, `pwd`, `whoami`, `command -v`, `which -a`, or `--version` instead of answering from memory.")
        parts.append("For shell execution tasks with follow-up analysis, do not collapse that into one tool call. Run the command, inspect the output, and when the output becomes a produced file or structured result, verify it with another tool step.")
        parts.append("If the prompt references an existing workspace script such as `x.sh`, execute that workspace file directly. If execution returns permission denied, retry with an interpreter. If the script returns structured text such as JSON, parse or inspect that observed result before deciding. The current command output from this turn is the source of truth. Do not substitute previous traces, memories, or handoff summaries for fresh command output. Student notes may guide strategy, but they do not replace current tool results. If the tool output includes an auth, permission, network, or other error payload, acknowledge the failure instead of guessing.")
        parts.append("Do not create planning files, setup scripts, or placeholder outputs unless the user explicitly asked for them. If the user did not ask for a result file, do not create one.")
    if task_signature == "local/runtime_inspection":
        parts.append("For local runtime or installed-software questions, start with `run_shell_command` and inspect the exact local executables or versions directly before answering version or path claims.")
    if task_signature == "repo/shell_execution":
        parts.append("For repo shell execution work, the first tool call should be `run_shell_command`. When the user asks you to create something and then inspect or verify it, do not collapse that into one tool call. If you create a workspace file and then need to inspect it, use `write_file` or `read_file` after execution. If you reference a script such as `x.sh`, execute that workspace file directly before summarizing.")
        lowered_prompt = user_prompt.lower()
        if any(term in lowered_prompt for term in ("price", "stock", "quote")) and any(
            term in lowered_prompt for term in ("today", "current", "latest")
        ):
            parts.append("For current company-price lookups, interpret the request as a stock quote unless the user explicitly asked for a product price. Prefer machine-readable CLI sources. Quote URLs that contain `?` or `&` so the shell does not misparse them. If multiple live sources fail because of rate limits, auth, or network errors, say you could not retrieve the live quote instead of inventing one.")
    if task_signature == "research/live_compare/general":
        if "http://" in user_prompt or "https://" in user_prompt:
            parts.append("For live research tasks where the user already gave a concrete URL, start with `fetch_url` on that exact URL before searching elsewhere. Use `agent_browser` only if the fetched page still leaves missing evidence because the content requires rendering or interaction.")
        else:
            parts.append("For live research tasks, start by discovering sources with `search_web` unless the user already gave a concrete URL. Then open at least one source with `fetch_url` or `agent_browser` before concluding.")
        parts.append("If the user asks for people, members, leaders, biographies, ownership, or role relationships, verify each requested claim from live source content instead of answering from memory or from search-result titles alone.")
        if prompt_requests_list_output(user_prompt):
            minimum_items = requested_minimum_list_items(user_prompt)
            if minimum_items > 0:
                parts.append(
                    f"For counted live-research lists, do not stop at search hits alone. Open a listing page with `fetch_url` or `agent_browser`, inspect observed items from the page, and keep gathering evidence until you can ground at least {minimum_items} items or explicitly say you could not verify enough."
                )
            else:
                parts.append(
                    "For live-research lists, do not stop at search hits alone. Open a listing page with `fetch_url` or `agent_browser` and build the final list only from observed live items."
                )
        parts.append("When using `agent_browser`, send exactly one browser subcommand per tool call. Do not chain `open`, `wait`, `snapshot`, or other steps together in one command string.")
        parts.append("If `agent_browser` is unavailable or fails, do not try to install Playwright or browsers from the shell. Fall back to `fetch_url` and continue the research.")
        parts.append("Return a real answer, not an empty placeholder, and include source URLs or a clear Sources section.")
    if task_signature == "site/understanding/general":
        parts.append("For site-understanding work, inspect the page or site first. Use at least one retrieval step and one reading step before summarizing what you found.")
    if task_signature == "data/spreadsheet/analysis":
        parts.append("For spreadsheet analysis, inspect the named CSV/XLSX file with `run_shell_command` first, and use `read_file` only when the source is plain text and small enough to read directly. If the prompt names a file, use that exact path first instead of searching or guessing. Do not stop after a single inspection command; use at least one follow-up step before concluding.")
    if task_signature == "extract/general":
        parts.append("For extraction work, return the requested JSON directly. Do not write output files unless the user explicitly asked. Use `read_file` for quick inspection and `run_shell_command` for parsing, filtering, or structured extraction. If a file path is not explicit, discover it with shell commands such as `find`, `rg --files`, or `ls`; otherwise use that exact path first instead of searching or guessing. `read_file` is formatted for humans and may include line prefixes, so do not treat those prefixes as raw file content. Use at least two steps for extraction work. Never create or mention output files unless the user explicitly asked.")
    if task_signature == "automation/general":
        parts.append("For automation tasks, write or edit the script first, then verify it with `run_shell_command` before answering. Keep the script path inside the workspace. Do not probe the environment or run verification commands before the file exists.")
        parts.append("For create, build, automate, scaffold, or cleanup-script tasks, the first successful tool call should usually be `write_file`. If you truly need discovery first, do at most one lightweight inspection, then use `write_file` immediately after.")
        parts.append("Do not use shell redirection, heredocs, `tee`, or inline interpreter one-liners as a substitute for `write_file` when creating the project's script or source files.")
        parts.append("When reporting a verified automation or mini-project result, mention the exact script or command you ran and the exact observed output, not just a paraphrase. Use at least three successful tool steps: `write_file`, reread it with `read_file`, and verify with `run_shell_command`. Once the main script exists, verify within your first five successful tool calls unless the user explicitly asked for multiple files before verification.")
    if not context.tool_families:
        parts.append("Never imply that you executed commands, read files, or browsed the web unless a tool actually did it.")
    _append_context_blocks(parts, context)
    if user_prompt:
        parts.append("## Current task")
        parts.append(user_prompt[:2000])
    parts.append("When doing live-source or browsing work, cite URLs or clearly name the sources used.")
    return "\n\n".join(parts)


def build_system_prompt_legacy(
    context: ContextPackage,
    mode: str,
    user_prompt: str = "",
    task_signature: str = "",
) -> str:
    """Pre-Phase-2.3 packer. Preserved for benchmark comparison (T8).

    This builds the same prompt as `build_system_prompt` but with the
    pre-canonical `_append_context_blocks_legacy` body — verbose
    `## Learned policies` dumps, 4000-char retrospective bodies, no
    style extraction. Used only by `tests/test_context_budget_benchmark.py`.
    """
    # Capture the system prompt using the legacy assembly so char counts are
    # measurable before/after without disturbing production callers.
    full = build_system_prompt(context, mode, user_prompt, task_signature)
    # Rebuild only the variable learning-pack region via the legacy path.
    legacy_parts: list[str] = []
    _append_context_blocks_legacy(legacy_parts, context)
    canonical_parts: list[str] = []
    _append_framing_blocks(canonical_parts, context)
    _append_learning_pack_blocks(canonical_parts, context)
    if canonical_parts and legacy_parts:
        return full.replace("\n\n".join(canonical_parts), "\n\n".join(legacy_parts))
    return full
