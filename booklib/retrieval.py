"""Situational retrieval: a described situation, classified and explained cards.

Retrieval triggers say WHEN a case should be recalled; they never prove that it
applies. Prose conditions stay prose and are served as unverified. Every card is
UNTRUSTED_DATA, extracted from canonical fields without summarisation.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import re
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from . import v0

INTENTS = ("preventive", "diagnostic", "corroborative")
OPERATORS = ("eq", "ne", "in", "not_in", "lt", "le", "gt", "ge")
TRIGGER_FIELDS = {"intents", "projects", "actions", "phases", "paths", "components", "aliases", "predicates"}
QUERY_FIELDS = {"text", "intent", "project", "action", "phase", "planned_paths", "changed_paths", "components", "facts"}
CLASSES = ("SPECIFIC", "SITUATIONAL", "EXPLORATORY", "LEXICAL")
PERTINENT = {"SPECIFIC", "SITUATIONAL"}
# Ordinal weights. A score orders candidates inside one class; it is not a probability.
WEIGHTS = {"action": 8, "changed_path": 4, "planned_path": 4, "component": 3, "phase": 2, "project": 2, "predicate": 2, "intent": 1, "alias_text": 3}
PROJECT_RE = re.compile(r"[a-z0-9][a-z0-9._/-]{0,127}\Z")
FACT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
LANG_RE = re.compile(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*\Z")
DEFAULT_PAGE_SIZE = 3
MAX_PAGE_SIZE = 50
DEFAULT_BUDGET = 8000
MIN_BUDGET, MAX_BUDGET = 256, 200_000
MAX_EXCLUDED = 20
CURSOR_VERSION = 1


def _fail(code: str, message: str, details: Any = None) -> None:
    raise v0.BookError(code, message, details)


# --- normalisation ---------------------------------------------------------

def slug(value: Any, where: str, code: str) -> str:
    """Identifier form used for actions, phases and components."""
    if not isinstance(value, str) or not value.strip() or len(value) > v0.MAX_SHORT_TEXT:
        _fail(code, f"{where} must be non-empty text within {v0.MAX_SHORT_TEXT} characters")
    tokens = v0.normalize_tokens(value)
    if not tokens:
        _fail(code, f"{where} has no identifier characters")
    return "-".join(tokens)


def normalize_project(value: Any, where: str, code: str) -> str:
    text = value.strip().casefold() if isinstance(value, str) else ""
    if not PROJECT_RE.fullmatch(text):
        _fail(code, f"{where} must match [a-z0-9][a-z0-9._/-]* (case-insensitive), at most 128 characters")
    return text


def normalize_path(value: Any, where: str, code: str, *, pattern: bool = False) -> str:
    """Repository-relative POSIX path; patterns may use ``*``, ``?`` and whole-segment ``**``."""
    if not isinstance(value, str) or not value or len(value) > v0.MAX_SHORT_TEXT:
        _fail(code, f"{where} must be a non-empty path within {v0.MAX_SHORT_TEXT} characters")
    if value != value.strip() or any(ch in value for ch in "[]{}\\\x00"):
        _fail(code, f"{where} is ambiguous: surrounding space, backslash, NUL, [] and {{}} are not accepted")
    if value.startswith("/"):
        _fail(code, f"{where} must be relative to the repository root")
    parts = [part for part in value.split("/") if part not in ("", ".")]
    if not parts:
        _fail(code, f"{where} names no path")
    if ".." in parts:
        _fail(code, f"{where} must not contain '..'")
    if not pattern and any(ch in value for ch in "*?"):
        _fail(code, f"{where} is a concrete path; wildcards are only accepted in case triggers")
    if pattern:
        if any("**" in part and part != "**" for part in parts):
            _fail(code, f"{where}: '**' must be a whole path segment")
        if parts.count("**") > 4:
            _fail(code, f"{where}: at most four '**' segments")
        if value.endswith("/"):
            parts.append("**")
    return "/".join(parts)


def path_matches(pattern: str, path: str) -> bool:
    def walk(pattern_parts: list[str], path_parts: list[str]) -> bool:
        if not pattern_parts:
            return not path_parts
        head = pattern_parts[0]
        if head == "**":
            return any(walk(pattern_parts[1:], path_parts[index:]) for index in range(len(path_parts) + 1))
        return bool(path_parts) and fnmatchcase(path_parts[0], head) and walk(pattern_parts[1:], path_parts[1:])
    return walk(pattern.split("/"), path.split("/"))


def _kind(value: Any) -> str | None:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return None


def _scalar(value: Any, where: str, code: str) -> Any:
    kind = _kind(value)
    if kind is None:
        _fail(code, f"{where} must be a string, number or boolean")
    if kind == "number" and not math.isfinite(value):
        _fail(code, f"{where} must be a finite number")
    if kind == "string" and (len(value) > v0.MAX_SHORT_TEXT or not value.strip()):
        _fail(code, f"{where} must be non-empty text within {v0.MAX_SHORT_TEXT} characters")
    return value


def _list(value: Any, where: str, code: str, *, allow_empty: bool) -> list[Any]:
    if not isinstance(value, list) or len(value) > v0.MAX_LIST or (not value and not allow_empty):
        _fail(code, f"{where} must be a list of {0 if allow_empty else 1}..{v0.MAX_LIST} items")
    return value


def _unique(values: list[str], where: str, code: str) -> list[str]:
    if len(values) != len(set(values)):
        _fail(code, f"{where} has entries that collide after normalisation")
    return values


# --- case triggers ---------------------------------------------------------

def validate_retrieval(value: Any) -> None:
    """Schema-2 ``retrieval``: null (UNKNOWN) or a non-empty trigger object."""
    if value is None:
        return
    code = "SCHEMA_INVALID"
    triggers = v0.require_object(value, "retrieval")
    unknown = sorted(set(triggers) - TRIGGER_FIELDS)
    if unknown or not triggers:
        _fail(code, "retrieval must be null or an object with known trigger fields", {"unknown": unknown, "allowed": sorted(TRIGGER_FIELDS)})
    # revise reaches here without the semantic payload's secret screening.
    serialized = json.dumps(triggers, ensure_ascii=False)
    if any(pattern.search(serialized) for pattern in v0.SECRET_PATTERNS):
        _fail("SENSITIVE_CONTENT", "retrieval resembles a secret")
    normalized_triggers(triggers)


def normalized_triggers(triggers: dict[str, Any] | None) -> dict[str, Any]:
    """Validate and normalise triggers. Absent field = the case declares nothing there."""
    if not triggers:
        return {}
    code = "SCHEMA_INVALID"
    out: dict[str, Any] = {}
    if "intents" in triggers:
        intents = _list(triggers["intents"], "retrieval.intents", code, allow_empty=False)
        if any(item not in INTENTS for item in intents):
            _fail(code, f"retrieval.intents accepts only {', '.join(INTENTS)}")
        out["intents"] = sorted(_unique(list(intents), "retrieval.intents", code))
    if "projects" in triggers:
        out["projects"] = sorted(_unique([normalize_project(item, f"retrieval.projects[{i}]", code) for i, item in enumerate(_list(triggers["projects"], "retrieval.projects", code, allow_empty=False))], "retrieval.projects", code))
    for field in ("actions", "phases", "components"):
        if field in triggers:
            out[field] = sorted(_unique([slug(item, f"retrieval.{field}[{i}]", code) for i, item in enumerate(_list(triggers[field], f"retrieval.{field}", code, allow_empty=False))], f"retrieval.{field}", code))
    if "paths" in triggers:
        out["paths"] = sorted(_unique([normalize_path(item, f"retrieval.paths[{i}]", code, pattern=True) for i, item in enumerate(_list(triggers["paths"], "retrieval.paths", code, allow_empty=False))], "retrieval.paths", code))
    if "aliases" in triggers:
        aliases = []
        for i, item in enumerate(_list(triggers["aliases"], "retrieval.aliases", code, allow_empty=False)):
            where = f"retrieval.aliases[{i}]"
            alias = v0.require_object(item, where)
            if not {"term", "for"} <= set(alias) <= {"term", "for", "lang"}:
                _fail(code, f"{where} requires term and for, optionally lang")
            v0.require_text(alias["term"], f"{where}.term", v0.MAX_SHORT_TEXT)
            target = alias["for"]
            if not isinstance(target, str) or ":" not in target:
                _fail(code, f"{where}.for must be action:<id> or component:<id>")
            dimension, _, name = target.partition(":")
            plural = {"action": "actions", "component": "components"}.get(dimension)
            if plural is None:
                _fail(code, f"{where}.for must be action:<id> or component:<id>")
            name = slug(name, f"{where}.for", code)
            # Contextual by construction: an alias only names what THIS case declares.
            if name not in out.get(plural, []):
                _fail(code, f"{where}.for names {dimension} {name!r}, which retrieval.{plural} does not declare")
            lang = alias.get("lang")
            if lang is not None and (not isinstance(lang, str) or not LANG_RE.fullmatch(lang)):
                _fail(code, f"{where}.lang must be a language tag such as pt or en-US")
            aliases.append({"term": alias["term"], "slug": slug(alias["term"], f"{where}.term", code), "for": f"{dimension}:{name}", "dimension": dimension, "target": name, "lang": lang})
        _unique([f"{a['slug']}>{a['for']}" for a in aliases], "retrieval.aliases", code)
        out["aliases"] = sorted(aliases, key=lambda a: (a["for"], a["slug"]))
    if "predicates" in triggers:
        predicates = []
        for i, item in enumerate(_list(triggers["predicates"], "retrieval.predicates", code, allow_empty=False)):
            where = f"retrieval.predicates[{i}]"
            predicate = v0.require_object(item, where)
            v0.require_exact_fields(predicate, {"fact", "op", "value"}, where)
            if not isinstance(predicate["fact"], str) or not FACT_RE.fullmatch(predicate["fact"]):
                _fail(code, f"{where}.fact must match {FACT_RE.pattern[:-2]}")
            if predicate["op"] not in OPERATORS:
                _fail(code, f"{where}.op must be one of {', '.join(OPERATORS)}")
            if predicate["op"] in ("in", "not_in"):
                values = [_scalar(v, f"{where}.value[{j}]", code) for j, v in enumerate(_list(predicate["value"], f"{where}.value", code, allow_empty=False))]
                if len({json.dumps(v) for v in values}) != len(values):
                    _fail(code, f"{where}.value has duplicate entries")
            elif predicate["op"] in ("lt", "le", "gt", "ge"):
                if _kind(predicate["value"]) != "number":
                    _fail(code, f"{where}.value must be a number for {predicate['op']}")
                _scalar(predicate["value"], f"{where}.value", code)
            else:
                _scalar(predicate["value"], f"{where}.value", code)
            predicates.append({"fact": predicate["fact"], "op": predicate["op"], "value": predicate["value"]})
        out["predicates"] = predicates
    return out


def case_triggers(case: dict[str, Any]) -> dict[str, Any]:
    return normalized_triggers(case.get("retrieval"))


def index_keys(case: dict[str, Any]) -> list[tuple[str, str]]:
    """Structural keys a derived index may use to propose candidates."""
    triggers = case_triggers(case)
    keys = {("action", item) for item in triggers.get("actions", [])}
    keys |= {("component", item) for item in triggers.get("components", [])}
    keys |= {(alias["dimension"], alias["slug"]) for alias in triggers.get("aliases", [])}
    if triggers.get("paths"):
        keys.add(("path", "*"))
    return sorted(keys)


def alias_text(case: dict[str, Any]) -> str:
    return " ".join(alias["term"] for alias in case_triggers(case).get("aliases", []))


# --- query -----------------------------------------------------------------

def parse_query(raw: Any) -> dict[str, Any]:
    """Validate a situation. Absent field = UNKNOWN; ``[]`` = known to be empty."""
    code = "QUERY_INVALID"
    if not isinstance(raw, dict):
        _fail(code, "query must be a JSON object")
    unknown = sorted(set(raw) - QUERY_FIELDS)
    if unknown:
        _fail(code, "query has unknown fields", {"unknown": unknown, "allowed": sorted(QUERY_FIELDS)})
    if not raw:
        _fail(code, "query must declare at least one field")
    query: dict[str, Any] = {}
    if "text" in raw:
        if not isinstance(raw["text"], str) or len(raw["text"]) > v0.MAX_SHORT_TEXT:
            _fail(code, f"query.text must be text within {v0.MAX_SHORT_TEXT} characters")
        query["text"] = raw["text"]
        if not v0.normalize_tokens(query["text"]):
            _fail(code, "query.text has no searchable token")
    if "intent" in raw:
        if raw["intent"] not in INTENTS:
            _fail(code, f"query.intent must be one of {', '.join(INTENTS)}")
        query["intent"] = raw["intent"]
    if "project" in raw:
        query["project"] = normalize_project(raw["project"], "query.project", code)
    for field in ("action", "phase"):
        if field in raw:
            query[field] = slug(raw[field], f"query.{field}", code)
    for field in ("planned_paths", "changed_paths"):
        if field in raw:
            query[field] = sorted({normalize_path(item, f"query.{field}[{i}]", code) for i, item in enumerate(_list(raw[field], f"query.{field}", code, allow_empty=True))})
    if "components" in raw:
        query["components"] = sorted({slug(item, f"query.components[{i}]", code) for i, item in enumerate(_list(raw["components"], "query.components", code, allow_empty=True))})
    if "facts" in raw:
        facts = raw["facts"]
        if not isinstance(facts, dict) or len(facts) > v0.MAX_LIST:
            _fail(code, f"query.facts must be an object with at most {v0.MAX_LIST} facts")
        normalized: dict[str, Any] = {}
        for name, value in sorted(facts.items()):
            where = f"query.facts.{name}"
            if not FACT_RE.fullmatch(name):
                _fail(code, f"{where}: fact names must match {FACT_RE.pattern[:-2]}")
            if value is None:
                _fail(code, f"{where} is null; omit a fact whose value is unknown")
            if isinstance(value, dict):
                if set(value) != {"conflicting"}:
                    _fail(code, f"{where}: an object value must be {{\"conflicting\": [v1, v2, ...]}}")
                values = [_scalar(v, f"{where}.conflicting[{j}]", code) for j, v in enumerate(_list(value["conflicting"], f"{where}.conflicting", code, allow_empty=False))]
                distinct = sorted({json.dumps(v, sort_keys=True) for v in values})
                if len(distinct) < 2:
                    _fail(code, f"{where}.conflicting needs at least two distinct values")
                normalized[name] = {"conflicting": [json.loads(v) for v in distinct]}
            else:
                normalized[name] = _scalar(value, where, code)
        query["facts"] = normalized
    return query


def query_tokens(query: dict[str, Any]) -> set[str]:
    words = [query.get("text", ""), query.get("action", "").replace("-", " "), *[item.replace("-", " ") for item in query.get("components", [])]]
    return set(v0.normalize_tokens(" ".join(words)))


def query_keys(query: dict[str, Any]) -> list[tuple[str, str]]:
    keys = set()
    if "action" in query:
        keys.add(("action", query["action"]))
    keys |= {("component", item) for item in query.get("components", [])}
    if query.get("planned_paths") or query.get("changed_paths"):
        keys.add(("path", "*"))
    return sorted(keys)


# --- evaluation ------------------------------------------------------------

def _compare(op: str, actual: Any, expected: Any) -> bool | None:
    """True/False when the comparison is meaningful; None when kinds differ."""
    if op in ("in", "not_in"):
        comparable = [item for item in expected if _kind(item) == _kind(actual)]
        if not comparable:
            return None
        found = actual in comparable
        return found if op == "in" else not found
    if _kind(actual) != _kind(expected):
        return None
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if _kind(actual) != "number":
        return None
    return {"lt": actual < expected, "le": actual <= expected, "gt": actual > expected, "ge": actual >= expected}[op]


def evaluate_predicate(predicate: dict[str, Any], facts: dict[str, Any] | None) -> dict[str, Any]:
    """SATISFIED, CONTRADICTED, UNKNOWN or CONFLICTING — never interpreted, never executed."""
    entry = dict(predicate)
    if facts is None or predicate["fact"] not in facts:
        return {**entry, "state": "UNKNOWN", "why": "fact not supplied"}
    actual = facts[predicate["fact"]]
    if isinstance(actual, dict):
        return {**entry, "state": "CONFLICTING", "supplied": actual["conflicting"]}
    outcome = _compare(predicate["op"], actual, predicate["value"])
    if outcome is None:
        return {**entry, "state": "UNKNOWN", "supplied": actual, "why": "supplied value has a different type"}
    return {**entry, "state": "SATISFIED" if outcome else "CONTRADICTED", "supplied": actual}


def match_action(query: dict[str, Any], triggers: dict[str, Any]) -> dict[str, Any] | None:
    wanted = query["action"]
    if wanted in triggers["actions"]:
        return {"signal": "action", "value": wanted}
    for alias in triggers.get("aliases", []):
        if alias["dimension"] == "action" and alias["slug"] == wanted:
            return {"signal": "action", "value": alias["target"], "via_alias": {"term": alias["term"], "lang": alias["lang"]}}
    return None


def evaluate(case: dict[str, Any], query: dict[str, Any], tokens: set[str]) -> dict[str, Any] | None:
    triggers = case_triggers(case)
    reasons: list[dict[str, Any]] = []
    unknown: list[str] = []
    excluded: list[dict[str, Any]] = []
    demotions: list[dict[str, Any]] = []
    points = 0
    action_match = action_mismatch = component_match = path_match = False

    if "projects" in triggers:
        if "project" not in query:
            unknown.append("project")
        elif query["project"] in triggers["projects"]:
            reasons.append({"signal": "project", "value": query["project"]}); points += WEIGHTS["project"]
        else:
            excluded.append({"signal": "project_mismatch", "query": query["project"], "case": triggers["projects"]})

    if "actions" in triggers:
        if "action" not in query:
            unknown.append("action")
        else:
            found = match_action(query, triggers)
            if found:
                reasons.append(found); points += WEIGHTS["action"]; action_match = True
            else:
                action_mismatch = True
                demotions.append({"signal": "action_mismatch", "query": query["action"], "case": triggers["actions"]})

    if "components" in triggers:
        if "components" not in query:
            unknown.append("components")
        else:
            matched = []
            for item in query["components"]:
                if item in triggers["components"]:
                    matched.append({"signal": "component", "value": item})
                else:
                    alias = next((a for a in triggers.get("aliases", []) if a["dimension"] == "component" and a["slug"] == item), None)
                    if alias:
                        matched.append({"signal": "component", "value": alias["target"], "via_alias": {"term": alias["term"], "lang": alias["lang"]}})
            if matched:
                reasons.extend(matched); points += WEIGHTS["component"]; component_match = True

    if "paths" in triggers:
        if "planned_paths" not in query and "changed_paths" not in query:
            unknown.append("paths")
        for field, signal in (("planned_paths", "planned_path"), ("changed_paths", "changed_path")):
            pairs = [{"signal": signal, "path": path, "pattern": pattern} for path in query.get(field, []) for pattern in triggers["paths"] if path_matches(pattern, path)]
            if pairs:
                reasons.extend(pairs[:8]); points += WEIGHTS[signal]; path_match = True

    for field, dimension in (("phases", "phase"), ("intents", "intent")):
        if field in triggers:
            if dimension not in query:
                unknown.append(dimension)
            elif query[dimension] in triggers[field]:
                reasons.append({"signal": dimension, "value": query[dimension]}); points += WEIGHTS[dimension]
            else:
                demotions.append({"signal": f"{dimension}_mismatch", "query": query[dimension], "case": triggers[field]})

    conditions = {"satisfied": [], "unknown": [], "conflicting": []}
    for predicate in triggers.get("predicates", []):
        state = evaluate_predicate(predicate, query.get("facts"))
        if state["state"] == "CONTRADICTED":
            excluded.append({"signal": "condition_contradicted", **{k: state[k] for k in ("fact", "op", "value", "supplied")}})
        elif state["state"] == "SATISFIED":
            conditions["satisfied"].append(state); reasons.append({"signal": "condition_satisfied", "fact": state["fact"]}); points += WEIGHTS["predicate"]
        else:
            conditions[state["state"].lower()].append(state)

    lexical, matched_cues = v0.lexical_score(case, tokens) if tokens else (0, [])
    if lexical:
        field_tokens = set(v0.normalize_tokens(" ".join(v0.searchable_fields(case).values())))
        reasons.append({"signal": "lexical", "score": lexical, "matched_tokens": sorted(tokens & field_tokens)[:8], "matched_cues": matched_cues[:8]})
    for alias in triggers.get("aliases", []):
        alias_tokens = set(v0.normalize_tokens(alias["term"]))
        if tokens and alias_tokens <= tokens:
            reasons.append({"signal": "alias_text", "term": alias["term"], "lang": alias["lang"], "expands_to": alias["for"]}); lexical += WEIGHTS["alias_text"]

    if action_match:
        rank = 0
    elif component_match or path_match:
        # A file or component match alone does not make every action on it relevant.
        rank = 2 if "actions" in triggers else 1
    elif lexical:
        rank = 3
    else:
        return None
    if demotions and rank < 2:
        rank += 1
    reasons.extend(demotions)
    return {
        "id": case["id"],
        "class": CLASSES[rank],
        "points": points,
        "lexical": lexical,
        "reasons": reasons,
        "excluded": excluded,
        "unknown": sorted(unknown),
        "conditions": conditions,
        "use": {"query_intent": query.get("intent"), "case_intents": triggers.get("intents")},
    }


# --- relations, ordering, cards ------------------------------------------

def supersession(relations: list[dict[str, Any]]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    by: dict[str, set[str]] = {}
    of: dict[str, set[str]] = {}
    for edge in relations:
        if edge.get("type") == "supersedes" and v0.CASE_ID_RE.fullmatch(edge["from"]) and v0.CASE_ID_RE.fullmatch(edge["to"]) and edge["from"] != edge["to"]:
            by.setdefault(edge["to"], set()).add(edge["from"]); of.setdefault(edge["from"], set()).add(edge["to"])
    return {k: sorted(v) for k, v in by.items()}, {k: sorted(v) for k, v in of.items()}


def is_superseded(case: dict[str, Any], superseded_by: dict[str, list[str]]) -> bool:
    return case["status"] in {"superseded", "historical"} or bool(superseded_by.get(case["id"]))


def rank_key(case: dict[str, Any], evaluation: dict[str, Any], superseded_by: dict[str, list[str]]) -> tuple:
    return (CLASSES.index(evaluation["class"]), is_superseded(case, superseded_by), -evaluation["points"], -evaluation["lexical"], case["id"])


def build_card(case: dict[str, Any], evaluation: dict[str, Any], superseded_by: dict[str, list[str]], supersedes: dict[str, list[str]]) -> dict[str, Any]:
    caveats = []
    if case["status"] == "challenged":
        caveats.append({"kind": "CHALLENGED", "challenges": [{"id": item["id"], "statement": item["statement"]} for item in case["challenges"]]})
    if case["status"] in {"superseded", "historical"}:
        caveats.append({"kind": "STATUS", "status": case["status"]})
    if superseded_by.get(case["id"]):
        caveats.append({"kind": "SUPERSEDED_BY", "cases": superseded_by[case["id"]]})
    if evaluation["conditions"]["conflicting"]:
        caveats.append({"kind": "CONFLICTING_CONDITIONS", "facts": sorted({item["fact"] for item in evaluation["conditions"]["conflicting"]})})
    query_intent, case_intents = evaluation["use"]["query_intent"], evaluation["use"]["case_intents"]
    if query_intent is None or case_intents is None:
        intent_match = "UNKNOWN"
    else:
        intent_match = "MATCH" if query_intent in case_intents else "MISMATCH"
    return {
        "id": case["id"],
        "title": case["title"],
        "revision": {"hash": case["revision"]["hash"], "number": case["revision"]["number"]},
        "status": case["status"],
        "class": evaluation["class"],
        "use": {**evaluation["use"], "match": intent_match},
        "reasons": evaluation["reasons"],
        "guidance": case["guidance"],
        "contraindications": case["contraindications"],
        "conditions": {**evaluation["conditions"], "prose_unverified": (case["scope"] or {}).get("conditions", [])},
        "unknown_dimensions": evaluation["unknown"],
        "caveats": caveats,
        "relations": {"superseded_by": superseded_by.get(case["id"], []), "supersedes": supersedes.get(case["id"], [])},
        "evidence_classes": sorted({item["class"] for item in case["evidence"]}),
        "references": case["references"],
        "full_case": f"book show {case['id']}",
    }


def card_size(card: dict[str, Any]) -> int:
    """Budget unit: characters of the card's compact JSON (sorted keys, no ASCII escaping)."""
    return len(json.dumps(card, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


# --- continuation ----------------------------------------------------------

def encode_cursor(query_digest: str, state: str, offset: int) -> str:
    raw = json.dumps({"v": CURSOR_VERSION, "q": query_digest[:32], "s": state[:32], "o": offset}, sort_keys=True, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str, query_digest: str, state: str) -> int:
    try:
        if not isinstance(cursor, str) or len(cursor) > 256:
            raise ValueError
        data = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if set(data) != {"v", "q", "s", "o"} or data["v"] != CURSOR_VERSION or type(data["o"]) is not int or data["o"] < 0:
            raise ValueError
    except (ValueError, binascii.Error, UnicodeDecodeError, TypeError, json.JSONDecodeError):
        _fail("CURSOR_INVALID", "cursor is malformed or from an unsupported version")
    if data["q"] != query_digest[:32]:
        _fail("CURSOR_QUERY_MISMATCH", "cursor belongs to a different query or cutoff; repeat the query without --cursor")
    if data["s"] != state[:32]:
        _fail("CURSOR_STALE", "the Book changed since this cursor was issued; repeat the query from the first page")
    return data["o"]


# --- entry point -----------------------------------------------------------

def consult(root: Path, raw_query: Any, *, explore: bool = False, page_size: int = DEFAULT_PAGE_SIZE, budget: int = DEFAULT_BUDGET, cursor: str | None = None) -> dict[str, Any]:
    from .core import digest
    from .index import consult_candidates, corpus_signature
    from .relations import load_relations

    query = parse_query(raw_query)
    if type(page_size) is not int or not 1 <= page_size <= MAX_PAGE_SIZE:
        _fail("QUERY_INVALID", f"page size must be in 1..{MAX_PAGE_SIZE}")
    if type(budget) is not int or not MIN_BUDGET <= budget <= MAX_BUDGET:
        _fail("QUERY_INVALID", f"budget must be in {MIN_BUDGET}..{MAX_BUDGET} characters")
    cutoff = "explore" if explore else "pertinent"
    query_digest = digest({"query": query, "cutoff": cutoff})
    state = corpus_signature(root)
    offset = decode_cursor(cursor, query_digest, state) if cursor is not None else 0

    tokens = query_tokens(query)
    candidate_ids, index_state = consult_candidates(root, tokens, query_keys(query))
    if candidate_ids is None:
        corpus = {case["id"]: case for case in v0.load_corpus(root)}
    else:
        corpus = {case_id: v0.load_case_file(root / "cases" / f"{case_id}.json") for case_id in sorted(candidate_ids)}
    superseded_by, supersedes = supersession(load_relations(root))

    evaluations: dict[str, dict[str, Any]] = {}
    for case in corpus.values():
        evaluation = evaluate(case, query, tokens)
        if evaluation is not None:
            evaluations[case["id"]] = evaluation
    # One hop, from pertinent results only: a newer case that supersedes a
    # pertinent one is surfaced for comparison. Never recursive, so cycles end.
    for case_id in sorted(evaluations):
        evaluation = evaluations[case_id]
        if evaluation["class"] not in PERTINENT or evaluation["excluded"]:
            continue
        for newer in superseded_by.get(case_id, []):
            reason = {"signal": "supersedes_pertinent", "case": case_id}
            if newer in evaluations:
                if reason not in evaluations[newer]["reasons"]:
                    evaluations[newer]["reasons"].append(reason)
                continue
            path = root / "cases" / f"{newer}.json"
            if not path.exists():
                continue
            # Every case with a signal of its own is already evaluated (the
            # index proposes a superset), so this one is surfaced only by the edge.
            corpus[newer] = v0.load_case_file(path)
            evaluations[newer] = {"id": newer, "class": "EXPLORATORY", "points": 0, "lexical": 0, "reasons": [reason], "excluded": [], "unknown": [], "conditions": {"satisfied": [], "unknown": [], "conflicting": []}, "use": {"query_intent": query.get("intent"), "case_intents": case_triggers(corpus[newer]).get("intents")}}

    excluded = sorted((e for e in evaluations.values() if e["excluded"]), key=lambda e: e["id"])
    ranked = sorted((e for e in evaluations.values() if not e["excluded"]), key=lambda e: rank_key(corpus[e["id"]], e, superseded_by))
    counts = {name.lower(): sum(1 for e in ranked if e["class"] == name) for name in CLASSES}
    included = [e for e in ranked if explore or e["class"] in PERTINENT]

    cards: list[dict[str, Any]] = []
    used = 0
    exhausted = False
    position = offset
    while position < len(included) and len(cards) < page_size:
        evaluation = included[position]
        card = build_card(corpus[evaluation["id"]], evaluation, superseded_by, supersedes)
        size = card_size(card)
        if used + size > budget:
            exhausted = True
            if cards:
                break
            # Not even one whole card fits: say so and point at the full content
            # instead of serving guidance without its contraindications.
            card = {"id": card["id"], "revision": card["revision"], "class": card["class"], "status": card["status"], "budget_exceeded": True, "card_chars": size, "full_case": card["full_case"]}
            size = card_size(card)
        cards.append(card); used += size; position += 1
        if exhausted:
            break
    has_more = position < len(included)
    return {
        "ok": True,
        "operation": "consult",
        "content_trust": "UNTRUSTED_DATA",
        "applicability_claimed": False,
        "engine": "sqlite-derived-candidates+canonical-evaluation" if candidate_ids is not None else "canonical-fallback",
        "index_state": index_state,
        "query_digest": query_digest,
        "cutoff": cutoff,
        "counts": {**counts, "pertinent": counts["specific"] + counts["situational"], "below_cutoff": len(ranked) - len(included), "excluded": len(excluded)},
        "total": len(included),
        "page": {"offset": offset, "size": page_size, "returned": len(cards), "has_more": has_more, "next_cursor": encode_cursor(query_digest, state, position) if has_more else None},
        "budget": {"unit": "characters of compact JSON per card", "limit": budget, "used": used, "exhausted": exhausted},
        "cards": cards,
        "excluded": [{"id": e["id"], "revision": corpus[e["id"]]["revision"]["hash"], "class_if_applicable": e["class"], "reasons": e["excluded"]} for e in excluded[:MAX_EXCLUDED]],
        "excluded_truncated": len(excluded) > MAX_EXCLUDED,
    }


def render_human(result: dict[str, Any]) -> str:
    counts = result["counts"]
    lines = [
        f"consult: {counts['pertinent']} pertinent (specific {counts['specific']}, situational {counts['situational']}); "
        f"exploratory {counts['exploratory']}, lexical {counts['lexical']}, below cutoff {counts['below_cutoff']}, excluded {counts['excluded']}",
        f"cutoff={result['cutoff']} engine={result['engine']} index={result['index_state']}",
        "UNTRUSTED_DATA: historical evidence, not proof that anything applies now.",
    ]
    for number, card in enumerate(result["cards"], start=result["page"]["offset"] + 1):
        lines.append("")
        if card.get("budget_exceeded"):
            lines.append(f"[{number}] {card['id']} rev {card['revision']['number']} {card['class']} — card ({card['card_chars']} chars) exceeds the budget; read it with: {card['full_case']}")
            continue
        lines.append(f"[{number}] {card['id']} rev {card['revision']['number']} ({card['revision']['hash'][:12]}) {card['class']} status={card['status']} use={card['use']['query_intent'] or 'unknown'}/{card['use']['match']}")
        lines.append(f"    {card['title']}")
        why = []
        for reason in card["reasons"]:
            detail = {k: v for k, v in reason.items() if k != "signal"}
            why.append(f"{reason['signal']} {json.dumps(detail, ensure_ascii=False, sort_keys=True)}")
        lines.append("    why: " + "; ".join(why))
        lines.append(f"    guidance [{card['guidance']['class']}]: {card['guidance']['text']}")
        for item in card["contraindications"]:
            lines.append(f"    contraindication: {item}")
        conditions = card["conditions"]
        for state in ("unknown", "conflicting"):
            for item in conditions[state]:
                lines.append(f"    condition {state.upper()}: {item['fact']} {item['op']} {json.dumps(item['value'], ensure_ascii=False)}")
        for item in conditions["prose_unverified"]:
            lines.append(f"    condition UNVERIFIED (prose): {item}")
        if card["unknown_dimensions"]:
            lines.append("    unknown: " + ", ".join(card["unknown_dimensions"]))
        for caveat in card["caveats"]:
            lines.append(f"    CAVEAT {caveat['kind']}: {json.dumps({k: v for k, v in caveat.items() if k != 'kind'}, ensure_ascii=False, sort_keys=True)}")
        if card["references"]:
            lines.append("    references: " + ", ".join(card["references"]))
        lines.append(f"    full: {card['full_case']}")
    if result["budget"]["exhausted"]:
        lines.append("")
        lines.append(f"budget exhausted ({result['budget']['used']}/{result['budget']['limit']} {result['budget']['unit']}).")
    if result["page"]["has_more"]:
        lines.append("")
        lines.append(f"more results: repeat the same query with --cursor {result['page']['next_cursor']}")
    if result["excluded"]:
        lines.append("")
        for item in result["excluded"]:
            lines.append(f"excluded {item['id']}: " + "; ".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in item["reasons"]))
    return "\n".join(lines) + "\n"
