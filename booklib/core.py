"""Shared deterministic primitives and storage errors."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import secrets
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


class BookError(Exception):
    def __init__(self, code: str, message: str, details: Any | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


def grant_shared_group_access(fd: int) -> None:
    """Make a writable Book data file accessible to ``pinker-agents``.

    The containing setgid directory supplies the group identity. Preserve the
    owner and other bits while adding only group read/write access.
    """
    mode = stat.S_IMODE(os.fstat(fd).st_mode)
    required = stat.S_IRGRP | stat.S_IWGRP
    if mode & required != required:
        os.fchmod(fd, mode | required)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def opaque_id(prefix: str, *, entropy: int = 9) -> str:
    return f"{prefix}-{secrets.token_hex(entropy).upper()}"


def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BookError("JSON_DUPLICATE_KEY", f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def loads_json(text: str, where: str = "input") -> Any:
    try:
        return json.loads(text, object_pairs_hook=reject_duplicate_keys)
    except BookError:
        raise
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise BookError("JSON_INVALID", f"invalid JSON in {where}: {exc}") from exc


def read_json(path: Path, *, max_bytes: int = 1_048_576) -> Any:
    try:
        if path.stat().st_size > max_bytes:
            raise BookError("CONTENT_LIMIT", f"{path} exceeds {max_bytes} bytes")
        return loads_json(path.read_text(encoding="utf-8"), str(path))
    except BookError:
        raise
    except OSError as exc:
        raise BookError("READ_FAILED", f"cannot read {path}: {exc.strerror or exc}") from exc


def atomic_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        grant_shared_group_access(fd)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


@contextlib.contextmanager
def lock(root: Path, name: str) -> Iterator[None]:
    lock_dir = root / ".book" / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    with (lock_dir / f"{name}.lock").open("a+", encoding="utf-8") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def require_object(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BookError("SCHEMA_INVALID", f"{where} must be an object")
    return value


def require_text(value: Any, where: str, *, maximum: int = 8192, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise BookError("SCHEMA_INVALID", f"{where} must be a non-empty string")
    if len(value) > maximum:
        raise BookError("CONTENT_LIMIT", f"{where} exceeds {maximum} characters")
    return value


def require_fields(value: dict[str, Any], required: set[str], allowed: set[str], where: str) -> None:
    missing = sorted(required - value.keys())
    unknown = sorted(value.keys() - allowed)
    if missing or unknown:
        raise BookError("SCHEMA_INVALID", f"invalid fields in {where}", {"missing": missing, "unknown": unknown})
