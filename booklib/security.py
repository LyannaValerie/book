"""Security boundaries for all persisted and served UNTRUSTED_DATA."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .core import BookError


SECRET_PATTERNS = (
    re.compile(r"(?i)\b(password|passwd|credential|api[_-]?key|secret|token)\s*[:=]\s*\S+"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)
MAX_TEXT = 8192


def reject_sensitive(value: Any, where: str = "content") -> None:
    if isinstance(value, str):
        if len(value) > MAX_TEXT:
            raise BookError("CONTENT_LIMIT", f"{where} exceeds {MAX_TEXT} characters")
        if any(pattern.search(value) for pattern in SECRET_PATTERNS):
            raise BookError("SENSITIVE_CONTENT", f"secret-like content rejected at {where}")
    elif isinstance(value, list):
        if len(value) > 128:
            raise BookError("CONTENT_LIMIT", f"{where} has too many items")
        for index, item in enumerate(value):
            reject_sensitive(item, f"{where}[{index}]")
    elif isinstance(value, dict):
        if len(value) > 128:
            raise BookError("CONTENT_LIMIT", f"{where} has too many fields")
        for key, item in value.items():
            if re.search(r"(?i)(password|credential|secret|token|api.?key)", str(key)):
                raise BookError("SENSITIVE_CONTENT", f"sensitive field rejected at {where}.{key}")
            reject_sensitive(item, f"{where}.{key}")


def redact_summary(text: str, *, maximum: int = 1024) -> str:
    if any(pattern.search(text) for pattern in SECRET_PATTERNS):
        return "[REDACTED_SENSITIVE_CONTENT]"
    return text[:maximum]


def safe_resolve_file(root: Path, reference: str, allowed_roots: list[Path]) -> Path:
    raw = reference.removeprefix("file:")
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise BookError("REFERENCE_UNRESOLVABLE", f"file reference cannot resolve: {exc}") from exc
    for allowed in allowed_roots:
        try:
            resolved.relative_to(allowed.resolve(strict=True))
            return resolved
        except (ValueError, OSError):
            continue
    raise BookError("REFERENCE_PATH_FORBIDDEN", "file reference escapes configured roots")
