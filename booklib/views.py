"""Facet-backed tree projections. Views never define case identity."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .core import BookError, atomic_write, read_json, utc_now
from . import v0


def facet_path(root: Path, case_id: str) -> Path:
    return root / "catalog" / f"{case_id}.json"


def infer_facets(case: dict[str, Any]) -> dict[str, Any]:
    repository = (case.get("environment") or {}).get("repository", "")
    references = case.get("references", [])
    domain = repository.rstrip("/").rsplit("/", 1)[-1] if repository else (str(references[0]).split(":", 1)[0].title() if references else "Uncategorized")
    components = (case.get("scope") or {}).get("components", [])
    views = [f"{domain}/{str(component).replace('-', ' ').title().replace(' ', '')}" for component in components]
    return {"case_id": case["id"], "domain": domain, "views": views, "synthetic": False, "updated_at": case["revision"]["updated_at"]}


def get_facets(root: Path, case: dict[str, Any]) -> dict[str, Any]:
    path = facet_path(root, case["id"])
    return read_json(path) if path.exists() else infer_facets(case)


def set_facets(root: Path, case_id: str, domain: str, views: list[str], *, synthetic: bool = False) -> dict[str, Any]:
    case = v0.find_case(root, case_id)
    normalized = []
    for view in views:
        value = view.strip().strip("/")
        if value and value != domain and not value.startswith(domain + "/"):
            value = f"{domain}/{value}"
        if value:
            normalized.append(value)
    data = {"case_id": case["id"], "domain": domain.strip(), "views": sorted(set(normalized)), "synthetic": synthetic, "updated_at": utc_now()}
    atomic_write(facet_path(root, case_id), data)
    return data


def case_views(root: Path, case: dict[str, Any]) -> list[str]:
    facets = get_facets(root, case)
    return sorted(set([facets["domain"], *facets.get("views", [])]))


def within_view(root: Path, case: dict[str, Any], view: str) -> bool:
    target = view.strip().strip("/").casefold()
    return any(item.casefold() == target or item.casefold().startswith(target + "/") for item in case_views(root, case))


def list_view(root: Path, view: str | None = None, *, limit: int = 100) -> dict[str, Any]:
    target = (view or "").strip().strip("/")
    children: set[str] = set()
    cases: list[dict[str, Any]] = []
    for case in v0.load_corpus(root):
        paths = case_views(root, case)
        if not target:
            children.update(path.split("/", 1)[0] for path in paths)
            continue
        matched = False
        for path in paths:
            if path.casefold() == target.casefold():
                matched = True
            elif path.casefold().startswith(target.casefold() + "/"):
                remainder = path[len(target) + 1:]
                children.add(remainder.split("/", 1)[0])
        if matched:
            cases.append({"id": case["id"], "title": case["title"], "status": case["status"]})
    ordered_children = sorted(children, key=str.casefold); ordered_cases = sorted(cases, key=lambda item: item["id"])
    total = len(ordered_children) + len(ordered_cases); child_slice = ordered_children[:limit]; remaining = max(0, limit - len(child_slice))
    return {"ok": True, "operation": "list", "view": target or "/", "children": child_slice, "cases": ordered_cases[:remaining], "count": total, "truncated": total > limit, "content_trust": "UNTRUSTED_DATA"}


def show_item(root: Path, identifier: str, *, metadata_only: bool = False) -> dict[str, Any]:
    if identifier.startswith("B-"):
        case = v0.find_case(root, identifier)
        if metadata_only:
            all_views = case_views(root, case)
            return {"ok": True, "content_trust": "UNTRUSTED_DATA", "case": {"id": case["id"], "title": case["title"], "status": case["status"], "views": [value[:128] for value in all_views[:16]], "views_truncated": len(all_views) > 16 or any(len(value) > 128 for value in all_views[:16]), "revision": case["revision"]["hash"]}}
        return {"content_trust": "UNTRUSTED_DATA", "case": case, "views": case_views(root, case)}
    result = list_view(root, identifier)
    if not result["children"] and not result["cases"]:
        raise BookError("VIEW_NOT_FOUND", f"view {identifier!r} does not exist")
    return result
