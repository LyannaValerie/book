"""Critérios de aceite da Issue #4 — associação, cobertura e recuperação.

Cada teste nomeia o critério que demonstra. Os controles positivos existem
tanto quanto os negativos: um gate que só sabe recusar bloqueia trabalho
legítimo, e isso é tão defeito quanto deixar passar.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from booklib import journal, mutation, v0
from booklib.authoring import create_case
from booklib.core import BookError
from booklib.events import classify_log, log_path
from booklib.maintenance import consistency
from booklib.relations import add_relation
from booklib.retention import record as retention_record
from booklib.tasks import begin, finish, require_association

BOOK = Path(__file__).resolve().parents[1] / "book.py"


def semantic(title: str = "Runtime archive stale") -> dict:
    return {
        "title": title,
        "cues": ["runtime", "stale-archive"],
        "scope": {"components": ["runtime"], "conditions": ["native link"]},
        "environment": {"repository": "example/project"},
        "problem": "A stale archive can mask the current runtime.",
        "discriminating_probe": {"action": "rebuild archive", "observable": "archive hash changes"},
        "observed_result": "The rebuilt archive changed the observed result.",
        "guidance": "Rebuild the material dependency before interpreting the test.",
        "contraindications": ["Not applicable when the archive is freshly built."],
        "evidence": [{"class": "OBSERVED", "description": "bounded observation", "source": "event:E-AAAAAAAAAAAA"}],
        "references": [],
        "domain": "Pinker",
        "views": ["Pinker/Tooling"],
    }


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "book"
        self.root.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def cli(self, *args: str, stdin: str | None = None) -> tuple[int, dict]:
        proc = subprocess.run(
            [sys.executable, str(BOOK), "--book", str(self.root), *args],
            input=stdin, text=True, capture_output=True, check=False,
        )
        try:
            return proc.returncode, json.loads(proc.stdout)
        except json.JSONDecodeError:
            return proc.returncode, {"stdout": proc.stdout, "stderr": proc.stderr}

    def task(self, task_id: str = "T-ISSUE4") -> str:
        begin(self.root, "fechar a Issue 4", "book", task_id=task_id)
        return task_id


# ---------------------------------------------------------------------------
# A1 — associação validada na operação compartilhada
# ---------------------------------------------------------------------------

class A1Associacao(Base):
    def test_a1_1_task_inexistente_recusada_antes_de_qualquer_escrita(self) -> None:
        with self.assertRaises(BookError) as erro:
            create_case(self.root, semantic(), task_id="T-NAOEXISTE")
        self.assertEqual(erro.exception.code, "TASK_REF_UNKNOWN")
        self.assertEqual(list((self.root / "cases").glob("*.json")), [])
        self.assertFalse(log_path(self.root).exists())

    def test_a1_1_vale_para_os_cinco_caminhos(self) -> None:
        """A obrigação mora na operação compartilhada, então nenhuma entrada
        equivalente — inclusive `import_case` — a contorna."""
        from booklib import authoring
        entrada = self.root / "draft.json"
        entrada.write_text(json.dumps(semantic()), encoding="utf-8")
        chamadas = [
            lambda: authoring.create_case(self.root, semantic(), task_id="T-X"),
            lambda: authoring.add_case_from_file(self.root, entrada, task_id="T-X"),
            lambda: authoring.import_case(self.root, entrada, task_id="T-X"),
            lambda: authoring.revise(self.root, "B-AAA", "h", entrada, "2026-01-01T00:00:00Z", "a", "r", task_id="T-X"),
            lambda: add_relation(self.root, "B-A", "references", "B-B", "ASSERTED", None, "a", task_id="T-X"),
        ]
        for chamada in chamadas:
            with self.assertRaises(BookError) as erro:
                chamada()
            self.assertEqual(erro.exception.code, "TASK_REF_UNKNOWN")

    def test_a1_2_modo_autonomo_continua_permitido(self) -> None:
        """Controle POSITIVO: exigir associação em toda escrita seria mudança
        de política. Sem Task, a escrita autônoma segue funcionando."""
        resultado = create_case(self.root, semantic())
        self.assertTrue(resultado["ok"])
        self.assertEqual(require_association(self.root, None), None)

    def test_a1_2_require_recusa_a_omissao_so_quando_o_modo_exige(self) -> None:
        with self.assertRaises(BookError) as erro:
            create_case(self.root, semantic(), require_task=True)
        self.assertEqual(erro.exception.code, "TASK_REF_REQUIRED")

    def test_a1_3_operacao_recusada_nao_emite_evento_de_sucesso(self) -> None:
        task = self.task()
        create_case(self.root, semantic(), task_id=task)
        antes = len(classify_log(log_path(self.root))["events"])
        with self.assertRaises(BookError):
            create_case(self.root, semantic(), task_id="T-NAOEXISTE")
        self.assertEqual(len(classify_log(log_path(self.root))["events"]), antes)

    def test_escrita_em_task_encerrada_e_recusada(self) -> None:
        task = self.task()
        finish(self.root, task, "done", assessment={"decision": "no-op", "summary": "nada a reter"})
        with self.assertRaises(BookError) as erro:
            create_case(self.root, semantic(), task_id=task)
        self.assertEqual(erro.exception.code, "TASK_FINISHED")


# ---------------------------------------------------------------------------
# A2 — journal, idempotência e visibilidade
# ---------------------------------------------------------------------------

class A2Journal(Base):
    def test_1_falha_ao_persistir_a_intencao_nao_produz_sucesso(self) -> None:
        with mock.patch.object(journal, "begin", side_effect=OSError("disco")):
            with self.assertRaises(OSError):
                create_case(self.root, semantic())
        self.assertEqual(list((self.root / "cases").glob("*.json")), [])

    def test_1_falha_ao_resolver_deixa_intencao_pendente_e_recuperavel(self) -> None:
        with mock.patch.object(journal, "resolve", side_effect=OSError("disco")):
            with self.assertRaises(OSError):
                create_case(self.root, semantic(), operation_id="OP-FIXA1")
        pendente = journal.load(self.root, "OP-FIXA1")
        self.assertEqual(pendente["state"], journal.PENDING)
        self.assertEqual(consistency(self.root)["consistency"], "UNCERTAIN")
        recuperado = mutation.recover(self.root)
        self.assertEqual([o["outcome"] for o in recuperado["recovered"]], ["completed"])
        self.assertEqual(journal.load(self.root, "OP-FIXA1")["state"], journal.CONFIRMED)

    def test_2_leitor_nao_recebe_estado_parcial_como_confirmado(self) -> None:
        """Intenção pendente é estado APLICADO e NÃO CONFIRMADO. Quem lê o
        relatório de integridade recebe `UNCERTAIN`, não `OK`."""
        with mock.patch.object(journal, "resolve", side_effect=OSError("disco")):
            with self.assertRaises(OSError):
                create_case(self.root, semantic(), operation_id="OP-FIXA2")
        estado = consistency(self.root)
        self.assertEqual(estado["consistency"], "UNCERTAIN")
        self.assertEqual([o["operation_id"] for o in estado["unresolved_operations"]], ["OP-FIXA2"])

    def test_3_mesma_identidade_e_solicitacao_recupera_o_resultado(self) -> None:
        primeiro = create_case(self.root, semantic(), operation_id="OP-FIXA3")
        repetido = create_case(self.root, semantic(), operation_id="OP-FIXA3")
        self.assertTrue(repetido.get("recovered"))
        self.assertEqual(primeiro["id"], repetido["id"])
        self.assertEqual(len(list((self.root / "cases").glob("*.json"))), 1)

    def test_3_recupera_mesmo_depois_de_a_task_encerrar(self) -> None:
        task = self.task()
        primeiro = create_case(self.root, semantic(), task_id=task, operation_id="OP-FIXA4")
        finish(
            self.root, task, "done",
            assessment={"decision": "new", "summary": "retido", "covers": [
                e["event_id"] for e in classify_log(log_path(self.root))["events"] if e["kind"] == "CASE_ADD"
            ]},
        )
        repetido = create_case(self.root, semantic(), task_id=task, operation_id="OP-FIXA4")
        self.assertTrue(repetido.get("recovered"))
        self.assertEqual(primeiro["id"], repetido["id"])

    def test_4_mesma_identidade_com_solicitacao_diferente_e_conflito(self) -> None:
        create_case(self.root, semantic("Primeiro"), operation_id="OP-FIXA5")
        casos = sorted(p.name for p in (self.root / "cases").glob("*.json"))
        eventos = len(classify_log(log_path(self.root))["events"])
        with self.assertRaises(BookError) as erro:
            create_case(self.root, semantic("Outro título"), operation_id="OP-FIXA5", allow_similar=True)
        self.assertEqual(erro.exception.code, "OPERATION_REQUEST_CONFLICT")
        self.assertEqual(sorted(p.name for p in (self.root / "cases").glob("*.json")), casos)
        self.assertEqual(len(classify_log(log_path(self.root))["events"]), eventos)

    def test_5_repetir_a_recuperacao_nao_duplica_nada(self) -> None:
        with mock.patch.object(journal, "resolve", side_effect=OSError("disco")):
            with self.assertRaises(OSError):
                create_case(self.root, semantic(), operation_id="OP-FIXA6")
        mutation.recover(self.root)
        eventos = len(classify_log(log_path(self.root))["events"])
        casos = len(list((self.root / "cases").glob("*.json")))
        mutation.recover(self.root)
        mutation.recover(self.root)
        self.assertEqual(len(classify_log(log_path(self.root))["events"]), eventos)
        self.assertEqual(len(list((self.root / "cases").glob("*.json"))), casos)

    def test_identidade_disponivel_ao_chamador_antes_da_tentativa(self) -> None:
        identidade = journal.new_operation_id()
        self.assertTrue(identidade.startswith("OP-"))
        create_case(self.root, semantic(), operation_id=identidade)
        self.assertEqual(journal.load(self.root, identidade)["state"], journal.CONFIRMED)

    def test_normalizacao_ignora_valores_gerados_pelo_book(self) -> None:
        """Id de caso, horário e hash de revisão são gerados pelo Book; se
        entrassem na solicitação, toda repetição legítima viraria conflito."""
        a = journal.request_digest("case_add", None, {"payload": semantic()})
        b = journal.request_digest("case_add", None, {"payload": semantic()})
        self.assertEqual(a, b)

    def test_operacao_compartilhada_protegida_alem_da_cli(self) -> None:
        """A proteção contra API futura é testada chamando a operação direto,
        sem passar pela CLI."""
        with self.assertRaises(BookError) as erro:
            add_relation(self.root, "B-A", "references", "B-B", "ASSERTED", None, "a", task_id="T-INEXISTENTE")
        self.assertEqual(erro.exception.code, "TASK_REF_UNKNOWN")


# ---------------------------------------------------------------------------
# A2 — injeções nos pontos de escrita
# ---------------------------------------------------------------------------

class A2Injecoes(Base):
    def test_apos_caso_antes_do_catalogo_conclui(self) -> None:
        original = mutation.apply_writes

        def parcial(root, writes):
            original(root, writes[:1])
            raise OSError("queda entre o caso e o catálogo")

        with mock.patch.object(mutation, "apply_writes", parcial):
            with self.assertRaises(OSError):
                create_case(self.root, semantic(), operation_id="OP-INJ1")
        self.assertEqual(len(list((self.root / "cases").glob("*.json"))), 1)
        self.assertEqual(len(list((self.root / "catalog").glob("*.json"))), 0)
        mutation.recover(self.root)
        self.assertEqual(len(list((self.root / "catalog").glob("*.json"))), 1)
        self.assertEqual(journal.load(self.root, "OP-INJ1")["state"], journal.CONFIRMED)

    def test_divergencia_incompativel_produz_incerto(self) -> None:
        """Estado observado que não é nem o pai nem o resultado da intenção é
        mudança que ninguém contabilizou: classifica, nunca sobrescreve."""
        with mock.patch.object(mutation, "apply_writes", side_effect=OSError("queda")):
            with self.assertRaises(OSError):
                create_case(self.root, semantic(), operation_id="OP-INJ2")
        planejado = journal.load(self.root, "OP-INJ2")["planned"]
        caminho = self.root / planejado["writes"][0]["path"]
        caminho.parent.mkdir(parents=True, exist_ok=True)
        v0.atomic_write(caminho, {**planejado["writes"][0]["value"], "title": "outra coisa"})
        mutation.recover(self.root)
        self.assertEqual(journal.load(self.root, "OP-INJ2")["state"], journal.UNCERTAIN)
        self.assertEqual(consistency(self.root)["consistency"], "UNCERTAIN")

    def test_apos_evento_antes_de_resolver_conclui_sem_duplicar(self) -> None:
        with mock.patch.object(journal, "resolve", side_effect=OSError("queda")):
            with self.assertRaises(OSError):
                create_case(self.root, semantic(), operation_id="OP-INJ3")
        eventos = [e for e in classify_log(log_path(self.root))["events"] if e["kind"] == "CASE_ADD"]
        self.assertEqual(len(eventos), 1)
        mutation.recover(self.root)
        eventos = [e for e in classify_log(log_path(self.root))["events"] if e["kind"] == "CASE_ADD"]
        self.assertEqual(len(eventos), 1)

    def test_escrita_curta_no_append_completa_o_registro(self) -> None:
        from booklib import events as modulo
        real = __import__("os").write
        estado = {"primeira": True}

        def curta(fd, data):
            if estado["primeira"] and len(data) > 10:
                estado["primeira"] = False
                return real(fd, data[:5])
            return real(fd, data)

        with mock.patch.object(modulo.os, "write", curta):
            create_case(self.root, semantic())
        self.assertEqual(classify_log(log_path(self.root))["status"], "OK")

    def test_laco_de_escrita_nao_gira_sem_progresso(self) -> None:
        from booklib import events as modulo
        with self.assertRaises(BookError) as erro:
            modulo.write_all(-1, b"x") if False else None
            with mock.patch.object(modulo.os, "write", return_value=0):
                modulo.write_all(1, b"payload")
        self.assertEqual(erro.exception.code, "EVENT_WRITE_STALLED")

    def test_cauda_incompleta_bloqueia_mutacao_e_declara_incompletude(self) -> None:
        create_case(self.root, semantic())
        with log_path(self.root).open("a", encoding="utf-8") as fluxo:
            fluxo.write('{"event_id":"E-TRUNCA')
        estado = classify_log(log_path(self.root))
        self.assertEqual(estado["status"], "INCOMPLETE_TAIL")
        self.assertEqual(len(estado["events"]), estado["verified"])
        with self.assertRaises(BookError) as erro:
            create_case(self.root, semantic("Outro"), allow_similar=True)
        self.assertEqual(erro.exception.code, "EVENT_LOG_NOT_APPENDABLE")

    def test_corrupcao_no_meio_e_classe_distinta_da_cauda(self) -> None:
        create_case(self.root, semantic())
        create_case(self.root, semantic("Segundo"), allow_similar=True)
        linhas = log_path(self.root).read_text(encoding="utf-8").splitlines()
        linhas[0] = "{isto nao e json"
        log_path(self.root).write_text("\n".join(linhas) + "\n", encoding="utf-8")
        self.assertEqual(classify_log(log_path(self.root))["status"], "CORRUPT")

    def test_registro_integro_com_hash_invalido_e_terceira_classe(self) -> None:
        create_case(self.root, semantic())
        linhas = log_path(self.root).read_text(encoding="utf-8").splitlines()
        evento = json.loads(linhas[-1]); evento["hash"] = "0" * 64
        linhas[-1] = json.dumps(evento, sort_keys=True, separators=(",", ":"))
        log_path(self.root).write_text("\n".join(linhas) + "\n", encoding="utf-8")
        self.assertEqual(classify_log(log_path(self.root))["status"], "HASH_INVALID")

    def test_quarentena_preserva_bytes_e_origem(self) -> None:
        create_case(self.root, semantic())
        bytes_originais = log_path(self.root).read_bytes()
        from booklib.events import quarantine
        destino = quarantine(self.root, "corrupção observada no teste")
        self.assertEqual(destino.read_bytes(), bytes_originais)
        origem = json.loads(destino.with_suffix("").with_suffix(".origin.json").read_text(encoding="utf-8"))
        self.assertEqual(origem["reason"], "corrupção observada no teste")


# ---------------------------------------------------------------------------
# A2-bis — vinculação à revisão e fronteira de adoção
# ---------------------------------------------------------------------------

class A2BisFronteira(Base):
    def test_1_operacao_nova_registra_a_revisao_resultante(self) -> None:
        resultado = create_case(self.root, semantic())
        evento = [e for e in classify_log(log_path(self.root))["events"] if e["kind"] == "CASE_ADD"][-1]
        self.assertEqual(evento["data"]["revision"], resultado["revision"])

    def test_2_verify_distingue_lacuna_historica_de_inconsistencia(self) -> None:
        create_case(self.root, semantic())
        # Um evento SEM revisão representa o protocolo anterior.
        from booklib.events import append_event
        append_event(self.root, "CASE_ADD", task_id=None, summary="B-LEGADO", data={"case_id": "B-LEGADO"})
        estado = consistency(self.root)
        self.assertEqual(estado["consistency"], "OK")
        self.assertIn("B-LEGADO", [item["case_id"] for item in estado["pre_frontier"]["unverifiable"]])
        self.assertIsNotNone(estado["adoption_frontier"])

    def test_3a_ausencia_historica_isolada_nao_bloqueia(self) -> None:
        """Controle POSITIVO da recíproca: falta de evidência que o protocolo
        antigo não produzia classifica, não bloqueia."""
        from booklib.events import append_event
        append_event(self.root, "CASE_ADD", task_id=None, summary="B-LEGADO", data={"case_id": "B-LEGADO"})
        estado = consistency(self.root)
        self.assertEqual(estado["consistency"], "OK")
        self.assertTrue(estado["pre_frontier"]["unverifiable"])

    def test_3b_corrupcao_comprovada_bloqueia_mesmo_sendo_anterior(self) -> None:
        """E o outro lado: corrupção é fato, não lacuna, e bloqueia onde
        estiver."""
        from booklib.events import append_event
        append_event(self.root, "CASE_ADD", task_id=None, summary="B-LEGADO", data={"case_id": "B-LEGADO"})
        linhas = log_path(self.root).read_text(encoding="utf-8").splitlines()
        evento = json.loads(linhas[0]); evento["hash"] = "0" * 64
        linhas[0] = json.dumps(evento, sort_keys=True, separators=(",", ":"))
        log_path(self.root).write_text("\n".join(linhas) + "\n", encoding="utf-8")
        self.assertEqual(consistency(self.root)["consistency"], "BLOCKED")

    def test_ok_nao_certifica_historia_inverificavel(self) -> None:
        from booklib.events import append_event
        append_event(self.root, "CASE_ADD", task_id=None, summary="B-LEGADO", data={"case_id": "B-LEGADO"})
        estado = consistency(self.root)
        self.assertEqual(estado["consistency"], "OK")
        self.assertNotEqual(estado["pre_frontier"]["unverifiable"], [])

    def test_as_duas_fronteiras_sao_distintas(self) -> None:
        create_case(self.root, semantic())
        primeira = consistency(self.root)
        create_case(self.root, semantic("Segundo"), allow_similar=True)
        segunda = consistency(self.root)
        self.assertNotEqual(primeira["frontier"], segunda["frontier"])
        self.assertEqual(primeira["adoption_frontier"], segunda["adoption_frontier"])


# ---------------------------------------------------------------------------
# A3 — cobertura de retenção
# ---------------------------------------------------------------------------

class A3Cobertura(Base):
    def eventos(self) -> list[dict]:
        return classify_log(log_path(self.root))["events"]

    def test_1_covers_invalido_e_recusado(self) -> None:
        task = self.task()
        create_case(self.root, semantic(), task_id=task)
        adicao = [e for e in self.eventos() if e["kind"] == "CASE_ADD"][0]["event_id"]
        outro = self.task("T-OUTRA")
        create_case(self.root, semantic("Outro"), task_id=outro, allow_similar=True)
        alheio = [e for e in self.eventos() if e.get("task_id") == outro and e["kind"] == "CASE_ADD"][0]["event_id"]
        acesso = [e for e in self.eventos() if e["kind"] not in {"CASE_ADD", "RETENTION"}]
        casos = [("E-NAOEXISTE", "COVERS_EVENT_UNKNOWN"), (alheio, "COVERS_EVENT_FOREIGN")]
        if acesso:
            casos.append((acesso[0]["event_id"], "COVERS_EVENT_INELIGIBLE"))
        for event_id, codigo in casos:
            with self.assertRaises(BookError) as erro:
                retention_record(self.root, task, "new", "resumo", None, [event_id])
            self.assertEqual(erro.exception.code, codigo)
        self.assertTrue(adicao)

    def test_2_cobertura_repetida_e_idempotente(self) -> None:
        task = self.task()
        create_case(self.root, semantic(), task_id=task)
        adicao = [e for e in self.eventos() if e["kind"] == "CASE_ADD"][0]["event_id"]
        retention_record(self.root, task, "new", "resumo", None, [adicao, adicao])
        evento = [e for e in self.eventos() if e["kind"] == "RETENTION"][-1]
        self.assertEqual(evento["data"]["covers"], [adicao])

    def test_3_criar_reter_revisar_finalizar_e_recusado(self) -> None:
        """A sequência que a diferença de conjuntos por `case_id` não pegava."""
        task = self.task()
        caso = create_case(self.root, semantic(), task_id=task)
        adicao = [e for e in self.eventos() if e["kind"] == "CASE_ADD"][0]["event_id"]
        retention_record(self.root, task, "new", "retido", caso["id"], [adicao])
        remendo = self.root / "patch.json"
        remendo.write_text(json.dumps({"title": "Runtime archive stale — revisado"}), encoding="utf-8")
        from booklib.authoring import revise
        revise(self.root, caso["id"], caso["revision"], remendo, "2026-01-02T00:00:00Z", "amara", "ajuste", task_id=task)
        with self.assertRaises(BookError) as erro:
            finish(self.root, task, "done", assessment={"decision": "no-op", "summary": "nada mais"})
        self.assertEqual(erro.exception.code, "RETENTION_UNRECONCILED")
        self.assertEqual(len(erro.exception.details["uncovered"]), 1)


# ---------------------------------------------------------------------------
# A4 — avaliação vinculada à tentativa
# ---------------------------------------------------------------------------

class A4Finalizacao(Base):
    def test_1_finish_recusa_com_uncovered(self) -> None:
        task = self.task()
        create_case(self.root, semantic(), task_id=task)
        with self.assertRaises(BookError) as erro:
            finish(self.root, task, "done", assessment={"decision": "no-op", "summary": "esqueci"})
        self.assertEqual(erro.exception.code, "RETENTION_UNRECONCILED")

    def test_2_noop_anterior_a_tentativa_nao_satisfaz(self) -> None:
        task = self.task()
        retention_record(self.root, task, "no-op", "decidido cedo demais")
        with self.assertRaises(BookError) as erro:
            finish(self.root, task, "done")
        self.assertEqual(erro.exception.code, "RETENTION_UNRECONCILED")

    def test_3_avaliacao_nao_e_inferivel_de_uncovered_vazio(self) -> None:
        """Zero mutações não dispensa a avaliação. Sem entrada explícita do
        chamador, `finish` recusa — é isto que impede o `no-op` automático."""
        task = self.task()
        self.assertEqual(
            [e for e in classify_log(log_path(self.root))["events"] if e["kind"] == "CASE_ADD"], []
        )
        with self.assertRaises(BookError) as erro:
            finish(self.root, task, "done")
        self.assertEqual(erro.exception.code, "RETENTION_UNRECONCILED")

    def test_4_noop_fundamentado_na_tentativa_encerra(self) -> None:
        """Controle POSITIVO: ausência de aprendizado reutilizável não obriga a
        fabricar caso."""
        task = self.task()
        resultado = finish(self.root, task, "done", assessment={"decision": "no-op", "summary": "nada reutilizável nesta Task"})
        self.assertEqual(resultado["task"]["state"], "finished")
        evento = [e for e in classify_log(log_path(self.root))["events"] if e["kind"] == "RETENTION"][-1]
        self.assertEqual(evento["data"]["attempt"], resultado["attempt"])

    def test_5_escrita_apos_encerramento_e_recusada(self) -> None:
        task = self.task()
        finish(self.root, task, "done", assessment={"decision": "no-op", "summary": "nada"})
        with self.assertRaises(BookError) as erro:
            create_case(self.root, semantic(), task_id=task)
        self.assertEqual(erro.exception.code, "TASK_FINISHED")

    def test_6_reabertura_indisponivel_sem_caminho_de_contorno(self) -> None:
        """Disposição (b) da Issue: reabertura não existe. O teste prova que
        não há superfície pela metade — nem comando, nem rota alternativa."""
        task = self.task()
        finish(self.root, task, "done", assessment={"decision": "no-op", "summary": "nada"})
        codigo, saida = self.cli("task", "reopen", task)
        self.assertNotEqual(codigo, 0)
        from booklib import tasks
        self.assertFalse(hasattr(tasks, "reopen"))
        for entrada in (lambda: create_case(self.root, semantic(), task_id=task),
                        lambda: retention_record(self.root, task, "new", "x")):
            with self.assertRaises(BookError):
                entrada()

    def test_retencao_durante_a_task_e_permitida(self) -> None:
        """Controle POSITIVO: A4 não proíbe reter durante o trabalho. Ela
        impede que uma decisão anterior SUBSTITUA a avaliação explícita da
        tentativa de finalização — são coisas diferentes, e adiar toda retenção
        para o fim não tem fundamento contratual."""
        from booklib.authoring import create_case as criar
        task = self.task()
        primeiro = criar(self.root, semantic(), task_id=task)
        adicao = [e for e in classify_log(log_path(self.root))["events"] if e["kind"] == "CASE_ADD"][0]["event_id"]

        # reter no meio do trabalho, cobrindo o que já foi aprendido
        retention_record(self.root, task, "new", "aprendizagem validada no meio", primeiro["id"], [adicao])

        # e seguir trabalhando
        segundo = criar(self.root, semantic("Outra dificuldade"), task_id=task, allow_similar=True)
        adicao2 = [e for e in classify_log(log_path(self.root))["events"] if e["kind"] == "CASE_ADD"][1]["event_id"]
        from booklib.retention import status
        self.assertEqual(status(self.root, task)["uncovered"], [adicao2])

        # na finalização, reconciliar o restante COM avaliação explícita
        fim = finish(self.root, task, "done", assessment={
            "decision": "new", "summary": "restante reconciliado", "case_id": segundo["id"], "covers": [adicao2],
        })
        self.assertEqual(fim["task"]["state"], "finished")
        self.assertTrue(status(self.root, task)["reconciled"])

    def test_concorrencia_mutacao_antes_da_finalizacao(self) -> None:
        """Ordem serializada 1: a avaliação anterior perde validade."""
        task = self.task()
        create_case(self.root, semantic(), task_id=task)
        with self.assertRaises(BookError) as erro:
            finish(self.root, task, "done", assessment={"decision": "no-op", "summary": "avaliei antes da escrita"})
        self.assertEqual(erro.exception.code, "RETENTION_UNRECONCILED")

    def test_concorrencia_finalizacao_antes_da_mutacao(self) -> None:
        """Ordem serializada 2: a Task encerra e a escrita posterior é
        recusada. Exigir recusa do `finish` nos dois casos reprovaria uma
        execução corretamente serializada."""
        task = self.task()
        resultado = finish(self.root, task, "done", assessment={"decision": "no-op", "summary": "nada"})
        self.assertEqual(resultado["task"]["state"], "finished")
        with self.assertRaises(BookError) as erro:
            create_case(self.root, semantic(), task_id=task)
        self.assertEqual(erro.exception.code, "TASK_FINISHED")


# ---------------------------------------------------------------------------
# Limites de durabilidade — o que a evidência realmente alcança
# ---------------------------------------------------------------------------

class LimitesDeDurabilidade(Base):
    def test_falha_de_processo_morte_real_sem_desenrolar_pilha(self) -> None:
        """Classe FALHA DE PROCESSO, demonstrada com `os._exit`: sem exceção,
        sem `finally`, sem desenrolar de pilha. Não é perda de energia — essa
        continua não demonstrada, e está declarada como tal."""
        roteiro = (
            "import os, sys, json\n"
            "from pathlib import Path\n"
            "sys.path.insert(0, %r)\n"
            "from booklib import journal\n"
            "from booklib.authoring import create_case\n"
            "journal.resolve = lambda *a, **k: os._exit(9)\n"
            "create_case(Path(%r), json.loads(sys.argv[1]), operation_id='OP-MORTE')\n"
        ) % (str(Path(__file__).resolve().parents[1]), str(self.root))
        processo = subprocess.run(
            [sys.executable, "-c", roteiro, json.dumps(semantic())],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(processo.returncode, 9, processo.stderr)

        # Estado aplicado e NÃO confirmado: nenhum consumidor pode lê-lo como
        # operação concluída.
        pendente = journal.load(self.root, "OP-MORTE")
        self.assertEqual(pendente["state"], journal.PENDING)
        self.assertEqual(consistency(self.root)["consistency"], "UNCERTAIN")

        # Recuperação determinística, idempotente, com registro próprio.
        resultado = mutation.recover(self.root)
        self.assertEqual([o["outcome"] for o in resultado["recovered"]], ["completed"])
        confirmado = journal.load(self.root, "OP-MORTE")
        self.assertEqual(confirmado["state"], journal.CONFIRMED)
        self.assertEqual(confirmado["recovery"]["by"], "recovery")
        self.assertEqual(consistency(self.root)["consistency"], "OK")
        self.assertEqual(len(list((self.root / "cases").glob("*.json"))), 1)

    def test_classes_nao_demonstradas_permanecem_declaradas(self) -> None:
        """Este teste não prova durabilidade: ele fixa o ENUNCIADO do que a
        suíte alcança, para que a entrega não generalize o que mediu."""
        alcancado = {"falha de operação", "falha de processo"}
        nao_demonstrado = {"perda de energia", "cache de disco que mente sobre fsync", "falha de mídia"}
        self.assertEqual(alcancado & nao_demonstrado, set())


if __name__ == "__main__":
    unittest.main()
