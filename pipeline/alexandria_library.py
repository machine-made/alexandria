#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Where a library keeps its vectors.

A library with ``scriptorium.toml`` at its root keeps one vector set per embedder:

    <library>/vectors/<embedder-id>/embedder.json
    <library>/vectors/<embedder-id>/<category>/<key>.jsonl

``active_embedder`` in ``scriptorium.toml`` names the set to search. A library without
that file is a legacy library and keeps ``embeddings.jsonl`` inside each work directory.

A vector row is valid only for the embedder that made it, so every row must carry the
model tag named in ``embedder.json`` (``runtime_tag``) and the declared dimension. Rows
from another model are an error, never a silent mix.
"""
from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from typing import Any

CONFIG_NAME = "scriptorium.toml"
LIBRARY_ENV = "ALEXANDRIA_LIBRARY"


def default_library() -> Path:
    """Library root when --library is not given: $ALEXANDRIA_LIBRARY, else the current directory."""
    return Path(os.environ.get(LIBRARY_ENV) or ".")


def active_embedder(library_root: Path) -> tuple[str | None, dict[str, Any]]:
    """Return (embedder id, descriptor) of the active vector set, or (None, {}) for a legacy library."""
    config = library_root / CONFIG_NAME
    if not config.is_file():
        return None, {}
    embedder_id = tomllib.loads(config.read_text(encoding="utf-8")).get("active_embedder")
    if not isinstance(embedder_id, str) or not embedder_id:
        raise ValueError(f"{config}: active_embedder is missing")
    descriptor = library_root / "vectors" / embedder_id / "embedder.json"
    if not descriptor.is_file():
        raise ValueError(f"{descriptor}: missing embedder descriptor")
    return embedder_id, json.loads(descriptor.read_text(encoding="utf-8"))


def embeddings_path(library_root: Path, category: str, key: str, work_dir: Path) -> Path:
    """Vector file of one work for the library's active embedder: <key>.jsonl, or <key>.jsonl.gz when only that exists."""
    embedder_id, _ = active_embedder(library_root)
    if embedder_id is None:
        return work_dir / "embeddings.jsonl"
    plain = library_root / "vectors" / embedder_id / category / f"{key}.jsonl"
    packed = plain.with_name(plain.name + ".gz")
    return packed if not plain.is_file() and packed.is_file() else plain


def embedding_row_errors(rows: list[dict[str, Any]], descriptor: dict[str, Any], label: str) -> list[str]:
    """Rows that do not match the active embedder's tag or dimension. Empty for a legacy library."""
    if not descriptor:
        return []
    tag = descriptor.get("runtime_tag")
    dim = descriptor.get("dim")
    errors: list[str] = []
    models = {str(row.get("model") or row.get("embedding_model") or "") for row in rows}
    wrong = sorted(models - {tag})
    if wrong:
        errors.append(f"{label}: vector model {wrong} is not the active embedder {tag!r}")
    dims = {row.get("embedding_dim") for row in rows}
    if dims - {dim}:
        errors.append(f"{label}: vector dimension {sorted(map(str, dims - {dim}))} is not {dim}")
    return errors
