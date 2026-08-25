"""Deterministic lexical retrieval with optional view scope and index fallback."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .core import BookError
from .views import case_views, within_view
from .index import candidate_ids
from .relations import load_relations
from . import v0


def search_cases(root: Path, query: str, within: str | None = None, *, limit: int = 50) -> dict[str, Any]:
    tokens = set(v0.normalize_tokens(query))
    if not tokens or len(query) > v0.MAX_SHORT_TEXT:
        raise BookError("CONTENT_LIMIT", "query must contain searchable text within 512 characters")
    indexed_ids = candidate_ids(root, tokens)
    corpus = v0.load_corpus(root) if indexed_ids is None else [v0.load_case_file(root / "cases" / f"{case_id}.json") for case_id in sorted(indexed_ids)]
    relation_text: dict[str, list[str]] = {}
    for edge in load_relations(root): relation_text.setdefault(edge["from"], []).extend([edge["type"], edge["to"]])
    results = []
    for case in corpus:
        if within and not within_view(root, case, within):
            continue
        score, matched = v0.lexical_score(case, tokens)
        view_text = " ".join(case_views(root, case))
        view_tokens = set(v0.normalize_tokens(view_text))
        view_matches = sum(1 for token in tokens if token in view_tokens)
        score += view_matches * 2
        relation_tokens = set(v0.normalize_tokens(" ".join(relation_text.get(case["id"], []))))
        score += sum(2 for token in tokens if token in relation_tokens)
        if score:
            candidate = v0.compact_candidate(case, score, matched)
            all_views = case_views(root, case)
            candidate["views"] = [value[:128] for value in all_views[:8]]
            candidate["views_truncated"] = len(all_views) > 8 or any(len(value) > 128 for value in all_views[:8])
            results.append(candidate)
    results.sort(key=lambda item: (-item["score"], item["id"]))
    return {"ok": True, "operation": "search", "query": query, "within": within, "count": len(results), "returned": min(len(results), limit), "results": results[:limit], "truncated": len(results) > limit, "content_trust": "UNTRUSTED_DATA", "applicability_claimed": False, "engine": "sqlite-fts5-derived+canonical-verification" if indexed_ids is not None else "lexical-canonical-fallback"}
