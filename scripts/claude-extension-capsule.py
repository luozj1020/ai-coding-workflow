#!/usr/bin/env python3
"""Build a bounded, privacy-limited timeout-advisor capsule.

The helper reads only the current Claude session transcript when its session
UUID is present in the transcript filename.  It never persists tool-result
payloads or chain-of-thought text.  Assistant text is treated as untrusted,
redacted, and size-bounded; tool activity is reduced to names, target hints,
and error state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Iterable


SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|secret|password|credential)\s*[:=]\s*\S+"),
    re.compile(r"\b(?:sk|gh[opusr])_[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)authorization:\s*bearer\s+\S+"),
)
CONTRACT_SECTIONS = {"goal", "scope", "handoff contract", "acceptance criteria"}
TARGET_KEYS = ("file_path", "path", "target", "notebook_path")


def _redact(value: str) -> str:
    text = " ".join(value.replace("\x00", "").split())
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def _bounded_tail(value: str, limit: int) -> str:
    value = _redact(value)
    if len(value) <= limit:
        return value
    return "…" + value[-(limit - 1) :]


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _contract_excerpt(path: Path, limit: int) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    selected: list[str] = []
    active = False
    for line in lines:
        if line.startswith("## "):
            active = line[3:].strip().lower() in CONTRACT_SECTIONS
        if active:
            selected.append(line)
    return _bounded_tail("\n".join(selected), limit)


def _candidate_transcripts(root: Path, session_id: str) -> list[Path]:
    if not root.is_dir() or not session_id:
        return []
    matches: list[Path] = []
    try:
        for path in root.rglob("*.jsonl"):
            if session_id in path.name:
                matches.append(path)
    except OSError:
        return []
    def safe_mtime(item: Path) -> float:
        try:
            return item.stat().st_mtime
        except OSError:
            return 0

    return sorted(matches, key=safe_mtime)[-4:]


def _iter_recent_json(path: Path, max_bytes: int) -> Iterable[dict[str, Any]]:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
                handle.readline()
            for raw in handle:
                try:
                    value = json.loads(raw.decode("utf-8", errors="replace"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if isinstance(value, dict):
                    yield value
    except OSError:
        return


def _content_blocks(event: dict[str, Any]) -> list[dict[str, Any]]:
    message = event.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if isinstance(content, dict):
        return [content]
    if isinstance(content, list):
        return [block for block in content if isinstance(block, dict)]
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return []


def _target_hint(tool_input: Any) -> str | None:
    if not isinstance(tool_input, dict):
        return None
    for key in TARGET_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return _bounded_tail(value, 240)
    return None


def _collect_events(paths: list[Path], max_bytes: int, max_events: int) -> tuple[list[dict[str, Any]], str, int]:
    events: list[dict[str, Any]] = []
    assistant_parts: list[str] = []
    reasoning_bytes = 0
    for path in paths:
        for value in _iter_recent_json(path, max_bytes):
            event_type = str(value.get("type", "unknown"))
            message = value.get("message")
            message_role = str(message.get("role", "")) if isinstance(message, dict) else ""
            assistant_event = event_type == "assistant" or message_role == "assistant"
            if event_type == "system" and value.get("subtype") == "init":
                tools = value.get("tools")
                events.append({
                    "kind": "provider-init",
                    "tools": [str(item) for item in tools[:24]] if isinstance(tools, list) else [],
                })
            for block in _content_blocks(value):
                block_type = str(block.get("type", ""))
                if block_type == "text" and assistant_event:
                    text = block.get("text")
                    if isinstance(text, str) and text.strip():
                        excerpt = _bounded_tail(text, 800)
                        assistant_parts.append(excerpt)
                        events.append({"kind": "assistant-output", "excerpt": excerpt})
                elif block_type == "thinking" and assistant_event:
                    thinking = block.get("thinking")
                    if isinstance(thinking, str):
                        reasoning_bytes += len(thinking.encode("utf-8", errors="replace"))
                        events.append({
                            "kind": "assistant-reasoning-activity",
                            "bytes": len(thinking.encode("utf-8", errors="replace")),
                            "content_persisted": False,
                        })
                elif block_type == "tool_use" and assistant_event:
                    entry: dict[str, Any] = {
                        "kind": "tool-start",
                        "tool": str(block.get("name", "unknown"))[:80],
                    }
                    hint = _target_hint(block.get("input"))
                    if hint:
                        entry["target_hint"] = hint
                    events.append(entry)
                elif block_type == "tool_result":
                    entry = {
                        "kind": "tool-result",
                        "is_error": bool(block.get("is_error", False)),
                    }
                    content = block.get("content")
                    if entry["is_error"] and isinstance(content, str):
                        entry["error_excerpt"] = _bounded_tail(content, 320)
                    events.append(entry)
            if event_type == "result":
                events.append({
                    "kind": "result",
                    "is_error": bool(value.get("is_error", False)),
                })
    return events[-max_events:], _bounded_tail("\n".join(assistant_parts), 2400), reasoning_bytes


def _activity_summary(
    events: list[dict[str, Any]], *, transcript_available: bool, session_recent: bool
) -> dict[str, Any]:
    """Reduce model activity to safe, decision-useful counters.

    A recent session-store mtime alone can be caused by runtime bookkeeping.
    Timeout triage must therefore distinguish it from evidence that Claude
    actually emitted assistant content, reasoning activity, or a tool call.
    The summary intentionally carries no thinking text or tool-result payload.
    """
    counts = {
        "assistant_output": 0,
        "assistant_reasoning_activity": 0,
        "tool_start": 0,
        "tool_result": 0,
        "tool_error": 0,
    }
    count_key = {
        "assistant-output": "assistant_output",
        "assistant-reasoning-activity": "assistant_reasoning_activity",
        "tool-start": "tool_start",
        "tool-result": "tool_result",
    }
    tools: list[str] = []
    targets: list[str] = []
    for event in events:
        kind = str(event.get("kind", ""))
        if kind in count_key:
            counts[count_key[kind]] += 1
        if kind == "tool-result" and event.get("is_error"):
            counts["tool_error"] += 1
        if kind == "tool-start":
            tool = str(event.get("tool", "")).strip()
            if tool and tool not in tools and len(tools) < 8:
                tools.append(tool)
            target = str(event.get("target_hint", "")).strip()
            if target and target not in targets and len(targets) < 8:
                targets.append(target)

    model_events = (
        counts["assistant_output"]
        + counts["assistant_reasoning_activity"]
        + counts["tool_start"]
        + counts["tool_result"]
    )
    transcript_activity_recent = bool(
        transcript_available and session_recent and model_events > 0
    )
    if not transcript_activity_recent:
        signal = "no-fresh-model-activity"
    elif counts["tool_start"]:
        signal = "recent-tool-activity"
    elif counts["assistant_output"]:
        signal = "recent-assistant-output"
    else:
        signal = "recent-reasoning-activity"
    return {
        "transcript_activity_recent": transcript_activity_recent,
        "activity_signal": signal,
        "assistant_output_count": counts["assistant_output"],
        "assistant_reasoning_event_count": counts["assistant_reasoning_activity"],
        "tool_start_count": counts["tool_start"],
        "tool_result_count": counts["tool_result"],
        "tool_error_count": counts["tool_error"],
        "recent_tools": tools,
        "recent_target_hints": targets,
    }


def _load_product_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(value, dict):
        return {}
    paths = value.get("product_changed_paths", [])
    if not isinstance(paths, list):
        paths = []
    return {
        "product_hash": value.get("product_hash"),
        "product_change_count": value.get(
            "incremental_product_change_count", value.get("product_change_count", 0)
        ),
        "product_changed_paths": [str(item) for item in paths[:40]],
    }


def _recent_status(path: Path, sampled_at: int, recent_window: int) -> tuple[str, bool]:
    try:
        modified = int(path.stat().st_mtime)
        with path.open("rb") as handle:
            size = path.stat().st_size
            if size > 8192:
                handle.seek(size - 8192)
                handle.readline()
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return "", False
    recent = modified > 0 and max(0, sampled_at - modified) <= recent_window
    return _bounded_tail(text, 1200), recent


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-root", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--task-card", required=True)
    parser.add_argument("--product-state", required=True)
    parser.add_argument("--status-file", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--evaluation-id", required=True)
    parser.add_argument(
        "--window-kind", choices=("context-acquisition", "active-execution"), required=True
    )
    parser.add_argument("--window-deadline", type=int, required=True)
    parser.add_argument("--hard-deadline", type=int, required=True)
    parser.add_argument("--sampled-at", type=int, required=True)
    parser.add_argument("--last-product-change", type=int, default=0)
    parser.add_argument("--last-session-activity", type=int, default=0)
    parser.add_argument("--recent-activity-window", type=int, default=120)
    parser.add_argument("--max-transcript-bytes", type=int, default=131072)
    parser.add_argument("--max-events", type=int, default=16)
    args = parser.parse_args()

    task_card = Path(args.task_card)
    paths = _candidate_transcripts(Path(args.session_root), args.session_id)
    events, assistant_excerpt, reasoning_bytes = _collect_events(
        paths, max(4096, args.max_transcript_bytes), max(1, min(args.max_events, 64))
    )
    status_excerpt, status_recent = _recent_status(
        Path(args.status_file), args.sampled_at, max(0, args.recent_activity_window)
    )
    session_recent = (
        args.last_session_activity > 0
        and max(0, args.sampled_at - args.last_session_activity)
        <= max(0, args.recent_activity_window)
    )
    try:
        card_bytes = task_card.read_bytes()
        card_sha = "sha256:" + hashlib.sha256(card_bytes).hexdigest()
    except OSError:
        card_sha = None
    activity_summary = _activity_summary(
        events, transcript_available=bool(paths), session_recent=session_recent
    )

    value = {
        "schema_version": 1,
        "kind": "claude-timeout-extension-candidate",
        "task_id": args.task_id,
        "evaluation_id": args.evaluation_id,
        "window_kind": args.window_kind,
        "sampled_at_epoch": args.sampled_at,
        "window_deadline_epoch": args.window_deadline,
        "active_deadline_epoch": (
            args.window_deadline if args.window_kind == "active-execution" else None
        ),
        "context_acquisition_deadline_epoch": (
            args.window_deadline if args.window_kind == "context-acquisition" else None
        ),
        "hard_deadline_epoch": args.hard_deadline,
        "last_product_change_epoch": args.last_product_change or None,
        "last_session_activity_epoch": args.last_session_activity or None,
        "task_card_sha256": card_sha,
        "task_contract_excerpt": _contract_excerpt(task_card, 5000),
        "product_state": _load_product_state(Path(args.product_state)),
        "transcript_available": bool(paths),
        "transcript_file_count": len(paths),
        "recent_events": events,
        "recent_assistant_output_untrusted": assistant_excerpt,
        "recent_runtime_status_untrusted": status_excerpt,
        "runtime_status_recent": status_recent,
        "session_activity_recent": session_recent,
        "activity_summary": activity_summary,
        # Runtime status may show that the dispatcher is alive, but it is not
        # model activity and cannot by itself justify an extension.
        "status_activity_available": bool(status_excerpt and status_recent),
        "activity_evidence_available": activity_summary["transcript_activity_recent"],
        "reasoning_activity_bytes": reasoning_bytes,
        "reasoning_content_persisted": False,
        "tool_result_payloads_persisted": False,
        "evidence_authority": "advisory-untrusted-model-activity",
    }
    value["activity_digest"] = _sha256_text(json.dumps(
        {"events": events, "assistant": assistant_excerpt}, sort_keys=True
    ))
    _atomic_write(Path(args.output), value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
