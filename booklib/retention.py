"""Selective retention decisions; no automatic memory creation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .authoring import semantic_to_draft
from .core import BookError
from .events import append_event
from . import v0


def assess(root: Path, payload: Any) -> dict[str, Any]:
    draft, _ = semantic_to_draft(payload)
    case = v0.prepare_new_case(draft); corpus = v0.load_corpus(root)
    exact = [old["id"] for old in corpus if v0.semantic_fingerprint(old) == v0.semantic_fingerprint(case)]
    similar = v0.similar_candidates(case, corpus)
    recommendation = "no-op" if exact else ("decide-revise-or-challenge" if similar else "new")
    return {"ok": True, "operation": "retention assess", "recommendation": recommendation, "exact": exact, "similar_candidates": similar, "automatic_write": False}


def record(root: Path, task_id: str, decision: str, summary: str, case_id: str | None = None) -> dict[str, Any]:
    if decision not in {"new", "revise", "challenge", "no-op"}:
        raise BookError("RETENTION_DECISION_INVALID", "decision must be new, revise, challenge, or no-op")
    return append_event(root, "RETENTION", task_id=task_id, summary=summary, data={"decision": decision, "case_id": case_id})
