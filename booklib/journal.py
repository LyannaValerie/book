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
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .core import BookError, atomic_write, digest, lock, opaque_id, read_json, utc_now

#: Operation kinds the journal covers. Each one mutates knowledge.
KINDS = {"case_add", "case_import", "case_revise", "case_challenge", "relation_add"}

PENDING = "pending"
CONFIRMED = "confirmed"
ABORTED = "aborted"
UNCERTAIN = "uncertain"


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
    record["state"] = UNCERTAIN
    record["recovery"] = {"outcome": UNCERTAIN, "reason": reason, "at": utc_now()}
    atomic_write(_path(root, operation_id), record)


def recover(root: Path, complete: Callable[[dict[str, Any]], dict[str, Any] | None]) -> list[dict[str, Any]]:
    """Resolve every unresolved intent by ONE rule: idempotent completion.

    ``complete`` returns the confirmed result when the intent and the verified
    pre-conditions suffice, or ``None`` when the observed state diverges from
    the intent. Divergence produces ``uncertain`` — it never authorises
    overwriting a change nobody can account for.
    """
    outcomes = []
    for record in pending(root):
        operation_id = record["operation_id"]
        try:
            result = complete(record)
        except BookError as exc:
            mark_uncertain(root, operation_id, f"{exc.code}: {exc.message}")
            outcomes.append({"operation_id": operation_id, "outcome": UNCERTAIN, "reason": exc.message})
            continue
        if result is None:
            mark_uncertain(root, operation_id, "observed state diverges from the recorded intent")
            outcomes.append({"operation_id": operation_id, "outcome": UNCERTAIN})
            continue
        resolve(
            root,
            operation_id,
            result,
            recovery={"outcome": "completed", "at": utc_now(), "by": "recovery"},
        )
        outcomes.append({"operation_id": operation_id, "outcome": "completed"})
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
