"""Explicitly authored branching ProbeLadders, never inferred from traces."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .core import BookError, atomic_write, digest, read_json, require_fields, require_object, require_text, utc_now
from .security import reject_sensitive

FIELDS = {"id", "schema_version", "title", "preconditions", "first_probe", "branches", "stop_conditions", "contraindications", "terminal_validation", "created_at", "created_by"}


def validate_ladder(value: Any) -> dict[str, Any]:
    item = require_object(value, "probe ladder"); require_fields(item, FIELDS, FIELDS, "probe ladder")
    if item["schema_version"] != 1:
        raise BookError("SCHEMA_FUTURE_UNSUPPORTED", "unsupported ProbeLadder schema")
    require_text(item["title"], "ladder.title", maximum=512)
    for field in ("preconditions", "branches", "stop_conditions", "contraindications"):
        if not isinstance(item[field], list) or len(item[field]) > 64:
            raise BookError("SCHEMA_INVALID", f"ladder.{field} must be a bounded list")
    if not isinstance(item["first_probe"], dict) or not {"action", "observable"} <= item["first_probe"].keys():
        raise BookError("SCHEMA_INVALID", "first_probe requires action and observable")
    if not isinstance(item["terminal_validation"], dict) or not {"oracle", "success"} <= item["terminal_validation"].keys():
        raise BookError("SCHEMA_INVALID", "terminal_validation requires oracle and success")
    for branch in item["branches"]:
        if not isinstance(branch, dict) or not {"when", "next_probe"} <= branch.keys():
            raise BookError("SCHEMA_INVALID", "each branch requires when and next_probe")
    reject_sensitive(item)
    return item


def add_ladder(root: Path, payload: Any, actor: str) -> dict[str, Any]:
    source = require_object(payload, "ladder payload")
    required = {"title", "preconditions", "first_probe", "branches", "stop_conditions", "contraindications", "terminal_validation"}
    require_fields(source, required, required, "ladder payload")
    base = {"schema_version": 1, **source, "created_at": utc_now(), "created_by": actor}
    item = {"id": "L-" + digest(base)[:16].upper(), **base}; validate_ladder(item)
    path = root / "ladders" / f"{item['id']}.json"
    existed = path.exists()
    if not existed: atomic_write(path, item)
    return {"ok": True, "operation": "ladder add", "ladder": item, "idempotent": existed}


def list_ladders(root: Path) -> dict[str, Any]:
    rows = []
    for path in sorted((root / "ladders").glob("L-*.json")) if (root / "ladders").exists() else []:
        item = validate_ladder(read_json(path)); rows.append({"id": item["id"], "title": item["title"]})
    return {"ok": True, "operation": "ladder list", "ladders": rows}


def show_ladder(root: Path, ladder_id: str) -> dict[str, Any]:
    path = root / "ladders" / f"{ladder_id}.json"
    if not path.exists(): raise BookError("LADDER_NOT_FOUND", f"ladder {ladder_id} not found")
    return {"ok": True, "operation": "ladder show", "content_trust": "UNTRUSTED_DATA", "ladder": validate_ladder(read_json(path)), "automatic_execution": False}
