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
from . import v0
from .core import BookError, read_json
from .events import append_event, classify_log, log_path


class Divergence(BookError):
    """The observed state does not match the recorded intent."""

    def __init__(self, message: str, details: Any | None = None):
        super().__init__("OPERATION_DIVERGED", message, details)


def apply_writes(root: Path, writes: list[dict[str, Any]]) -> None:
    """Aplica as escritas do plano, idempotentemente.

    Três estados observáveis por arquivo, e só um é surpresa: já contém o
    conteúdo planejado (aplicado), contém o pai declarado (aplicar agora), ou
    contém outra coisa — mudança que ninguém contabilizou, que a recuperação
    reporta em vez de sobrescrever.
    """
    for write in writes:
        path = root / write["path"]
        value = write["value"]
        if path.exists():
            current = read_json(path)
            if current == value:
                continue
            parent = write.get("parent")
            if parent is None or current.get("revision", {}).get("hash") != parent:
                raise Divergence(
                    f"{path} holds content that matches neither the intent's parent nor its result",
                    {"path": write["path"]},
                )
        v0.atomic_write(path, value)


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
) -> dict[str, Any]:
    """Run one knowledge mutation under the protected protocol.

    ``plan`` decides everything the Book generates — identity, revision,
    timestamps — and returns a SELF-CONTAINED plan:

    ```text
    {"writes": [{"path", "value", "parent"?}], "event": {"summary", "data"},
     "result": {...}}
    ```

    Self-contained is what makes recovery generic: completing an interrupted
    operation replays the stored plan instead of re-deciding anything, so the
    outcome does not depend on who is running the recovery or when.
    """
    task_id = tasks.require_association(root, task_id, require=require_task)
    operation_id = operation_id or journal.new_operation_id()

    intent = journal.begin(
        root, operation_id=operation_id, kind=kind, task_id=task_id, request=request
    )
    if intent["state"] == journal.CONFIRMED:
        # Repetir operação já confirmada RECUPERA o resultado e não escreve
        # nada. Vale inclusive depois de a Task encerrar: recuperar não é
        # escrever.
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
        apply_writes(root, planned["writes"])

    append_event(
        root,
        event_kind,
        task_id=task_id,
        summary=planned["event"]["summary"],
        data={**planned["event"]["data"], "operation_id": operation_id},
        idempotency_key=operation_id,
    )
    journal.resolve(root, operation_id, planned["result"])
    journal.adopt(root, len(estado["events"]))
    return {**planned["result"], "operation_id": operation_id}


EVENT_KIND_BY_OPERATION = {
    "case_add": "CASE_ADD",
    "case_import": "CASE_ADD",
    "case_revise": "CASE_REVISE",
    "case_challenge": "CASE_CHALLENGE",
    "relation_add": "RELATION_ADD",
}


def recover(root: Path) -> dict[str, Any]:
    """Completa toda intenção não resolvida pela regra única declarada.

    Conclusão idempotente quando a intenção e as pré-condições verificadas
    bastam; ``uncertain`` quando o estado observado diverge. A recuperação
    carrega registro próprio, com o relógio dela: ela não antedata confirmação
    que a evidência não sustenta, e não reescreve evento já íntegro — o
    ``idempotency_key`` devolve o que existe em vez de duplicar.
    """
    def complete(record: dict[str, Any]) -> dict[str, Any] | None:
        planned = record.get("planned")
        if planned is None:
            # Queda antes de decidir o plano: não há o que concluir, e nada foi
            # escrito. Abortar é honesto e determinístico.
            journal.abort(root, record["operation_id"], "interrupted before the plan was decided")
            return None
        apply_writes(root, planned["writes"])
        append_event(
            root,
            EVENT_KIND_BY_OPERATION[record["kind"]],
            task_id=record["task_id"],
            summary=planned["event"]["summary"],
            data={**planned["event"]["data"], "operation_id": record["operation_id"]},
            idempotency_key=record["operation_id"],
        )
        return planned["result"]

    outcomes = journal.recover(root, complete)
    return {"ok": True, "operation": "recover", "recovered": outcomes}
