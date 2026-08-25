"""Integrity, environment diagnostics, migration overlays, and conservative GC."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .core import BookError, atomic_write, read_json
from .events import load_events, validate_events
from .index import check_index
from .ladders import validate_ladder
from .relations import load_relations, validate_relation
from .tasks import load_task
from .views import facet_path, infer_facets
from . import v0


def verify(root: Path) -> tuple[dict[str, Any], int]:
    base, _ = v0.verify_corpus(root); errors = list(base["errors"])
    def capture(kind: str, path: Path, fn) -> None:
        try: fn()
        except (BookError, v0.BookError) as exc: errors.append({"file": str(path), "error": exc.code, "message": exc.message, "kind": kind})
        except Exception as exc: errors.append({"file": str(path), "error": "VERIFY_INTERNAL", "message": str(exc), "kind": kind})
    for path in sorted((root / "relations").glob("*.json")) if (root / "relations").exists() else []:
        capture("relation", path, lambda p=path: validate_relation(read_json(p)))
    event_file = root / "events" / "events.jsonl"
    capture("events", event_file, lambda: validate_events(load_events(root)))
    for path in sorted((root / "tasks").glob("*.json")) if (root / "tasks").exists() else []:
        def check_task(p=path):
            task = read_json(p)
            required = {"task_id", "goal", "project", "domain", "started_at", "finished_at", "state", "known", "hypotheses", "missing", "next_probe", "loaded_refs", "followed_refs", "validation", "outcome"}
            if set(task) != required or task["task_id"] != p.stem or task["state"] not in {"active", "finished"}: raise BookError("TASK_SCHEMA_INVALID", "task structure is invalid")
        capture("task", path, check_task)
    for path in sorted((root / "ladders").glob("*.json")) if (root / "ladders").exists() else []:
        capture("ladder", path, lambda p=path: validate_ladder(read_json(p)))
    case_ids = {case["id"] for case in v0.load_corpus(root)} if not base["errors"] else set()
    for path in sorted((root / "catalog").glob("*.json")) if (root / "catalog").exists() else []:
        def check_facet(p=path):
            item = read_json(p)
            if set(item) != {"case_id", "domain", "views", "synthetic", "updated_at"} or item["case_id"] != p.stem or item["case_id"] not in case_ids: raise BookError("FACET_INVALID", "facet metadata is invalid or orphaned")
        capture("facet", path, check_facet)
    index = check_index(root)
    result = {"ok": not errors, "operation": "verify", "tool_version": "1.0.0", "schema_version": v0.SCHEMA_VERSION, "files": base["files"], "valid_cases": base["valid_cases"], "relations": len(relation_files) if (relation_files := (list((root / "relations").glob("*.json")) if (root / "relations").exists() else [])) else 0, "events": len(load_events(root)) if not any(e.get("kind") == "events" for e in errors) else None, "tasks": len(list((root / "tasks").glob("*.json"))) if (root / "tasks").exists() else 0, "ladders": len(list((root / "ladders").glob("*.json"))) if (root / "ladders").exists() else 0, "derived_index": index, "errors": errors}
    return result, 0 if not errors else 1


def doctor(root: Path) -> dict[str, Any]:
    directories = {}
    for name in ("cases", "catalog", "relations", "events", "tasks", "ladders", ".book"):
        path = root / name
        directories[name] = {"exists": path.exists(), "readable": os.access(path, os.R_OK) if path.exists() else os.access(root, os.R_OK), "writable": os.access(path, os.W_OK) if path.exists() else os.access(root, os.W_OK)}
    from .references import config
    cfg = config(root)
    return {"ok": all(item["readable"] and item["writable"] for item in directories.values()), "operation": "doctor", "python": {"stdlib_only": True, "sqlite_available": True}, "directories": directories, "index": check_index(root), "resolvers": {"book": "AVAILABLE", "file": "AVAILABLE", "git": "AVAILABLE" if (Path(cfg.get("git_root", root)) / ".git").exists() else "UNAVAILABLE", "trama": "AVAILABLE" if cfg.get("trama_catalog") else "UNAVAILABLE"}}


def migrate_v0(root: Path) -> dict[str, Any]:
    created = []
    for case in v0.load_corpus(root):
        path = facet_path(root, case["id"])
        if not path.exists(): atomic_write(path, infer_facets(case)); created.append(str(path.relative_to(root)))
    return {"ok": True, "operation": "migrate-v0", "case_schema_rewritten": False, "created_overlays": created}


def gc_candidates(root: Path) -> dict[str, Any]:
    candidates = []
    for case in v0.load_corpus(root):
        reasons = []
        if case["status"] in {"superseded", "historical"}: reasons.append(case["status"])
        if case["status"] == "challenged": reasons.append("requires-review")
        if reasons: candidates.append({"id": case["id"], "status": case["status"], "reasons": reasons, "action": "review-only"})
    return {"ok": True, "operation": "gc candidates", "candidates": candidates, "deleted": 0, "low_usage_is_not_deletion_reason": True}
