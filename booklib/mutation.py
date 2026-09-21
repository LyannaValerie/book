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
from .core import BookError, digest, lock, read_json
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
    # Existência da Task primeiro; o estado "encerrada" só depois de consultar
    # o journal. Repetir uma operação já confirmada RECUPERA o resultado, e
    # recuperar não é escrever — recusá-la por a Task ter encerrado confundiria
    # repetição com escrita nova.
    task_id = tasks.require_association(root, task_id, require=require_task, allow_finished=True)
    operation_id = operation_id or journal.new_operation_id()

    intent = journal.begin(
        root, operation_id=operation_id, kind=kind, task_id=task_id, request=request
    )
    if intent["state"] == journal.CONFIRMED:
        # Repetir operação já confirmada RECUPERA o resultado e não escreve
        # nada. Vale inclusive depois de a Task encerrar: recuperar não é
        # escrever.
        return {**intent["result"], "operation_id": operation_id, "recovered": True}
    # Não é repetição: daqui para a frente é escrita nova, e Task encerrada não
    # a recebe.
    tasks.require_association(root, task_id, require=require_task)
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

#: The canonical surfaces each operation kind can mutate, read off the plans
#: the five entry points actually build. A no-effect conclusion has to account
#: for EVERY surface listed here — not for the event alone.
EFFECT_SURFACES_BY_OPERATION = {
    "case_add": ("cases/<id>.json", "catalog/<id>.json", "events:CASE_ADD"),
    "case_import": ("cases/<id>.json", "events:CASE_ADD"),
    "case_revise": ("cases/<id>.json@revision", "events:CASE_REVISE"),
    "case_challenge": ("cases/<id>.json@revision", "events:CASE_CHALLENGE"),
    "relation_add": ("relations/<id>.json", "events:RELATION_ADD"),
}

#: The lock each kind writes under. Reconsideration takes the SAME one, so the
#: proof and the state transition cannot straddle another writer's commit.
STORE_LOCK_BY_OPERATION = {
    "case_add": lambda root: v0.exclusive_book_lock(root),
    "case_import": lambda root: v0.exclusive_book_lock(root),
    "case_revise": lambda root: v0.exclusive_book_lock(root),
    "case_challenge": lambda root: v0.exclusive_book_lock(root),
    "relation_add": lambda root: lock(root, "relations"),
}

#: How a planned write's target can be found, and what each state licenses.
APPLIED = "APPLIED"      # holds exactly the planned value — the mutation landed
PARENT = "PARENT"        # holds the declared parent — the mutation did not land
ABSENT = "ABSENT"        # nothing there at all
FOREIGN = "FOREIGN"      # something nobody accounted for
UNREADABLE = "UNREADABLE"


def event_identity(task_id: str | None, operation_id: str) -> str:
    """The event id THIS operation would necessarily have produced.

    ``append_event`` derives the id from the task and the idempotency key, and
    every guarded mutation passes the operation id as that key. So the identity
    is computable without the event: attribution by construction, never by
    timestamp proximity.
    """
    return "E-" + digest({"task": task_id, "key": operation_id})[:20].upper()


def attributed_events(record: dict[str, Any], events: list[dict[str, Any]]) -> list[str]:
    """Events the log attributes to this operation, by identity only."""
    operation_id = record["operation_id"]
    expected = event_identity(record.get("task_id"), operation_id)
    return [
        event["event_id"]
        for event in events
        if event["event_id"] == expected or event.get("data", {}).get("operation_id") == operation_id
    ]


def classify_surface(root: Path, write: dict[str, Any]) -> str:
    path = root / write["path"]
    if not path.exists():
        return ABSENT
    try:
        current = read_json(path)
    except BookError:
        return UNREADABLE
    if current == write["value"]:
        return APPLIED
    parent = write.get("parent")
    if parent is not None and isinstance(current, dict) and current.get("revision", {}).get("hash") == parent:
        return PARENT
    return FOREIGN


def _undecidable(reason: str, evidence: dict[str, Any]) -> dict[str, Any]:
    return {"disposition": journal.UNDECIDABLE, "reason": reason, "evidence": evidence}


def disposition(root: Path, record: dict[str, Any], estado: dict[str, Any]) -> dict[str, Any]:
    """Read the disposition of an unresolved intent off the durable evidence.

    Fail-closed by construction: ``undecidable`` is what the function returns
    unless one — and only one — of the other two is positively supported.

    ```text
    log not OK                        -> undecidable   (absence is not establishable)
    any surface FOREIGN/UNREADABLE    -> undecidable   (a change nobody accounted for)
    an event, or any surface APPLIED  -> effect        (reconcile the bookkeeping)
    every surface ABSENT/PARENT       -> no effect
    ```

    The asymmetry is deliberate. ``effect`` needs one positive trace, because
    one trace already proves the mutation happened. ``no effect`` needs EVERY
    surface accounted for, because any unchecked surface could hold the trace
    that refutes it.
    """
    operation_id = record["operation_id"]
    kind = record["kind"]
    surfaces = EFFECT_SURFACES_BY_OPERATION.get(kind)
    if surfaces is None:
        return _undecidable(
            f"no reconciler defines the effect surfaces of {kind!r}",
            {"operation_id": operation_id, "kind": kind},
        )

    base = {
        "operation_id": operation_id,
        "kind": kind,
        "request_digest": record.get("request_digest"),
        "effect_surfaces": list(surfaces),
        "event_identity": event_identity(record.get("task_id"), operation_id),
        "event_log_status": estado["status"],
    }

    # Sem log íntegro não se estabelece AUSÊNCIA de nada. Um journal corrompido
    # não vira "sem efeito" só porque o efeito não pôde ser encontrado.
    if estado["status"] != "OK":
        return _undecidable(
            f"event log is {estado['status'].lower()}; absence of an effect cannot be established",
            {**base, "detail": estado["detail"]},
        )

    found = attributed_events(record, estado["events"])
    planned = record.get("planned")

    if planned is None:
        # R1: o PLANO é persistido, atomicamente, antes da primeira escrita de
        # conhecimento e sob o mesmo lock. Intenção sem plano durável é prova
        # estrutural de que nenhuma superfície chegou a ser escrita por esta
        # operação — não é a mera ausência de um evento.
        evidence = {
            **base,
            "plan": "absent",
            "rule": "R1_PLAN_DURABLE_BEFORE_ANY_WRITE",
            "attributed_events": found,
            "surface_states": {},
        }
        if found:
            return _undecidable(
                "the intent carries no plan yet the log attributes an event to it",
                evidence,
            )
        return {"disposition": journal.NO_EFFECT, "evidence": evidence}

    states = {write["path"]: classify_surface(root, write) for write in planned["writes"]}
    evidence = {
        **base,
        "plan": "present",
        "rule": "PLANNED_SURFACES_FULLY_ACCOUNTED",
        "attributed_events": found,
        "surface_states": states,
    }

    unaccounted = sorted(path for path, state in states.items() if state in {FOREIGN, UNREADABLE})
    if unaccounted:
        return _undecidable(
            "a planned surface holds content that matches neither the intent's parent nor its result",
            {**evidence, "unaccounted": unaccounted},
        )
    if found or APPLIED in states.values():
        return {"disposition": journal.EFFECT, "evidence": evidence}
    orphan = sorted(
        write["path"] for write in planned["writes"]
        if states[write["path"]] == ABSENT and write.get("parent") is not None
    )
    if orphan:
        # Escrita que declara um pai e cujo alvo sumiu: o caso que deveria
        # existir não existe. Isso é anomalia, não prova de que nada ocorreu.
        return _undecidable(
            "a planned update names a target that is no longer in the store",
            {**evidence, "missing": orphan},
        )
    return {"disposition": journal.NO_EFFECT, "evidence": evidence}


def complete_planned(root: Path, record: dict[str, Any]) -> dict[str, Any]:
    """Reconcile the bookkeeping an interrupted operation still owes.

    Both steps are idempotent: ``apply_writes`` skips what already holds the
    planned content, and ``idempotency_key`` returns the existing event instead
    of appending a second one.
    """
    planned = record["planned"]
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


def recover(root: Path, *, operation_id: str | None = None) -> dict[str, Any]:
    """Reconsidera intenções não resolvidas pela regra única declarada.

    Sem ``operation_id``, varre as intenções PENDENTES, como sempre fez. Com
    ``operation_id``, reconsidera exatamente aquela — inclusive uma já
    classificada ``uncertain``, que nesta revisão deixa de ser um destino final
    e volta a ser o que sempre foi: a ausência de uma decisão, revisável quando
    a evidência passa a bastar.

    O alvo é nomeado, e não varrido, de propósito: reabrir em massa a história
    não resolvida de terceiros é decisão de quem a conhece, não da ferramenta.
    """
    def settle(record: dict[str, Any]) -> dict[str, Any]:
        estado = classify_log(log_path(root))
        verdict = disposition(root, record, estado)
        if verdict["disposition"] != journal.EFFECT:
            return verdict
        return {"disposition": journal.EFFECT, "result": complete_planned(root, record)}

    if operation_id is None:
        outcomes = journal.recover(root, settle)
        return {"ok": True, "operation": "recover", "recovered": outcomes}

    record = journal.load(root, operation_id)
    if record is None:
        raise BookError("JOURNAL_MISSING", f"no intent for {operation_id}")
    store_lock = STORE_LOCK_BY_OPERATION.get(record["kind"])
    if store_lock is None:
        raise BookError(
            "JOURNAL_KIND_INVALID",
            f"no reconciler defines the effect surfaces of {record['kind']!r}",
            {"operation_id": operation_id, "kind": record["kind"]},
        )
    # A prova e a transição sob o MESMO lock que a mutação daquele tipo usa:
    # separá-las abriria exatamente a corrida "constatei ausência -> outro
    # escritor confirmou o efeito -> marquei sem efeito".
    with store_lock(root):
        outcomes = journal.recover(root, settle, operation_id=operation_id)
    return {"ok": True, "operation": "recover", "operation_id": operation_id, "recovered": outcomes}
