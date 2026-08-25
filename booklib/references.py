"""Namespace-specific authority resolvers; no arbitrary shell adapters."""

from __future__ import annotations

import subprocess
import re
from pathlib import Path
from typing import Any

from .core import BookError, read_json
from .security import safe_resolve_file
from . import v0


def config(root: Path) -> dict[str, Any]:
    path = root / "book.config.json"
    return read_json(path) if path.exists() else {}


def resolve(root: Path, reference: str) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": True, "operation": "resolve", "reference": reference, "content_trust": "UNTRUSTED_DATA"}
    if ":" not in reference:
        return {**result, "state": "UNKNOWN", "reason": "reference has no known namespace"}
    namespace, value = reference.split(":", 1)
    try:
        if namespace == "book":
            case = v0.find_case(root, value)
            return {**result, "state": "RESOLVED", "authority": "book", "metadata": {"id": case["id"], "title": case["title"], "status": case["status"]}}
        if namespace == "file":
            cfg = config(root); roots = [root, *[Path(item) for item in cfg.get("file_roots", [])]]
            path = safe_resolve_file(root, reference, roots)
            return {**result, "state": "RESOLVED", "authority": "filesystem", "metadata": {"path": str(path), "bytes": path.stat().st_size, "is_file": path.is_file()}}
        if namespace == "git":
            if not re.fullmatch(r"[0-9A-Fa-f]{7,64}", value):
                return {**result, "state": "UNRESOLVABLE", "authority": "git", "reason": "git object syntax is invalid"}
            repo = Path(config(root).get("git_root", root))
            if not (repo / ".git").exists():
                return {**result, "state": "UNAVAILABLE", "authority": "git", "reason": "configured Git repository is unavailable"}
            proc = subprocess.run(["git", "-C", str(repo), "cat-file", "-t", value], text=True, capture_output=True, timeout=5, check=False)
            if proc.returncode:
                return {**result, "state": "UNRESOLVABLE", "authority": "git", "reason": "object is absent"}
            return {**result, "state": "RESOLVED", "authority": "git", "metadata": {"object": value, "type": proc.stdout.strip(), "repository": str(repo)}}
        if namespace == "trama":
            adapter = config(root).get("trama_catalog")
            if not adapter:
                return {**result, "state": "UNAVAILABLE", "authority": "trama", "reason": "optional adapter is not configured"}
            catalog_path = safe_resolve_file(root, f"file:{adapter}", [root, Path(adapter).parent])
            catalog = read_json(catalog_path)
            if value not in catalog:
                return {**result, "state": "UNRESOLVABLE", "authority": "trama", "reason": "key is absent from adapter catalog"}
            return {**result, "state": "RESOLVED", "authority": "trama", "metadata": catalog[value]}
        if namespace == "event":
            from .events import find_event
            event = find_event(root, value)
            return {**result, "state": "RESOLVED", "authority": "book-event-log", "metadata": {"event_id": event["event_id"], "kind": event["kind"], "timestamp": event["timestamp"]}}
    except BookError as exc:
        if exc.code in {"CASE_NOT_FOUND", "REFERENCE_UNRESOLVABLE"}:
            return {**result, "state": "UNRESOLVABLE", "reason": exc.message}
        raise
    return {**result, "state": "UNKNOWN", "reason": f"namespace {namespace!r} is unknown"}
