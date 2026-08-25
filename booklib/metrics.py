"""ACCESS is automatic; UTILITY and VALIDITY remain explicit signals."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .events import load_events


def _seconds(a: str, b: str) -> float:
    return max(0.0, (datetime.fromisoformat(b.replace("Z", "+00:00")) - datetime.fromisoformat(a.replace("Z", "+00:00"))).total_seconds())


def task_metrics(root: Path, task_id: str) -> dict[str, Any]:
    events = load_events(root, task_id)
    counts = {kind: sum(1 for event in events if event["kind"] == kind) for kind in ("SEARCH", "LIST", "SHOW", "REFERENCE_FOLLOW")}
    served_bytes = sum(int(event["data"].get("served_bytes", 0)) for event in events)
    served_chars = sum(int(event["data"].get("served_chars", 0)) for event in events)
    source_reads = sum(int(event["data"].get("source_reads", 0)) for event in events)
    tool_calls = sum(int(event["data"].get("tool_calls", 0)) for event in events)
    first = events[0]["timestamp"] if events else None
    probe_event = next((event for event in events if event["kind"] == "PROBE_RESULT"), None)
    validation = next((event for event in events if event["kind"] == "VALIDATION"), None)
    return {"access": {"searches": counts["SEARCH"], "lists": counts["LIST"], "shows": counts["SHOW"], "references_followed": counts["REFERENCE_FOLLOW"], "content_views": counts["SHOW"] + counts["REFERENCE_FOLLOW"], "served_bytes": served_bytes, "served_chars": served_chars, "estimated_tokens": {"value": (served_chars + 3) // 4, "is_estimate": True}}, "reported_cost": {"source_reads": source_reads, "tool_calls": tool_calls}, "timing": {"time_to_first_probe": _seconds(first, probe_event["timestamp"]) if first and probe_event else None, "time_to_validation": _seconds(first, validation["timestamp"]) if first and validation else None}, "utility": [event["data"] for event in events if event["kind"] == "UTILITY"], "validity": [event["data"] for event in events if event["kind"] == "VALIDATION"]}
