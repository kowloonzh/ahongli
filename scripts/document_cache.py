#!/usr/bin/env python3
"""Content-addressed helpers for auditable source-document caches."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_document(path: Path, expected_sha256: str) -> bool:
    path = Path(path)
    return path.is_file() and file_sha256(path) == expected_sha256


def atomic_write_text(path: Path, content: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def store_document(
    *,
    directory: Path,
    announcement_id: str,
    logical_role: str,
    suffix: str,
    content: bytes,
) -> dict[str, str]:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{_safe_component(announcement_id)}-{_safe_component(logical_role)}{suffix}"
    path = directory / filename
    digest = hashlib.sha256(content).hexdigest()
    if path.exists() and file_sha256(path) == digest:
        return {"source_file": filename, "sha256": digest, "cache_status": "reused"}
    cache_status = "replaced" if path.exists() else "stored"
    with tempfile.NamedTemporaryFile(dir=directory, prefix=f".{filename}.", delete=False) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    os.replace(temporary, path)
    return {"source_file": filename, "sha256": digest, "cache_status": cache_status}
