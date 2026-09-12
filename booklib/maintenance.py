"""Integrity, environment diagnostics, migration overlays, and conservative GC."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .core import BookError, atomic_write, read_json
from .events import classify_log, load_events, log_path, validate_events
from .index import check_index
from .ladders import validate_ladder
from .relations import load_relations, validate_relation
from .tasks import EXTERNAL_TASK_REF_RE, load_task
from .views import facet_path, infer_facets
from . import TOOL_VERSION, journal, v0


def consistency(root: Path) -> dict[str, Any]:
    """Integridade conferível, e a história que o protocolo antigo não produzia.

    São DUAS fronteiras, e confundi-las inverte o sentido do relatório:

    - ``frontier`` é a fronteira de eventos observada NESTA avaliação; avança
      conforme o Book muda.
    - ``adoption_frontier`` é a adoção do protocolo novo; é histórica e fixa, e
      ``pre_frontier`` refere-se exclusivamente a ela.

    ``consistency`` considera TODA corrupção ou inconsistência demonstrável,
    independentemente da posição em relação à adoção. A ausência das evidências
    que o protocolo anterior não produzia é classificada em ``pre_frontier`` e
    não causa, por si só, ``UNCERTAIN`` nem ``BLOCKED``. ``OK`` não certifica
    retrospectivamente a história classificada como inverificável.
    """
    estado = classify_log(log_path(root))
    adocao = journal.adoption(root)
    limite = (adocao or {}).get("adoption_seq", 0)
    eventos = estado["events"]

    classificados: list[dict[str, Any]] = []
    inverificaveis: list[dict[str, Any]] = []
    sem_par: list[dict[str, Any]] = []
    revisoes = {case["id"]: case["revision"]["hash"] for case in v0.load_corpus(root)}
    for evento in eventos:
        if evento["kind"] not in {"CASE_ADD", "CASE_REVISE", "CASE_CHALLENGE"}:
            continue
        registrada = evento["data"].get("revision")
        entrada = {"event_id": evento["event_id"], "seq": evento["seq"], "case_id": evento["data"].get("case_id")}
        if evento["seq"] <= limite or registrada is None:
            # Anterior à adoção, ou sem a evidência que o protocolo antigo não
            # produzia: classifica, não presume e não bloqueia por isso.
            (classificados if registrada is not None else inverificaveis).append(entrada)
            continue
        atual = revisoes.get(entrada["case_id"])
        if atual is None:
            sem_par.append({**entrada, "reason": "event names a case that is not in the corpus"})

    pendentes = journal.unresolved(root)
    if estado["status"] == "INCOMPLETE_TAIL":
        nivel = "INCOMPLETE"
    elif estado["status"] in {"CORRUPT", "HASH_INVALID"}:
        nivel = "BLOCKED"
    elif sem_par:
        nivel = "BLOCKED"
    elif pendentes:
        nivel = "UNCERTAIN"
    else:
        nivel = "OK"
    return {
        "consistency": nivel,
        "detail": estado["detail"],
        "quarantine": [str(path.name) for path in sorted((root / ".book" / "quarantine").glob("events-*.jsonl"))] if (root / ".book" / "quarantine").exists() else [],
        "unmatched": sem_par,
        "unresolved_operations": [{"operation_id": r["operation_id"], "state": r["state"]} for r in pendentes],
        "frontier": eventos[-1]["event_id"] if eventos else None,
        "adoption_frontier": adocao,
        "pre_frontier": {"classified": classificados, "unverifiable": inverificaveis},
    }


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
            legacy = {"task_id", "goal", "project", "domain", "started_at", "finished_at", "state", "known", "hypotheses", "missing", "next_probe", "loaded_refs", "followed_refs", "validation", "outcome"}
            current = legacy | {"external_task_ref"}
            external = task.get("external_task_ref")
            if frozenset(task) not in {frozenset(legacy), frozenset(current)} or task["task_id"] != p.stem or task["state"] not in {"active", "finished"}: raise BookError("TASK_SCHEMA_INVALID", "task structure is invalid")
            if external is not None and (not isinstance(external, str) or not EXTERNAL_TASK_REF_RE.fullmatch(external)): raise BookError("EXTERNAL_TASK_REF_INVALID", "external task reference must use pinker:<task-id>")
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
    integridade = consistency(root)
    if integridade["consistency"] in {"BLOCKED", "INCOMPLETE"}:
        errors.append({"file": str(event_file), "error": "LOG_INTEGRITY", "message": integridade["detail"] or integridade["consistency"], "kind": "events"})
    result = {"ok": not errors, "operation": "verify", "tool_version": TOOL_VERSION, "schema_version": v0.SCHEMA_VERSION, "files": base["files"], "valid_cases": base["valid_cases"], "relations": len(relation_files) if (relation_files := (list((root / "relations").glob("*.json")) if (root / "relations").exists() else [])) else 0, "events": len(load_events(root)) if not any(e.get("kind") == "events" for e in errors) else None, "tasks": len(list((root / "tasks").glob("*.json"))) if (root / "tasks").exists() else 0, "ladders": len(list((root / "ladders").glob("*.json"))) if (root / "ladders").exists() else 0, "derived_index": index, **integridade, "errors": errors}
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
