#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Cache of query embeddings: a repeated query skips the embedder.

Embedding a query takes about 1.8 s on a 2014 Xeon and is almost all of the time of a search. The same
query text gives the same vector, so the vector is kept in a small SQLite file next to the index:

    <index>.query_cache.sqlite      (or the path in ALEXANDRIA_QUERY_CACHE; the value "off" turns the cache off)

The key is the SHA-256 of the model tag and the exact text sent to the embedder, so the file holds no query text.
Entries are dropped least recently used first once there are more than MAX_ENTRIES (a 4096-dimension vector is
16 KB). A cache error of any kind is ignored: the caller then embeds the query as before. Delete the file to reset it.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from pathlib import Path

import numpy as np

MAX_ENTRIES = 2000
ENV = "ALEXANDRIA_QUERY_CACHE"
_SCHEMA = "CREATE TABLE IF NOT EXISTS q (key TEXT PRIMARY KEY, dim INTEGER NOT NULL, vec BLOB NOT NULL, used REAL NOT NULL)"


def cache_path(db_file: str | os.PathLike[str] | None) -> Path | None:
    """Where the cache for this index lives, or None when the cache is off or the index is not a file."""
    setting = os.environ.get(ENV, "").strip()
    if setting.lower() == "off":
        return None
    if setting:
        return Path(setting)
    if not db_file:
        return None
    return Path(str(db_file) + ".query_cache.sqlite")


def _key(model: str, text: str) -> str:
    return hashlib.sha256(model.encode("utf-8") + b"\0" + text.encode("utf-8")).hexdigest()


def _open(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=2.0)
    conn.execute(_SCHEMA)
    return conn


def get(path: Path | None, model: str, text: str) -> np.ndarray | None:
    """The cached vector of this query, or None."""
    if path is None:
        return None
    try:
        conn = _open(path)
        try:
            key = _key(model, text)
            row = conn.execute("SELECT dim, vec FROM q WHERE key = ?", (key,)).fetchone()
            if row is None:
                return None
            dim, blob = row
            vector = np.frombuffer(blob, dtype=np.float32)
            if dim != vector.size or dim == 0:
                return None
            conn.execute("UPDATE q SET used = ? WHERE key = ?", (time.time(), key))
            conn.commit()
            return vector.copy()
        finally:
            conn.close()
    except (sqlite3.Error, OSError, ValueError):
        return None


def put(path: Path | None, model: str, text: str, vector: np.ndarray) -> None:
    """Remember the vector of this query."""
    if path is None:
        return
    vec = np.asarray(vector, dtype=np.float32)
    if vec.ndim != 1 or vec.size == 0 or not np.all(np.isfinite(vec)):
        return
    try:
        conn = _open(path)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO q (key, dim, vec, used) VALUES (?, ?, ?, ?)",
                (_key(model, text), int(vec.size), vec.tobytes(), time.time()),
            )
            conn.execute(
                "DELETE FROM q WHERE key IN (SELECT key FROM q ORDER BY used DESC LIMIT -1 OFFSET ?)",
                (MAX_ENTRIES,),
            )
            conn.commit()
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        return
