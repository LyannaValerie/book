"""Situational retrieval: matrix, engine parity, schema 2, migration, CLI path.

The corpus is synthetic and isolated (tests/fixtures/consult_matrix.json). Keys
are symbolic; real IDs never appear in the engine or in expectations.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from booklib import retrieval, v0
from booklib.authoring import challenge, create_case
from booklib.index import index_path, reindex
from booklib.maintenance import verify
from booklib.relations import add_relation

BOOK = Path(__file__).resolve().parents[1] / "book.py"
MATRIX = json.loads((Path(__file__).resolve().parent / "fixtures" / "consult_matrix.json").read_text(encoding="utf-8"))
EVIDENCE = [{"class": "OBSERVED", "description": "synthetic fixture", "source": "event:E-AAAAAAAAAAAA"}]


def payload(spec: dict) -> dict:
    data = {
        "title": spec["title"], "cues": spec["cues"], "scope": spec.get("scope"), "environment": None,
        "problem": spec["problem"], "discriminating_probe": None, "observed_result": "Synthetic fixture result.",
        "guidance": spec["guidance"], "contraindications": spec["contraindications"], "evidence": EVIDENCE,
        "references": [], "domain": "Fixture",
    }
    if "retrieval" in spec:
        data["retrieval"] = spec["retrieval"]
    return data


def build_corpus(root: Path) -> dict[str, str]:
    ids: dict[str, str] = {}
    for key, spec in MATRIX["cases"].items():
        ids[key] = create_case(root, payload(spec), actor="fixture", allow_similar=True)["id"]
        if "challenge" in spec:
            case = v0.find_case(root, ids[key])
            item = {"id": "C-" + hashlib.sha256(key.encode()).hexdigest()[:10].upper(), "observed_at": None, "reported_by": "fixture",
                    "statement": {"class": "OBSERVED", "text": spec["challenge"]}, "evidence": EVIDENCE, "references": []}
            path = root / f"{key}.challenge.json"; path.write_text(json.dumps(item), encoding="utf-8")
            challenge(root, ids[key], case["revision"]["hash"], path, "2026-01-01T00:00:00Z", "fixture", "fixture challenge")
            path.unlink()
    for edge in MATRIX["relations"]:
        add_relation(root, ids[edge["from"]], edge["type"], ids[edge["to"]], "ASSERTED", None, "fixture")
    return ids


def comparable(result: dict) -> dict:
    return {key: value for key, value in result.items() if key not in {"engine", "index_state"}}


def tree_digest(root: Path) -> str:
    hasher = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file() and ".book" not in p.parts):
        hasher.update(str(path.relative_to(root)).encode()); hasher.update(path.read_bytes())
    return hasher.hexdigest()


def check_expectations(test: unittest.TestCase, ids: dict[str, str], entry: dict, result: dict) -> None:
    keys = {value: key for key, value in ids.items()}
    expect = entry["expect"]
    order = [keys[card["id"]] for card in result["cards"]]
    by_key = {keys[card["id"]]: card for card in result["cards"]}
    name = entry["name"]
    if "order" in expect:
        test.assertEqual(order, expect["order"], name)
    if "order_prefix" in expect:
        test.assertEqual(order[: len(expect["order_prefix"])], expect["order_prefix"], name)
    if "order_by_id" in expect:
        test.assertEqual(order, sorted(expect["order_by_id"], key=lambda key: ids[key]), name)
    if "pertinent" in expect:
        test.assertEqual(result["counts"]["pertinent"], expect["pertinent"], name)
    for key, cls in expect.get("classes", {}).items():
        test.assertEqual(by_key[key]["class"], cls, f"{name}: {key}")
    for key, signals in expect.get("signals", {}).items():
        test.assertLessEqual(set(signals), {reason["signal"] for reason in by_key[key]["reasons"]}, f"{name}: {key}")
    for key, signals in expect.get("absent_signals", {}).items():
        test.assertFalse(set(signals) & {reason["signal"] for reason in by_key[key]["reasons"]}, f"{name}: {key}")
    for key, term in expect.get("via_alias", {}).items():
        test.assertIn(term, [reason.get("via_alias", {}).get("term") for reason in by_key[key]["reasons"]], f"{name}: {key}")
    if "excluded" in expect:
        test.assertEqual(sorted(keys[item["id"]] for item in result["excluded"]), sorted(expect["excluded"]), name)
    for key, reasons in expect.get("excluded_reasons", {}).items():
        item = next(item for item in result["excluded"] if item["id"] == ids[key])
        test.assertLessEqual(set(reasons), {reason["signal"] for reason in item["reasons"]}, f"{name}: {key}")
    for key, dims in expect.get("unknown", {}).items():
        test.assertEqual(by_key[key]["unknown_dimensions"], dims, f"{name}: {key}")
    for field, state in (("condition_unknown", "unknown"), ("condition_satisfied", "satisfied"), ("condition_conflicting", "conflicting")):
        for key, facts in expect.get(field, {}).items():
            test.assertEqual(sorted(item["fact"] for item in by_key[key]["conditions"][state]), facts, f"{name}: {key}")
    for key, prose in expect.get("prose_unverified", {}).items():
        test.assertEqual(by_key[key]["conditions"]["prose_unverified"], prose, f"{name}: {key}")
    for key, kinds in expect.get("caveats", {}).items():
        test.assertEqual(sorted(caveat["kind"] for caveat in by_key[key]["caveats"]), sorted(kinds), f"{name}: {key}")


class MatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.ids = build_corpus(cls.root)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def run_query(self, entry: dict) -> dict:
        return retrieval.consult(self.root, entry["query"], explore=entry.get("explore", False), page_size=50, budget=retrieval.MAX_BUDGET)

    def run_matrix(self) -> None:
        for entry in MATRIX["queries"]:
            check_expectations(self, self.ids, entry, self.run_query(entry))

    def test_matrix_on_fallback_and_index_with_parity(self) -> None:
        index_path(self.root).unlink(missing_ok=True)
        fallback = {}
        for entry in MATRIX["queries"]:
            with self.subTest(query=entry["name"], engine="fallback"):
                result = self.run_query(entry)
                self.assertEqual((result["engine"], result["index_state"]), ("canonical-fallback", "MISSING"))
                check_expectations(self, self.ids, entry, result)
                fallback[entry["name"]] = result
        reindex(self.root)
        try:
            for entry in MATRIX["queries"]:
                with self.subTest(query=entry["name"], engine="sqlite"):
                    result = self.run_query(entry)
                    self.assertEqual(result["index_state"], "READY")
                    # Divergence between engines is a failure, not an implementation detail.
                    self.assertEqual(comparable(result), comparable(fallback[entry["name"]]))
                    for page_size in (1, 2, 3):
                        self.assertEqual(self.paged(entry, page_size), [card["id"] for card in fallback[entry["name"]]["cards"]])
        finally:
            index_path(self.root).unlink(missing_ok=True)

    def paged(self, entry: dict, page_size: int) -> list[str]:
        seen, cursor = [], None
        while True:
            result = retrieval.consult(self.root, entry["query"], explore=entry.get("explore", False), page_size=page_size, budget=retrieval.MAX_BUDGET, cursor=cursor)
            self.assertLessEqual(len(result["cards"]), page_size)
            seen.extend(card["id"] for card in result["cards"])
            cursor = result["page"]["next_cursor"]
            if cursor is None:
                self.assertFalse(result["page"]["has_more"])
                return seen

    def test_fallback_pagination_matches_full_listing(self) -> None:
        index_path(self.root).unlink(missing_ok=True)
        for entry in MATRIX["queries"]:
            full = [card["id"] for card in self.run_query(entry)["cards"]]
            self.assertEqual(self.paged(entry, 3), full, entry["name"])
            self.assertEqual(len(set(full)), len(full))

    def test_matrix_detects_a_removed_structural_signal(self) -> None:
        with mock.patch.object(retrieval, "match_action", return_value=None):
            with self.assertRaises(AssertionError):
                self.run_matrix()

    def test_matrix_detects_unknown_treated_as_satisfied(self) -> None:
        original = retrieval.evaluate_predicate

        def optimistic(predicate, facts):
            state = original(predicate, facts)
            return {**state, "state": "SATISFIED"} if state["state"] == "UNKNOWN" else state

        with mock.patch.object(retrieval, "evaluate_predicate", optimistic):
            with self.assertRaises(AssertionError):
                self.run_matrix()

    def test_matrix_detects_path_signal_removed(self) -> None:
        with mock.patch.object(retrieval, "path_matches", return_value=False):
            with self.assertRaises(AssertionError):
                self.run_matrix()

    def test_cards_carry_canonical_guidance_and_every_contraindication(self) -> None:
        result = retrieval.consult(self.root, {"action": "vacuum-db"})
        card = result["cards"][0]
        case = v0.find_case(self.root, card["id"])
        self.assertEqual(card["guidance"], case["guidance"])
        self.assertEqual(card["contraindications"], case["contraindications"])
        self.assertEqual(card["revision"], {"hash": case["revision"]["hash"], "number": case["revision"]["number"]})
        self.assertEqual(card["evidence_classes"], ["OBSERVED"])
        self.assertEqual((result["content_trust"], result["applicability_claimed"]), ("UNTRUSTED_DATA", False))

    def test_default_page_is_three_and_continuation_is_complete(self) -> None:
        first = retrieval.consult(self.root, {"action": "tag release"})
        self.assertEqual((len(first["cards"]), first["page"]["has_more"], first["total"]), (3, True, 4))
        second = retrieval.consult(self.root, {"action": "tag release"}, cursor=first["page"]["next_cursor"])
        self.assertEqual((len(second["cards"]), second["page"]["has_more"], second["page"]["next_cursor"]), (1, False, None))
        ids = [card["id"] for card in first["cards"] + second["cards"]]
        self.assertEqual(ids, sorted(self.ids[key] for key in ("tag_a", "tag_b", "tag_c", "tag_d")))

    def test_cursor_rejects_other_query_and_cutoff(self) -> None:
        first = retrieval.consult(self.root, {"action": "tag release"}, page_size=1)
        for query, explore in (({"action": "deploy"}, False), ({"action": "tag release"}, True)):
            with self.assertRaises(v0.BookError) as caught:
                retrieval.consult(self.root, query, explore=explore, cursor=first["page"]["next_cursor"])
            self.assertEqual(caught.exception.code, "CURSOR_QUERY_MISMATCH")
        for cursor in ("not-base64!", "e30", "x" * 300):
            with self.assertRaises(v0.BookError) as caught:
                retrieval.consult(self.root, {"action": "tag release"}, cursor=cursor)
            self.assertEqual(caught.exception.code, "CURSOR_INVALID")

    def test_adversarial_case_content_stays_data(self) -> None:
        before = tree_digest(self.root)
        cwd = os.getcwd()
        os.chdir(self.root)
        try:
            result = retrieval.consult(self.root, {"action": "adversarial probe"})
            text = retrieval.render_human(result)
        finally:
            os.chdir(cwd)
        card = result["cards"][0]
        self.assertEqual(card["guidance"]["text"], MATRIX["cases"]["adversarial"]["guidance"])
        self.assertIn("$(touch BOOK_PWNED)", text)
        self.assertEqual((result["cutoff"], result["counts"]["pertinent"]), ("pertinent", 1))
        self.assertFalse((self.root / "BOOK_PWNED").exists())
        self.assertEqual(tree_digest(self.root), before)
        self.assertEqual(verify(self.root)[0]["valid_cases"], len(MATRIX["cases"]))


class BudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        long = "Keep the long guidance whole. " * 120
        self.long_id = create_case(self.root, payload({"title": "Long guidance case", "cues": ["long"], "problem": "Long.", "guidance": long, "contraindications": ["Never drop this caveat."], "retrieval": {"actions": ["long-action"]}}), actor="t")["id"]
        self.short_id = create_case(self.root, payload({"title": "Short guidance case", "cues": ["short"], "problem": "Short.", "guidance": "Short guidance.", "contraindications": ["Short caveat."], "retrieval": {"actions": ["long-action"]}}), actor="t", allow_similar=True)["id"]

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_card_too_large_is_an_explicit_stub_with_full_access(self) -> None:
        result = retrieval.consult(self.root, {"action": "long action"}, budget=1000)
        first = result["cards"][0]
        self.assertEqual(first["id"], self.long_id)  # same points; "long" also matches its title and cue
        self.assertTrue(first["budget_exceeded"])
        self.assertNotIn("guidance", first)
        self.assertEqual(first["full_case"], f"book show {self.long_id}")
        self.assertTrue(result["budget"]["exhausted"])
        self.assertTrue(result["page"]["has_more"])
        rest = retrieval.consult(self.root, {"action": "long action"}, budget=1000, cursor=result["page"]["next_cursor"])
        self.assertEqual(rest["cards"][0]["contraindications"], ["Short caveat."])
        self.assertFalse(rest["page"]["has_more"])

    def test_whole_cards_only_and_unit_documented(self) -> None:
        result = retrieval.consult(self.root, {"action": "long action"}, budget=retrieval.MAX_BUDGET)
        self.assertEqual(result["budget"]["unit"], "characters of compact JSON per card")
        self.assertEqual(result["budget"]["used"], sum(retrieval.card_size(card) for card in result["cards"]))
        for card in result["cards"]:
            self.assertEqual(card["guidance"], v0.find_case(self.root, card["id"])["guidance"])
            self.assertEqual(card["contraindications"], v0.find_case(self.root, card["id"])["contraindications"])

    def test_cursor_goes_stale_when_the_book_changes(self) -> None:
        first = retrieval.consult(self.root, {"action": "long action"}, page_size=1)
        create_case(self.root, payload({"title": "Third case", "cues": ["third"], "problem": "Third.", "guidance": "Third.", "contraindications": [], "retrieval": {"actions": ["long-action"]}}), actor="t", allow_similar=True)
        with self.assertRaises(v0.BookError) as caught:
            retrieval.consult(self.root, {"action": "long action"}, page_size=1, cursor=first["page"]["next_cursor"])
        self.assertEqual(caught.exception.code, "CURSOR_STALE")


class ValidationTests(unittest.TestCase):
    def assert_query_invalid(self, query) -> None:
        with self.assertRaises(v0.BookError) as caught:
            retrieval.parse_query(query)
        self.assertEqual(caught.exception.code, "QUERY_INVALID", query)

    def test_invalid_queries(self) -> None:
        for query in (
            [], {}, {"unknown": 1}, {"intent": "curious"}, {"project": "has space"}, {"action": "!!!"},
            {"planned_paths": ["/abs/path"]}, {"planned_paths": ["../escape"]}, {"changed_paths": ["src/*.py"]},
            {"changed_paths": ["a\\b"]}, {"changed_paths": "src/a.py"}, {"components": [1]},
            {"facts": {"x": None}}, {"facts": {"x": [1]}}, {"facts": {"x": {"conflicting": [1, 1]}}},
            {"facts": {"x": {"other": [1, 2]}}}, {"facts": {"bad name": 1}}, {"facts": {"x": float("nan")}},
            {"text": "   "}, {"text": "x" * 600},
        ):
            self.assert_query_invalid(query)

    def test_absence_empty_and_conflict_are_distinct(self) -> None:
        self.assertNotIn("changed_paths", retrieval.parse_query({"action": "x"}))
        self.assertEqual(retrieval.parse_query({"changed_paths": []})["changed_paths"], [])
        self.assertEqual(retrieval.parse_query({"facts": {"ci": False}})["facts"], {"ci": False})
        self.assertEqual(retrieval.parse_query({"facts": {"ci": {"conflicting": [True, False]}}})["facts"], {"ci": {"conflicting": [False, True]}})

    def test_path_normalisation_and_patterns(self) -> None:
        self.assertEqual(retrieval.normalize_path("./src//a/./b.py", "p", "QUERY_INVALID"), "src/a/b.py")
        self.assertEqual(retrieval.normalize_path("db/migrations/", "p", "X", pattern=True), "db/migrations/**")
        self.assertTrue(retrieval.path_matches("db/**/*.sql", "db/a/b/c.sql"))
        self.assertTrue(retrieval.path_matches("db/**/*.sql", "db/c.sql"))
        self.assertFalse(retrieval.path_matches("db/*.sql", "db/a/c.sql"))
        self.assertFalse(retrieval.path_matches("src/cache/**", "src/cachex/a.py"))
        self.assertFalse(retrieval.path_matches("Src/**", "src/a.py"))

    def test_invalid_triggers(self) -> None:
        for triggers in (
            {}, {"unknown": []}, {"actions": []}, {"actions": ["a", "A"]}, {"intents": ["curious"]},
            {"paths": ["src/**x/a"]}, {"paths": ["/abs"]}, {"paths": ["a/[ab]"]},
            {"actions": ["a"], "aliases": [{"term": "b", "for": "action:c"}]},
            {"actions": ["a"], "aliases": [{"term": "b", "for": "phase:a"}]},
            {"actions": ["a"], "aliases": [{"term": "b", "for": "action:a", "lang": "Portuguese!"}]},
            {"predicates": [{"fact": "x", "op": "matches", "value": "y"}]},
            {"predicates": [{"fact": "x", "op": "lt", "value": "3"}]},
            {"predicates": [{"fact": "x", "op": "in", "value": []}]},
            {"predicates": [{"fact": "x", "op": "eq", "value": None}]},
            {"predicates": [{"fact": "x", "op": "eq", "value": 1, "code": "eval()"}]},
        ):
            with self.assertRaises(v0.BookError, msg=triggers) as caught:
                retrieval.validate_retrieval(triggers)
            self.assertIn(caught.exception.code, {"SCHEMA_INVALID", "CONTENT_LIMIT"}, triggers)

    def test_secret_like_trigger_is_rejected(self) -> None:
        with self.assertRaises(v0.BookError) as caught:
            retrieval.validate_retrieval({"predicates": [{"fact": "x", "op": "eq", "value": "password=hunter22"}]})
        self.assertEqual(caught.exception.code, "SENSITIVE_CONTENT")

    def test_typed_operators(self) -> None:
        def state(op, value, facts):
            return retrieval.evaluate_predicate({"fact": "n", "op": op, "value": value}, facts)["state"]
        self.assertEqual(state("lt", 3, {"n": 2}), "SATISFIED")
        self.assertEqual(state("ge", 3, {"n": 2}), "CONTRADICTED")
        self.assertEqual(state("eq", False, {"n": False}), "SATISFIED")
        self.assertEqual(state("eq", False, {}), "UNKNOWN")
        self.assertEqual(state("eq", False, {"n": "false"}), "UNKNOWN")
        self.assertEqual(state("in", ["a", "b"], {"n": "c"}), "CONTRADICTED")
        self.assertEqual(state("not_in", ["a", "b"], {"n": "c"}), "SATISFIED")
        self.assertEqual(state("eq", 1, {"n": {"conflicting": [1, 2]}}), "CONFLICTING")
        self.assertEqual(state("lt", 3, {"n": True}), "UNKNOWN")


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def cli(self, *args: str, stdin: str | None = None, raw: bool = False):
        proc = subprocess.run([sys.executable, str(BOOK), "--book", str(self.root), "--actor", "tester", *args], input=stdin, text=True, capture_output=True)
        return proc.returncode, (proc.stdout if raw else json.loads(proc.stdout))

    def test_create_index_consult_revise_continue_end_to_end(self) -> None:
        spec = payload(MATRIX["cases"]["migrate_rename"])
        code, created = self.cli("add-case", "--stdin", stdin=json.dumps(spec))
        self.assertEqual(code, 0, created)
        case_id = created["id"]
        self.assertEqual(json.loads((self.root / "cases" / f"{case_id}.json").read_text())["schema_version"], 2)
        self.assertEqual(self.cli("reindex")[0], 0)
        code, result = self.cli("consult", "--action", "renomear coluna", "--project", "ACME/shop", "--intent", "preventive", "--no-changed-paths", "--planned-path", "db/migrations/0042.sql")
        self.assertEqual(code, 0, result)
        self.assertEqual((result["engine"], result["index_state"]), ("sqlite-derived-candidates+canonical-evaluation", "READY"))
        card = result["cards"][0]
        self.assertEqual((card["id"], card["class"], card["use"]["match"]), (case_id, "SPECIFIC", "MATCH"))
        self.assertEqual(card["contraindications"], spec["contraindications"])
        # Revise through the CLI: add an action trigger; the stale index falls back, reindex restores parity.
        triggers = {**spec["retrieval"], "actions": ["rename-column", "backfill-column"]}
        code, revised = self.cli("revise", case_id, "--if-revision", created["revision"], "--json", json.dumps({"retrieval": triggers}))
        self.assertEqual(code, 0, revised)
        code, stale = self.cli("consult", "--action", "backfill column")
        self.assertEqual((stale["index_state"], stale["cards"][0]["id"], stale["cards"][0]["revision"]["number"]), ("DIVERGED", case_id, 2))
        self.cli("reindex")
        code, fresh = self.cli("consult", "--action", "backfill column")
        self.assertEqual(fresh["index_state"], "READY")
        self.assertEqual(comparable(fresh), comparable(stale))
        code, text = self.cli("consult", "--action", "backfill column", "--format", "human", raw=True)
        self.assertEqual(code, 0)
        self.assertIn("guidance [ASSERTED]: " + spec["guidance"], text)
        self.assertIn("contraindication: " + spec["contraindications"][0], text)
        self.assertIn("UNTRUSTED_DATA", text)
        self.assertEqual(verify(self.root)[0]["ok"], True)

    def test_cli_continuation(self) -> None:
        for name in ("tag_a", "tag_b", "tag_c", "tag_d"):
            self.assertEqual(self.cli("add-case", "--allow-similar", "--stdin", stdin=json.dumps(payload(MATRIX["cases"][name])))[0], 0)
        code, first = self.cli("consult", "--action", "tag release")
        self.assertEqual((code, len(first["cards"]), first["page"]["has_more"]), (0, 3, True))
        code, text = self.cli("consult", "--action", "tag release", "--format", "human", raw=True)
        self.assertIn(f"--cursor {first['page']['next_cursor']}", text)
        code, second = self.cli("consult", "--action", "tag release", "--cursor", first["page"]["next_cursor"])
        self.assertEqual((code, len(second["cards"])), (0, 1))
        self.assertFalse({c["id"] for c in first["cards"]} & {c["id"] for c in second["cards"]})

    def test_cli_rejects_malformed_input_with_exit_2(self) -> None:
        cases = (
            (("consult",), "QUERY_INVALID"),
            (("consult", "--planned-path", "/etc/passwd"), "QUERY_INVALID"),
            (("consult", "--fact", "novalue"), "QUERY_INVALID"),
            (("consult", "--fact", "x=null"), "QUERY_INVALID"),
            (("consult", "--action", "a", "--page-size", "0"), "QUERY_INVALID"),
            (("consult", "--action", "a", "--budget", "10"), "QUERY_INVALID"),
            (("consult", "--action", "a", "--cursor", "zzz"), "CURSOR_INVALID"),
            (("consult", "--query-json", "{\"action\": 1}"), "QUERY_INVALID"),
            (("consult", "--query-json", "{\"action\": \"a\"}", "--text", "b"), "INPUT_AMBIGUOUS"),
            (("consult", "--changed-path", "a.py", "--no-changed-paths"), "INPUT_AMBIGUOUS"),
            (("consult", "--query-json", "{not json"), "JSON_INVALID"),
        )
        for args, error in cases:
            code, result = self.cli(*args)
            self.assertEqual((code, result["ok"], result["error"]), (2, False, error), args)

    def test_fact_flags_types_and_conflict(self) -> None:
        spec = payload(MATRIX["cases"]["sqlite_only"])
        self.cli("add-case", "--stdin", stdin=json.dumps(spec))
        code, result = self.cli("consult", "--action", "vacuum-db", "--fact", "ci=false", "--fact", "db.engine=sqlite")
        self.assertEqual(sorted(item["fact"] for item in result["cards"][0]["conditions"]["satisfied"]), ["ci", "db.engine"])
        code, result = self.cli("consult", "--action", "vacuum-db", "--fact", "ci=\"false\"")
        self.assertEqual([item["why"] for item in result["cards"][0]["conditions"]["unknown"] if item["fact"] == "ci"], ["supplied value has a different type"])
        code, result = self.cli("consult", "--action", "vacuum-db", "--fact", "db.engine=sqlite", "--fact", "db.engine=postgres")
        self.assertEqual(result["cards"][0]["conditions"]["conflicting"][0]["supplied"], ["postgres", "sqlite"])

    def test_consult_is_read_only_and_records_access_only_with_task(self) -> None:
        self.cli("add-case", "--stdin", stdin=json.dumps(payload(MATRIX["cases"]["push_branch"])))
        before = tree_digest(self.root)
        self.assertEqual(self.cli("consult", "--action", "git push")[0], 0)
        self.assertEqual(tree_digest(self.root), before)
        code, task = self.cli("task", "begin", "--goal", "g", "--project", "p")
        task_id = task["task"]["task_id"]
        events_before = (self.root / "events" / "events.jsonl").read_text().count("\n")
        code, result = self.cli("--task", task_id, "consult", "--action", "git push")
        lines = (self.root / "events" / "events.jsonl").read_text().splitlines()
        self.assertEqual(len(lines) - events_before, 1)
        event = json.loads(lines[-1])
        self.assertEqual((event["kind"], event["data"]["result_count"]), ("SEARCH", 1))
        self.assertNotIn("UTILITY", {json.loads(line)["kind"] for line in lines})

    def test_legacy_search_unchanged_by_triggers(self) -> None:
        self.cli("add-case", "--stdin", stdin=json.dumps(payload(MATRIX["cases"]["push_branch"])))
        code, before = self.cli("search", "publishing")
        self.cli("reindex")
        code, after = self.cli("search", "publishing")
        self.assertEqual({k: v for k, v in before.items() if k != "engine"}, {k: v for k, v in after.items() if k != "engine"})
        self.assertEqual(before["count"], 1)
        self.assertNotIn("class", before["results"][0])


class SchemaMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.legacy = create_case(self.root, payload(MATRIX["cases"]["legacy"]), actor="t")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def cli(self, *args: str):
        proc = subprocess.run([sys.executable, str(BOOK), "--book", str(self.root), "--actor", "tester", *args], text=True, capture_output=True)
        return proc.returncode, json.loads(proc.stdout)

    def test_legacy_case_stays_schema_1_and_consultable(self) -> None:
        case = v0.find_case(self.root, self.legacy["id"])
        self.assertEqual((case["schema_version"], "retrieval" in case), (1, False))
        result = retrieval.consult(self.root, {"text": "legacy readers"}, explore=True)
        self.assertEqual([(c["id"], c["class"]) for c in result["cards"]], [(self.legacy["id"], "LEXICAL")])
        self.assertEqual(result["cards"][0]["unknown_dimensions"], [])

    def test_revise_with_triggers_on_schema_1_requires_migration(self) -> None:
        code, result = self.cli("revise", self.legacy["id"], "--if-revision", self.legacy["revision"], "--json", json.dumps({"retrieval": {"actions": ["x"]}}))
        self.assertEqual((code, result["error"]), (2, "SCHEMA_MIGRATION_REQUIRED"))
        self.assertEqual(v0.find_case(self.root, self.legacy["id"])["revision"]["hash"], self.legacy["revision"])

    def test_migration_is_explicit_repeatable_and_lossless(self) -> None:
        before = v0.find_case(self.root, self.legacy["id"])
        code, dry = self.cli("migrate-schema", "--all")
        self.assertEqual((code, dry["dry_run"], dry["would_migrate"]), (0, True, [self.legacy["id"]]))
        self.assertEqual(v0.find_case(self.root, self.legacy["id"]), before)
        code, applied = self.cli("migrate-schema", "--case", self.legacy["id"], "--apply")
        self.assertEqual(code, 0, applied)
        after = v0.find_case(self.root, self.legacy["id"])
        self.assertEqual((after["schema_version"], after["retrieval"], after["revision"]["number"], after["revision"]["parent_hash"]), (2, None, 2, before["revision"]["hash"]))
        self.assertEqual({k: v for k, v in after.items() if k not in {"schema_version", "retrieval", "revision"}}, {k: v for k, v in before.items() if k not in {"schema_version", "revision"}})
        code, again = self.cli("migrate-schema", "--case", self.legacy["id"], "--apply")
        self.assertEqual((code, again["migrated"], again["already_current"]), (0, [], [self.legacy["id"]]))
        self.assertEqual(v0.find_case(self.root, self.legacy["id"]), after)
        report, status = verify(self.root)
        self.assertEqual((status, report["consistency"]), (0, "OK"))
        kinds = [json.loads(line)["kind"] for line in (self.root / "events" / "events.jsonl").read_text().splitlines()]
        self.assertEqual(kinds.count("CASE_REVISE"), 1)
        # After migration, triggers can be added by an ordinary revision.
        code, revised = self.cli("revise", self.legacy["id"], "--if-revision", after["revision"]["hash"], "--json", json.dumps({"retrieval": {"actions": ["rename-column"]}}))
        self.assertEqual(code, 0, revised)
        self.assertEqual(retrieval.consult(self.root, {"action": "rename column"})["cards"][0]["id"], self.legacy["id"])

    def test_migrate_requires_a_target_and_known_cases(self) -> None:
        proc = subprocess.run([sys.executable, str(BOOK), "--book", str(self.root), "migrate-schema"], text=True, capture_output=True)
        self.assertEqual(proc.returncode, 2)
        code, result = self.cli("migrate-schema", "--case", "B-AAAAAAAAAAAA", "--apply")
        self.assertEqual((code, result["error"]), (2, "CASE_NOT_FOUND"))

    def test_future_schema_still_fails_closed(self) -> None:
        case = v0.find_case(self.root, self.legacy["id"])
        case["schema_version"] = 3
        with self.assertRaises(v0.BookError) as caught:
            v0.validate_case(case)
        self.assertEqual(caught.exception.code, "SCHEMA_FUTURE_UNSUPPORTED")


class IndexStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        for name in ("push_branch", "push_challenged", "legacy"):
            create_case(self.root, payload(MATRIX["cases"][name]), actor="t", allow_similar=True)
        self.query = {"action": "git push", "text": "readers"}

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_missing_outdated_corrupt_and_ready_agree(self) -> None:
        results = {}
        results["MISSING"] = retrieval.consult(self.root, self.query, explore=True)
        reindex(self.root)
        results["READY"] = retrieval.consult(self.root, self.query, explore=True)
        db = sqlite3.connect(index_path(self.root)); db.execute("DELETE FROM metadata WHERE key='retrieval_index'"); db.commit(); db.close()
        results["OUTDATED"] = retrieval.consult(self.root, self.query, explore=True)
        index_path(self.root).write_bytes(b"not a database")
        results["CORRUPT"] = retrieval.consult(self.root, self.query, explore=True)
        for state, result in results.items():
            self.assertEqual(result["index_state"], state)
            self.assertEqual(comparable(result), comparable(results["MISSING"]), state)
        self.assertEqual(results["READY"]["engine"], "sqlite-derived-candidates+canonical-evaluation")
        self.assertEqual(results["CORRUPT"]["engine"], "canonical-fallback")
        reindex(self.root)
        self.assertEqual(retrieval.consult(self.root, self.query, explore=True)["index_state"], "READY")


if __name__ == "__main__":
    unittest.main()
