"""Short operational Task state; no transcripts or chain-of-thought."""

from __future__ import annotations

import secrets
import string
from pathlib import Path
from typing import Any

from .core import BookError, atomic_write, lock, read_json, require_text, utc_now
from .events import append_event
from .security import reject_sensitive


def new_task_id() -> str:
    return "T-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(16))


def task_path(root: Path, task_id: str) -> Path:
    return root / "tasks" / f"{task_id}.json"


def load_task(root: Path, task_id: str) -> dict[str, Any]:
    path = task_path(root, task_id)
    if not path.exists():
        raise BookError("TASK_NOT_FOUND", f"task {task_id} not found")
    return read_json(path)


def begin(root: Path, goal: str, project: str, *, domain: str | None = None, task_id: str | None = None, now: str | None = None) -> dict[str, Any]:
    require_text(goal, "goal", maximum=2048); require_text(project, "project", maximum=256)
    reject_sensitive({"goal": goal, "project": project, "domain": domain})
    identifier = task_id or new_task_id(); timestamp = now or utc_now()
    task = {"task_id": identifier, "goal": goal, "project": project, "domain": domain, "started_at": timestamp, "finished_at": None, "state": "active", "known": [], "hypotheses": [], "missing": [], "next_probe": None, "loaded_refs": [], "followed_refs": [], "validation": None, "outcome": None}
    with lock(root, f"task-{identifier}"):
        if task_path(root, identifier).exists():
            raise BookError("TASK_ID_DUPLICATE", f"task {identifier} already exists")
        atomic_write(task_path(root, identifier), task)
    append_event(root, "TASK_BEGIN", task_id=identifier, summary=goal, data={"signature": f"{domain or 'UNKNOWN'}:{project}"}, idempotency_key="begin", timestamp=timestamp)
    return {"ok": True, "operation": "task begin", "task": task}


def update(root: Path, task_id: str, mutator) -> dict[str, Any]:
    with lock(root, f"task-{task_id}"):
        task = load_task(root, task_id)
        if task["state"] != "active":
            raise BookError("TASK_FINISHED", f"task {task_id} is already finished")
        mutator(task); reject_sensitive(task); atomic_write(task_path(root, task_id), task)
    return task


def add_missing(root: Path, task_id: str, question: str) -> dict[str, Any]:
    require_text(question, "missing question", maximum=1024)
    task = update(root, task_id, lambda item: item["missing"].append(question) if question not in item["missing"] else None)
    return {"ok": True, "operation": "task missing", "task_id": task_id, "missing": task["missing"]}


def add_state(root: Path, task_id: str, field: str, statement: str) -> dict[str, Any]:
    if field not in {"known", "hypotheses"}:
        raise BookError("TASK_FIELD_INVALID", "task state field is invalid")
    require_text(statement, field, maximum=1024)
    task = update(root, task_id, lambda item: item[field].append(statement) if statement not in item[field] else None)
    return {"ok": True, "operation": f"task {field}", "task_id": task_id, field: task[field]}


def set_next_probe(root: Path, task_id: str, action: str, observable: str, oracle: str, *, authorized: bool, bounded: bool, discriminative: bool, no_high_risk_gap: bool) -> dict[str, Any]:
    for field, value in (("action", action), ("observable", observable), ("oracle", oracle)):
        require_text(value, field, maximum=2048)
    probe = {"action": action, "observable": observable, "oracle": oracle, "authorized": authorized, "bounded": bounded, "discriminative": discriminative, "no_known_high_risk_gap": no_high_risk_gap, "recorded_at": utc_now()}
    task = update(root, task_id, lambda item: item.update(next_probe=probe))
    return {"ok": True, "operation": "task next-probe", "task_id": task_id, "next_probe": task["next_probe"], "ready": all((authorized, bounded, discriminative, no_high_risk_gap, bool(observable)))}


def validate(root: Path, task_id: str, oracle: str, outcome: str, *, passed: bool, summary: str = "") -> dict[str, Any]:
    validation = {"oracle": require_text(oracle, "oracle", maximum=1024), "outcome": require_text(outcome, "outcome", maximum=2048), "passed": bool(passed), "validated_at": utc_now(), "summary": summary}
    task = update(root, task_id, lambda item: item.update(validation=validation))
    append_event(root, "VALIDATION", task_id=task_id, summary=summary or outcome, data={"oracle": oracle, "outcome": outcome, "validated": bool(passed)})
    return {"ok": True, "operation": "task validate", "task_id": task_id, "validation": task["validation"]}


def finish(root: Path, task_id: str, outcome: str) -> dict[str, Any]:
    require_text(outcome, "outcome", maximum=2048)
    with lock(root, f"task-{task_id}"):
        task = load_task(root, task_id)
        if task["state"] == "finished":
            return {"ok": True, "operation": "task finish", "task": task, "idempotent": True}
        task.update(state="finished", finished_at=utc_now(), outcome=outcome)
        atomic_write(task_path(root, task_id), task)
    append_event(root, "TASK_FINISH", task_id=task_id, summary=outcome, data={"outcome": outcome}, idempotency_key="finish")
    return {"ok": True, "operation": "task finish", "task": task, "idempotent": False}


def note_loaded(root: Path, task_id: str | None, refs: list[str], *, followed: bool = False) -> None:
    if not task_id:
        return
    def mutate(task: dict[str, Any]) -> None:
        field = "followed_refs" if followed else "loaded_refs"
        task[field] = list(dict.fromkeys([*task[field], *refs]))[-128:]
    update(root, task_id, mutate)


def receipt(root: Path, task_id: str) -> dict[str, Any]:
    from .metrics import task_metrics
    task = load_task(root, task_id); probe = task.get("next_probe") or {}
    ready = bool(probe) and all(probe.get(key) for key in ("authorized", "bounded", "observable", "discriminative", "no_known_high_risk_gap"))
    return {"ok": True, "operation": "task receipt", "content_trust": "UNTRUSTED_DATA", "task": {"task_id": task_id, "goal": task["goal"], "project": task["project"], "domain": task["domain"], "state": task["state"]}, "loaded_knowledge": task["loaded_refs"], "followed_refs": task["followed_refs"], "missing": task["missing"], "next_probe": task["next_probe"], "ready": ready, "validation": task["validation"], "outcome": task["outcome"], "metrics": task_metrics(root, task_id)}
