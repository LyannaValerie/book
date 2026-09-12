"""Selective retention decisions; no automatic memory creation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .authoring import semantic_to_draft
from .core import BookError, require_text
from .events import append_event, load_events
from . import journal, tasks, v0

#: Os eventos que representam MUTAÇÃO DE CONHECIMENTO e por isso precisam ser
#: reconciliados. Acesso e métrica não entram: não há o que reter neles.
MUTATION_KINDS = {"CASE_ADD", "CASE_REVISE", "CASE_CHALLENGE", "RELATION_ADD"}

DECISIONS = {"new", "revise", "challenge", "no-op"}


def assess(root: Path, payload: Any) -> dict[str, Any]:
    draft, _ = semantic_to_draft(payload)
    case = v0.prepare_new_case(draft); corpus = v0.load_corpus(root)
    exact = [old["id"] for old in corpus if v0.semantic_fingerprint(old) == v0.semantic_fingerprint(case)]
    similar = v0.similar_candidates(case, corpus)
    recommendation = "no-op" if exact else ("decide-revise-or-challenge" if similar else "new")
    return {"ok": True, "operation": "retention assess", "recommendation": recommendation, "exact": exact, "similar_candidates": similar, "automatic_write": False}


def _mutations(events: list[dict[str, Any]], task_id: str) -> list[str]:
    return [e["event_id"] for e in events if e.get("task_id") == task_id and e["kind"] in MUTATION_KINDS]


def _covered(events: list[dict[str, Any]], task_id: str) -> set[str]:
    covered: set[str] = set()
    for event in events:
        if event.get("task_id") == task_id and event["kind"] == "RETENTION":
            covered.update(event["data"].get("covers") or [])
    return covered


def validate_covers(events: list[dict[str, Any]], task_id: str, covers: list[str]) -> list[str]:
    """Cobertura é afirmação sobre eventos concretos, e é conferida como tal.

    Um identificador arbitrário, um evento de outra Task, um evento que não
    representa aprendizagem a reconciliar ou um evento posterior à própria
    reconciliação não podem entrar. Repetição é idempotente — cobrir duas vezes
    o mesmo evento não é erro e não infla contagem.
    """
    by_id = {event["event_id"]: event for event in events}
    seen: list[str] = []
    for event_id in covers:
        require_text(event_id, "covers entry", maximum=64)
        event = by_id.get(event_id)
        if event is None:
            raise BookError("COVERS_EVENT_UNKNOWN", f"event {event_id} does not exist")
        if event.get("task_id") != task_id:
            raise BookError("COVERS_EVENT_FOREIGN", f"event {event_id} belongs to another task")
        if event["kind"] not in MUTATION_KINDS:
            raise BookError("COVERS_EVENT_INELIGIBLE", f"event {event_id} is not a knowledge mutation")
        if event_id not in seen:
            seen.append(event_id)
    return seen


def status(root: Path, task_id: str) -> dict[str, Any]:
    """O veredito que a Forja consome. Carrega a fronteira de eventos observada
    nesta avaliação: dois relatórios lidos em momentos diferentes não compõem
    autorização, e a decisão efetiva reavalia."""
    events = load_events(root)
    mutations = _mutations(events, task_id)
    covered = _covered(events, task_id)
    uncovered = [event_id for event_id in mutations if event_id not in covered]
    decisions = sum(1 for e in events if e.get("task_id") == task_id and e["kind"] == "RETENTION")
    return {
        "ok": True,
        "operation": "retention status",
        "task_id": task_id,
        "reconciled": not uncovered and decisions > 0,
        "mutations": mutations,
        "covered": sorted(covered),
        "uncovered": uncovered,
        "decisions": decisions,
        "frontier": events[-1]["event_id"] if events else None,
        "adoption_frontier": (journal.adoption(root) or {}).get("adoption_seq"),
    }


def record(
    root: Path,
    task_id: str,
    decision: str,
    summary: str,
    case_id: str | None = None,
    covers: list[str] | None = None,
    attempt: str | None = None,
) -> dict[str, Any]:
    if decision not in DECISIONS:
        raise BookError("RETENTION_DECISION_INVALID", "decision must be new, revise, challenge, or no-op")
    # Registrar retenção é escrever no razão da Task; Task encerrada não
    # recebe escrita nova, e a reabertura é deliberadamente indisponível.
    tasks.require_association(root, task_id, require=True)
    events = load_events(root)
    validated = validate_covers(events, task_id, covers or [])
    data: dict[str, Any] = {"decision": decision, "case_id": case_id}
    if validated:
        data["covers"] = validated
    if attempt is not None:
        data["attempt"] = attempt
    return append_event(root, "RETENTION", task_id=task_id, summary=summary, data=data)
