from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from rocky.tools.base import ToolResult
from rocky.util.text import safe_json, truncate


MODEL_TEXT_LIMIT = 2200
MODEL_TEXT_TOTAL_LIMIT = 9000
RAW_TEXT_INLINE_LIMIT = 4000
RAW_PREVIEW_LIMIT = 1000
DEFAULT_FACT_LIMIT = 8
WEB_FACT_LIMIT = 14
PATH_RE = re.compile(r"(?<![A-Za-z0-9])(?:\.{1,2}/)?(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+")
GENERIC_WEB_PATH_SEGMENTS = {
    "",
    "about",
    "blog",
    "collection",
    "collections",
    "dataset",
    "datasets",
    "doc",
    "docs",
    "documentation",
    "explore",
    "library",
    "libraries",
    "model",
    "models",
    "search",
    "space",
    "spaces",
    "tag",
    "tags",
}


def _payload_from_output(output: Any) -> tuple[dict[str, Any], str]:
    if isinstance(output, ToolResult):
        payload = output.as_payload()
        return payload, safe_json(payload)
    if isinstance(output, dict):
        return dict(output), safe_json(output)
    if isinstance(output, str):
        raw_text = output
        try:
            payload = json.loads(output)
        except Exception:
            payload = {}
        return payload if isinstance(payload, dict) else {}, raw_text
    raw_text = safe_json(output)
    try:
        payload = json.loads(raw_text)
    except Exception:
        payload = {}
    return payload if isinstance(payload, dict) else {}, raw_text


def _clean_path(value: Any) -> str:
    path = str(value or "").strip()
    return path.strip(".,:;()[]{}<>`\"'")


def _artifact(kind: str, ref: Any, *, source: str = "") -> dict[str, Any] | None:
    normalized = _clean_path(ref)
    if not normalized:
        return None
    return {"kind": kind, "ref": normalized, "source": source}


def _fact(kind: str, text: str, **extra: Any) -> dict[str, Any]:
    return {"kind": kind, "text": text, **extra}


def _informative_lines(text: str, *, limit: int = 4) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    for raw_line in str(text or "").splitlines():
        normalized = " ".join(raw_line.strip().split())
        if not normalized or normalized in seen:
            continue
        if len(normalized) < 2:
            continue
        seen.add(normalized)
        lines.append(normalized[:240])
        if len(lines) >= limit:
            break
    return lines


def _scalar_preview(data: dict[str, Any], *, limit: int = 4) -> list[str]:
    items: list[str] = []
    for key, value in data.items():
        if value in (None, "", [], {}, ()):
            continue
        if isinstance(value, (str, int, float, bool)):
            items.append(f"{key}: {value}")
        if len(items) >= limit:
            break
    return items


def _append_path_artifacts(target: list[dict[str, Any]], text: str, *, source: str) -> None:
    seen = {(item.get("kind"), item.get("ref")) for item in target}
    for match in PATH_RE.findall(text or ""):
        artifact = _artifact("path", match, source=source)
        if artifact is None:
            continue
        key = (artifact["kind"], artifact["ref"])
        if key in seen:
            continue
        target.append(artifact)
        seen.add(key)
        if len(target) >= DEFAULT_FACT_LIMIT:
            break


def _rank_web_item(item: dict[str, Any], *, index: int) -> tuple[int, int]:
    url = str(item.get("url") or "").strip()
    title = str(item.get("title") or item.get("text") or "").strip()
    parsed = urlparse(url) if url else None
    parts = [part for part in (parsed.path.split("/") if parsed else []) if part]
    lowered_parts = [part.lower() for part in parts]
    score = 0
    if url:
        score += 1
    if (
        len(parts) >= 2
        and parsed is not None
        and not parsed.query
        and all(part not in GENERIC_WEB_PATH_SEGMENTS for part in lowered_parts[:2])
    ):
        score += 8
    if parsed is not None and parsed.query:
        score -= 2
        if len(parts) <= 1 or (lowered_parts and lowered_parts[0] in GENERIC_WEB_PATH_SEGMENTS):
            score -= 6
    if lowered_parts and lowered_parts[0] in GENERIC_WEB_PATH_SEGMENTS:
        score -= 4
    if "search" in lowered_parts[:2]:
        score -= 5
    if "/" in title and len(title.split()) <= 10:
        score += 3
    if title:
        score += 1
    return (-score, index)


def _summarize_shell_like(
    *,
    name: str,
    arguments: dict[str, Any],
    data: dict[str, Any],
    summary_text: str,
    facts: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> None:
    command = str(data.get("command") or arguments.get("command") or "").strip()
    if command:
        facts.append(_fact("command", f"Command: {command}", command=command))
        if artifact := _artifact("command", command, source=name):
            artifacts.append(artifact)
    returncode = data.get("returncode")
    if returncode is not None:
        facts.append(_fact("returncode", f"Exit code: {returncode}", returncode=returncode))
    for line in _informative_lines(str(data.get("stdout") or ""), limit=3):
        facts.append(_fact("stdout", f"Stdout: {line}", stdout=line))
    for line in _informative_lines(str(data.get("stderr") or ""), limit=2):
        facts.append(_fact("stderr", f"Stderr: {line}", stderr=line))
    cwd = str(data.get("cwd") or "").strip()
    if cwd:
        facts.append(_fact("cwd", f"Cwd: {cwd}", cwd=cwd))
    script_path = str(data.get("script_path") or "").strip()
    if script_path and (artifact := _artifact("path", script_path, source=name)):
        artifacts.append(artifact)
    if summary_text and not facts:
        facts.append(_fact("summary", summary_text))


def _summarize_agent_browser(
    *,
    name: str,
    arguments: dict[str, Any],
    data: dict[str, Any],
    summary_text: str,
    facts: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> None:
    _summarize_shell_like(
        name=name,
        arguments=arguments,
        data=data,
        summary_text=summary_text,
        facts=facts,
        artifacts=artifacts,
    )
    url = str(data.get("url") or "").strip()
    if url:
        facts.append(_fact("url", f"URL: {url}", url=url))
        if artifact := _artifact("url", url, source=name):
            artifacts.append(artifact)
    title = str(data.get("title") or "").strip()
    if title:
        facts.append(_fact("title", f"Title: {title}", title=title))
    error = str(data.get("error") or "").strip()
    if error:
        facts.append(_fact("error", f"Error: {error.splitlines()[0][:220]}", error=truncate(error, 800)))
    for line in _informative_lines(str(data.get("snapshot") or ""), limit=4):
        facts.append(_fact("snapshot", f"Snapshot: {line}", snapshot=line))
    for item in list(data.get("items") or [])[:8]:
        if not isinstance(item, dict):
            continue
        item_name = str(item.get("name") or "").strip()
        item_role = str(item.get("role") or "").strip()
        item_ref = str(item.get("ref") or "").strip()
        label = item_name or item_ref
        if item_role:
            label = f"{label} [{item_role}]"
        if label:
            facts.append(_fact("item", f"Item: {label[:220]}", ref=item_ref, role=item_role))


def _summarize_file_read(
    *,
    name: str,
    arguments: dict[str, Any],
    data: Any,
    metadata: dict[str, Any],
    facts: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> None:
    path = str(metadata.get("path") or arguments.get("path") or "").strip()
    if path and (artifact := _artifact("path", path, source=name)):
        artifacts.append(artifact)
    line_count = metadata.get("line_count")
    if line_count:
        facts.append(_fact("line_count", f"Line count: {line_count}", line_count=line_count))
    if isinstance(data, str):
        for line in _informative_lines(data, limit=4):
            facts.append(_fact("content_line", line, path=path))


def _summarize_write_like(
    *,
    name: str,
    arguments: dict[str, Any],
    data: dict[str, Any],
    facts: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> None:
    for key in ("path", "src", "dst"):
        value = str(data.get(key) or arguments.get(key) or "").strip()
        if value and (artifact := _artifact("path", value, source=name)):
            artifacts.append(artifact)
    if replacements := data.get("replacements"):
        facts.append(_fact("replacements", f"Replacements: {replacements}", replacements=replacements))


def _summarize_path_list(
    *,
    name: str,
    data: list[Any],
    facts: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> None:
    for item in data[:6]:
        value = str(item or "").strip()
        if not value:
            continue
        facts.append(_fact("path", value, path=value))
        if artifact := _artifact("path", value, source=name):
            artifacts.append(artifact)


def _summarize_grep(
    *,
    name: str,
    data: list[Any],
    facts: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> None:
    for hit in data[:6]:
        if not isinstance(hit, dict):
            continue
        path = str(hit.get("path") or "").strip()
        line = hit.get("line")
        text = str(hit.get("text") or "").strip()
        label = f"{path}:{line}: {text}".strip(": ")
        if label:
            facts.append(_fact("match", label, path=path, line=line))
        if path and (artifact := _artifact("path", path, source=name)):
            artifacts.append(artifact)


def _summarize_web_fetch(
    *,
    name: str,
    data: dict[str, Any],
    facts: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> None:
    url = str(data.get("url") or "").strip()
    if url and (artifact := _artifact("url", url, source=name)):
        artifacts.append(artifact)
    title = str(data.get("title") or "").strip()
    if title:
        facts.append(_fact("title", f"Title: {title}", title=title))
    excerpt = str(data.get("text_excerpt") or "").strip()
    for line in _informative_lines(excerpt, limit=3):
        facts.append(_fact("excerpt", f"Excerpt: {line}", excerpt=line))
    link_items = data.get("link_items") or []
    if isinstance(link_items, list):
        ranked_items = sorted(
            ((index, item) for index, item in enumerate(link_items) if isinstance(item, dict)),
            key=lambda pair: _rank_web_item(pair[1], index=pair[0]),
        )
        for _index, item in ranked_items[:WEB_FACT_LIMIT]:
            if not isinstance(item, dict):
                continue
            link_url = str(item.get("url") or "").strip()
            link_text = str(item.get("text") or "").strip()
            label = f"{link_text} ({link_url})" if link_text and link_url else (link_text or link_url)
            if not label:
                continue
            facts.append(_fact("link_item", f"Link item: {label[:220]}", title=link_text, url=link_url))
            if link_url and (artifact := _artifact("url", link_url, source=name)):
                artifacts.append(artifact)
        if link_items:
            return
    for link in list(data.get("links") or [])[:4]:
        normalized = str(link or "").strip()
        if not normalized:
            continue
        facts.append(_fact("link", f"Link: {normalized}", url=normalized))
        if artifact := _artifact("url", normalized, source=name):
            artifacts.append(artifact)


def _summarize_web_list(
    *,
    name: str,
    data: list[Any],
    facts: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> None:
    ranked_items = sorted(
        ((index, item) for index, item in enumerate(data) if isinstance(item, dict)),
        key=lambda pair: _rank_web_item(pair[1], index=pair[0]),
    )
    for _index, item in ranked_items[:WEB_FACT_LIMIT]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or item.get("text") or "").strip()
        snippet = str(item.get("snippet") or "").strip()
        text = title or url
        if text:
            detail = f"{text} ({url})" if url and url not in text else text
            facts.append(_fact("result", detail[:240], title=title, url=url))
        if snippet:
            facts.append(_fact("snippet", f"Snippet: {snippet[:180]}", url=url))
        if url and (artifact := _artifact("url", url, source=name)):
            artifacts.append(artifact)


def _summarize_spreadsheet(
    *,
    name: str,
    arguments: dict[str, Any],
    data: dict[str, Any],
    facts: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> None:
    path = str(data.get("path") or arguments.get("path") or "").strip()
    if path and (artifact := _artifact("path", path, source=name)):
        artifacts.append(artifact)
    headers = data.get("headers")
    if isinstance(headers, list) and headers:
        facts.append(_fact("headers", "Headers: " + ", ".join(str(item) for item in headers[:8]), path=path))
    if rows := data.get("rows"):
        if isinstance(rows, int):
            facts.append(_fact("row_count", f"Rows: {rows}", path=path, rows=rows))
    sample_rows = data.get("sample_rows")
    if isinstance(sample_rows, list):
        for row in sample_rows[:3]:
            if isinstance(row, list):
                facts.append(_fact("sample_row", "Sample row: " + ", ".join(str(item) for item in row[:8]), path=path))
    sheets = data.get("sheets")
    if isinstance(sheets, list):
        facts.append(_fact("sheet_count", f"Sheets: {len(sheets)}", path=path, count=len(sheets)))
        for sheet in sheets[:3]:
            if not isinstance(sheet, dict):
                continue
            sheet_name = str(sheet.get("name") or "").strip()
            headers = sheet.get("headers") or []
            description = f"Sheet {sheet_name}: {sheet.get('rows', 0)} rows, {sheet.get('columns', 0)} columns"
            if headers:
                description += f", headers {', '.join(str(item) for item in headers[:6])}"
            facts.append(_fact("sheet", description, path=path, sheet=sheet_name))
            for row in (sheet.get("sample_rows") or [])[:2]:
                if isinstance(row, list):
                    facts.append(_fact("sheet_row", f"Sheet {sheet_name} sample: " + ", ".join(str(item) for item in row[:8]), path=path, sheet=sheet_name))


def _summarize_scalar_dict(
    *,
    name: str,
    data: dict[str, Any],
    facts: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> None:
    for item in _scalar_preview(data):
        facts.append(_fact("scalar", item))
    for value in data.values():
        if isinstance(value, str):
            _append_path_artifacts(artifacts, value, source=name)


def derive_tool_event_details(
    name: str,
    arguments: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    summary_text = str(payload.get("summary") or "").strip()
    success = bool(payload.get("success", True))
    data = payload.get("data")
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    facts: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []

    error_code = str(metadata.get("error") or "").strip()
    if error_code:
        facts.append(_fact("error_code", f"Guard: {error_code}", error_code=error_code))

    if name == "agent_browser" and isinstance(data, dict):
        _summarize_agent_browser(
            name=name,
            arguments=arguments,
            data=data,
            summary_text=summary_text,
            facts=facts,
            artifacts=artifacts,
        )
    elif name in {"run_shell_command", "run_python"} and isinstance(data, dict):
        _summarize_shell_like(
            name=name,
            arguments=arguments,
            data=data,
            summary_text=summary_text,
            facts=facts,
            artifacts=artifacts,
        )
    elif name == "read_file":
        _summarize_file_read(
            name=name,
            arguments=arguments,
            data=data,
            metadata=metadata,
            facts=facts,
            artifacts=artifacts,
        )
    elif name in {"write_file", "replace_in_file", "move_path", "copy_path", "delete_path", "stat_path"} and isinstance(data, dict):
        _summarize_write_like(name=name, arguments=arguments, data=data, facts=facts, artifacts=artifacts)
        if name == "stat_path" and isinstance(data.get("exists"), bool):
            facts.append(_fact("exists", f"Exists: {data.get('exists')}", exists=data.get("exists")))
    elif name in {"list_files", "glob_paths"} and isinstance(data, list):
        _summarize_path_list(name=name, data=data, facts=facts, artifacts=artifacts)
    elif name == "grep_files" and isinstance(data, list):
        _summarize_grep(name=name, data=data, facts=facts, artifacts=artifacts)
    elif name == "fetch_url" and isinstance(data, dict):
        _summarize_web_fetch(name=name, data=data, facts=facts, artifacts=artifacts)
    elif name in {"search_web", "extract_links"} and isinstance(data, list):
        _summarize_web_list(name=name, data=data, facts=facts, artifacts=artifacts)
    elif name in {"inspect_spreadsheet", "read_sheet_range"} and isinstance(data, dict):
        _summarize_spreadsheet(name=name, arguments=arguments, data=data, facts=facts, artifacts=artifacts)
    elif isinstance(data, dict):
        _summarize_scalar_dict(name=name, data=data, facts=facts, artifacts=artifacts)
    elif isinstance(data, list):
        preview = [str(item) for item in data[:4] if item not in (None, "", [], {})]
        for item in preview:
            facts.append(_fact("item", item[:240]))
    elif isinstance(data, str):
        for line in _informative_lines(data, limit=4):
            facts.append(_fact("text", line))
    if not summary_text:
        if facts:
            summary_text = str(facts[0].get("text") or "").strip()
        else:
            status = "succeeded" if success else "failed"
            summary_text = f"{name} {status}"
    fact_limit = WEB_FACT_LIMIT if name in {"fetch_url", "search_web", "extract_links", "agent_browser"} else DEFAULT_FACT_LIMIT
    return summary_text, facts[:fact_limit], artifacts[:fact_limit]


def build_model_text(
    *,
    name: str,
    summary_text: str,
    facts: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    success: bool,
    limit: int = MODEL_TEXT_LIMIT,
) -> str:
    lines = [summary_text.strip() or f"{name} {'succeeded' if success else 'failed'}"]
    fact_lines = [str(item.get("text") or "").strip() for item in facts if str(item.get("text") or "").strip()]
    if fact_lines:
        fact_line_limit = 12 if name in {"fetch_url", "search_web", "extract_links", "agent_browser"} else 6
        lines.extend(fact_lines[:fact_line_limit])
    if not fact_lines and artifacts:
        refs = [str(item.get("ref") or "").strip() for item in artifacts if str(item.get("ref") or "").strip()]
        if refs:
            lines.append("Artifacts: " + ", ".join(refs[:4]))
    text = "\n".join(lines)
    return truncate(text, limit)


def normalize_tool_result_event(
    name: str,
    arguments: dict[str, Any],
    output: Any,
    *,
    tool_call_id: str | None = None,
) -> dict[str, Any]:
    payload, raw_text = _payload_from_output(output)
    success = bool(payload.get("success", True)) if payload else '"success": false' not in raw_text.lower()
    summary_text, facts, artifacts = derive_tool_event_details(name, arguments, payload)
    model_text = build_model_text(
        name=name,
        summary_text=summary_text,
        facts=facts,
        artifacts=artifacts,
        success=success,
    )
    event = {
        "type": "tool_result",
        "id": tool_call_id or "",
        "tool_call_id": tool_call_id or "",
        "name": name,
        "arguments": arguments,
        "success": success,
        "summary_text": summary_text,
        "model_text": model_text,
        "facts": facts,
        "artifacts": artifacts,
        "raw_text": raw_text,
        "raw_ref": "",
        "text": model_text,
    }
    return event


def ensure_tool_result_event(event: dict[str, Any]) -> dict[str, Any]:
    if event.get("type") != "tool_result":
        return event
    has_new_fields = any(key in event for key in ("summary_text", "model_text", "facts", "artifacts", "raw_text", "raw_ref"))
    if has_new_fields:
        normalized = dict(event)
        normalized.setdefault("summary_text", str(normalized.get("text") or "").strip())
        normalized.setdefault("model_text", str(normalized.get("text") or "").strip())
        normalized.setdefault("facts", [])
        normalized.setdefault("artifacts", [])
        normalized.setdefault("raw_text", "")
        normalized.setdefault("raw_ref", "")
        normalized["text"] = str(normalized.get("text") or normalized.get("model_text") or normalized.get("summary_text") or "")
        return normalized
    name = str(event.get("name") or "tool_result").strip() or "tool_result"
    arguments = event.get("arguments") or {}
    normalized = normalize_tool_result_event(name, arguments, str(event.get("text") or ""), tool_call_id=str(event.get("tool_call_id") or event.get("id") or ""))
    normalized.update({key: value for key, value in event.items() if key not in normalized})
    if "success" in event:
        normalized["success"] = bool(event.get("success"))
    if "id" in event:
        normalized["id"] = event.get("id")
    if "tool_call_id" in event:
        normalized["tool_call_id"] = event.get("tool_call_id")
    if "name" in event:
        normalized["name"] = event.get("name")
    if "arguments" in event:
        normalized["arguments"] = event.get("arguments")
    normalized["text"] = str(normalized.get("model_text") or normalized.get("summary_text") or "")
    return normalized


def tool_event_summary_text(event: dict[str, Any]) -> str:
    normalized = ensure_tool_result_event(event)
    return str(normalized.get("summary_text") or "").strip()


def tool_event_model_text(event: dict[str, Any]) -> str:
    normalized = ensure_tool_result_event(event)
    return str(normalized.get("model_text") or normalized.get("text") or "").strip()


def tool_event_facts(event: dict[str, Any]) -> list[dict[str, Any]]:
    normalized = ensure_tool_result_event(event)
    facts = normalized.get("facts") or []
    return [dict(item) for item in facts if isinstance(item, dict)]


def tool_event_artifacts(event: dict[str, Any]) -> list[dict[str, Any]]:
    normalized = ensure_tool_result_event(event)
    artifacts = normalized.get("artifacts") or []
    return [dict(item) for item in artifacts if isinstance(item, dict)]


def tool_event_raw_text(event: dict[str, Any], *, dereference_ref: bool = False) -> str:
    normalized = ensure_tool_result_event(event)
    raw_text = str(normalized.get("raw_text") or "")
    raw_ref = str(normalized.get("raw_ref") or "").strip()
    if raw_ref and dereference_ref:
        try:
            return Path(raw_ref).read_text(encoding="utf-8")
        except Exception:
            return raw_text
    return raw_text


def tool_event_payload(event: dict[str, Any], *, exact: bool = False) -> dict[str, Any]:
    raw_text = tool_event_raw_text(event, dereference_ref=exact)
    if raw_text:
        try:
            payload = json.loads(raw_text)
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass
    return {}


def tool_event_debug_text(event: dict[str, Any], *, exact: bool = False, limit: int = RAW_PREVIEW_LIMIT) -> str:
    normalized = ensure_tool_result_event(event)
    raw_text = tool_event_raw_text(normalized, dereference_ref=exact)
    if raw_text:
        if exact:
            return raw_text
        raw_ref = str(normalized.get("raw_ref") or "").strip()
        preview = truncate(raw_text, limit)
        if raw_ref:
            return f"{preview}\n\n[full raw output saved to {raw_ref}]"
        return preview
    return tool_event_model_text(normalized)


def tool_event_brief_for_prompt(event: dict[str, Any], *, exact: bool = False, limit: int = MODEL_TEXT_LIMIT) -> str:
    if exact:
        raw_text = tool_event_raw_text(event, dereference_ref=True)
        if raw_text:
            return truncate(raw_text, limit)
    return truncate(tool_event_model_text(event), limit)


def tool_event_observation_lines(event: dict[str, Any], *, exact: bool = False, limit: int = 6) -> list[str]:
    normalized = ensure_tool_result_event(event)
    facts = tool_event_facts(normalized)
    lines = [str(item.get("text") or "").strip() for item in facts if str(item.get("text") or "").strip()]
    if lines:
        return lines[:limit]
    text = tool_event_brief_for_prompt(normalized, exact=exact, limit=MODEL_TEXT_LIMIT)
    return _informative_lines(text, limit=limit)


def compact_tool_result_event(
    event: dict[str, Any],
    *,
    storage_dir: Path,
    inline_limit: int = RAW_TEXT_INLINE_LIMIT,
    preview_limit: int = RAW_PREVIEW_LIMIT,
) -> dict[str, Any]:
    normalized = ensure_tool_result_event(event)
    raw_text = str(normalized.get("raw_text") or "")
    if not raw_text or len(raw_text) <= inline_limit:
        return normalized
    storage_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(raw_text.encode("utf-8")).hexdigest()[:16]
    suffix = ".json" if raw_text.lstrip().startswith("{") or raw_text.lstrip().startswith("[") else ".txt"
    path = storage_dir / f"{normalized.get('name', 'tool')}_{digest}{suffix}"
    if not path.exists():
        path.write_text(raw_text, encoding="utf-8")
    compacted = dict(normalized)
    compacted["raw_ref"] = str(path)
    compacted["raw_text"] = truncate(raw_text, preview_limit)
    return compacted


def truncate_model_text(text: str, remaining: int) -> str:
    if remaining <= 0:
        return ""
    return truncate(text, remaining)
