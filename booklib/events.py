"""Append-only factual event log with hash chaining and idempotency."""

from __future__ import annotations

import json
import os
import secrets
import string
from pathlib import Path
from typing import Any

from .core import BookError, canonical_bytes, digest, grant_shared_group_access, loads_json, lock, utc_now
from .security import redact_summary, reject_sensitive

EVENT_KINDS = {
    "TASK_BEGIN", "SEARCH", "LIST", "SHOW", "REFERENCE_FOLLOW", "CASE_ADD",
    "CASE_REVISE", "CASE_CHALLENGE", "RELATION_ADD", "VALIDATION", "TASK_FINISH",
    "PROBE_RESULT", "UTILITY", "RETENTION", "PATH_STATUS", "PATH_EXPLORE",
    "SOURCE_READ_REPORTED", "TOOL_CALLS_REPORTED", "LADDER_ADD", "PATH_SEARCH",
}
DATA_FIELDS = {
    "query", "view", "id", "reference", "result_count", "served_bytes", "served_chars",
    "oracle", "outcome", "utility", "source_reads", "tool_calls", "full_file_reads",
    "wall_time", "reverts", "status", "path_id", "budget", "decision", "case_id",
    "relation_id", "validated", "action", "observable", "reason", "signature", "evidence",
}


def log_path(root: Path) -> Path:
    return root / "events" / "events.jsonl"


def _parse_lines(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            events.append(loads_json(line, f"event line {number}"))
        except (json.JSONDecodeError, BookError) as exc:
            if isinstance(exc, BookError):
                raise BookError("EVENT_LOG_INVALID", f"invalid event at line {number}: {exc.message}") from exc
            raise BookError("EVENT_LOG_INVALID", f"invalid event JSON at line {number}: {exc}") from exc
    return events


def load_events(root: Path, task_id: str | None = None) -> list[dict[str, Any]]:
    events = _parse_lines(log_path(root))
    return [item for item in events if task_id is None or item.get("task_id") == task_id]


def validate_events(events: list[dict[str, Any]]) -> None:
    previous = None
    ids: set[str] = set()
    for expected_seq, event in enumerate(events, 1):
        required = {"event_id", "seq", "timestamp", "kind", "task_id", "summary", "data", "previous_hash", "hash"}
        if set(event) != required:
            raise BookError("EVENT_LOG_INVALID", f"event {expected_seq} fields are invalid")
        if event["seq"] != expected_seq or event["previous_hash"] != previous:
            raise BookError("EVENT_ORDER_INVALID", f"event {event['event_id']} breaks ordering/hash chain")
        if event["event_id"] in ids:
            raise BookError("EVENT_ID_DUPLICATE", f"duplicate event {event['event_id']}")
        if event["kind"] not in EVENT_KINDS:
            raise BookError("EVENT_KIND_INVALID", f"unsupported event kind {event['kind']!r}")
        expected_hash = digest({key: value for key, value in event.items() if key != "hash"})
        if event["hash"] != expected_hash:
            raise BookError("EVENT_HASH_INVALID", f"event {event['event_id']} hash mismatch")
        ids.add(event["event_id"]); previous = event["hash"]


def append_event(root: Path, kind: str, *, task_id: str | None = None, summary: str = "", data: dict[str, Any] | None = None, idempotency_key: str | None = None, timestamp: str | None = None) -> dict[str, Any]:
    if kind not in EVENT_KINDS:
        raise BookError("EVENT_KIND_INVALID", f"unsupported event kind {kind!r}")
    safe_data = dict(data or {})
    unknown = sorted(set(safe_data) - DATA_FIELDS)
    if unknown:
        raise BookError("EVENT_FIELD_INVALID", "event data is not allowlisted", {"unknown": unknown})
    reject_sensitive(safe_data, "event.data")
    clean_summary = redact_summary(summary)
    event_id = "E-" + (digest({"task": task_id, "key": idempotency_key})[:20].upper() if idempotency_key else "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(20)))
    with lock(root, "events"):
        path = log_path(root); events = _parse_lines(path); validate_events(events)
        for event in events:
            if event["event_id"] == event_id:
                comparable = {"kind": kind, "task_id": task_id, "summary": clean_summary, "data": safe_data}
                if any(event[key] != value for key, value in comparable.items()):
                    raise BookError("EVENT_IDEMPOTENCY_CONFLICT", "idempotency key was reused with different event content")
                return {"ok": True, "operation": "event", "event": event, "idempotent": True}
        base = {"event_id": event_id, "seq": len(events) + 1, "timestamp": timestamp or utc_now(), "kind": kind, "task_id": task_id, "summary": clean_summary, "data": safe_data, "previous_hash": events[-1]["hash"] if events else None}
        event = {**base, "hash": digest(base)}
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o660)
        try:
            grant_shared_group_access(fd)
            os.write(fd, canonical_bytes(event) + b"\n"); os.fsync(fd)
        finally:
            os.close(fd)
    return {"ok": True, "operation": "event", "event": event, "idempotent": False}


def find_event(root: Path, event_id: str) -> dict[str, Any]:
    wanted = event_id if event_id.startswith("E-") else f"E-{event_id}"
    for event in load_events(root):
        if event["event_id"] == wanted:
            return event
    raise BookError("EVENT_NOT_FOUND", f"event {wanted} not found")
