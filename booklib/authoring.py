"""Semantic authoring: callers provide meaning; Book supplies mechanics."""

from __future__ import annotations

import copy
import secrets
import string
from pathlib import Path
from typing import Any

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


def create_case(root: Path, payload: Any, *, actor: str = "author", allow_similar: bool = False, now: str | None = None) -> dict[str, Any]:
    draft, facets = semantic_to_draft(payload, actor=actor, now=now)
    case = v0.prepare_new_case(draft)
    with v0.exclusive_book_lock(root):
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
        v0.atomic_write(v0.cases_dir(root) / f"{case['id']}.json", case)
        atomic_write(root / "catalog" / f"{case['id']}.json", facets)
    return {"ok": True, "operation": "add-case", "id": case["id"], "status": case["status"], "revision": case["revision"]["hash"], "generated": ["id", "schema_version", "status", "revision", "timestamps"], "similar_candidates": candidates}


def import_case(root: Path, path: Path, *, allow_similar: bool = False) -> dict[str, Any]:
    """Import either a V0 creation draft or an already canonical schema-1 case."""
    source = v0.read_json(path)
    revision = source.get("revision") if isinstance(source, dict) else None
    case = v0.validate_case(source) if isinstance(revision, dict) and "hash" in revision else v0.prepare_new_case(source)
    with v0.exclusive_book_lock(root):
        corpus = v0.load_corpus(root)
        if any(old["id"] == case["id"] for old in corpus):
            raise BookError("CASE_ID_DUPLICATE", f"case id {case['id']} already exists")
        if any(v0.semantic_fingerprint(old) == v0.semantic_fingerprint(case) for old in corpus):
            raise BookError("CASE_EXACT_DUPLICATE", "an exact semantic duplicate exists")
        candidates = v0.similar_candidates(case, corpus)
        if candidates and not allow_similar:
            raise BookError("CASE_SIMILAR_CANDIDATES", "similar cases require an explicit decision", {"candidates": candidates})
        v0.atomic_write(v0.cases_dir(root) / f"{case['id']}.json", case)
    return {"ok": True, "operation": "import-case", "id": case["id"], "status": case["status"], "revision": case["revision"]["hash"], "similar_candidates": candidates}


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
