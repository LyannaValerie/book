"""Reconsideração determinística de uma intenção classificada `uncertain`.

`uncertain` diz que a EVIDÊNCIA não decidia, não que a operação é
irrecuperável. Quando o Book pode provar, por evidência canônica durável, que
a mutação pretendida não ocorreu, manter a intenção no conjunto não resolvido
não é prudência: é confundir INTENÇÃO não resolvida com MUTAÇÃO não resolvida,
e contar a segunda onde só existe a primeira.

A correção tem dois lados, e o segundo é o que a torna segura:

```text
UNCERTAIN != PERMANENTLY_UNRECOVERABLE
UNCERTAIN != ASSUME_NO_EFFECT
```

Por isso cada teste aqui tem um par. Os que demonstram a resolução vêm
acompanhados dos que demonstram a RECUSA de resolver — ambiguidade, log
corrompido, alvo sumido. Uma recuperação que só sabe fechar operação fecha
também a que não devia.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from booklib import journal, mutation, v0
from booklib.authoring import create_case
from booklib.core import BookError
from booklib.events import classify_log, log_path, quarantine
from booklib.maintenance import consistency, verify


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

    # -- contagens das superfícies canônicas ---------------------------------

    def cases(self) -> int:
        return len(list((self.root / "cases").glob("*.json")))

    def catalog(self) -> int:
        return len(list((self.root / "catalog").glob("*.json")))

    def relations(self) -> int:
        return len(list((self.root / "relations").glob("*.json")))

    def events_of(self, kind: str) -> list[dict]:
        return [e for e in classify_log(log_path(self.root))["events"] if e["kind"] == kind]

    def counts(self) -> tuple[int, int, int, int]:
        return (self.cases(), self.catalog(), self.relations(), len(classify_log(log_path(self.root))["events"]))

    def canonical_bytes(self) -> dict[str, bytes]:
        """Bytes das superfícies canônicas — caso, catálogo, relação, evento.

        O journal fica de fora de propósito: ele é o registro da decisão, e
        registrar que não se decidiu é mudança legítima. O que não pode mudar
        quando a recuperação recusa decidir é o CONHECIMENTO.
        """
        snapshot: dict[str, bytes] = {}
        for directory in ("cases", "catalog", "relations", "events"):
            base = self.root / directory
            for path in sorted(base.rglob("*")) if base.exists() else []:
                if path.is_file():
                    snapshot[str(path.relative_to(self.root))] = path.read_bytes()
        return snapshot

    def unresolved(self) -> list[str]:
        return [r["operation_id"] for r in journal.unresolved(self.root)]

    # -- construção do estado testemunha -------------------------------------

    def ghost_intent(self, operation_id: str, payload: dict | None = None) -> dict:
        """Reproduz a forma do PC-01: intenção durável, plano ausente, nenhum
        efeito canônico, estado `uncertain`.

        A queda é injetada exatamente onde a testemunha real caiu — antes de o
        plano se tornar durável. A classificação `uncertain` é então aplicada
        pela API do journal porque o caminho de código que a produzia é o que
        esta correção remove: reconstruir o estado em que o Book operacional
        realmente está é o ponto do teste, não reencená-lo pelo defeito.
        """
        with mock.patch.object(journal, "update", side_effect=OSError("queda antes de o plano durar")):
            with self.assertRaises(OSError):
                create_case(self.root, payload or semantic(), operation_id=operation_id, allow_similar=True)
        registro = journal.load(self.root, operation_id)
        self.assertEqual(registro["state"], journal.PENDING)
        self.assertNotIn("planned", registro)
        journal.mark_uncertain(self.root, operation_id, "observed state diverges from the recorded intent")
        testemunha = journal.load(self.root, operation_id)
        self.assertEqual(testemunha["state"], journal.UNCERTAIN)
        self.assertIsNone(testemunha["result"])
        self.assertNotIn("planned", testemunha)
        return testemunha


# ---------------------------------------------------------------------------
# A — a forma do PC-01
# ---------------------------------------------------------------------------

class AFormaDoPC01(Base):
    def test_intencao_sem_plano_e_sem_efeito_resolve_como_sem_efeito(self) -> None:
        self.ghost_intent("OP-PC01")
        antes = self.counts()
        self.assertIn("OP-PC01", self.unresolved())

        resultado = mutation.recover(self.root, operation_id="OP-PC01")

        self.assertEqual([o["outcome"] for o in resultado["recovered"]], [journal.RECOVERED_NO_EFFECT])
        self.assertNotIn("OP-PC01", self.unresolved())
        registro = journal.load(self.root, "OP-PC01")
        self.assertEqual(registro["state"], journal.ABORTED)
        self.assertEqual(registro["recovery"]["disposition"], journal.NO_EFFECT)
        self.assertEqual(self.counts(), antes)
        self.assertEqual(verify(self.root)[0]["ok"], True)
        self.assertEqual(consistency(self.root)["consistency"], "OK")

    def test_a_prova_nomeia_as_superficies_e_a_regra_que_a_sustenta(self) -> None:
        """A conclusão precisa ser auditável sem reexecutar a ferramenta."""
        self.ghost_intent("OP-PC01")
        mutation.recover(self.root, operation_id="OP-PC01")
        prova = journal.load(self.root, "OP-PC01")["recovery"]["evidence"]
        self.assertEqual(prova["rule"], "R1_PLAN_DURABLE_BEFORE_ANY_WRITE")
        self.assertEqual(prova["plan"], "absent")
        self.assertEqual(prova["attributed_events"], [])
        self.assertEqual(prova["event_log_status"], "OK")
        self.assertEqual(
            prova["effect_surfaces"],
            ["cases/<id>.json", "catalog/<id>.json", "events:CASE_ADD"],
        )

    def test_o_historico_original_sobrevive_a_resolucao(self) -> None:
        self.ghost_intent("OP-PC01")
        antes = journal.load(self.root, "OP-PC01")
        mutation.recover(self.root, operation_id="OP-PC01")
        depois = journal.load(self.root, "OP-PC01")
        self.assertEqual(depois["operation_id"], antes["operation_id"])
        self.assertEqual(depois["opened_at"], antes["opened_at"])
        self.assertEqual(depois["request_digest"], antes["request_digest"])
        self.assertEqual(depois["history"][-1]["state"], journal.UNCERTAIN)
        self.assertEqual(depois["history"][-1]["recovery"], antes["recovery"])

    def test_recuperacao_nao_inventa_horario_de_conclusao(self) -> None:
        self.ghost_intent("OP-PC01")
        registro = mutation.recover(self.root, operation_id="OP-PC01") and journal.load(self.root, "OP-PC01")
        self.assertIsNone(registro["result"])
        self.assertIsNone(registro["recovery"]["original_completion"])
        self.assertEqual(registro["resolved_at"], registro["recovery"]["at"])
        self.assertEqual(registro["recovery"]["by"], "recovery")
        self.assertIn("tool_version", registro["recovery"])


# ---------------------------------------------------------------------------
# B — retentativa bem-sucedida depois de um `uncertain` sem efeito
# ---------------------------------------------------------------------------

class BRetentativa(Base):
    def test_a_retentativa_permanece_autoritativa_e_a_primeira_fecha_sem_efeito(self) -> None:
        primeira = self.ghost_intent("OP-PRIMEIRA")
        confirmada = create_case(
            self.root, semantic("Outro problema, outra solicitação"),
            operation_id="OP-RETENTATIVA", allow_similar=True,
        )
        segunda = journal.load(self.root, "OP-RETENTATIVA")
        self.assertNotEqual(primeira["request_digest"], segunda["request_digest"])

        mutation.recover(self.root, operation_id="OP-PRIMEIRA")

        self.assertEqual(self.cases(), 1)
        self.assertEqual(self.catalog(), 1)
        adicoes = self.events_of("CASE_ADD")
        self.assertEqual(len(adicoes), 1)
        # A atribuição é por identidade da operação, nunca por proximidade
        # temporal: o único CASE_ADD pertence à retentativa.
        self.assertEqual(adicoes[0]["data"]["operation_id"], "OP-RETENTATIVA")
        self.assertEqual(adicoes[0]["data"]["case_id"], confirmada["id"])
        self.assertEqual(journal.load(self.root, "OP-RETENTATIVA")["state"], journal.CONFIRMED)
        self.assertEqual(journal.load(self.root, "OP-PRIMEIRA")["state"], journal.ABORTED)
        self.assertEqual(self.unresolved(), [])

    def test_o_evento_que_a_primeira_teria_produzido_nao_existe(self) -> None:
        """Discriminação por identidade construída, e não por vizinhança."""
        primeira = self.ghost_intent("OP-PRIMEIRA")
        create_case(self.root, semantic("Outro"), operation_id="OP-RETENTATIVA", allow_similar=True)
        esperado = mutation.event_identity(primeira["task_id"], "OP-PRIMEIRA")
        presentes = {e["event_id"] for e in classify_log(log_path(self.root))["events"]}
        self.assertNotIn(esperado, presentes)
        self.assertNotEqual(esperado, self.events_of("CASE_ADD")[0]["event_id"])


# ---------------------------------------------------------------------------
# C — `uncertain` genuinamente ambíguo: a recuperação recusa decidir
# ---------------------------------------------------------------------------

class CAmbiguidade(Base):
    def uncertain_divergente(self, operation_id: str) -> None:
        """Plano decidido, escrita interrompida, e a superfície contendo
        conteúdo que não é nem o pai nem o resultado da intenção."""
        with mock.patch.object(mutation, "apply_writes", side_effect=OSError("queda")):
            with self.assertRaises(OSError):
                create_case(self.root, semantic(), operation_id=operation_id)
        planejado = journal.load(self.root, operation_id)["planned"]
        caminho = self.root / planejado["writes"][0]["path"]
        caminho.parent.mkdir(parents=True, exist_ok=True)
        v0.atomic_write(caminho, {**planejado["writes"][0]["value"], "title": "mudança que ninguém contabilizou"})
        mutation.recover(self.root)
        self.assertEqual(journal.load(self.root, operation_id)["state"], journal.UNCERTAIN)

    def test_superficie_nao_contabilizada_permanece_incerta(self) -> None:
        self.uncertain_divergente("OP-AMBIGUA")
        antes_bytes = self.canonical_bytes()
        antes_nao_resolvidas = self.unresolved()

        resultado = mutation.recover(self.root, operation_id="OP-AMBIGUA")

        self.assertEqual([o["outcome"] for o in resultado["recovered"]], [journal.UNCERTAIN])
        self.assertEqual(journal.load(self.root, "OP-AMBIGUA")["state"], journal.UNCERTAIN)
        self.assertEqual(self.unresolved(), antes_nao_resolvidas)
        self.assertEqual(self.canonical_bytes(), antes_bytes)

    def test_a_recusa_nomeia_a_superficie_que_a_impediu(self) -> None:
        self.uncertain_divergente("OP-AMBIGUA")
        resultado = mutation.recover(self.root, operation_id="OP-AMBIGUA")
        prova = resultado["recovered"][0]["evidence"]
        self.assertEqual(list(prova["unaccounted"]), sorted(prova["unaccounted"]))
        self.assertTrue(prova["unaccounted"][0].startswith("cases/"))
        self.assertEqual(prova["surface_states"][prova["unaccounted"][0]], mutation.FOREIGN)

    def test_intencao_sem_plano_com_evento_atribuido_e_contradicao_nao_prova(self) -> None:
        """A ausência do plano prova ausência de efeito porque R1 ordena as
        duas coisas. Um evento atribuído à operação REFUTA essa ordenação — e
        evidência que se contradiz não autoriza nenhuma das conclusões."""
        self.ghost_intent("OP-CONTRADITORIA")
        registro = journal.load(self.root, "OP-CONTRADITORIA")
        from booklib.events import append_event
        append_event(
            self.root, "CASE_ADD", task_id=registro["task_id"], summary="B-INVENTADO",
            data={"case_id": "B-INVENTADO", "operation_id": "OP-CONTRADITORIA"},
            idempotency_key="OP-CONTRADITORIA",
        )
        resultado = mutation.recover(self.root, operation_id="OP-CONTRADITORIA")
        self.assertEqual([o["outcome"] for o in resultado["recovered"]], [journal.UNCERTAIN])
        self.assertIn("OP-CONTRADITORIA", self.unresolved())

    def test_alvo_de_atualizacao_que_sumiu_nao_e_prova_de_que_nada_ocorreu(self) -> None:
        """Escrita que declara um pai e cujo alvo não está mais no acervo: o
        caso deveria existir e não existe. Anomalia, não ausência de efeito."""
        criado = create_case(self.root, semantic(), operation_id="OP-CRIADO")
        with mock.patch.object(mutation, "apply_writes", side_effect=OSError("queda")):
            with self.assertRaises(OSError):
                mutation.guarded(
                    self.root, kind="case_revise", event_kind="CASE_REVISE", task_id=None,
                    require_task=False, operation_id="OP-REVISAO",
                    request={"case_id": criado["id"]},
                    store_lock=lambda: v0.exclusive_book_lock(self.root),
                    plan=lambda: {
                        "writes": [{
                            "path": f"cases/{criado['id']}.json",
                            "value": {**v0.find_case(self.root, criado["id"]), "title": "revisto"},
                            "parent": criado["revision"],
                        }],
                        "event": {"summary": criado["id"], "data": {"case_id": criado["id"]}},
                        "result": {"ok": True, "operation": "revise", "id": criado["id"]},
                    },
                )
        journal.mark_uncertain(self.root, "OP-REVISAO", "observed state diverges from the recorded intent")
        (self.root / "cases" / f"{criado['id']}.json").unlink()

        resultado = mutation.recover(self.root, operation_id="OP-REVISAO")
        self.assertEqual([o["outcome"] for o in resultado["recovered"]], [journal.UNCERTAIN])
        self.assertIn("missing", resultado["recovered"][0]["evidence"])


# ---------------------------------------------------------------------------
# D — idempotência
# ---------------------------------------------------------------------------

class DIdempotencia(Base):
    def test_segunda_recuperacao_e_no_op(self) -> None:
        self.ghost_intent("OP-PC01")
        mutation.recover(self.root, operation_id="OP-PC01")
        primeira = journal.load(self.root, "OP-PC01")
        antes = self.counts()

        segunda = mutation.recover(self.root, operation_id="OP-PC01")

        self.assertEqual([o["outcome"] for o in segunda["recovered"]], [journal.ALREADY_RESOLVED])
        self.assertEqual(segunda["recovered"][0]["state"], journal.ABORTED)
        self.assertEqual(self.counts(), antes)
        self.assertEqual(journal.load(self.root, "OP-PC01"), primeira)

    def test_terceira_recuperacao_nao_acumula_registro_de_recuperacao(self) -> None:
        self.ghost_intent("OP-PC01")
        mutation.recover(self.root, operation_id="OP-PC01")
        historico = len(journal.load(self.root, "OP-PC01")["history"])
        mutation.recover(self.root, operation_id="OP-PC01")
        mutation.recover(self.root, operation_id="OP-PC01")
        self.assertEqual(len(journal.load(self.root, "OP-PC01")["history"]), historico)

    def test_varredura_sem_seletor_nao_reabre_o_que_ja_fechou(self) -> None:
        self.ghost_intent("OP-PC01")
        mutation.recover(self.root, operation_id="OP-PC01")
        antes = self.counts()
        mutation.recover(self.root)
        self.assertEqual(self.counts(), antes)
        self.assertEqual(journal.load(self.root, "OP-PC01")["state"], journal.ABORTED)


# ---------------------------------------------------------------------------
# E — a semântica de `pending` permanece a que era
# ---------------------------------------------------------------------------

class EPendentePreservado(Base):
    def test_pendente_com_efeito_aplicado_continua_concluindo(self) -> None:
        with mock.patch.object(journal, "resolve", side_effect=OSError("disco")):
            with self.assertRaises(OSError):
                create_case(self.root, semantic(), operation_id="OP-PENDENTE")
        self.assertEqual(journal.load(self.root, "OP-PENDENTE")["state"], journal.PENDING)

        resultado = mutation.recover(self.root)

        self.assertEqual([o["outcome"] for o in resultado["recovered"]], [journal.COMPLETED])
        self.assertEqual(journal.load(self.root, "OP-PENDENTE")["state"], journal.CONFIRMED)
        self.assertEqual(journal.load(self.root, "OP-PENDENTE")["recovery"]["by"], "recovery")
        self.assertEqual(len(self.events_of("CASE_ADD")), 1)
        self.assertEqual(self.cases(), 1)

    def test_a_varredura_sem_seletor_nao_reabre_intencoes_incertas(self) -> None:
        """Reabrir em massa a história não resolvida de terceiros não é decisão
        da ferramenta. O seletor existe para que isso seja estrutural."""
        self.ghost_intent("OP-INTOCADA")
        antes = journal.load(self.root, "OP-INTOCADA")

        mutation.recover(self.root)

        self.assertEqual(journal.load(self.root, "OP-INTOCADA"), antes)
        self.assertIn("OP-INTOCADA", self.unresolved())

    def test_operacao_desconhecida_e_erro_e_nao_silencio(self) -> None:
        with self.assertRaises(BookError) as erro:
            mutation.recover(self.root, operation_id="OP-NAOEXISTE")
        self.assertEqual(erro.exception.code, "JOURNAL_MISSING")


# ---------------------------------------------------------------------------
# F — corrupção e quarentena não viram "sem efeito"
# ---------------------------------------------------------------------------

class FCorrupcaoPreservada(Base):
    def test_cauda_incompleta_impede_a_conclusao_de_ausencia(self) -> None:
        create_case(self.root, semantic("Primeiro"))
        self.ghost_intent("OP-PC01", semantic("Segundo"))
        with log_path(self.root).open("a", encoding="utf-8") as fluxo:
            fluxo.write('{"event_id":"E-TRUNCA')
        self.assertEqual(classify_log(log_path(self.root))["status"], "INCOMPLETE_TAIL")

        resultado = mutation.recover(self.root, operation_id="OP-PC01")

        self.assertEqual([o["outcome"] for o in resultado["recovered"]], [journal.UNCERTAIN])
        self.assertEqual(journal.load(self.root, "OP-PC01")["state"], journal.UNCERTAIN)
        self.assertIn("OP-PC01", self.unresolved())

    def test_corrupcao_no_meio_impede_a_conclusao_de_ausencia(self) -> None:
        create_case(self.root, semantic("Primeiro"))
        self.ghost_intent("OP-PC01", semantic("Segundo"))
        linhas = log_path(self.root).read_text(encoding="utf-8").splitlines()
        linhas[0] = "{isto nao e json"
        log_path(self.root).write_text("\n".join(linhas) + "\n", encoding="utf-8")
        self.assertEqual(classify_log(log_path(self.root))["status"], "CORRUPT")

        resultado = mutation.recover(self.root, operation_id="OP-PC01")

        self.assertEqual([o["outcome"] for o in resultado["recovered"]], [journal.UNCERTAIN])
        self.assertEqual(resultado["recovered"][0]["evidence"]["event_log_status"], "CORRUPT")

    def test_hash_invalido_impede_a_conclusao_de_ausencia(self) -> None:
        create_case(self.root, semantic("Primeiro"))
        self.ghost_intent("OP-PC01", semantic("Segundo"))
        linhas = log_path(self.root).read_text(encoding="utf-8").splitlines()
        evento = json.loads(linhas[0]); evento["hash"] = "0" * 64
        linhas[0] = json.dumps(evento, sort_keys=True, separators=(",", ":"))
        log_path(self.root).write_text("\n".join(linhas) + "\n", encoding="utf-8")
        self.assertEqual(classify_log(log_path(self.root))["status"], "HASH_INVALID")

        resultado = mutation.recover(self.root, operation_id="OP-PC01")
        self.assertEqual([o["outcome"] for o in resultado["recovered"]], [journal.UNCERTAIN])

    def test_quarentena_preserva_bytes_e_a_recuperacao_nao_a_consome(self) -> None:
        create_case(self.root, semantic("Primeiro"))
        self.ghost_intent("OP-PC01", semantic("Segundo"))
        originais = log_path(self.root).read_bytes()
        destino = quarantine(self.root, "corrupção observada no teste")

        mutation.recover(self.root, operation_id="OP-PC01")

        self.assertEqual(destino.read_bytes(), originais)
        origem = json.loads(destino.with_suffix("").with_suffix(".origin.json").read_text(encoding="utf-8"))
        self.assertEqual(origem["reason"], "corrupção observada no teste")


if __name__ == "__main__":
    unittest.main()
