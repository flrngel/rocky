from __future__ import annotations

from rocky.core.context import ContextPackage
from rocky.core.runtime_state import prompt_requests_list_output, requested_minimum_list_items


def _append_context_blocks(parts: list[str], context: ContextPackage) -> None:
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
            "Retrieved learned policies are corrective memories from earlier feedback in this workspace. When a learned policy applies, follow it before generic heuristics. Treat explicit prohibitions in learned policies as hard constraints for this answer, even if the policy is still marked candidate."
        )
        learned_constraints: list[str] = []
        for item in context.learned_policies:
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
