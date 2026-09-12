"""Typed epistemic graph stored independently from cases."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .core import BookError, atomic_write, digest, lock, read_json, require_text, utc_now
from .security import reject_sensitive

RELATION_TYPES = {"references", "depends_on", "affects", "causes", "supersedes", "validated_by", "learned_from"}
EPISTEMIC_CLASSES = {"OBSERVED", "ASSERTED", "VALIDATED"}


def relation_files(root: Path) -> list[Path]:
    directory = root / "relations"
    return sorted(directory.glob("R-*.json")) if directory.exists() else []


def load_relations(root: Path) -> list[dict[str, Any]]:
    return [read_json(path) for path in relation_files(root)]


def validate_relation(edge: Any) -> dict[str, Any]:
    if not isinstance(edge, dict) or set(edge) != {"id", "from", "type", "to", "epistemic", "oracle", "created_at", "created_by"}:
        raise BookError("SCHEMA_INVALID", "relation has invalid fields")
    if edge["type"] not in RELATION_TYPES:
        raise BookError("RELATION_TYPE_INVALID", f"unsupported relation type {edge['type']!r}")
    if edge["epistemic"] not in EPISTEMIC_CLASSES:
        raise BookError("EPISTEMIC_CLASS_INVALID", "relation epistemic class is invalid")
    if edge["epistemic"] == "VALIDATED" and not edge["oracle"]:
        raise BookError("VALIDATION_ORACLE_REQUIRED", "validated relation requires an oracle reference")
    for field in ("from", "to", "created_at", "created_by"):
        require_text(edge[field], f"relation.{field}", maximum=1024)
    from . import v0
    for field in ("from", "to"):
        endpoint = edge[field]
        if not v0.CASE_ID_RE.fullmatch(endpoint):
            v0.validate_reference(endpoint, f"relation.{field}")
    reject_sensitive(edge)
    expected = "R-" + digest({key: edge[key] for key in edge if key != "id"})[:16].upper()
    if edge["id"] != expected:
        raise BookError("RELATION_ID_INVALID", "relation ID does not match canonical content")
    return edge


def add_relation(
    root: Path,
    source: str,
    kind: str,
    target: str,
    epistemic: str,
    oracle: str | None,
    actor: str,
    now: str | None = None,
    *,
    task_id: str | None = None,
    require_task: bool = False,
    operation_id: str | None = None,
) -> dict[str, Any]:
    from . import mutation

    semantic = {"from": source, "type": kind, "to": target, "epistemic": epistemic, "oracle": oracle}

    def plan() -> dict[str, Any]:
        for existing in load_relations(root):
            if all(existing[key] == value for key, value in semantic.items()):
                return {"edge": existing, "idempotent": True}
        base = {**semantic, "created_at": now or utc_now(), "created_by": actor}
        edge = {"id": "R-" + digest(base)[:16].upper(), **base}
        validate_relation(edge)
        return {"edge": edge, "idempotent": False}

    def write(planned: dict[str, Any]) -> None:
        if planned["idempotent"]:
            return
        edge = planned["edge"]
        path = root / "relations" / f"{edge['id']}.json"
        if path.exists():
            if read_json(path) == edge:
                return
            raise mutation.Divergence(f"{path} exists with content that differs from the recorded intent")
        atomic_write(path, edge)

    def event(planned: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        return planned["edge"]["id"], {"relation_id": planned["edge"]["id"]}

    def result(planned: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "operation": "relate", "relation": planned["edge"], "idempotent": planned["idempotent"]}

    return mutation.guarded(
        root, kind="relation_add", event_kind="RELATION_ADD", task_id=task_id,
        require_task=require_task, operation_id=operation_id,
        request=semantic,
        store_lock=lambda: lock(root, "relations"),
        plan=plan, write=write, event=event, result=result,
    )


def references(root: Path, identifier: str, *, depth: int = 1, maximum: int = 100) -> dict[str, Any]:
    if depth < 1 or depth > 5:
        raise BookError("TRAVERSAL_LIMIT", "depth must be in 1..5")
    edges = load_relations(root)
    if identifier.startswith("B-"):
        try:
            from . import v0
            case = v0.find_case(root, identifier)
            for target in case.get("references", []):
                edge_id = "R-" + digest({"from": identifier, "type": "references", "to": target})[:16].upper()
                edges.append({"id": edge_id, "from": identifier, "type": "references", "to": target, "epistemic": "ASSERTED", "oracle": None, "created_at": case["revision"]["updated_at"], "created_by": case["revision"]["updated_by"], "derived_from_case_field": True})
        except v0.BookError:
            pass
    outgoing: list[dict[str, Any]] = []
    incoming: list[dict[str, Any]] = []
    frontier = {identifier}
    visited = set(frontier)
    for _ in range(depth):
        next_frontier: set[str] = set()
        for edge in edges:
            if edge["from"] in frontier:
                outgoing.append(edge); next_frontier.add(edge["to"])
            if edge["to"] in frontier:
                incoming.append(edge); next_frontier.add(edge["from"])
            if len(outgoing) + len(incoming) >= maximum:
                break
        frontier = next_frontier - visited
        visited.update(frontier)
        if not frontier or len(outgoing) + len(incoming) >= maximum:
            break
    key = lambda edge: (edge["from"], edge["type"], edge["to"], edge["id"])
    return {"ok": True, "operation": "references", "id": identifier, "outgoing": sorted({e["id"]: e for e in outgoing}.values(), key=key), "incoming": sorted({e["id"]: e for e in incoming}.values(), key=key), "bounded": len(outgoing) + len(incoming) >= maximum, "content_trust": "UNTRUSTED_DATA"}
