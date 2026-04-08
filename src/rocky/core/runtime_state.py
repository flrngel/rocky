from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from rocky.util.text import tokenize_keywords
from rocky.util.time import utc_iso


CLAIM_WORD_RE = re.compile(r"(?<=[.!?])\s+|\n+")
PATH_RE = re.compile(r"(?<![A-Za-z0-9])(?:\.{1,2}/)?(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+")
REFERENCE_RE = re.compile(r"\b(?:it|that|those|them|this|these|again|same|continue|resume|rerun|re-run|fix|update|improve|carry on|keep going|keep working|pick up|pick back up|next step|what next|finish it|finish this)\b", re.I)


_PROVENANCE_STRENGTH = {
    "tool_observed": 4,
    "user_asserted": 3,
    "learned_rule": 2,
    "agent_inferred": 1,
}


def _new_id(prefix: str, text: str = "") -> str:
    digest = hashlib.sha1(f"{prefix}:{text}:{utc_iso()}".encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


@dataclass(slots=True)
class Claim:
    claim_id: str
    thread_id: str
    text: str
    provenance_type: str
    provenance_source: str
    confidence: float = 0.6
    support_refs: list[str] = field(default_factory=list)
    contradiction_refs: list[str] = field(default_factory=list)
    status: str = "active"
    created_at: str = field(default_factory=utc_iso)

    @property
    def keywords(self) -> set[str]:
        return tokenize_keywords(self.text)

    @property
    def provenance_strength(self) -> int:
        return _PROVENANCE_STRENGTH.get(self.provenance_type, 0)

    def as_record(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_record(cls, payload: dict[str, Any]) -> "Claim":
        return cls(**payload)


@dataclass(slots=True)
class EvidenceGraph:
    thread_id: str
    claims: list[Claim] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    entities: list[dict[str, Any]] = field(default_factory=list)
    questions: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    corrections: list[dict[str, Any]] = field(default_factory=list)

    def add_claim(
        self,
        text: str,
        provenance_type: str,
        provenance_source: str,
        *,
        confidence: float = 0.6,
        support_refs: list[str] | None = None,
        status: str = "active",
    ) -> Claim:
        cleaned = " ".join(text.split())
        if not cleaned:
            raise ValueError("claim text is required")
        for claim in self.claims:
            if claim.text == cleaned and claim.provenance_type == provenance_type and claim.status == status:
                return claim
        claim = Claim(
            claim_id=_new_id("claim", cleaned),
            thread_id=self.thread_id,
            text=cleaned,
            provenance_type=provenance_type,
            provenance_source=provenance_source,
            confidence=confidence,
            support_refs=list(support_refs or []),
            status=status,
        )
        self.claims.append(claim)
        return claim

    def add_artifact(self, kind: str, ref: str, *, source: str = "") -> dict[str, Any]:
        normalized = ref.strip()
        if not normalized:
            return {"kind": kind, "ref": "", "source": source}
        for item in self.artifacts:
            if item.get("kind") == kind and item.get("ref") == normalized:
                return item
        record = {"artifact_id": _new_id("artifact", normalized), "kind": kind, "ref": normalized, "source": source}
        self.artifacts.append(record)
        return record

    def add_entity(self, kind: str, value: str, *, source: str = "") -> dict[str, Any]:
        normalized = value.strip()
        if not normalized:
            return {"kind": kind, "value": "", "source": source}
        for item in self.entities:
            if item.get("kind") == kind and item.get("value") == normalized:
                return item
        record = {"entity_id": _new_id("entity", normalized), "kind": kind, "value": normalized, "source": source}
        self.entities.append(record)
        return record

    def add_question(self, text: str) -> None:
        cleaned = " ".join(text.split())
        if cleaned and cleaned not in {item.get("text") for item in self.questions}:
            self.questions.append({"question_id": _new_id("question", cleaned), "text": cleaned, "created_at": utc_iso()})

    def add_decision(self, text: str, *, source: str = "") -> None:
        cleaned = " ".join(text.split())
        if cleaned and cleaned not in {item.get("text") for item in self.decisions}:
            self.decisions.append({"decision_id": _new_id("decision", cleaned), "text": cleaned, "source": source, "created_at": utc_iso()})

    def add_correction(self, text: str, *, source: str = "user") -> None:
        cleaned = " ".join(text.split())
        if cleaned and cleaned not in {item.get("text") for item in self.corrections}:
            self.corrections.append({"correction_id": _new_id("correction", cleaned), "text": cleaned, "source": source, "created_at": utc_iso()})

    def mark_contradictions(self) -> None:
        by_subject: dict[str, list[Claim]] = {}
        for claim in self.claims:
            subject = " ".join(list(claim.keywords)[:5]) or claim.text[:40]
            by_subject.setdefault(subject, []).append(claim)
        for _, claims in by_subject.items():
            if len(claims) < 2:
                continue
            claims.sort(key=lambda item: (item.provenance_strength, item.created_at), reverse=True)
            winner = claims[0]
            for loser in claims[1:]:
                if winner.text == loser.text:
                    continue
                overlap = len(winner.keywords & loser.keywords)
                if overlap < 2:
                    continue
                if winner.text.lower() != loser.text.lower() and winner.provenance_type != loser.provenance_type:
                    if loser.claim_id not in winner.contradiction_refs:
                        winner.contradiction_refs.append(loser.claim_id)
                    if winner.claim_id not in loser.contradiction_refs:
                        loser.contradiction_refs.append(winner.claim_id)
                    if loser.status == "active":
                        loser.status = "disputed"

    def supported_claims(self, *, include_statuses: set[str] | None = None) -> list[Claim]:
        statuses = include_statuses or {"active"}
        return [claim for claim in self.claims if claim.status in statuses]

    def claim_by_id(self, claim_id: str) -> Claim | None:
        for claim in self.claims:
            if claim.claim_id == claim_id:
                return claim
        return None

    def summary(self, limit: int = 8) -> dict[str, Any]:
        self.mark_contradictions()
        return {
            "thread_id": self.thread_id,
            "claim_count": len(self.claims),
            "claims": [claim.as_record() for claim in self.claims[:limit]],
            "artifacts": self.artifacts[:limit],
            "entities": self.entities[:limit],
            "questions": self.questions[:limit],
            "decisions": self.decisions[:limit],
            "corrections": self.corrections[:limit],
        }

    def as_record(self) -> dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "claims": [claim.as_record() for claim in self.claims],
            "artifacts": self.artifacts,
            "entities": self.entities,
            "questions": self.questions,
            "decisions": self.decisions,
            "corrections": self.corrections,
        }

    @classmethod
    def from_record(cls, payload: dict[str, Any]) -> "EvidenceGraph":
        graph = cls(thread_id=str(payload.get("thread_id") or _new_id("thread")))
        graph.claims = [Claim.from_record(item) for item in (payload.get("claims") or []) if isinstance(item, dict)]
        graph.artifacts = [dict(item) for item in (payload.get("artifacts") or []) if isinstance(item, dict)]
        graph.entities = [dict(item) for item in (payload.get("entities") or []) if isinstance(item, dict)]
        graph.questions = [dict(item) for item in (payload.get("questions") or []) if isinstance(item, dict)]
        graph.decisions = [dict(item) for item in (payload.get("decisions") or []) if isinstance(item, dict)]
        graph.corrections = [dict(item) for item in (payload.get("corrections") or []) if isinstance(item, dict)]
        return graph


@dataclass(slots=True)
class ActiveTaskThread:
    thread_id: str
    workspace_root: str
    execution_cwd: str
    task_family: str
    task_signature: str
    parent_thread_id: str | None = None
    route_history: list[dict[str, Any]] = field(default_factory=list)
    prompt_history: list[dict[str, Any]] = field(default_factory=list)
    tool_history: list[dict[str, Any]] = field(default_factory=list)
    artifact_refs: list[str] = field(default_factory=list)
    entity_refs: list[str] = field(default_factory=list)
    claims: list[str] = field(default_factory=list)
    unresolved_questions: list[str] = field(default_factory=list)
    answer_history: list[dict[str, Any]] = field(default_factory=list)
    verification_history: list[dict[str, Any]] = field(default_factory=list)
    memory_candidates: list[str] = field(default_factory=list)
    learning_candidates: list[str] = field(default_factory=list)
    last_active_at: str = field(default_factory=utc_iso)
    status: str = "active"
    created_at: str = field(default_factory=utc_iso)
    updated_at: str = field(default_factory=utc_iso)

    def touch(self) -> None:
        self.updated_at = utc_iso()
        self.last_active_at = self.updated_at

    def add_prompt(self, prompt: str) -> None:
        self.prompt_history.append({"at": utc_iso(), "prompt": prompt})
        self.touch()

    def add_route(self, route: dict[str, Any]) -> None:
        self.route_history.append({"at": utc_iso(), **route})
        self.touch()

    def add_tool_events(self, tool_events: list[dict[str, Any]]) -> None:
        self.tool_history.extend(tool_events)
        self.touch()

    def add_answer(self, answer: str) -> None:
        self.answer_history.append({"at": utc_iso(), "answer": answer})
        self.touch()

    def add_verification(self, verification: dict[str, Any]) -> None:
        self.verification_history.append({"at": utc_iso(), **verification})
        self.touch()
        if verification.get("status") == "pass":
            self.status = "awaiting_user"
        elif verification.get("status") == "fail":
            self.status = "needs_repair"
        else:
            self.status = "active"

    def summary_text(self) -> str:
        latest_prompt = self.prompt_history[-1]["prompt"] if self.prompt_history else ""
        latest_answer = self.answer_history[-1]["answer"] if self.answer_history else ""
        parts = [
            f"thread={self.thread_id}",
            f"task={self.task_signature}",
            f"cwd={self.execution_cwd}",
        ]
        if self.artifact_refs:
            parts.append("artifacts=" + ", ".join(self.artifact_refs[:4]))
        if self.entity_refs:
            parts.append("entities=" + ", ".join(self.entity_refs[:4]))
        if latest_prompt:
            parts.append("prompt=" + " ".join(latest_prompt.split())[:140])
        if latest_answer:
            parts.append("answer=" + " ".join(latest_answer.split())[:140])
        return " | ".join(parts)

    def as_record(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_record(cls, payload: dict[str, Any]) -> "ActiveTaskThread":
        return cls(**payload)


@dataclass(slots=True)
class AnswerContract:
    current_question: str
    target_scope: str
    relevant_claim_ids: list[str] = field(default_factory=list)
    allowed_claim_ids: list[str] = field(default_factory=list)
    forbidden_claim_ids: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    uncertainty_required: bool = False
    format_requirements: list[str] = field(default_factory=list)
    brevity_target: str = "short"
    do_not_repeat_context: bool = False

    def as_record(self) -> dict[str, Any]:
        return asdict(self)


class AnswerContractBuilder:
    def build(
        self,
        prompt: str,
        route_task_signature: str,
        thread: ActiveTaskThread | None,
        evidence_graph: EvidenceGraph,
        *,
        prior_answer: str | None = None,
    ) -> AnswerContract:
        query_words = tokenize_keywords(prompt)
        relevant: list[str] = []
        allowed: list[str] = []
        forbidden: list[str] = []
        missing: list[str] = []
        for claim in evidence_graph.claims:
            overlap = len(query_words & claim.keywords)
            if claim.status in {"superseded", "stale"}:
                forbidden.append(claim.claim_id)
                continue
            if claim.status == "disputed":
                forbidden.append(claim.claim_id)
                continue
            if overlap or claim.provenance_type in {"user_asserted", "learned_rule"}:
                relevant.append(claim.claim_id)
                if claim.provenance_type in {"tool_observed", "user_asserted", "learned_rule"}:
                    allowed.append(claim.claim_id)
                else:
                    forbidden.append(claim.claim_id)
        if route_task_signature.startswith(("repo/", "local/", "data/", "extract/", "automation/", "research/")) and not allowed:
            missing.append("No supported evidence-bearing claims were collected for this task yet.")
        if any(term in prompt.lower() for term in ("exact", "json", "yaml", "table")):
            if "json" in prompt.lower():
                requirements = ["Return valid JSON only."]
            else:
                requirements = ["Respect the requested structured format."]
        else:
            requirements = []
        short_prompt = len(prompt.split()) <= 18
        do_not_repeat = bool(thread and (short_prompt or REFERENCE_RE.search(prompt)))
        if prior_answer and len(prior_answer.split()) > 60 and short_prompt:
            do_not_repeat = True
        target_scope = route_task_signature.rsplit("/", 1)[0] if "/" in route_task_signature else route_task_signature
        return AnswerContract(
            current_question=prompt,
            target_scope=target_scope,
            relevant_claim_ids=relevant[:24],
            allowed_claim_ids=allowed[:24],
            forbidden_claim_ids=forbidden[:24],
            missing_evidence=missing[:6],
            uncertainty_required=bool(missing),
            format_requirements=requirements,
            brevity_target="tight" if short_prompt else "standard",
            do_not_repeat_context=do_not_repeat,
        )


class EvidenceAccumulator:
    USER_CORRECTION_MARKERS = (
        "actually",
        "correction",
        "instead",
        "not ",
        "must",
        "must not",
        "should",
        "do not",
        "don't",
        "keep",
        "inside",
        "prefer",
    )
    OUTPUT_LINE_LIMIT = 12

    def _informative_output_lines(self, text: str, *, max_lines: int | None = None) -> list[str]:
        limit = max_lines or self.OUTPUT_LINE_LIMIT
        lines: list[str] = []
        seen: set[str] = set()
        for raw_line in text.splitlines():
            stripped = " ".join(raw_line.strip().split())
            if not stripped:
                continue
            if len(stripped) < 5:
                continue
            if all(char in "-_=:#|*.`" for char in stripped):
                continue
            if stripped in seen:
                continue
            seen.add(stripped)
            lines.append(stripped[:240])
            if len(lines) >= limit:
                break
        return lines

    def _extract_paths(self, text: str) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for match in PATH_RE.findall(text or ""):
            cleaned = match.strip(".,:;()[]{}<>`\"'")
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            ordered.append(cleaned)
        return ordered

    def ingest_prompt(self, graph: EvidenceGraph, prompt: str) -> None:
        lowered = prompt.lower()
        if any(marker in lowered for marker in self.USER_CORRECTION_MARKERS):
            graph.add_claim(prompt.strip(), "user_asserted", "user_prompt", confidence=0.9)
            graph.add_correction(prompt.strip())
        elif len(prompt.split()) < 24:
            graph.add_question(prompt.strip())
        for path in self._extract_paths(prompt):
            graph.add_artifact("path", path, source="user_prompt")
            graph.add_claim(f"Workspace path in scope: {path}", "user_asserted", "user_prompt", confidence=0.85)
        for token in sorted(tokenize_keywords(prompt))[:8]:
            if len(token) > 3:
                graph.add_entity("keyword", token, source="user_prompt")

    def _tool_payload(self, event: dict[str, Any]) -> dict[str, Any]:
        text = str(event.get("text") or "")
        try:
            payload = json.loads(text)
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    def ingest_tool_events(self, graph: EvidenceGraph, tool_events: list[dict[str, Any]]) -> None:
        for event in tool_events:
            if event.get("type") != "tool_result":
                continue
            name = str(event.get("name") or "")
            arguments = event.get("arguments") or {}
            success = bool(event.get("success", True))
            payload = self._tool_payload(event)
            payload_data = payload.get("data")
            data = payload_data if isinstance(payload_data, dict) else {}
            if not success:
                summary = str(payload.get("summary") or "tool failed")
                graph.add_claim(f"Tool {name} failed: {summary}", "tool_observed", name, confidence=0.6, status="provisional")
                continue
            path = str(arguments.get("path") or data.get("path") or "").strip()
            if path:
                graph.add_artifact("path", path, source=name)
                graph.add_claim(f"Observed path {path} via {name}", "tool_observed", name, confidence=0.9)
            command = str(data.get("command") or arguments.get("command") or "").strip()
            if command:
                graph.add_artifact("command", command, source=name)
                returncode = data.get("returncode")
                if returncode is not None:
                    graph.add_claim(
                        f"Shell command `{command}` exited with code {returncode}",
                        "tool_observed",
                        name,
                        confidence=0.9,
                    )
                stdout = str(data.get("stdout") or "").strip()
                stderr = str(data.get("stderr") or "").strip()
                if stdout:
                    snippet = stdout.splitlines()[0][:240]
                    graph.add_claim(f"Observed stdout from `{command}`: {snippet}", "tool_observed", name, confidence=0.8)
                    for line in self._informative_output_lines(stdout, max_lines=6):
                        graph.add_claim(
                            f"Observed stdout line from `{command}`: {line}",
                            "tool_observed",
                            name,
                            confidence=0.75,
                        )
                if stderr:
                    snippet = stderr.splitlines()[0][:240]
                    graph.add_claim(f"Observed stderr from `{command}`: {snippet}", "tool_observed", name, confidence=0.75, status="provisional")
                    for line in self._informative_output_lines(stderr, max_lines=4):
                        graph.add_claim(
                            f"Observed stderr line from `{command}`: {line}",
                            "tool_observed",
                            name,
                            confidence=0.7,
                            status="provisional",
                        )
            targets = data.get("targets") or arguments.get("targets") or []
            versions = data.get("versions") or {}
            if isinstance(versions, dict):
                for target, info in list(versions.items())[:8]:
                    if isinstance(info, dict):
                        version = info.get("version") or info.get("path") or info.get("status")
                    else:
                        version = info
                    if version:
                        graph.add_entity("runtime_target", str(target), source=name)
                        graph.add_claim(f"Observed {target}: {version}", "tool_observed", name, confidence=0.9)
            if name == "inspect_runtime_versions" and isinstance(targets, list):
                for target_info in targets[:8]:
                    if not isinstance(target_info, dict):
                        normalized = str(target_info).strip()
                        if normalized:
                            graph.add_entity("runtime_target", normalized, source=name)
                        continue
                    target_name = str(target_info.get("target") or "").strip()
                    if target_name:
                        graph.add_entity("runtime_target", target_name, source=name)
                    exact_path = str(target_info.get("exact_path") or "").strip()
                    if target_name and exact_path:
                        graph.add_artifact("path", exact_path, source=name)
                        graph.add_claim(
                            f"Exact runtime path for {target_name}: {exact_path}",
                            "tool_observed",
                            name,
                            confidence=0.9,
                        )
                    matches = target_info.get("matches") or []
                    if not isinstance(matches, list):
                        continue
                    for match in matches[:8]:
                        if not isinstance(match, dict):
                            continue
                        command_name = str(match.get("command") or "").strip()
                        match_path = str(match.get("path") or "").strip()
                        match_version = str(match.get("version") or "").strip()
                        if command_name:
                            graph.add_entity("runtime_target", command_name, source=name)
                        if match_path:
                            graph.add_artifact("path", match_path, source=name)
                        details = []
                        if match_version:
                            details.append(f"version {match_version}")
                        if match_path:
                            details.append(f"path {match_path}")
                        if command_name and details:
                            graph.add_claim(
                                f"Observed runtime command {command_name}: {', '.join(details)}",
                                "tool_observed",
                                name,
                                confidence=0.9,
                            )
                        if command_name and match_path and bool(match.get("exact")):
                            graph.add_claim(
                                f"Exact runtime command {command_name} resolves to {match_path}",
                                "tool_observed",
                                name,
                                confidence=0.9,
                            )
            if isinstance(targets, list):
                for target in targets[:8]:
                    if isinstance(target, dict):
                        continue
                    graph.add_entity("runtime_target", str(target), source=name)
            if name == "write_file" and path:
                graph.add_claim(f"Created or updated file {path}", "tool_observed", name, confidence=0.9)
            elif name == "read_file" and path:
                graph.add_claim(f"Read file {path}", "tool_observed", name, confidence=0.85)
                text_body = payload.get("data")
                if isinstance(text_body, str):
                    for line in self._informative_output_lines(text_body, max_lines=10):
                        graph.add_claim(
                            f"Observed file line {path}: {line}",
                            "tool_observed",
                            name,
                            confidence=0.8,
                        )
            elif name == "stat_path" and path:
                exists = data.get("exists")
                if exists is not None:
                    graph.add_claim(f"Path {path} exists={exists}", "tool_observed", name, confidence=0.9)
            elif name == "run_python":
                stdout = str(data.get("stdout") or "").strip()
                if stdout:
                    graph.add_claim(f"Python output: {stdout[:240]}", "tool_observed", name, confidence=0.8)
                    for line in self._informative_output_lines(stdout, max_lines=8):
                        graph.add_claim(
                            f"Python output line: {line}",
                            "tool_observed",
                            name,
                            confidence=0.75,
                        )
            elif name == "inspect_spreadsheet":
                sheet_path = str(data.get("path") or arguments.get("path") or "").strip()
                if sheet_path:
                    graph.add_artifact("path", sheet_path, source=name)
                sheet_format = str(data.get("format") or "").strip()
                headers = data.get("headers")
                if sheet_path and isinstance(headers, list):
                    header_text = ", ".join(str(item) for item in headers[:8])
                    graph.add_claim(
                        f"Spreadsheet {sheet_path} format {sheet_format or 'unknown'} headers: {header_text}",
                        "tool_observed",
                        name,
                        confidence=0.9,
                    )
                sample_rows = data.get("sample_rows")
                if sheet_path and isinstance(sample_rows, list):
                    for row in sample_rows[:3]:
                        if not isinstance(row, list):
                            continue
                        row_text = ", ".join(str(item) for item in row[:8])
                        if row_text:
                            graph.add_claim(
                                f"Spreadsheet sample row {sheet_path}: {row_text}",
                                "tool_observed",
                                name,
                                confidence=0.75,
                            )
                sheets = data.get("sheets")
                if sheet_path and isinstance(sheets, list):
                    graph.add_claim(
                        f"Workbook {sheet_path} has {len(sheets)} sheet(s)",
                        "tool_observed",
                        name,
                        confidence=0.9,
                    )
                    for sheet in sheets[:6]:
                        if not isinstance(sheet, dict):
                            continue
                        sheet_name = str(sheet.get("name") or "").strip()
                        if sheet_name:
                            graph.add_entity("sheet", sheet_name, source=name)
                        sheet_headers = sheet.get("headers") or []
                        header_text = ", ".join(str(item) for item in sheet_headers[:8])
                        rows_count = sheet.get("rows")
                        columns_count = sheet.get("columns")
                        details: list[str] = []
                        if rows_count is not None:
                            details.append(f"{rows_count} rows")
                        if columns_count is not None:
                            details.append(f"{columns_count} columns")
                        if header_text:
                            details.append(f"headers {header_text}")
                        if sheet_path and sheet_name and details:
                            graph.add_claim(
                                f"Sheet {sheet_name} in {sheet_path}: {', '.join(details)}",
                                "tool_observed",
                                name,
                                confidence=0.9,
                            )
                        sample_rows = sheet.get("sample_rows") or []
                        if isinstance(sample_rows, list):
                            for row in sample_rows[:2]:
                                if not isinstance(row, list):
                                    continue
                                row_text = ", ".join(str(item) for item in row[:8])
                                if sheet_name and row_text:
                                    graph.add_claim(
                                        f"Sheet {sheet_name} sample row in {sheet_path}: {row_text}",
                                        "tool_observed",
                                        name,
                                        confidence=0.75,
                                    )
            elif name == "read_sheet_range":
                sheet_name = str(data.get("sheet") or arguments.get("sheet") or "").strip()
                rows = data.get("rows") or []
                for row in rows[:4]:
                    if not isinstance(row, list):
                        continue
                    row_text = ", ".join(str(item) for item in row[:8])
                    if row_text:
                        label = f"{sheet_name} " if sheet_name else ""
                        graph.add_claim(
                            f"Spreadsheet {label}row: {row_text}",
                            "tool_observed",
                            name,
                            confidence=0.75,
                        )
            elif name == "search_web" and isinstance(payload_data, list):
                query = str(arguments.get("query") or "").strip()
                for item in payload_data[:6]:
                    if not isinstance(item, dict):
                        continue
                    url = str(item.get("url") or "").strip()
                    title = " ".join(str(item.get("title") or "").split())[:240]
                    snippet = " ".join(str(item.get("snippet") or "").split())[:240]
                    if url:
                        graph.add_artifact("url", url, source=name)
                        graph.add_claim(f"Consulted live source {url}", "tool_observed", name, confidence=0.8)
                    if title:
                        prefix = f'Search result for "{query}": ' if query else "Search result: "
                        graph.add_claim(
                            f"{prefix}{title}",
                            "tool_observed",
                            name,
                            confidence=0.8,
                        )
                    if title and snippet:
                        graph.add_claim(
                            f'Search snippet for "{title}": {snippet}',
                            "tool_observed",
                            name,
                            confidence=0.72,
                            status="provisional",
                        )
            elif name in {"fetch_url", "browser_render_page"}:
                url = str(arguments.get("url") or data.get("url") or data.get("final_url") or "").strip()
                title = " ".join(str(data.get("title") or "").split())[:240]
                excerpt = " ".join(str(data.get("text_excerpt") or "").split())[:280]
                if url:
                    graph.add_artifact("url", url, source=name)
                    graph.add_claim(f"Consulted live source {url}", "tool_observed", name, confidence=0.8)
                if title and url:
                    graph.add_claim(
                        f'Fetched page "{title}" from {url}',
                        "tool_observed",
                        name,
                        confidence=0.82,
                    )
                elif title:
                    graph.add_claim(
                        f'Fetched page "{title}"',
                        "tool_observed",
                        name,
                        confidence=0.82,
                    )
                if excerpt:
                    label = title or url or "live source"
                    graph.add_claim(
                        f'Page excerpt from "{label}": {excerpt}',
                        "tool_observed",
                        name,
                        confidence=0.74,
                        status="provisional",
                    )
            elif name == "grep_files" and isinstance(payload.get("data"), list):
                for item in payload["data"][:10]:
                    if not isinstance(item, dict):
                        continue
                    hit_path = str(item.get("path") or "").strip()
                    hit_line = item.get("line")
                    hit_text = " ".join(str(item.get("text") or "").split())
                    if hit_path:
                        graph.add_artifact("path", hit_path, source=name)
                    if hit_path and hit_text:
                        line_label = f":{hit_line}" if hit_line is not None else ""
                        graph.add_claim(
                            f"Observed grep hit {hit_path}{line_label}: {hit_text[:240]}",
                            "tool_observed",
                            name,
                            confidence=0.8,
                        )
        graph.mark_contradictions()


class ThreadRegistry:
    SESSION_KEY = "task_threads_v1_0"
    LEGACY_SESSION_KEYS = ("task_threads_v1_0", "task_threads_v0_3")
    CURRENT_KEY = "current_thread_id_v1_0"
    LEGACY_CURRENT_KEYS = ("current_thread_id_v1_0", "current_thread_id_v0_3")
    CONTINUABLE_STATUSES = {"active", "awaiting_user", "needs_repair"}

    def __init__(self, session) -> None:
        self.session = session
        payload: dict[str, Any] = {}
        for key in self.LEGACY_SESSION_KEYS:
            candidate = session.meta.get(key)
            if isinstance(candidate, dict) and candidate:
                payload = dict(candidate)
                break
        self.threads: dict[str, ActiveTaskThread] = {}
        self.evidence: dict[str, EvidenceGraph] = {}
        for thread_id, item in payload.items():
            if not isinstance(item, dict):
                continue
            thread_payload = item.get("thread") or {}
            evidence_payload = item.get("evidence") or {"thread_id": thread_id}
            try:
                self.threads[thread_id] = ActiveTaskThread.from_record(thread_payload)
            except Exception:
                continue
            try:
                self.evidence[thread_id] = EvidenceGraph.from_record(evidence_payload)
            except Exception:
                self.evidence[thread_id] = EvidenceGraph(thread_id=thread_id)
        self.current_thread_id = None
        for key in self.LEGACY_CURRENT_KEYS:
            current = str(session.meta.get(key) or "") or None
            if current:
                self.current_thread_id = current
                break

    def save(self) -> None:
        payload: dict[str, Any] = {}
        for thread_id, thread in self.threads.items():
            payload[thread_id] = {
                "thread": thread.as_record(),
                "evidence": self.evidence.get(thread_id, EvidenceGraph(thread_id=thread_id)).as_record(),
            }
        self.session.meta[self.SESSION_KEY] = payload
        self.session.meta["task_threads_v0_3"] = payload
        if self.current_thread_id:
            self.session.meta[self.CURRENT_KEY] = self.current_thread_id
            self.session.meta["current_thread_id_v0_3"] = self.current_thread_id

    def current(self) -> ActiveTaskThread | None:
        if self.current_thread_id and self.current_thread_id in self.threads:
            thread = self.threads[self.current_thread_id]
            if thread.status in self.CONTINUABLE_STATUSES:
                return thread
        active = [thread for thread in self.threads.values() if thread.status in self.CONTINUABLE_STATUSES]
        if active:
            active.sort(key=lambda item: item.updated_at, reverse=True)
            self.current_thread_id = active[0].thread_id
            return active[0]
        return None

    def current_evidence(self) -> EvidenceGraph | None:
        current = self.current()
        if current is None:
            return None
        return self.evidence.setdefault(current.thread_id, EvidenceGraph(thread_id=current.thread_id))

    def active_threads(self) -> list[ActiveTaskThread]:
        rows = [thread for thread in self.threads.values() if thread.status in self.CONTINUABLE_STATUSES]
        rows.sort(key=lambda item: item.updated_at, reverse=True)
        return rows

    def recent_threads(self, limit: int = 6) -> list[ActiveTaskThread]:
        rows = list(self.threads.values())
        rows.sort(key=lambda item: item.updated_at, reverse=True)
        return rows[:limit]

    def thread_summary_records(self, limit: int = 6) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for thread in self.recent_threads(limit=limit):
            graph = self.evidence.get(thread.thread_id)
            rows.append(
                {
                    "thread_id": thread.thread_id,
                    "task_signature": thread.task_signature,
                    "task_family": thread.task_family,
                    "execution_cwd": thread.execution_cwd,
                    "status": thread.status,
                    "text": thread.summary_text(),
                    "artifacts": thread.artifact_refs[:6],
                    "entities": thread.entity_refs[:6],
                    "updated_at": thread.updated_at,
                    "claim_count": len(graph.claims) if graph else 0,
                }
            )
        return rows

    def start_thread(
        self,
        *,
        workspace_root: str,
        execution_cwd: str,
        task_signature: str,
        task_family: str,
        parent_thread_id: str | None = None,
    ) -> ActiveTaskThread:
        current = self.current()
        if current is not None and current.status == "active":
            current.status = "awaiting_user"
        thread_id = _new_id("thread", task_signature)
        thread = ActiveTaskThread(
            thread_id=thread_id,
            workspace_root=workspace_root,
            execution_cwd=execution_cwd,
            task_family=task_family,
            task_signature=task_signature,
            parent_thread_id=parent_thread_id,
        )
        self.threads[thread_id] = thread
        self.evidence[thread_id] = EvidenceGraph(thread_id=thread_id)
        self.current_thread_id = thread_id
        return thread

    def use_thread(self, thread_id: str) -> ActiveTaskThread | None:
        if thread_id not in self.threads:
            return None
        if self.current_thread_id and self.current_thread_id in self.threads and self.current_thread_id != thread_id:
            previous = self.threads[self.current_thread_id]
            if previous.status == "active":
                previous.status = "awaiting_user"
        self.current_thread_id = thread_id
        thread = self.threads[thread_id]
        thread.status = "active"
        thread.touch()
        return thread

    def ensure_thread(
        self,
        *,
        route_task_signature: str,
        task_family: str,
        workspace_root: str,
        execution_cwd: str,
        continued_thread_id: str | None = None,
    ) -> ActiveTaskThread:
        if continued_thread_id:
            existing = self.use_thread(continued_thread_id)
            if existing is not None:
                existing.task_signature = route_task_signature
                existing.task_family = task_family
                existing.execution_cwd = execution_cwd
                return existing
        return self.start_thread(
            workspace_root=workspace_root,
            execution_cwd=execution_cwd,
            task_signature=route_task_signature,
            task_family=task_family,
            parent_thread_id=self.current_thread_id if self.current_thread_id else None,
        )

    def snapshot(self, thread_id: str | None = None) -> dict[str, Any]:
        current_id = thread_id or self.current_thread_id
        if not current_id or current_id not in self.threads:
            return {"current_thread_id": None, "threads": []}
        return {
            "current_thread_id": current_id,
            "threads": self.thread_summary_records(limit=8),
            "current_thread": self.threads[current_id].as_record(),
            "evidence": self.evidence[current_id].summary(limit=12),
        }


def continuation_signal_score(prompt: str, thread: ActiveTaskThread, *, execution_cwd: str, workspace_root: str) -> tuple[float, list[str]]:
    lowered = prompt.lower().strip()
    prompt_tokens = tokenize_keywords(prompt)
    reasons: list[str] = []
    score = 0.0
    if thread.workspace_root == workspace_root:
        score += 2.5
        reasons.append("same_workspace")
    if thread.execution_cwd == execution_cwd:
        score += 1.5
        reasons.append("same_execution_cwd")
    if len(prompt.split()) <= 18:
        score += 1.0
        reasons.append("short_prompt")
    if REFERENCE_RE.search(lowered):
        score += 2.0
        reasons.append("reference_marker")
    if any(phrase in lowered for phrase in ("continue", "resume", "keep going", "keep working", "carry on", "pick up", "pick back up", "same task", "what next", "next step", "finish it", "finish this")):
        score += 1.4
        reasons.append("explicit_continuation_phrase")
    overlap = prompt_tokens & (set(thread.entity_refs) | set(thread.artifact_refs) | tokenize_keywords(thread.summary_text()))
    if overlap:
        score += min(3.0, 0.8 + len(overlap) * 0.6)
        reasons.append("artifact_entity_overlap")
    task_sig_tokens = tokenize_keywords(thread.task_signature.replace("/", " "))
    if prompt_tokens & task_sig_tokens:
        score += 0.8
        reasons.append("task_signature_overlap")
    if thread.unresolved_questions:
        q_tokens = tokenize_keywords(" ".join(thread.unresolved_questions))
        if q_tokens & prompt_tokens:
            score += 2.0
            reasons.append("matches_unresolved_question")
    if thread.task_signature.startswith("conversation/") and any(word in lowered for word in ("run", "execute", "inspect", "check")):
        score -= 1.0
        reasons.append("possible_family_shift")
    return score, reasons
