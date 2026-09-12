"""The one protected path every knowledge mutation takes.

Five public operations write knowledge — ``add-case``, ``import-case``,
``revise``, ``challenge`` and ``relate``. Installing the guarantees in the CLI
would leave a future API free to walk around them, so they live here, in the
shared operation, and each public entry point calls through.

The shape is always the same:

```text
associação validada  (antes de qualquer escrita)
-> intenção durável com o PLANO já decidido
-> escritas idempotentes
-> evento, idempotente pela identidade da operação
-> resolução da intenção            <- ponto de confirmação
```

Planning before the intent is what makes recovery deterministic: the artefact
and its identity are fixed once, so completing an interrupted operation writes
exactly what the intent says, or reports divergence and asks for a decision.
"""

from __future__ import annotations

from pathlib import Path
from contextlib import AbstractContextManager
from typing import Any, Callable

from . import journal, tasks
from .core import BookError
from .events import append_event, classify_log, log_path


class Divergence(BookError):
    """The observed state does not match the recorded intent."""

    def __init__(self, message: str, details: Any | None = None):
        super().__init__("OPERATION_DIVERGED", message, details)


def guarded(
    root: Path,
    *,
    kind: str,
    event_kind: str,
    task_id: str | None,
    require_task: bool,
    operation_id: str | None,
    request: Any,
    store_lock: Callable[[], AbstractContextManager],
    plan: Callable[[], dict[str, Any]],
    write: Callable[[dict[str, Any]], None],
    event: Callable[[dict[str, Any]], tuple[str, dict[str, Any]]],
    result: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    """Run one knowledge mutation under the protected protocol.

    ``plan`` decides the artefact and every Book-generated value; it runs once,
    inside the store lock, and its output is persisted in the intent. ``write``
    applies it idempotently. ``event`` names the factual record. ``result`` is
    what the caller gets back — and what a retry recovers verbatim.
    """
    task_id = tasks.require_association(root, task_id, require=require_task)
    operation_id = operation_id or journal.new_operation_id()

    intent = journal.begin(
        root, operation_id=operation_id, kind=kind, task_id=task_id, request=request
    )
    if intent["state"] == journal.CONFIRMED:
        # Repetir uma operação já confirmada RECUPERA o resultado; não escreve
        # nada de novo. Vale inclusive depois de a Task encerrar: recuperar não
        # é escrever.
        return {**intent["result"], "operation_id": operation_id, "recovered": True}
    if intent["state"] == journal.UNCERTAIN:
        raise BookError(
            "OPERATION_UNCERTAIN",
            f"operation {operation_id} is unresolved and needs a decision",
            {"recovery": intent.get("recovery")},
        )

    estado = classify_log(log_path(root))
    if estado["status"] != "OK":
        raise BookError(
            "EVENT_LOG_NOT_APPENDABLE",
            f"event log is {estado['status'].lower()}; recover before mutating",
            {"status": estado["status"], "detail": estado["detail"]},
        )

    # Plano e escrita sob o MESMO lock: separá-los deixaria duas execuções
    # concorrentes planejarem antes de qualquer uma escrever, e a checagem de
    # duplicata deixaria de valer. A intenção é persistida ainda dentro dele,
    # antes da primeira escrita de conhecimento.
    with store_lock():
        planned = intent.get("planned")
        if planned is None:
            planned = plan()
            intent["planned"] = planned
            journal.update(root, intent)
        write(planned)
    summary, data = event(planned)
    append_event(
        root,
        event_kind,
        task_id=task_id,
        summary=summary,
        data={**data, "operation_id": operation_id},
        idempotency_key=operation_id,
    )
    confirmed = result(planned)
    journal.resolve(root, operation_id, confirmed)
    journal.adopt(root, len(estado["events"]))
    return {**confirmed, "operation_id": operation_id}


def recover_pending(root: Path, completers: dict[str, Callable[[dict[str, Any]], dict[str, Any] | None]]) -> list[dict[str, Any]]:
    """Complete every unresolved intent by the single declared rule.

    Idempotent completion when the intent and the verified pre-conditions
    suffice; ``uncertain`` otherwise. The recovery carries its own record and
    its own clock — it never backdates a confirmation the evidence cannot
    support, and it never rewrites an event that is already intact.
    """
    def complete(record: dict[str, Any]) -> dict[str, Any] | None:
        completer = completers.get(record["kind"])
        if completer is None or record.get("planned") is None:
            return None
        return completer(record)

    return journal.recover(root, complete)
