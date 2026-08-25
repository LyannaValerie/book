"""Derived success paths from factual Task traces. A path is never a solution."""

from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any

from .core import BookError, digest
from .events import append_event, load_events
from .tasks import load_task
from . import v0

TRACE_KINDS = {"SEARCH", "LIST", "SHOW", "REFERENCE_FOLLOW", "PROBE_RESULT", "VALIDATION"}
PATH_STATUSES = {"ACTIVE", "SUSPECT", "STALE", "SUPERSEDED"}


def _node(event: dict[str, Any]) -> str:
    data = event["data"]
    return str(data.get("id") or data.get("reference") or data.get("query") or data.get("view") or data.get("oracle") or event["summary"] or event["kind"])


def derive_paths(root: Path) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in load_events(root):
        if event.get("task_id"):
            grouped.setdefault(event["task_id"], []).append(event)
    traces = []
    for task_id, events in grouped.items():
        try:
            task = load_task(root, task_id)
        except BookError:
            continue
        if task["state"] != "finished":
            continue
        nodes = [{"kind": event["kind"], "value": _node(event)} for event in events if event["kind"] in TRACE_KINDS]
        if not nodes:
            continue
        validation = task.get("validation") or {}
        signature = {"domain": task.get("domain"), "project": task.get("project"), "component": None, "phase": None, "backend": None, "symptom": task.get("goal"), "environment": None, "positive_conditions": [], "contraindications": []}
        key = digest({"signature": signature, "nodes": nodes})[:20].upper()
        metrics = __import__("booklib.metrics", fromlist=["task_metrics"]).task_metrics(root, task_id)
        traces.append({"key": key, "task_id": task_id, "signature": signature, "nodes": nodes, "success": bool(validation.get("passed")), "validated_at": validation.get("validated_at"), "cost": {"served_bytes": metrics["access"]["served_bytes"], "estimated_tokens": metrics["access"]["estimated_tokens"]["value"], "tool_calls": metrics["reported_cost"]["tool_calls"], "references_followed": metrics["access"]["references_followed"], "source_reads": metrics["reported_cost"]["source_reads"], "full_file_reads": sum(int(e["data"].get("full_file_reads", 0)) for e in events), "wall_time": metrics["timing"]["time_to_validation"], "reverts": sum(int(e["data"].get("reverts", 0)) for e in events)}})
    results = []
    for key in sorted({trace["key"] for trace in traces}):
        items = [trace for trace in traces if trace["key"] == key]
        successes = [item for item in items if item["success"]]
        byte_costs = [item["cost"]["served_bytes"] for item in successes or items]
        path = {"path_id": f"P-{key}", "signature": items[0]["signature"], "nodes": items[0]["nodes"], "uses": len(items), "successful_uses": len(successes), "success_rate": len(successes) / len(items), "median_context_cost": statistics.median(byte_costs), "source_fallbacks": sum(item["cost"]["source_reads"] for item in items), "last_validated": max((item["validated_at"] for item in successes if item["validated_at"]), default=None), "status": "ACTIVE" if successes else "SUSPECT", "cost_vector": {field: sum((item["cost"][field] or 0) for item in items) for field in items[0]["cost"]}, "derived": True, "path_is_solution": False}
        results.append(path)
    marks = [event for event in load_events(root) if event["kind"] == "PATH_STATUS"]
    for path in results:
        relevant = [event for event in marks if event["data"].get("path_id") == path["path_id"]]
        if relevant:
            path["status"] = relevant[-1]["data"]["status"]
    return results


def search_paths(root: Path, query: str, *, success_only: bool = False, domain: str | None = None, limit: int = 50) -> dict[str, Any]:
    tokens = set(v0.normalize_tokens(query)); matches = []
    for path in derive_paths(root):
        haystack = " ".join([str(path["signature"]), *(node["value"] for node in path["nodes"])])
        if tokens and not tokens.intersection(v0.normalize_tokens(haystack)):
            continue
        if success_only and not path["successful_uses"]:
            continue
        if domain is not None and path["signature"].get("domain") != domain:
            continue
        matches.append(path)
    matches.sort(key=lambda item: (item["status"] != "ACTIVE", -item["successful_uses"], item["median_context_cost"], item["path_id"]))
    return {"ok": True, "operation": "path search", "query": query, "count": len(matches), "returned": min(len(matches), limit), "results": matches[:limit], "truncated": len(matches) > limit, "content_trust": "UNTRUSTED_DATA", "automatic_execution": False}


def mark_path(root: Path, path_id: str, status: str, reason: str, task_id: str | None = None, evidence: str | None = None) -> dict[str, Any]:
    if status not in PATH_STATUSES:
        raise BookError("PATH_STATUS_INVALID", f"unsupported path status {status!r}")
    if status == "STALE":
        if not reason.strip() or not evidence:
            raise BookError("STALE_EVIDENCE_REQUIRED", "STALE requires explicit failure evidence and a typed reference")
        v0.validate_reference(evidence, "path stale evidence")
    if path_id not in {item["path_id"] for item in derive_paths(root)}:
        raise BookError("PATH_NOT_FOUND", f"path {path_id} not found")
    data = {"path_id": path_id, "status": status, "reason": reason}
    if evidence: data["evidence"] = evidence
    return append_event(root, "PATH_STATUS", task_id=task_id, summary=reason, data=data)


def explore(root: Path, primary: str, candidate: str, budget: int, task_id: str | None = None) -> dict[str, Any]:
    paths = {item["path_id"]: item for item in derive_paths(root)}
    if primary not in paths or candidate not in paths:
        raise BookError("PATH_NOT_FOUND", "primary or candidate path is absent")
    p0, p1 = paths[primary], paths[candidate]
    if budget <= 0 or p1["median_context_cost"] >= p0["median_context_cost"]:
        decision = "ABANDON_P1_RETURN_P0"
    elif p1["successful_uses"] and p1["median_context_cost"] < p0["median_context_cost"]:
        decision = "P1_CANDIDATE_PREFERRED_ROUTE"
    else:
        decision = "EXPLORE_P1_WITHIN_BUDGET"
    append_event(root, "PATH_EXPLORE", task_id=task_id, summary=decision, data={"path_id": candidate, "budget": budget, "decision": decision})
    return {"ok": True, "operation": "path explore", "decision": decision, "automatic_execution": False}
