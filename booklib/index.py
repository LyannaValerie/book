"""Disposable SQLite/FTS index. Canonical JSON/JSONL remains authoritative."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import hashlib
from pathlib import Path
from typing import Any

from .core import BookError, grant_shared_group_access, lock
from .relations import load_relations
from .views import case_views
from . import v0


def index_path(root: Path) -> Path:
    return root / ".book" / "index.sqlite3"


def corpus_signature(root: Path) -> str:
    hasher = hashlib.sha256()
    for directory in ("cases", "catalog", "relations"):
        for path in sorted((root / directory).glob("*.json")) if (root / directory).exists() else []:
            stat = path.stat(); hasher.update(f"{directory}/{path.name}:{stat.st_size}:{stat.st_mtime_ns}\n".encode())
    return hasher.hexdigest()


def reindex(root: Path) -> dict[str, Any]:
    target = index_path(root); target.parent.mkdir(parents=True, exist_ok=True)
    with lock(root, "index"):
        fd, temp_name = tempfile.mkstemp(prefix="index.", suffix=".sqlite3", dir=target.parent)
        grant_shared_group_access(fd); os.close(fd)
        try:
            db = sqlite3.connect(temp_name)
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("CREATE TABLE cases(id TEXT PRIMARY KEY, title TEXT, body TEXT, views TEXT, revision TEXT)")
            try:
                db.execute("CREATE VIRTUAL TABLE case_fts USING fts5(id UNINDEXED, title, body, views)"); fts = True
            except sqlite3.OperationalError:
                fts = False
            cases = v0.load_corpus(root); relations = load_relations(root)
            relation_text: dict[str, list[str]] = {}
            for edge in relations:
                relation_text.setdefault(edge["from"], []).extend([edge["type"], edge["to"]])
            for case in cases:
                body = " ".join([*v0.searchable_fields(case).values(), *relation_text.get(case["id"], [])]); views = " ".join(case_views(root, case))
                db.execute("INSERT INTO cases VALUES(?,?,?,?,?)", (case["id"], case["title"], body, views, case["revision"]["hash"]))
                if fts: db.execute("INSERT INTO case_fts VALUES(?,?,?,?)", (case["id"], case["title"], body, views))
            db.execute("CREATE TABLE relations(id TEXT PRIMARY KEY, source TEXT, kind TEXT, target TEXT)")
            for edge in load_relations(root): db.execute("INSERT INTO relations VALUES(?,?,?,?)", (edge["id"], edge["from"], edge["type"], edge["to"]))
            db.execute("INSERT INTO metadata VALUES('derived','true')"); db.execute("INSERT INTO metadata VALUES('fts5',?)", (str(fts).lower(),)); db.execute("INSERT INTO metadata VALUES('corpus_signature',?)", (corpus_signature(root),)); db.commit(); db.close(); os.replace(temp_name, target)
        finally:
            try: os.unlink(temp_name)
            except FileNotFoundError: pass
    return {"ok": True, "operation": "reindex", "derived": True, "cases": len(cases), "relations": len(load_relations(root)), "fts5": fts, "path": str(target)}


def candidate_ids(root: Path, tokens: set[str]) -> set[str] | None:
    path = index_path(root)
    if not path.exists() or not tokens: return None
    db = None
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        metadata = dict(db.execute("SELECT key,value FROM metadata"))
        if metadata.get("fts5") != "true" or metadata.get("corpus_signature") != corpus_signature(root): return None
        query = " OR ".join('"' + token.replace('"', '""') + '"' for token in sorted(tokens))
        return {row[0] for row in db.execute("SELECT id FROM case_fts WHERE case_fts MATCH ?", (query,))}
    except sqlite3.DatabaseError:
        return None
    finally:
        if db is not None: db.close()


def check_index(root: Path) -> dict[str, Any]:
    path = index_path(root)
    if not path.exists(): return {"state": "MISSING", "derived": True}
    db = None
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True); integrity = db.execute("PRAGMA integrity_check").fetchone()[0]; count = db.execute("SELECT count(*) FROM cases").fetchone()[0]; metadata = dict(db.execute("SELECT key,value FROM metadata"))
        canonical = len(v0.load_corpus(root))
        current_signature = corpus_signature(root); ready = integrity == "ok" and count == canonical and metadata.get("corpus_signature") == current_signature
        return {"state": "READY" if ready else "DIVERGED", "derived": True, "cases": count, "canonical_cases": canonical, "integrity": integrity}
    except sqlite3.DatabaseError as exc:
        return {"state": "CORRUPT", "derived": True, "reason": str(exc)}
    finally:
        if db is not None:
            db.close()
