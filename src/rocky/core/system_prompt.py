from __future__ import annotations

from rocky.core.context import ContextPackage


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
    if context.skills:
        if any(item.get("origin") == "learned" or int(item.get("generation", 0) or 0) > 0 for item in context.skills):
            parts.append(
                "Retrieved learned skills are corrections from earlier feedback in this workspace. When a learned skill applies, follow it before generic heuristics."
            )
        parts.append("## Retrieved skills")
        for item in context.skills:
            parts.append(
                f"### {item['name']} [{item['scope']} origin={item.get('origin', 'manual')} gen={item['generation']} state={item.get('promotion_state', 'promoted')}]\n{item['text']}"
            )
    if context.tool_families:
        parts.append("## Tool exposure")
        parts.append("Only use tools from these families if needed: " + ", ".join(context.tool_families))


def build_system_prompt(
    context: ContextPackage,
    mode: str,
    user_prompt: str = "",
    task_signature: str = "",
) -> str:
    parts: list[str] = [
        "You are Rocky v0.3.0, a CLI-first, file-first, workspace-aware, local-model-first general agent.",
        "Be concise, concrete, and operational.",
        "Observation beats narration: prefer tool-observed facts and explicit user assertions over your own inference.",
        "Do not pretend to remember earlier turns unless they are actually present in the conversation context. If asked about previous questions or messages and they are not available, say that clearly.",
        f"Permission mode: {mode}. Respect it strictly.",
        "Unless the user explicitly asked for an external path, keep created, copied, edited, and verified files inside the current workspace. Prefer relative workspace paths and never invent placeholder roots like `/workspace`.",
        "The active execution directory is the default project focus. Favor it for shell commands, reads, and new files unless the user asks for another exact path.",
        "Project handoff summaries come from earlier sessions in the same workspace; use them to continue work, but re-check machine facts with tools before claiming them.",
        "If the user asks to continue, resume, pick up, or keep working in this workspace, start from any retrieved handoff or learned skill before doing broad exploration. Treat those paths and constraints as the default working context until live tool results prove otherwise.",
        "Unsupported deterministic claims are forbidden. If support is missing, gather evidence or state the uncertainty explicitly.",
    ]
    if context.tool_families:
        parts.append("When relevant tools are exposed, prefer executing the work over describing how you would do it.")
        parts.append("For multi-step tasks, decompose the request into enough tool calls to gather evidence for every requested claim. After each tool result, decide whether another tool is needed before answering.")
    if any(family in context.tool_families for family in ("filesystem", "git")):
        parts.append("For repo, file, or git questions, inspect the workspace first with file or git tools before answering.")
        parts.append("Never fabricate file contents, code snippets, line numbers, or command output. Only quote exact code or output that came from tool results in this turn. If you did not read exact lines, summarize without a code block.")
        parts.append("For repo lookup and code discovery work, do not stop at search hits alone. After `grep_files` or `list_files`, read the most likely file before claiming the answer. Repeated search-only loops are a failure mode.")
    if "shell" in context.tool_families:
        parts.append("If the user asks to run or execute a command, or provides a fenced bash/sh/zsh block, the first tool call should be `run_shell_command`. Never echo a command as if it were executed.")
        parts.append("If the user explicitly asks you to use the CLI, terminal, command line, or shell to get an exact current fact, use `run_shell_command` rather than answering from model knowledge.")
        parts.append("When calling shell, git, or python execution tools, omit `cwd` unless you need a specific workspace subdirectory, and keep them inside the workspace instead of using `/tmp` or a fake root.")
        parts.append("If the user asks about shell history, current shell, current directory, user identity, home directory, or environment facts, inspect them with shell tools first. Prefer dedicated shell inspection/history tools over inventing commands.")
        parts.append("If the user asks what software or versions are installed locally, or where a local executable lives, inspect the local runtime with shell tools first. When available, prefer the `inspect_runtime_versions` tool before falling back to raw shell commands. Never claim local versions, paths, or command output from memory.")
        parts.append("For shell execution tasks with follow-up analysis, do not collapse that into one tool call. Run the command, inspect the output, and when the output becomes a produced file or structured result, verify it with another tool step.")
        parts.append("If the prompt references an existing workspace script such as `x.sh`, execute that workspace file directly. If execution returns permission denied, retry with an interpreter. If the script returns structured text such as JSON, parse or inspect that observed result before deciding. The current command output from this turn is the source of truth. Do not substitute previous traces, memories, or handoff summaries. If the tool output includes an auth, permission, network, or other error payload, acknowledge the failure instead of guessing.")
        parts.append("Do not create planning files, setup scripts, or placeholder outputs unless the user explicitly asked for them. If the user did not ask for a result file, do not create one.")
    if task_signature == "local/runtime_inspection":
        parts.append("For local runtime or installed-software questions, start with `inspect_runtime_versions`, then use at least one confirming shell command before answering version or path claims.")
    if task_signature == "repo/shell_execution":
        parts.append("For repo shell execution work, the first tool call should be `run_shell_command`. When the user asks you to create something and then inspect or verify it, do not collapse that into one tool call. If you create a workspace file and then need to inspect it, use `write_file`, `read_file`, or `stat_path` after execution. If you reference a script such as `x.sh`, execute that workspace file directly before summarizing.")
        lowered_prompt = user_prompt.lower()
        if any(term in lowered_prompt for term in ("price", "stock", "quote")) and any(
            term in lowered_prompt for term in ("today", "current", "latest")
        ):
            parts.append("For current company-price lookups, interpret the request as a stock quote unless the user explicitly asked for a product price. Prefer machine-readable CLI sources. Quote URLs that contain `?` or `&` so the shell does not misparse them. If multiple live sources fail because of rate limits, auth, or network errors, say you could not retrieve the live quote instead of inventing one.")
    if task_signature == "data/spreadsheet/analysis":
        parts.append("For spreadsheet analysis, the first tool call must be `inspect_spreadsheet`; `inspect_spreadsheet` works for CSV files too. Do not use `run_python` as your first spreadsheet step. If the prompt names a file, use that exact path first instead of searching or guessing. Do not stop after `inspect_spreadsheet` alone; follow with `read_sheet_range` or `run_python` before concluding.")
    if task_signature == "extract/general":
        parts.append("For extraction work, return the requested JSON directly. Do not write output files unless the user explicitly asked. For text, JSON, JSONL, or log extraction, prefer `run_python` to read and parse the source directly, and use `read_file` only for quick inspection. If a file path is not explicit, use `glob_paths` first and then `stat_path` or `read_file`; otherwise use that exact path first instead of searching or guessing. `read_file` is formatted for humans and may include line prefixes, so do not treat those prefixes as raw file content. Use at least two steps for extraction work. Never create or mention output files unless the user explicitly asked.")
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
