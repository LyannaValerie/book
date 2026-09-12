"""Semantic authoring: callers provide meaning; Book supplies mechanics."""

from __future__ import annotations

import copy
import secrets
import string
from pathlib import Path
from typing import Any

from . import journal, mutation
from .core import BookError, atomic_write, require_object, require_text, utc_now
from .security import reject_sensitive
from . import v0


SEMANTIC_FIELDS = {
    "title", "cues", "scope", "observed_at", "environment", "problem",
    "discriminating_probe", "observed_result", "guidance", "contraindications",
    "evidence", "references", "domain", "views", "synthetic", "author",
}


def new_case_id() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "B-" + "".join(secrets.choice(alphabet) for _ in range(12))


def claim(value: Any, default_class: str) -> dict[str, Any]:
    if isinstance(value, str):
        return {"class": default_class, "text": value}
    if isinstance(value, dict):
        return copy.deepcopy(value)
    raise BookError("SCHEMA_INVALID", "claim must be text or an epistemic claim object")


def semantic_to_draft(payload: Any, *, actor: str = "author", now: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    data = require_object(copy.deepcopy(payload), "semantic case payload")
    unknown = sorted(set(data) - SEMANTIC_FIELDS)
    if unknown:
        raise BookError("SCHEMA_INVALID", "unknown semantic case fields", {"unknown": unknown})
    reject_sensitive(data)
    title = require_text(data.get("title"), "title", maximum=512)
    cues = data.get("cues", [])
    if not isinstance(cues, list) or not cues:
        cues = [word.casefold() for word in title.split()[:3]]
    if not all(isinstance(item, str) and item.strip() for item in cues):
        raise BookError("SCHEMA_INVALID", "cues must be non-empty strings")
    timestamp = now or utc_now()
    author = str(data.get("author") or actor)
    draft = {
        "id": new_case_id(),
        "schema_version": v0.SCHEMA_VERSION,
        "title": title,
        "cues": cues,
        "scope": data.get("scope"),
        "observed_at": data.get("observed_at"),
        "environment": data.get("environment"),
        "problem": claim(data.get("problem"), "OBSERVED"),
        "discriminating_probe": data.get("discriminating_probe"),
        "observed_result": claim(data.get("observed_result"), "OBSERVED"),
        "guidance": claim(data.get("guidance"), "ASSERTED"),
        "contraindications": data.get("contraindications", []),
        "evidence": data.get("evidence", []),
        "references": data.get("references", []),
        "status": "candidate",
        "challenges": [],
        "revision": {"updated_at": timestamp, "updated_by": author, "reason": "created through semantic authoring"},
    }
    facets = {
        "case_id": draft["id"],
        "domain": data.get("domain", "Uncategorized"),
        "views": data.get("views", []),
        "synthetic": bool(data.get("synthetic", False)),
        "updated_at": timestamp,
    }
    return draft, facets


def create_case(
    root: Path,
    payload: Any,
    *,
    actor: str = "author",
    allow_similar: bool = False,
    now: str | None = None,
    task_id: str | None = None,
    require_task: bool = False,
    operation_id: str | None = None,
) -> dict[str, Any]:
    # A recusa por conteúdo é decidida antes de qualquer lock ou diretório:
    # um payload rejeitado não deve deixar rastro de estrutura no acervo.
    draft, facets = semantic_to_draft(payload, actor=actor, now=now)
    prepared = v0.prepare_new_case(draft)

    def plan() -> dict[str, Any]:
        case = copy.deepcopy(prepared)
        corpus = v0.load_corpus(root)
        fingerprint = v0.semantic_fingerprint(case)
        if any(v0.semantic_fingerprint(old) == fingerprint for old in corpus):
            raise BookError("CASE_EXACT_DUPLICATE", "an exact semantic duplicate exists")
        candidates = v0.similar_candidates(case, corpus)
        if candidates and not allow_similar:
            raise BookError("CASE_SIMILAR_CANDIDATES", "similar cases require an explicit decision", {"candidates": candidates})
        while any(old["id"] == case["id"] for old in corpus):
            case["id"] = new_case_id()
            facets["case_id"] = case["id"]
            v0.set_revision(case, number=1, parent_hash=None, updated_at=case["revision"]["updated_at"], updated_by=case["revision"]["updated_by"], reason=case["revision"]["reason"])
        return {
            "writes": [
                {"path": f"cases/{case['id']}.json", "value": case},
                {"path": f"catalog/{case['id']}.json", "value": facets},
            ],
            "event": {"summary": case["id"], "data": {"case_id": case["id"], "revision": case["revision"]["hash"]}},
            "result": {"ok": True, "operation": "add-case", "id": case["id"], "status": case["status"], "revision": case["revision"]["hash"], "generated": ["id", "schema_version", "status", "revision", "timestamps"], "similar_candidates": candidates},
        }

    return mutation.guarded(
        root,
        kind="case_add",
        event_kind="CASE_ADD",
        task_id=task_id,
        require_task=require_task,
        operation_id=operation_id,
        request={"payload": payload, "actor": actor, "allow_similar": allow_similar},
        store_lock=lambda: v0.exclusive_book_lock(root),
        plan=plan,
    )


def add_case_from_file(
    root: Path,
    input_path: Path,
    *,
    allow_similar: bool = False,
    task_id: str | None = None,
    require_task: bool = False,
    operation_id: str | None = None,
) -> dict[str, Any]:
    """The ``--input`` path of ``add-case``, under the same guarantees.

    It accepts a draft already in canonical shape; leaving it outside the
    protected path would let a file argument walk around the association and
    the journal.
    """
    source = v0.read_json(input_path)
    prepared = v0.prepare_new_case(source)

    def plan() -> dict[str, Any]:
        case = copy.deepcopy(prepared)
        corpus = v0.load_corpus(root)
        if any(old["id"] == case["id"] for old in corpus):
            raise BookError("CASE_ID_DUPLICATE", f"case id {case['id']} already exists")
        fingerprint = v0.semantic_fingerprint(case)
        exact = [old["id"] for old in corpus if v0.semantic_fingerprint(old) == fingerprint]
        if exact:
            raise BookError("CASE_EXACT_DUPLICATE", "exact case content already exists", {"candidates": exact})
        candidates = v0.similar_candidates(case, corpus)
        if candidates and not allow_similar:
            raise BookError(
                "CASE_SIMILAR_CANDIDATES",
                "lexically similar cases require an explicit new-case decision",
                {"candidates": candidates, "hint": "use revise or repeat add-case with --allow-similar"},
            )
        return {
            "writes": [{"path": f"cases/{case['id']}.json", "value": case}],
            "event": {"summary": case["id"], "data": {"case_id": case["id"], "revision": case["revision"]["hash"]}},
            "result": {"ok": True, "operation": "add", "id": case["id"], "revision": case["revision"]["hash"], "similar_candidates": candidates},
        }

    return mutation.guarded(
        root, kind="case_add", event_kind="CASE_ADD", task_id=task_id,
        require_task=require_task, operation_id=operation_id,
        request={"source": source, "allow_similar": allow_similar},
        store_lock=lambda: v0.exclusive_book_lock(root),
        plan=plan,
    )


def revise(
    root: Path,
    case_id: str,
    expected: str,
    patch_path: Path,
    updated_at: str,
    updated_by: str,
    reason: str,
    *,
    task_id: str | None = None,
    require_task: bool = False,
    operation_id: str | None = None,
) -> dict[str, Any]:
    patch = require_object(v0.read_json(patch_path), "revision patch")
    invalid = sorted(set(patch) - v0.EDITABLE_FIELDS)
    if invalid or not patch:
        raise BookError("PATCH_INVALID", "revision patch has invalid or no fields", {"invalid": invalid})

    def plan() -> dict[str, Any]:
        case = v0.find_case(root, case_id)
        v0.require_current_revision(case, expected)
        for key, value in patch.items():
            case[key] = value
        v0.next_revision(case, updated_at=updated_at, updated_by=updated_by, reason=reason)
        v0.validate_case(case)
        return {
            "writes": [{"path": f"cases/{case_id}.json", "value": case, "parent": expected}],
            "event": {"summary": case_id, "data": {"case_id": case_id, "revision": case["revision"]["hash"]}},
            "result": {"ok": True, "operation": "revise", "id": case_id, "revision": case["revision"]["hash"], "parent_revision": expected},
        }

    return mutation.guarded(
        root, kind="case_revise", event_kind="CASE_REVISE", task_id=task_id,
        require_task=require_task, operation_id=operation_id,
        request={"case_id": case_id, "expected": expected, "patch": patch, "reason": reason},
        store_lock=lambda: v0.exclusive_book_lock(root),
        plan=plan,
    )


def challenge(
    root: Path,
    case_id: str,
    expected: str,
    challenge_path: Path,
    updated_at: str,
    updated_by: str,
    reason: str,
    *,
    task_id: str | None = None,
    require_task: bool = False,
    operation_id: str | None = None,
) -> dict[str, Any]:
    payload = v0.read_json(challenge_path)
    v0.validate_challenge(payload)

    def plan() -> dict[str, Any]:
        case = v0.find_case(root, case_id)
        v0.require_current_revision(case, expected)
        if case["status"] in {"superseded", "historical"}:
            raise BookError("STATUS_TRANSITION_INVALID", f"cannot challenge a {case['status']} case")
        if any(item["id"] == payload["id"] for item in case["challenges"]):
            raise BookError("CHALLENGE_ID_DUPLICATE", f"challenge id {payload['id']} already exists")
        case["challenges"].append(payload)
        case["status"] = "challenged"
        v0.next_revision(case, updated_at=updated_at, updated_by=updated_by, reason=reason)
        v0.validate_case(case)
        return {
            "writes": [{"path": f"cases/{case_id}.json", "value": case, "parent": expected}],
            "event": {"summary": case_id, "data": {"case_id": case_id, "revision": case["revision"]["hash"]}},
            "result": {"ok": True, "operation": "challenge", "id": case_id, "status": case["status"], "revision": case["revision"]["hash"], "parent_revision": expected, "challenge_id": payload["id"]},
        }

    return mutation.guarded(
        root, kind="case_challenge", event_kind="CASE_CHALLENGE", task_id=task_id,
        require_task=require_task, operation_id=operation_id,
        request={"case_id": case_id, "expected": expected, "challenge": payload, "reason": reason},
        store_lock=lambda: v0.exclusive_book_lock(root),
        plan=plan,
    )


def import_case(
    root: Path,
    path: Path,
    *,
    allow_similar: bool = False,
    task_id: str | None = None,
    require_task: bool = False,
    operation_id: str | None = None,
) -> dict[str, Any]:
    """Import either a V0 creation draft or an already canonical schema-1 case."""
    source = v0.read_json(path)
    revision = source.get("revision") if isinstance(source, dict) else None
    prepared = v0.validate_case(source) if isinstance(revision, dict) and "hash" in revision else v0.prepare_new_case(source)

    def plan() -> dict[str, Any]:
        case = copy.deepcopy(prepared)
        corpus = v0.load_corpus(root)
        if any(old["id"] == case["id"] for old in corpus):
            raise BookError("CASE_ID_DUPLICATE", f"case id {case['id']} already exists")
        if any(v0.semantic_fingerprint(old) == v0.semantic_fingerprint(case) for old in corpus):
            raise BookError("CASE_EXACT_DUPLICATE", "an exact semantic duplicate exists")
        candidates = v0.similar_candidates(case, corpus)
        if candidates and not allow_similar:
            raise BookError("CASE_SIMILAR_CANDIDATES", "similar cases require an explicit decision", {"candidates": candidates})
        return {
            "writes": [{"path": f"cases/{case['id']}.json", "value": case}],
            "event": {"summary": case["id"], "data": {"case_id": case["id"], "revision": case["revision"]["hash"]}},
            "result": {"ok": True, "operation": "import-case", "id": case["id"], "status": case["status"], "revision": case["revision"]["hash"], "similar_candidates": candidates},
        }

    return mutation.guarded(
        root, kind="case_import", event_kind="CASE_ADD", task_id=task_id,
        require_task=require_task, operation_id=operation_id,
        request={"source": source, "allow_similar": allow_similar},
        store_lock=lambda: v0.exclusive_book_lock(root),
        plan=plan,
    )


def minimal_interactive(input_fn=input, output_fn=print) -> dict[str, Any]:
    def ask(label: str, required: bool = True) -> str:
        value = input_fn(f"{label}: ").strip()
        if required and not value:
            raise BookError("INPUT_REQUIRED", f"{label} is required")
        return value
    title = ask("Title")
    problem = ask("Problem")
    cue = ask("Cue")
    probe = ask("Probe", False)
    observable = ask("Observable", False) if probe else ""
    result = ask("Result")
    guidance = ask("Guidance")
    evidence = ask("Evidence")
    evidence_reference = ask("Evidence reference")
    domain = ask("Domain", False) or "Uncategorized"
    return {
        "title": title,
        "cues": [item.strip() for item in cue.split(",") if item.strip()],
        "scope": None,
        "environment": None,
        "problem": problem,
        "discriminating_probe": {"action": probe, "observable": observable} if probe else None,
        "observed_result": result,
        "guidance": guidance,
        "contraindications": [],
        "evidence": [{"class": "ASSERTED", "description": evidence, "source": evidence_reference}],
        "references": [],
        "domain": domain,
        "views": [],
    }
