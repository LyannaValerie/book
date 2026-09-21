"""Intent journal: durable intent, one confirmation point, deterministic recovery.

A knowledge mutation touches three files with different synchronisation — the
case, the catalog and the event log. Nothing tied them together, so a failure
between writes left knowledge stored with no record of it.

The journal makes every mutation a named operation with a durable intent:

```text
begin(intent)  -> apply writes -> resolve(confirmed, result)
                                  ^ the confirmation point
```

Anything before the resolution is *applied but not confirmed*; no consumer may
present it as a concluded operation. Recovery is deterministic: an unresolved
intent is completed idempotently when the intent and the verified
pre-conditions suffice, and classified ``uncertain`` when they do not. Recovery
never attributes to an operation facts, confirmation or temporal fulfilment the
evidence does not support — it carries its own record, with its own clock.

``uncertain`` is a state of the EVIDENCE, not a verdict about the operation.
It says "this Book cannot decide right now", and later evidence may decide it:

```text
uncertain -> reconsider -> effect      -> confirmed
                        -> no effect   -> aborted, classified recovered_no_effect
                        -> undecidable -> uncertain, unchanged
```

Two rules keep that from becoming a laundering channel. Reconsideration is
fail-closed — silence is never read as proof — and it invents no state:
"no effect" reuses ``aborted``, which is already the Book's name for "the
intent exists and nothing came of it". Nothing is erased either: the previous
disposition moves into ``history``, so *why did this stop being unresolved?*
keeps a durable answer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from . import TOOL_VERSION
from .core import BookError, atomic_write, digest, lock, opaque_id, read_json, utc_now

#: Operation kinds the journal covers. Each one mutates knowledge.
KINDS = {"case_add", "case_import", "case_revise", "case_challenge", "relation_add"}

PENDING = "pending"
CONFIRMED = "confirmed"
ABORTED = "aborted"
UNCERTAIN = "uncertain"

#: States a reconsideration may still act on. Anything else is already decided.
OPEN_STATES = {PENDING, UNCERTAIN}

#: Dispositions a reconsideration can reach. They classify the EVIDENCE; each
#: maps onto a state the Book already has, so there is no parallel model.
EFFECT = "effect"
NO_EFFECT = "no_effect"
UNDECIDABLE = "undecidable"

#: Outcomes reported back to the caller.
COMPLETED = "completed"
RECOVERED_NO_EFFECT = "recovered_no_effect"
ALREADY_RESOLVED = "already_resolved"


def journal_dir(root: Path) -> Path:
    return root / ".book" / "journal"


def adoption_path(root: Path) -> Path:
    return root / ".book" / "adoption.json"


def new_operation_id() -> str:
    """Identity for an operation, obtainable BEFORE the attempt that may lose
    the response. A caller that keeps it can retry and recover the result."""
    return opaque_id("OP")


def request_digest(kind: str, task_id: str | None, request: Any) -> str:
    """Fingerprint of what the CALLER asked for.

    Book-generated values — case id, timestamps, revision hashes — are not part
    of the request, so a legitimate retry does not read as a different request.
    """
    return digest({"kind": kind, "task_id": task_id, "request": request})


def _path(root: Path, operation_id: str) -> Path:
    return journal_dir(root) / f"{operation_id}.json"


def load(root: Path, operation_id: str) -> dict[str, Any] | None:
    path = _path(root, operation_id)
    if not path.exists():
        return None
    return read_json(path)


def pending(root: Path) -> list[dict[str, Any]]:
    directory = journal_dir(root)
    if not directory.exists():
        return []
    entries = []
    for path in sorted(directory.glob("OP-*.json")):
        record = read_json(path)
        if record.get("state") == PENDING:
            entries.append(record)
    return entries


def begin(
    root: Path,
    *,
    operation_id: str,
    kind: str,
    task_id: str | None,
    request: Any,
) -> dict[str, Any]:
    """Persist the intent. Returns the existing record on a retry."""
    if kind not in KINDS:
        raise BookError("JOURNAL_KIND_INVALID", f"unsupported operation kind {kind!r}")
    fingerprint = request_digest(kind, task_id, request)
    existing = load(root, operation_id)
    if existing is not None:
        if existing["request_digest"] != fingerprint:
            raise BookError(
                "OPERATION_REQUEST_CONFLICT",
                "operation id was reused with a different request",
                {"operation_id": operation_id, "state": existing["state"]},
            )
        return existing
    record = {
        "operation_id": operation_id,
        "kind": kind,
        "task_id": task_id,
        "request_digest": fingerprint,
        "state": PENDING,
        "opened_at": utc_now(),
        "resolved_at": None,
        "result": None,
        "recovery": None,
    }
    atomic_write(_path(root, operation_id), record)
    return record


def update(root: Path, record: dict[str, Any]) -> None:
    """Persist a change to an intent — used to fix the PLAN before applying it."""
    atomic_write(_path(root, record["operation_id"]), record)


def resolve(root: Path, operation_id: str, result: Any, *, recovery: dict[str, Any] | None = None) -> None:
    """The confirmation point. A durable record of the confirmation, not the
    absence of the intent, is what says the operation succeeded."""
    record = load(root, operation_id)
    if record is None:
        raise BookError("JOURNAL_MISSING", f"no intent for {operation_id}")
    record["state"] = CONFIRMED
    record["resolved_at"] = utc_now()
    record["result"] = result
    if recovery is not None:
        record["recovery"] = recovery
    atomic_write(_path(root, operation_id), record)


def abort(root: Path, operation_id: str, reason: str) -> None:
    record = load(root, operation_id)
    if record is None:
        return
    record["state"] = ABORTED
    record["resolved_at"] = utc_now()
    record["recovery"] = {"outcome": ABORTED, "reason": reason, "at": utc_now()}
    atomic_write(_path(root, operation_id), record)


def mark_uncertain(root: Path, operation_id: str, reason: str) -> None:
    """Preserve the state and ask for the decision. Never overwrite an unknown
    change to make the ledger look tidy."""
    record = load(root, operation_id)
    if record is None:
        return
    if record.get("state") == UNCERTAIN and (record.get("recovery") or {}).get("reason") == reason:
        # Reconsideração que chegou à mesma conclusão: nada mudou, e repetir o
        # registro só inflaria o histórico com a ausência de novidade.
        return
    remember(record)
    record["state"] = UNCERTAIN
    record["recovery"] = {"outcome": UNCERTAIN, "reason": reason, "at": utc_now()}
    atomic_write(_path(root, operation_id), record)


def remember(record: dict[str, Any]) -> dict[str, Any]:
    """Move the current disposition into ``history`` before replacing it.

    A reconsideration that overwrote the previous classification would destroy
    the only evidence that the operation was ever unresolved, and with it the
    answer to "why did this stop being unresolved?".
    """
    previous = record.get("recovery")
    if previous is None and record.get("state") not in {UNCERTAIN, ABORTED}:
        return record
    history = list(record.get("history") or [])
    history.append({
        "state": record.get("state"),
        "resolved_at": record.get("resolved_at"),
        "recovery": previous,
    })
    record["history"] = history
    return record


def resolve_no_effect(root: Path, operation_id: str, evidence: dict[str, Any]) -> dict[str, Any]:
    """Close an intent that provably produced NO canonical mutation.

    It reuses ``aborted`` — the state the Book already has for "the intent
    exists and nothing came of it" — rather than inventing a state whose only
    job would be to look different. ``result`` stays ``null``: there is no
    result, and recovery does not manufacture one. The timestamps are the
    RECOVERY's, never a fabricated original completion.
    """
    record = load(root, operation_id)
    if record is None:
        raise BookError("JOURNAL_MISSING", f"no intent for {operation_id}")
    remember(record)
    now = utc_now()
    record["state"] = ABORTED
    record["resolved_at"] = now
    record["result"] = None
    record["recovery"] = {
        "outcome": RECOVERED_NO_EFFECT,
        "disposition": NO_EFFECT,
        "reason": "canonical evidence shows no mutation attributable to this operation",
        "evidence": evidence,
        "at": now,
        "by": "recovery",
        "tool_version": TOOL_VERSION,
        # A recuperação não sabe — e não finge saber — quando a operação teria
        # concluído. Ela só sabe quando decidiu que não concluiu.
        "original_completion": None,
    }
    atomic_write(_path(root, operation_id), record)
    return record


def recover(
    root: Path,
    settle: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    operation_id: str | None = None,
) -> list[dict[str, Any]]:
    """Reconsider unresolved intents by ONE rule: a disposition read off the
    durable evidence.

    ``settle`` inspects an intent and returns the disposition the evidence
    supports — ``effect`` with the result to confirm, ``no_effect`` with the
    proof, or ``undecidable``. The journal owns the state transitions so the
    three outcomes cannot drift apart, and ``undecidable`` is the default the
    other two have to earn.

    Without ``operation_id`` this sweeps the PENDING intents only. Reopening
    every ``uncertain`` intent in the store is a decision about someone else's
    unresolved history, so it is taken one operation at a time, by name.
    """
    if operation_id is not None:
        record = load(root, operation_id)
        if record is None:
            raise BookError("JOURNAL_MISSING", f"no intent for {operation_id}")
        if record.get("state") not in OPEN_STATES:
            # Idempotência: uma operação já decidida não é decidida de novo,
            # nem ganha um segundo registro de recuperação.
            return [{
                "operation_id": operation_id,
                "outcome": ALREADY_RESOLVED,
                "state": record["state"],
            }]
        records = [record]
    else:
        records = pending(root)

    outcomes = []
    for record in records:
        current = record["operation_id"]
        try:
            verdict = settle(record)
        except BookError as exc:
            mark_uncertain(root, current, f"{exc.code}: {exc.message}")
            outcomes.append({"operation_id": current, "outcome": UNCERTAIN, "reason": exc.message})
            continue
        disposition = verdict["disposition"]
        if disposition == EFFECT:
            resolve(
                root,
                current,
                verdict["result"],
                recovery={"outcome": COMPLETED, "at": utc_now(), "by": "recovery"},
            )
            outcomes.append({"operation_id": current, "outcome": COMPLETED})
        elif disposition == NO_EFFECT:
            resolve_no_effect(root, current, verdict["evidence"])
            outcomes.append({
                "operation_id": current,
                "outcome": RECOVERED_NO_EFFECT,
                "evidence": verdict["evidence"],
            })
        else:
            mark_uncertain(root, current, verdict["reason"])
            outcomes.append({
                "operation_id": current,
                "outcome": UNCERTAIN,
                "reason": verdict["reason"],
                "evidence": verdict.get("evidence"),
            })
    return outcomes


def unresolved(root: Path) -> list[dict[str, Any]]:
    """Intents that are neither confirmed nor aborted — pending or uncertain."""
    directory = journal_dir(root)
    if not directory.exists():
        return []
    return [
        record
        for path in sorted(directory.glob("OP-*.json"))
        for record in [read_json(path)]
        if record.get("state") in {PENDING, UNCERTAIN}
    ]


def adopt(root: Path, seq: int) -> dict[str, Any]:
    """Establish the adoption frontier once. Idempotent.

    It marks where the new protocol starts, and nothing more: it makes no claim
    about the history before it.
    """
    path = adoption_path(root)
    if path.exists():
        return read_json(path)
    record = {"adopted_at": utc_now(), "adoption_seq": seq}
    atomic_write(path, record)
    return record


def adoption(root: Path) -> dict[str, Any] | None:
    path = adoption_path(root)
    return read_json(path) if path.exists() else None
