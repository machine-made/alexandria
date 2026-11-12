#!/usr/bin/env python3
"""Alexandria SQLite search index — FTS5 + numpy cosine + hybrid + CLI.

Derived, rebuildable state. Read-only over the filed library; never writes to
library files (*.md, *.bib, metadata.json, registry, root bibliography).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from alexandria_metadata_common import VALID_CATEGORIES, load_json, sha256_file
from alexandria_scholar_bib import BibTeXParseError, parse_one_entry

SCHEMA_VERSION = "1"
DEFAULT_EMBED_MODEL = os.environ.get("EMBED_MODEL", "qwen3-embedding:8b-q8_0")
DEFAULT_EMBED_DIM = 4096
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
BIB_ENTRY_HEAD_RE = re.compile(r"@\s*([A-Za-z]+)\s*\{\s*([^,\s]+)", re.MULTILINE)

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS works (
    key TEXT PRIMARY KEY,
    bib_key TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    authors TEXT NOT NULL DEFAULT '[]',
    year TEXT NOT NULL DEFAULT '',
    doi TEXT NOT NULL DEFAULT '',
    isbn TEXT NOT NULL DEFAULT '',
    arxiv_id TEXT NOT NULL DEFAULT '',
    md_path TEXT NOT NULL,
    bib_path TEXT NOT NULL DEFAULT '',
    work_dir TEXT NOT NULL DEFAULT '',
    md_hash TEXT NOT NULL DEFAULT '',
    bib_hash TEXT NOT NULL DEFAULT '',
    metadata_hash TEXT NOT NULL DEFAULT '',
    source_hashes TEXT NOT NULL DEFAULT '{}',
    parser TEXT NOT NULL DEFAULT '',
    indexed_at TEXT NOT NULL,
    valid INTEGER NOT NULL DEFAULT 1,
    validation_errors TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    key TEXT NOT NULL,
    bib_key TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    year TEXT NOT NULL DEFAULT '',
    authors TEXT NOT NULL DEFAULT '[]',
    chunk_ordinal INTEGER NOT NULL,
    text TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    heading_path TEXT NOT NULL DEFAULT '[]',
    md_path TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (key) REFERENCES works(key) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_chunks_key_ordinal ON chunks(key, chunk_ordinal);
CREATE INDEX IF NOT EXISTS idx_chunks_bib_key ON chunks(bib_key);
-- idx_chunks_text_hash (text_hash) and idx_emb_dim (embedding_dim) intentionally absent:
-- no query in this file filters/joins on either column (text_hash is only read for the
-- stale-embedding comparison in validate_index, embedding_dim only via SELECT DISTINCT /
-- MAX), and embedding_dim is a single constant (4096) so an index on it could never narrow
-- a scan. 4.9 MB + index overhead reclaimed on the next full rebuild.

CREATE TABLE IF NOT EXISTS chunk_embeddings (
    chunk_id TEXT PRIMARY KEY,
    key TEXT NOT NULL,
    bib_key TEXT NOT NULL,
    embedding BLOB NOT NULL,
    embedding_dim INTEGER NOT NULL,
    embedding_model TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    FOREIGN KEY (chunk_id) REFERENCES chunks(chunk_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_emb_key ON chunk_embeddings(key);

CREATE TABLE IF NOT EXISTS source_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    filed_name TEXT NOT NULL DEFAULT '',
    format TEXT NOT NULL DEFAULT '',
    sha256 TEXT NOT NULL DEFAULT '',
    size INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (key) REFERENCES works(key) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_source_files_key ON source_files(key);

CREATE TABLE IF NOT EXISTS bib_entries (
    key TEXT PRIMARY KEY,
    bib_key TEXT NOT NULL,
    entry_type TEXT NOT NULL DEFAULT '',
    bib_path TEXT NOT NULL DEFAULT '',
    bib_text TEXT NOT NULL DEFAULT '',
    bib_hash TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (key) REFERENCES works(key) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS index_meta (
    meta_key TEXT PRIMARY KEY,
    meta_value TEXT NOT NULL
);

-- External-content FTS5: text lives once, in chunks.text — chunks_fts stores only the
-- inverted index, not a second copy (was ~142 MB of duplicated text). content_rowid is the
-- chunks table's implicit SQLite rowid (chunks has no INTEGER PRIMARY KEY column to alias
-- it); FTS5 shadow tables store that rowid as data, and SQLite is free to renumber rowids on
-- VACUUM for a table without one — a bare VACUUM on chunks would desync chunks_fts silently.
-- Never VACUUM index.db without following with `INSERT INTO chunks_fts(chunks_fts) VALUES
-- ('rebuild')` (see export-serving, the one place that VACUUMs).
--
-- Because this is external-content, FTS is NOT auto-maintained on writes to chunks — the
-- chunks_ai/ad/au triggers below do that, so every INSERT/DELETE/UPDATE on chunks (harvest,
-- upsert, rebuild, cascade deletes from works) keeps chunks_fts in sync structurally, with
-- no per-call-site bookkeeping to forget.
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id UNINDEXED,
    key UNINDEXED,
    bib_key UNINDEXED,
    category UNINDEXED,
    title,
    text,
    content = 'chunks',
    content_rowid = 'rowid',
    tokenize = 'porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, chunk_id, key, bib_key, category, title, text)
    VALUES (new.rowid, new.chunk_id, new.key, new.bib_key, new.category, new.title, new.text);
END;

CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, chunk_id, key, bib_key, category, title, text)
    VALUES ('delete', old.rowid, old.chunk_id, old.key, old.bib_key, old.category, old.title, old.text);
END;

CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, chunk_id, key, bib_key, category, title, text)
    VALUES ('delete', old.rowid, old.chunk_id, old.key, old.bib_key, old.category, old.title, old.text);
    INSERT INTO chunks_fts(rowid, chunk_id, key, bib_key, category, title, text)
    VALUES (new.rowid, new.chunk_id, new.key, new.bib_key, new.category, new.title, new.text);
END;
"""

REQUIRED_WORK_COLUMNS = {
    "key",
    "bib_key",
    "category",
    "title",
    "authors",
    "year",
    "doi",
    "isbn",
    "arxiv_id",
    "md_path",
    "bib_path",
    "md_hash",
    "bib_hash",
    "source_hashes",
    "valid",
    "validation_errors",
}
REQUIRED_CHUNK_COLUMNS = {
    "chunk_id",
    "key",
    "bib_key",
    "category",
    "title",
    "year",
    "chunk_ordinal",
    "text",
    "text_hash",
    "heading_path",
    "md_path",
}
REQUIRED_EMBEDDING_COLUMNS = {
    "chunk_id",
    "key",
    "bib_key",
    "embedding",
    "embedding_dim",
    "embedding_model",
    "text_hash",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_sha256_optional(path: Path) -> str:
    if not path.is_file():
        return ""
    return sha256_file(path)


def compact_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, (list, tuple, set)):
        return "; ".join(text for item in value if (text := compact_text(item)))
    if isinstance(value, dict):
        for key in ("x-default", "en", "en-US"):
            if key in value:
                text = compact_text(value[key])
                if text:
                    return text
        for item in value.values():
            text = compact_text(item)
            if text:
                return text
        return ""
    return " ".join(str(value).split())


def authors_list(metadata: dict[str, Any]) -> list[str]:
    raw = metadata.get("authors")
    if isinstance(raw, list):
        out = [compact_text(item) for item in raw if compact_text(item)]
        if out:
            return out
    author = compact_text(metadata.get("author"))
    if not author:
        return []
    parts = re.split(r"\s+and\s+|;\s*", author)
    return [part.strip() for part in parts if part.strip()]


def pack_embedding(vector: Sequence[float]) -> bytes:
    arr = np.asarray(vector, dtype=np.float32)
    return arr.tobytes(order="C")


def unpack_embedding(blob: bytes, dim: int) -> np.ndarray:
    expected = dim * 4
    if len(blob) != expected:
        raise ValueError(f"embedding blob length {len(blob)} != {expected} for dim={dim}")
    return np.frombuffer(blob, dtype=np.float32).copy()


def cosine_similarity(query: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """Row-wise cosine similarity of query (d,) against matrix (n, d)."""
    if matrix.size == 0:
        return np.zeros(0, dtype=np.float64)
    q = query.astype(np.float64, copy=False)
    m = matrix.astype(np.float64, copy=False)
    q_norm = np.linalg.norm(q)
    if q_norm == 0.0:
        return np.zeros(m.shape[0], dtype=np.float64)
    m_norms = np.linalg.norm(m, axis=1)
    dots = m @ q
    denom = m_norms * q_norm
    out = np.zeros(m.shape[0], dtype=np.float64)
    np.divide(dots, denom, out=out, where=denom > 0)
    return out


def snippet_from(text: str, query: str, width: int = 160) -> str:
    compact = " ".join(text.split())
    if not compact:
        return ""
    lower = compact.lower()
    needle = " ".join(query.split()).lower() if query else ""
    pos = lower.find(needle) if needle else -1
    if pos < 0:
        for token in needle.split():
            if len(token) >= 3:
                pos = lower.find(token)
                if pos >= 0:
                    break
    if pos < 0:
        return compact[:width] + ("…" if len(compact) > width else "")
    start = max(0, pos - width // 4)
    end = min(len(compact), start + width)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(compact) else ""
    return prefix + compact[start:end] + suffix


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    set_meta(conn, "schema_version", SCHEMA_VERSION)
    conn.commit()


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO index_meta(meta_key, meta_value) VALUES (?, ?) "
        "ON CONFLICT(meta_key) DO UPDATE SET meta_value = excluded.meta_value",
        (key, value),
    )


def get_meta(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT meta_value FROM index_meta WHERE meta_key = ?", (key,)).fetchone()
    return str(row["meta_value"]) if row else default


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row["name"]) for row in rows}


def schema_column_check(conn: sqlite3.Connection) -> list[str]:
    errors: list[str] = []
    checks = {
        "works": REQUIRED_WORK_COLUMNS,
        "chunks": REQUIRED_CHUNK_COLUMNS,
        "chunk_embeddings": REQUIRED_EMBEDDING_COLUMNS,
        "source_files": {"id", "key", "name", "sha256"},
        "bib_entries": {"key", "bib_key", "entry_type", "bib_path"},
        "index_meta": {"meta_key", "meta_value"},
    }
    for table, required in checks.items():
        cols = table_columns(conn, table)
        missing = sorted(required - cols)
        if missing:
            errors.append(f"{table}: missing columns {missing}")
    return errors


@dataclass
class HarvestedWork:
    key: str
    category: str
    md_path: Path
    bib_path: Path | None
    work_dir: Path
    metadata: dict[str, Any]
    bib_key: str
    bib_text: str
    bib_entry_type: str
    chunks: list[dict[str, Any]] = field(default_factory=list)
    embeddings: list[dict[str, Any]] = field(default_factory=list)
    valid: bool = True
    validation_errors: list[str] = field(default_factory=list)


def parse_bib_key_and_type(bib_text: str) -> tuple[str, str]:
    match = BIB_ENTRY_HEAD_RE.search(bib_text)
    if not match:
        return "", ""
    return match.group(2).strip(), match.group(1).strip().lower()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL {path}:{line_no}: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"expected JSON object in {path}:{line_no}")
        rows.append(data)
    return rows


def discover_work_markdown(library_root: Path) -> list[Path]:
    paths: list[Path] = []
    for category in sorted(VALID_CATEGORIES):
        category_dir = library_root / category
        if not category_dir.is_dir():
            continue
        for md in sorted(category_dir.glob("*.md")):
            if md.name.startswith("."):
                continue
            if md.name.endswith(".notes.md"):
                continue
            paths.append(md)
    return paths


def harvest_work(library_root: Path, md_path: Path) -> HarvestedWork:
    library_root = library_root.resolve()
    md_path = md_path.resolve()
    category = md_path.parent.name
    key = md_path.stem
    work_dir = md_path.parent / key
    bib_path = md_path.with_suffix(".bib")
    metadata_path = work_dir / "metadata.json"
    chunks_path = work_dir / "chunks.jsonl"
    embeddings_path = work_dir / "embeddings.jsonl"

    errors: list[str] = []
    metadata: dict[str, Any] = {}
    if category not in VALID_CATEGORIES:
        errors.append(f"{md_path}: category dir {category!r} not in VALID_CATEGORIES")

    if metadata_path.is_file():
        try:
            metadata = load_json(metadata_path)
        except ValueError as exc:
            errors.append(str(exc))
            metadata = {}
    else:
        errors.append(f"{key}: missing metadata.json")

    meta_category = compact_text(metadata.get("category"))
    if meta_category and meta_category != category:
        errors.append(f"{key}: metadata category {meta_category!r} != path category {category!r}")

    # metadata may optionally carry key; if present it must match filename stem.
    meta_key = compact_text(metadata.get("key"))
    if meta_key and meta_key != key:
        errors.append(f"{key}: metadata.key {meta_key!r} disagrees with filename stem")

    bib_text = ""
    bib_key = ""
    bib_entry_type = ""
    if not bib_path.is_file():
        errors.append(f"{key}: missing .bib sidecar (validation ERROR)")
        bib_path_resolved: Path | None = None
    else:
        bib_path_resolved = bib_path
        bib_text = bib_path.read_text(encoding="utf-8")
        bib_key, bib_entry_type = parse_bib_key_and_type(bib_text)
        if not bib_key:
            errors.append(f"{key}: could not parse @type{{key}} from .bib")
        elif bib_key != key:
            errors.append(f"{key}: .bib key {bib_key!r} disagrees with filename stem")
        try:
            entry = parse_one_entry(bib_text)
            if entry.key != key:
                errors.append(f"{key}: parsed bib key {entry.key!r} disagrees with filename stem")
            bib_entry_type = entry.entry_type or bib_entry_type
            # Prefer parsed title/year when metadata is thin.
            if not compact_text(metadata.get("title")) and entry.fields.get("title"):
                metadata = dict(metadata)
                metadata["title"] = entry.fields["title"]
            if not compact_text(metadata.get("year")) and entry.fields.get("year"):
                metadata = dict(metadata)
                metadata["year"] = entry.fields["year"]
        except BibTeXParseError as exc:
            errors.append(f"{key}: bib parse error: {exc}")

    chunks: list[dict[str, Any]] = []
    embeddings: list[dict[str, Any]] = []
    try:
        chunks = load_jsonl(chunks_path)
    except ValueError as exc:
        errors.append(str(exc))
    try:
        embeddings = load_jsonl(embeddings_path)
    except ValueError as exc:
        errors.append(str(exc))

    # Empty text chunks are rejected from the index body.
    cleaned_chunks: list[dict[str, Any]] = []
    for row in chunks:
        text = str(row.get("text") or "")
        if not text.strip():
            continue
        cleaned_chunks.append(row)

    valid = not errors
    return HarvestedWork(
        key=key,
        category=category,
        md_path=md_path,
        bib_path=bib_path_resolved,
        work_dir=work_dir,
        metadata=metadata,
        bib_key=bib_key or key,
        bib_text=bib_text,
        bib_entry_type=bib_entry_type,
        chunks=cleaned_chunks,
        embeddings=embeddings,
        valid=valid,
        validation_errors=errors,
    )


def delete_work_rows(conn: sqlite3.Connection, key: str) -> None:
    chunk_ids = [
        str(row["chunk_id"])
        for row in conn.execute("SELECT chunk_id FROM chunks WHERE key = ?", (key,)).fetchall()
    ]
    if chunk_ids:
        placeholders = ",".join("?" for _ in chunk_ids)
        conn.execute(f"DELETE FROM chunk_embeddings WHERE chunk_id IN ({placeholders})", chunk_ids)
    # DELETE FROM chunks fires the chunks_ad trigger per row, which removes the matching
    # chunks_fts entry (external-content FTS5 — no manual FTS delete here anymore).
    conn.execute("DELETE FROM chunks WHERE key = ?", (key,))
    conn.execute("DELETE FROM source_files WHERE key = ?", (key,))
    conn.execute("DELETE FROM bib_entries WHERE key = ?", (key,))
    conn.execute("DELETE FROM works WHERE key = ?", (key,))


def upsert_harvested(conn: sqlite3.Connection, work: HarvestedWork, library_root: Path) -> None:
    delete_work_rows(conn, work.key)
    md_hash = file_sha256_optional(work.md_path)
    bib_hash = file_sha256_optional(work.bib_path) if work.bib_path else ""
    metadata_path = work.work_dir / "metadata.json"
    metadata_hash = file_sha256_optional(metadata_path)
    source_hashes = work.metadata.get("source_sha256") or {}
    if not isinstance(source_hashes, dict):
        source_hashes = {}
    title = compact_text(work.metadata.get("title"))
    year = compact_text(work.metadata.get("year"))
    doi = compact_text(work.metadata.get("doi"))
    isbn = compact_text(work.metadata.get("isbn"))
    arxiv_id = compact_text(work.metadata.get("arxiv_id") or work.metadata.get("arxiv") or work.metadata.get("eprint"))
    authors = authors_list(work.metadata)
    authors_json = json.dumps(authors, ensure_ascii=False, sort_keys=False)
    rel_md = str(work.md_path.relative_to(library_root)) if work.md_path.is_relative_to(library_root) else str(work.md_path)
    rel_bib = ""
    if work.bib_path:
        rel_bib = (
            str(work.bib_path.relative_to(library_root))
            if work.bib_path.is_relative_to(library_root)
            else str(work.bib_path)
        )
    rel_dir = (
        str(work.work_dir.relative_to(library_root))
        if work.work_dir.is_relative_to(library_root)
        else str(work.work_dir)
    )
    parser = compact_text(work.metadata.get("parser"))
    conn.execute(
        """
        INSERT INTO works(
            key, bib_key, category, title, authors, year, doi, isbn, arxiv_id,
            md_path, bib_path, work_dir, md_hash, bib_hash, metadata_hash,
            source_hashes, parser, indexed_at, valid, validation_errors
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            work.key,
            work.bib_key,
            work.category,
            title,
            authors_json,
            year,
            doi,
            isbn,
            arxiv_id,
            rel_md,
            rel_bib,
            rel_dir,
            md_hash,
            bib_hash,
            metadata_hash,
            json.dumps(source_hashes, sort_keys=True),
            parser,
            utc_now(),
            1 if work.valid else 0,
            json.dumps(work.validation_errors, ensure_ascii=False),
        ),
    )

    source_files = work.metadata.get("source_files") or []
    if isinstance(source_files, list):
        for row in source_files:
            if not isinstance(row, dict):
                continue
            conn.execute(
                """
                INSERT INTO source_files(key, name, filed_name, format, sha256, size)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    work.key,
                    compact_text(row.get("name")),
                    compact_text(row.get("filed_name")),
                    compact_text(row.get("format")),
                    compact_text(row.get("sha256")),
                    int(row.get("size") or 0),
                ),
            )

    if work.bib_path and work.bib_text:
        conn.execute(
            """
            INSERT INTO bib_entries(key, bib_key, entry_type, bib_path, bib_text, bib_hash)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (work.key, work.bib_key, work.bib_entry_type, rel_bib, work.bib_text, bib_hash),
        )

    # Invalid works stay in works/bib_entries for validate diagnostics but skip chunks.
    if not work.valid:
        return

    emb_by_id: dict[str, dict[str, Any]] = {}
    for row in work.embeddings:
        cid = compact_text(row.get("chunk_id"))
        if cid:
            emb_by_id[cid] = row

    models: set[str] = set()
    dims: set[int] = set()

    for row in work.chunks:
        text = str(row.get("text") or "")
        if not text.strip():
            continue
        ordinal = int(row.get("chunk_index") if row.get("chunk_index") is not None else row.get("chunk_ordinal") or 0)
        digest = text_sha256(text)
        # Prefer pipeline chunk_id (already content-addressed); fall back to deterministic id.
        chunk_id = compact_text(row.get("chunk_id")) or f"{work.key}:{ordinal:04d}:{digest[:16]}"
        heading = row.get("heading_path") or []
        if not isinstance(heading, list):
            heading = [compact_text(heading)]
        heading_json = json.dumps(heading, ensure_ascii=False)
        conn.execute(
            """
            INSERT INTO chunks(
                chunk_id, key, bib_key, category, title, year, authors,
                chunk_ordinal, text, text_hash, heading_path, md_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chunk_id,
                work.key,
                work.bib_key,
                work.category,
                title,
                year,
                authors_json,
                ordinal,
                text,
                digest,
                heading_json,
                rel_md,
            ),
        )
        # chunks_fts stays in sync via the chunks_ai trigger (external-content FTS5) — no
        # manual INSERT INTO chunks_fts here anymore.

        emb_row = emb_by_id.get(chunk_id)
        if not emb_row:
            continue
        vector = emb_row.get("embedding")
        if not isinstance(vector, list) or not vector:
            continue
        dim = int(emb_row.get("embedding_dim") or len(vector))
        if dim != len(vector):
            # Dimension mismatch vs declared — skip and leave for validate.
            continue
        model = compact_text(emb_row.get("model") or emb_row.get("embedding_model") or DEFAULT_EMBED_MODEL)
        models.add(model)
        dims.add(dim)
        conn.execute(
            """
            INSERT INTO chunk_embeddings(
                chunk_id, key, bib_key, embedding, embedding_dim, embedding_model, text_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (chunk_id, work.key, work.bib_key, pack_embedding(vector), dim, model, digest),
        )

    if models:
        set_meta(conn, "embedding_model", sorted(models)[0] if len(models) == 1 else ",".join(sorted(models)))
    if dims:
        set_meta(conn, "embedding_dim", str(sorted(dims)[0] if len(dims) == 1 else ",".join(str(d) for d in sorted(dims))))


def rebuild_index(conn: sqlite3.Connection, library_root: Path) -> dict[str, Any]:
    library_root = library_root.resolve()
    init_schema(conn)
    # Full wipe of data tables (keep schema). chunks_fts is external-content: deleting
    # chunks fires the chunks_ad trigger per row and empties it too, so no separate
    # DELETE FROM chunks_fts here (issuing one alongside a firing delete trigger would
    # double-delete already-gone FTS rows).
    conn.execute("DELETE FROM chunk_embeddings")
    conn.execute("DELETE FROM chunks")
    conn.execute("DELETE FROM source_files")
    conn.execute("DELETE FROM bib_entries")
    conn.execute("DELETE FROM works")
    set_meta(conn, "library_root", str(library_root))
    set_meta(conn, "built_at", utc_now())
    set_meta(conn, "schema_version", SCHEMA_VERSION)

    works = [harvest_work(library_root, md) for md in discover_work_markdown(library_root)]
    for work in works:
        upsert_harvested(conn, work, library_root)
    conn.commit()
    build_vectors_sidecar(conn)  # regenerate the mmap sidecar shipped alongside index.db
    return index_stats(conn)


def upsert_work(conn: sqlite3.Connection, library_root: Path, key: str, category: str | None = None) -> HarvestedWork:
    library_root = library_root.resolve()
    init_schema(conn)
    candidates: list[Path] = []
    if category:
        md = library_root / category / f"{key}.md"
        if md.is_file():
            candidates.append(md)
    else:
        for cat in sorted(VALID_CATEGORIES):
            md = library_root / cat / f"{key}.md"
            if md.is_file():
                candidates.append(md)
    if not candidates:
        raise FileNotFoundError(f"work not found: key={key!r} category={category!r}")
    if len(candidates) > 1:
        raise RuntimeError(f"ambiguous key {key!r}: {[str(p) for p in candidates]}")
    work = harvest_work(library_root, candidates[0])
    upsert_harvested(conn, work, library_root)
    set_meta(conn, "library_root", str(library_root))
    set_meta(conn, "last_upsert_at", utc_now())
    conn.commit()
    build_vectors_sidecar(conn)  # keep the mmap sidecar in sync after an incremental add
    return work


def index_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    def count(table: str) -> int:
        return int(conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])

    invalid = int(conn.execute("SELECT COUNT(*) AS n FROM works WHERE valid = 0").fetchone()["n"])
    return {
        "schema_version": get_meta(conn, "schema_version"),
        "embedding_model": get_meta(conn, "embedding_model"),
        "embedding_dim": get_meta(conn, "embedding_dim"),
        "works": count("works"),
        "works_invalid": invalid,
        "chunks": count("chunks"),
        "chunk_embeddings": count("chunk_embeddings"),
        "source_files": count("source_files"),
        "bib_entries": count("bib_entries"),
        "library_root": get_meta(conn, "library_root"),
        "built_at": get_meta(conn, "built_at"),
    }


def export_serving_db(source_db: Path, out_path: Path) -> dict[str, Any]:
    """Write a serving-only copy of `source_db` at `out_path`: identical schema and rows,
    except `chunk_embeddings.embedding` is emptied (zero-length blob — the row, chunk_id,
    dim, and joins are all unchanged). the serving host never reads that blob (search_vector serves
    vectors from the mmap sidecar, not the DB), so shipping it is ~966 MB of dead weight per
    deploy. Only ever reads `source_db` (via sqlite3's backup API, which copies pages out —
    it never opens the source for writing)."""
    if out_path.exists():
        out_path.unlink()
    src_size = source_db.stat().st_size

    src = sqlite3.connect(str(source_db))
    dst = sqlite3.connect(str(out_path))
    try:
        src.backup(dst)
    finally:
        src.close()

    dst.execute("UPDATE chunk_embeddings SET embedding = X''")
    dst.commit()
    dst.execute("VACUUM")
    # VACUUM can renumber rowids for `chunks` (no INTEGER PRIMARY KEY column to pin them),
    # and chunks_fts is keyed on that rowid (external-content FTS5) — re-key it so FTS still
    # points at the right rows post-VACUUM.
    dst.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')")
    dst.commit()
    dst.close()

    out_size = out_path.stat().st_size
    return {"source_size": src_size, "output_size": out_size}


def validate_index(conn: sqlite3.Connection) -> tuple[list[str], list[str]]:
    """Return (problems, notes). Empty problems means clean; notes are informational
    (e.g. "this is a stripped serving copy") and never affect the ok/fail verdict."""
    problems: list[str] = []
    notes: list[str] = []
    problems.extend(schema_column_check(conn))

    for row in conn.execute("SELECT key, bib_key, valid, validation_errors FROM works").fetchall():
        key = str(row["key"])
        bib_key = str(row["bib_key"] or "")
        if not bib_key:
            problems.append(f"work {key}: missing bib_key")
        if int(row["valid"] or 0) == 0:
            try:
                errs = json.loads(row["validation_errors"] or "[]")
            except json.JSONDecodeError:
                errs = [str(row["validation_errors"])]
            problems.append(f"work {key}: invalid — {'; '.join(errs) if errs else 'unknown'}")

    for row in conn.execute(
        "SELECT chunk_id, key, bib_key, text_hash FROM chunks WHERE bib_key IS NULL OR bib_key = ''"
    ).fetchall():
        problems.append(f"chunk {row['chunk_id']}: missing bib_key")

    # Stale embedding: stored text_hash != current chunk text_hash.
    for row in conn.execute(
        """
        SELECT e.chunk_id, e.text_hash AS emb_hash, c.text_hash AS chunk_hash
        FROM chunk_embeddings e
        JOIN chunks c ON c.chunk_id = e.chunk_id
        WHERE e.text_hash != c.text_hash
        """
    ).fetchall():
        problems.append(
            f"chunk {row['chunk_id']}: stale embedding (text_hash mismatch emb={row['emb_hash'][:12]}… chunk={row['chunk_hash'][:12]}…)"
        )

    # Dimension consistency vs index_meta / within table.
    dim_rows = conn.execute("SELECT DISTINCT embedding_dim FROM chunk_embeddings").fetchall()
    dims = [int(r["embedding_dim"]) for r in dim_rows]
    if len(dims) > 1:
        problems.append(f"embedding dimension mismatch across rows: {sorted(dims)}")
    meta_dim = get_meta(conn, "embedding_dim")
    if meta_dim and dims:
        try:
            expected = int(meta_dim.split(",")[0])
            if any(d != expected for d in dims):
                problems.append(f"embedding_dim meta={meta_dim} disagrees with rows {sorted(dims)}")
        except ValueError:
            pass

    # `export-serving` (export_serving_db) writes a deploy copy with every
    # chunk_embeddings.embedding blob emptied to X'' — the serving host serves vectors from the mmap
    # sidecar and never reads this column, so the blob is deliberately dropped dead
    # weight. Checking blob length row-by-row against that copy would report every single
    # row as "corrupt", which is noise, not signal. Detect the all-empty shape up front and
    # skip the per-row length check in that case; a *partially* empty table (some rows
    # stripped, some not) is real corruption and must still fail loudly via the normal loop.
    blob_counts = conn.execute(
        "SELECT COUNT(*) AS total, SUM(CASE WHEN length(embedding) = 0 THEN 1 ELSE 0 END) AS empty "
        "FROM chunk_embeddings"
    ).fetchone()
    total_blobs = int(blob_counts["total"] or 0)
    empty_blobs = int(blob_counts["empty"] or 0)

    if total_blobs > 0 and empty_blobs == total_blobs:
        notes.append(
            f"stripped serving copy — embeddings live in the vector sidecar; blob "
            f"validation not applicable ({total_blobs} chunk_embeddings rows, all empty)"
        )
        # export_serving_db VACUUMs the copy and re-keys chunks_fts because VACUUM can
        # renumber `chunks` rowids (external-content FTS5 is keyed on them) — that re-key
        # step is exactly the part of a stripped copy that IS still real corruption risk, so
        # check it even though the embedding blobs themselves are skipped above. FTS5's
        # 'integrity-check' special command with rank=1 does the expensive cross-check of
        # the FTS index against the live `chunks` content table (rank=0/omitted only
        # verifies the index's own internal structure and would miss a content/index
        # desync); it scans without modifying data, and raises if they disagree. Confirmed
        # empirically: rank=1 catches a `chunks.text` row edited without a matching FTS
        # re-index, plain 'integrity-check' does not.
        try:
            conn.execute("INSERT INTO chunks_fts(chunks_fts, rank) VALUES ('integrity-check', 1)")
            conn.commit()
        except sqlite3.DatabaseError as exc:
            conn.rollback()
            problems.append(f"chunks_fts integrity-check failed: {exc}")
    else:
        for row in conn.execute("SELECT chunk_id, embedding, embedding_dim FROM chunk_embeddings").fetchall():
            dim = int(row["embedding_dim"])
            blob = row["embedding"]
            if not isinstance(blob, (bytes, memoryview)):
                problems.append(f"chunk {row['chunk_id']}: embedding is not a BLOB")
                continue
            raw = bytes(blob)
            if len(raw) != dim * 4:
                problems.append(
                    f"chunk {row['chunk_id']}: embedding blob length {len(raw)} != dim*{4}={dim * 4}"
                )

    return problems, notes


def fts_query_escape(query: str) -> str:
    """Build a tolerant FTS5 MATCH string from free text."""
    tokens = re.findall(r"[A-Za-z0-9_]+", query)
    if not tokens:
        # phrase fallback: strip quotes
        cleaned = query.replace('"', " ").strip()
        if not cleaned:
            return '""'
        return '"' + cleaned.replace('"', "") + '"'
    return " ".join(tokens)


@dataclass
class SearchHit:
    chunk_id: str
    key: str
    bib_key: str
    category: str
    title: str
    year: str
    snippet: str
    path: str
    score: float
    channel: str
    fts_score: float | None = None
    vector_score: float | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "chunk_id": self.chunk_id,
            "key": self.key,
            "bib_key": self.bib_key,
            "category": self.category,
            "title": self.title,
            "year": self.year,
            "snippet": self.snippet,
            "path": self.path,
            "score": self.score,
            "channel": self.channel,
        }
        if self.fts_score is not None:
            out["fts_score"] = self.fts_score
        if self.vector_score is not None:
            out["vector_score"] = self.vector_score
        return out


def search_fts(conn: sqlite3.Connection, query: str, limit: int = 10) -> list[SearchHit]:
    match = fts_query_escape(query)
    sql = """
        SELECT
            f.chunk_id AS chunk_id,
            f.key AS key,
            f.bib_key AS bib_key,
            f.category AS category,
            c.title AS title,
            c.year AS year,
            c.text AS text,
            c.md_path AS path,
            bm25(chunks_fts) AS rank
        FROM chunks_fts f
        JOIN chunks c ON c.chunk_id = f.chunk_id
        WHERE chunks_fts MATCH ?
        ORDER BY rank
        LIMIT ?
    """
    hits: list[SearchHit] = []
    try:
        rows = conn.execute(sql, (match, limit)).fetchall()
    except sqlite3.OperationalError:
        return []
    for row in rows:
        # SQLite BM25: lower (more negative) is better → invert for a higher-is-better score.
        bm = float(row["rank"])
        score = float(-bm)
        text = str(row["text"] or "")
        hits.append(
            SearchHit(
                chunk_id=str(row["chunk_id"]),
                key=str(row["key"]),
                bib_key=str(row["bib_key"]),
                category=str(row["category"]),
                title=str(row["title"] or ""),
                year=str(row["year"] or ""),
                snippet=snippet_from(text, query),
                path=str(row["path"] or ""),
                score=score,
                channel="fts",
                fts_score=score,
            )
        )
    return hits


# How often a cache hit re-checks the on-disk sidecar for a deploy (see load_vector_index).
# Reading a 152-byte meta.json is free; doing it on every query is not — this throttles it.
RELOAD_CHECK_SECONDS = int(os.environ.get("ALEXANDRIA_RELOAD_CHECK_SECONDS", "30"))


@dataclass
class _VecCacheEntry:
    chunk_ids: list[str]
    matrix: np.ndarray
    norms: np.ndarray
    stamp: dict | None  # None for legacy sidecars with no meta.json (mtime fallback only)
    last_checked: float  # time.monotonic() of the last on-disk staleness check


# Cache of (chunk_ids, matrix float32, row_norms) keyed on the DB's own filesystem path
# (not id(conn) — that is unstable across GC and, more importantly, does not represent the
# data: a deploy replaces index.db and the sidecar with new inodes while an already-open
# connection and mmap keep serving the old ones, so a same-path cache lets a live process
# notice a redeploy instead of serving stale results forever). A long-running server (the
# MCP) still pays the sidecar-mmap cost once and every later query is a matmul; a hit only
# pays a throttled meta.json re-read, not a rebuild.
#
# Deliberately does NOT hold chunk TEXT: on a 14 GB serving box the resident 8B model
# leaves little headroom, and the corpus text (~1-2 GB) would push it into swap. Text is
# fetched lazily from the DB for the handful of top-k hits instead.
_VEC_CACHE: dict[str, _VecCacheEntry] = {}


def _conn_db_path(conn: sqlite3.Connection) -> Path | None:
    """Filesystem path of the connection's main database, or None (e.g. :memory:)."""
    try:
        row = conn.execute("PRAGMA database_list").fetchone()
    except sqlite3.Error:
        return None
    path = row[2] if row and len(row) >= 3 else None
    return Path(path) if path else None


# Inode recorded per open connection at first check — id(conn) is a safe key here (unlike
# _VEC_CACHE) because a live connection can't be GC'd while something holds it open, so the
# id can't be silently reassigned to an unrelated connection mid-use.
_CONN_INODE: dict[int, tuple[int, int]] = {}


def connection_is_stale(conn: sqlite3.Connection) -> bool:
    """True when the DB file at this connection's path is a different inode than the one the
    connection holds open — i.e. a deploy replaced it underneath us."""
    db_path = _conn_db_path(conn)
    if db_path is None:
        return False
    try:
        st = db_path.stat()
    except OSError:
        return False
    current = (st.st_dev, st.st_ino)
    recorded = _CONN_INODE.get(id(conn))
    if recorded is None:
        _CONN_INODE[id(conn)] = current
        return False
    return recorded != current


def _sidecar_paths(db_path: Path) -> tuple[Path, Path, Path, Path]:
    base = str(db_path)
    return (
        Path(base + ".vectors.npy"),
        Path(base + ".vectors.norms.npy"),
        Path(base + ".vectors.ids.json"),
        Path(base + ".vectors.meta.json"),
    )


def _read_sidecar_meta(db_path: Path) -> dict | None:
    """Parse <db>.vectors.meta.json if present, else None. A file read, never a DB query —
    the throttled cache re-check in load_vector_index relies on that."""
    _vec, _nrm, _ids, meta = _sidecar_paths(db_path)
    if not meta.exists():
        return None
    try:
        return json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _index_stamp(conn: sqlite3.Connection) -> dict:
    """Content identity of the embedding set: enough to detect any add/remove/re-embed.
    Cheap enough to run once per process load (not per query).

    MUST be computable from a blob-stripped serving copy (see `export-serving`): the serving host never
    rebuilds, so if this stamp needed anything from `chunk_embeddings.embedding` it would
    never match the build host's meta.json on a stripped DB, `_sidecar_fresh` would report stale forever,
    and — with rebuilding forbidden on a serving box — every search would silently fall back
    to FTS. Do NOT add SUM(LENGTH(embedding)) or any other blob-derived term here.
    """
    count, min_id, max_id, dim = conn.execute(
        "SELECT COUNT(*), MIN(chunk_id), MAX(chunk_id), MAX(embedding_dim) FROM chunk_embeddings"
    ).fetchone()
    (text_len,) = conn.execute("SELECT SUM(LENGTH(text)) FROM chunks").fetchone()
    return {
        "count": count,
        "min_id": min_id,
        "max_id": max_id,
        "dim": dim,
        "embedding_model": get_meta(conn, "embedding_model"),
        "built_at": get_meta(conn, "built_at"),
        "text_len": text_len,
    }


def _sidecar_fresh(conn: sqlite3.Connection, db_path: Path) -> bool:
    vec, nrm, ids, meta = _sidecar_paths(db_path)
    if not (vec.exists() and nrm.exists() and ids.exists()):
        return False
    if meta.exists():
        # Content stamp is authoritative once present — mtimes are irrelevant here.
        try:
            return json.loads(meta.read_text(encoding="utf-8")) == _index_stamp(conn)
        except (OSError, ValueError):
            return False
    # No meta file: this sidecar predates the content-stamp mechanism (e.g. a sidecar
    # already deployed to a serving box before the meta file existed there). Fall back to
    # the old mtime heuristic so an existing, valid sidecar isn't declared stale just
    # because it hasn't been regenerated yet. Do not remove this fallback.
    try:
        db_m = db_path.stat().st_mtime
        return min(vec.stat().st_mtime, nrm.stat().st_mtime, ids.stat().st_mtime) >= db_m
    except OSError:
        return False


def _write_sidecar(
    db_path: Path, chunk_ids: list[str], matrix: np.ndarray, norms: np.ndarray, stamp: dict
) -> None:
    """Persist the flat vector matrix beside the DB so a slow-disk server can mmap it in
    seconds instead of rebuilding from tens of thousands of blobs. Written atomically.
    The meta file is written last: it is the commit marker, so a torn write (crash mid-write)
    leaves no meta file and the sidecar falls back to the mtime heuristic instead of looking
    falsely fresh."""
    vec, nrm, ids, meta = _sidecar_paths(db_path)
    for path, arr in ((vec, np.ascontiguousarray(matrix, dtype=np.float32)), (nrm, np.ascontiguousarray(norms, dtype=np.float32))):
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "wb") as fh:
            np.save(fh, arr)
        tmp.replace(path)
    tmp_ids = ids.with_name(ids.name + ".tmp")
    tmp_ids.write_text(json.dumps(chunk_ids), encoding="utf-8")
    tmp_ids.replace(ids)
    tmp_meta = meta.with_name(meta.name + ".tmp")
    tmp_meta.write_text(json.dumps(stamp), encoding="utf-8")
    tmp_meta.replace(meta)


def build_vectors_sidecar(conn: sqlite3.Connection) -> bool:
    """(Re)build the vector sidecar from the DB. Called after any index mutation on the
    authoritative (build) host; the built files are deployed alongside index.db. Returns True
    if written."""
    db_path = _conn_db_path(conn)
    if db_path is None:
        return False
    chunk_ids, matrix, norms = _build_vectors_from_db(conn)
    _write_sidecar(db_path, chunk_ids, matrix, norms, _index_stamp(conn))
    _VEC_CACHE.pop(str(db_path), None)
    return True


def invalidate_vector_cache(db_path: Path | str | None = None) -> None:
    """Drop one cached vector-index entry, or all of them when db_path is None. Lets a
    long-lived caller (the MCP server) force a reload without waiting out the throttle,
    e.g. right after reopening a connection whose file was replaced underneath it."""
    if db_path is None:
        _VEC_CACHE.clear()
        return
    _VEC_CACHE.pop(str(db_path), None)


def _build_vectors_from_db(conn: sqlite3.Connection) -> tuple[list[str], np.ndarray, np.ndarray]:
    rows = conn.execute(
        "SELECT chunk_id, embedding, embedding_dim FROM chunk_embeddings ORDER BY chunk_id"
    ).fetchall()
    if not rows:
        return [], np.zeros((0, 0), dtype=np.float32), np.zeros(0, dtype=np.float32)
    dim = int(rows[0]["embedding_dim"])
    chunk_ids = [str(r["chunk_id"]) for r in rows]
    # float32 throughout — cosine ranking does not need float64.
    matrix = np.vstack([unpack_embedding(bytes(r["embedding"]), dim) for r in rows])
    norms = np.linalg.norm(matrix, axis=1).astype(np.float32)
    norms[norms == 0.0] = 1.0
    return chunk_ids, matrix, norms


class StaleIndexError(RuntimeError):
    """The vector sidecar is missing or stale and rebuilding is not permitted here
    (serving boxes must never rebuild — see load_vector_index)."""


def _cache_entry_stale(db_path: Path, entry: _VecCacheEntry) -> bool:
    """Compare on-disk sidecar state to what `entry` was loaded from. One meta.json read
    (or a few stats for the legacy no-meta case) — never a DB query, never a re-mmap;
    that only happens once this returns True and the caller reloads."""
    vec, nrm, ids, meta = _sidecar_paths(db_path)
    current_stamp = _read_sidecar_meta(db_path)
    if current_stamp is not None or entry.stamp is not None:
        return current_stamp != entry.stamp
    # Neither side has a content stamp: legacy sidecar, fall back to the mtime heuristic
    # (mirrors _sidecar_fresh) so behaviour is unchanged for boxes without a meta file.
    try:
        db_m = db_path.stat().st_mtime
        return not (min(vec.stat().st_mtime, nrm.stat().st_mtime, ids.stat().st_mtime) >= db_m)
    except OSError:
        return True


def load_vector_index(
    conn: sqlite3.Connection,
    expected_dim: int | None = None,
    allow_rebuild: bool = True,
) -> tuple[list[str], np.ndarray, np.ndarray]:
    db_path = _conn_db_path(conn)
    # id(conn) is only a fallback for path-less connections (:memory:, tests) where there is
    # no file to re-check anyway — real serving always keys on the resolved DB path.
    cache_key = str(db_path) if db_path is not None else f"id:{id(conn)}"
    cached = _VEC_CACHE.get(cache_key)
    if cached is not None and db_path is not None:
        now = time.monotonic()
        if now - cached.last_checked >= RELOAD_CHECK_SECONDS:
            if _cache_entry_stale(db_path, cached):
                _VEC_CACHE.pop(cache_key, None)
                cached = None
            else:
                cached.last_checked = now
    if cached is not None:
        chunk_ids, matrix, norms = cached.chunk_ids, cached.matrix, cached.norms
        if expected_dim is not None and matrix.shape[0] and int(matrix.shape[1]) != expected_dim:
            raise ValueError(f"dimension mismatch: query dim={expected_dim}, index dim={int(matrix.shape[1])}")
        return chunk_ids, matrix, norms
    if db_path is not None:
        # First real use of this connection for vector search: seed connection_is_stale's
        # inode baseline now, while it still reflects what this connection actually has
        # open. Without this, a caller that never checks connection_is_stale until well
        # after a deploy would "first-check" against the *new* inode and never notice.
        connection_is_stale(conn)
    # Fast path: memory-map the precomputed sidecar (one sequential file, OS page cache).
    if db_path is not None and _sidecar_fresh(conn, db_path):
        vec, nrm, ids, _meta = _sidecar_paths(db_path)
        matrix = np.load(vec, mmap_mode="r")
        norms = np.load(nrm)
        chunk_ids = json.loads(ids.read_text(encoding="utf-8"))
        if matrix.shape[0] == 0:
            return [], matrix, norms
        if expected_dim is not None and int(matrix.shape[1]) != expected_dim:
            raise ValueError(f"dimension mismatch: query dim={expected_dim}, index dim={int(matrix.shape[1])}")
        stamp = _read_sidecar_meta(db_path)
        _VEC_CACHE[cache_key] = _VecCacheEntry(chunk_ids, matrix, norms, stamp, time.monotonic())
        return chunk_ids, matrix, norms
    if not allow_rebuild:
        raise StaleIndexError(
            "vector sidecar missing or stale; rebuild on the authoritative box "
            "(alexandria_index.py rebuild) and redeploy"
        )
    # Fallback: build from the DB (slow), then persist a sidecar for next time.
    chunk_ids, matrix, norms = _build_vectors_from_db(conn)
    if not chunk_ids:
        return [], matrix, norms
    if expected_dim is not None and int(matrix.shape[1]) != expected_dim:
        raise ValueError(f"dimension mismatch: query dim={expected_dim}, index dim={int(matrix.shape[1])}")
    stamp = _index_stamp(conn)
    if db_path is not None:
        try:
            _write_sidecar(db_path, chunk_ids, matrix, norms, stamp)
        except OSError:
            pass
    _VEC_CACHE[cache_key] = _VecCacheEntry(chunk_ids, matrix, norms, stamp, time.monotonic())
    return chunk_ids, matrix, norms


def _top_k_desc(sims: np.ndarray, limit: int) -> np.ndarray:
    """Indices of the top `limit` values in `sims`, descending. Avoids sorting the whole
    58k-row similarity array for a `limit` usually in the single digits: argpartition finds
    the k-th largest in O(n) (vs. O(n log n) for a full argsort), then only that small slice
    is sorted. Ranking matches a full `np.argsort(-sims)[:limit]` whenever the k-th and
    (k+1)-th scores differ; ties are broken arbitrarily either way (neither argsort's
    introsort nor argpartition's selection is a stable tie-break)."""
    n = sims.shape[0]
    if limit <= 0 or n == 0:
        return np.empty(0, dtype=np.intp)
    if limit >= n:
        return np.argsort(-sims)
    part = np.argpartition(-sims, limit - 1)[:limit]
    return part[np.argsort(-sims[part])]


def search_vector(
    conn: sqlite3.Connection,
    query_vec: np.ndarray,
    limit: int = 10,
    query_text: str = "",
    allow_rebuild: bool = True,
) -> list[SearchHit]:
    q = np.asarray(query_vec, dtype=np.float32).reshape(-1)
    chunk_ids, matrix, norms = load_vector_index(
        conn, expected_dim=int(q.shape[0]), allow_rebuild=allow_rebuild
    )
    if not chunk_ids:
        return []
    q_norm = float(np.linalg.norm(q))
    if q_norm == 0.0:
        return []
    sims = (matrix @ q) / (norms * q_norm)  # float32 cosine, no big transient
    order = _top_k_desc(sims, limit)
    top_ids = [chunk_ids[int(i)] for i in order]
    # Fetch display fields (incl. text) ONLY for the top-k hits — not the whole corpus.
    placeholders = ",".join("?" for _ in top_ids)
    disp: dict[str, sqlite3.Row] = {}
    if top_ids:
        for row in conn.execute(
            f"""
            SELECT e.chunk_id, e.key, e.bib_key, c.category, c.title, c.year, c.text,
                   c.md_path AS path
            FROM chunk_embeddings e
            JOIN chunks c ON c.chunk_id = e.chunk_id
            WHERE e.chunk_id IN ({placeholders})
            """,
            top_ids,
        ).fetchall():
            disp[str(row["chunk_id"])] = row
    hits: list[SearchHit] = []
    for i in order:
        cid = chunk_ids[int(i)]
        row = disp.get(cid)
        if row is None:
            continue
        score = float(sims[int(i)])
        text = str(row["text"] or "")
        hits.append(
            SearchHit(
                chunk_id=cid,
                key=str(row["key"]),
                bib_key=str(row["bib_key"]),
                category=str(row["category"]),
                title=str(row["title"] or ""),
                year=str(row["year"] or ""),
                snippet=snippet_from(text, query_text),
                path=str(row["path"] or ""),
                score=score,
                channel="vector",
                vector_score=score,
            )
        )
    return hits


def rrf_fuse(
    fts_hits: Sequence[SearchHit],
    vec_hits: Sequence[SearchHit],
    limit: int = 10,
    k: int = 60,
) -> list[SearchHit]:
    scores: dict[str, float] = {}
    meta: dict[str, SearchHit] = {}
    fts_map: dict[str, float] = {}
    vec_map: dict[str, float] = {}

    for rank, hit in enumerate(fts_hits, start=1):
        scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (k + rank)
        meta[hit.chunk_id] = hit
        fts_map[hit.chunk_id] = hit.score
    for rank, hit in enumerate(vec_hits, start=1):
        scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (k + rank)
        if hit.chunk_id not in meta:
            meta[hit.chunk_id] = hit
        vec_map[hit.chunk_id] = hit.score

    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]
    out: list[SearchHit] = []
    for chunk_id, score in ordered:
        base = meta[chunk_id]
        channels = []
        if chunk_id in fts_map:
            channels.append("fts")
        if chunk_id in vec_map:
            channels.append("vector")
        out.append(
            SearchHit(
                chunk_id=base.chunk_id,
                key=base.key,
                bib_key=base.bib_key,
                category=base.category,
                title=base.title,
                year=base.year,
                snippet=base.snippet,
                path=base.path,
                score=float(score),
                channel="+".join(channels) if channels else "hybrid",
                fts_score=fts_map.get(chunk_id),
                vector_score=vec_map.get(chunk_id),
            )
        )
    return out


# Qwen3-Embedding is asymmetric: QUERIES carry an instruction prefix, DOCUMENTS are
# embedded raw (our corpus was embedded raw — see the re-embed driver). Mismatching this
# silently degrades retrieval ~1-5%. Task string is overridable via ALEXANDRIA_QUERY_TASK.
QUERY_INSTRUCT_TASK = os.environ.get(
    "ALEXANDRIA_QUERY_TASK",
    "Given a research question, retrieve relevant passages from scientific and technical documents",
)


def format_query_for_model(query: str, model: str) -> str:
    """Prefix the Qwen3-Embedding query instruction; pass other models' queries through."""
    if "qwen3-embedding" in model.lower():
        return f"Instruct: {QUERY_INSTRUCT_TASK}\nQuery:{query}"
    return query


def embed_query_ollama(text: str, model: str, timeout: int = 60) -> np.ndarray:
    payload = json.dumps({"model": model, "input": text}).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_BASE_URL}/api/embed",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"query embedding failed via Ollama: {exc}") from exc
    vectors = body.get("embeddings")
    if not isinstance(vectors, list) or not vectors:
        # Some Ollama builds return {"embedding": [...]} for single input.
        single = body.get("embedding")
        if isinstance(single, list) and single:
            vectors = [single]
        else:
            raise RuntimeError("Ollama returned no embeddings")
    vector = vectors[0]
    if not isinstance(vector, list) or not vector:
        raise RuntimeError("Ollama returned empty embedding")
    return np.asarray(vector, dtype=np.float32)


def resolve_query_vector(
    conn: sqlite3.Connection,
    query: str,
    query_vector: Sequence[float] | None = None,
    query_vector_json: Path | None = None,
) -> np.ndarray:
    if query_vector is not None:
        return np.asarray(list(query_vector), dtype=np.float32)
    if query_vector_json is not None:
        data = json.loads(query_vector_json.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "embedding" in data:
            data = data["embedding"]
        if not isinstance(data, list):
            raise ValueError("query vector JSON must be a list or {embedding: [...]}")
        return np.asarray(data, dtype=np.float32)
    env_vec = os.environ.get("ALEXANDRIA_QUERY_VECTOR")
    if env_vec:
        data = json.loads(env_vec)
        return np.asarray(data, dtype=np.float32)
    model = get_meta(conn, "embedding_model") or DEFAULT_EMBED_MODEL
    # If multiple models stored comma-separated, take the first.
    model = model.split(",")[0].strip() or DEFAULT_EMBED_MODEL
    return embed_query_ollama(format_query_for_model(query, model), model)


def search_ex(
    conn: sqlite3.Connection,
    query: str,
    mode: str = "hybrid",
    limit: int = 10,
    query_vector: Sequence[float] | None = None,
    query_vector_json: Path | None = None,
    allow_rebuild: bool = True,
) -> tuple[list[SearchHit], list[str]]:
    """Like search(), but also returns a list of human-readable degradation notes
    (empty when the search ran fully). Callers that surface results to users should
    use this so a silent fallback is never invisible."""
    mode = mode.lower().strip()
    if mode not in {"fts", "vector", "hybrid"}:
        raise ValueError(f"unknown search mode: {mode}")
    if mode == "fts":
        return search_fts(conn, query, limit=limit), []
    if mode == "vector":
        qv = resolve_query_vector(conn, query, query_vector, query_vector_json)
        try:
            return search_vector(conn, qv, limit=limit, query_text=query, allow_rebuild=allow_rebuild), []
        except StaleIndexError as exc:
            # Serving box, index stale, rebuild forbidden: fall back to FTS-only rather
            # than raise — search_fts already tags its hits channel="fts".
            note = f"vector channel unavailable: {exc}; returned FTS-only results"
            return search_fts(conn, query, limit=limit), [note]
    # hybrid
    fts_hits = search_fts(conn, query, limit=max(limit * 3, 30))
    try:
        qv = resolve_query_vector(conn, query, query_vector, query_vector_json)
        vec_hits = search_vector(
            conn, qv, limit=max(limit * 3, 30), query_text=query, allow_rebuild=allow_rebuild
        )
    except (RuntimeError, ValueError) as exc:
        # Offline / no embedder, or stale index with rebuild forbidden: degrade to FTS-only.
        reason = str(exc) if isinstance(exc, StaleIndexError) else f"embedder unreachable: {exc}"
        hits = [
            SearchHit(
                chunk_id=h.chunk_id,
                key=h.key,
                bib_key=h.bib_key,
                category=h.category,
                title=h.title,
                year=h.year,
                snippet=h.snippet,
                path=h.path,
                score=h.score,
                channel="fts",
                fts_score=h.fts_score,
            )
            for h in fts_hits[:limit]
        ]
        return hits, [f"vector channel unavailable: {reason}; returned FTS-only results"]
    return rrf_fuse(fts_hits, vec_hits, limit=limit), []


def search(
    conn: sqlite3.Connection,
    query: str,
    mode: str = "hybrid",
    limit: int = 10,
    query_vector: Sequence[float] | None = None,
    query_vector_json: Path | None = None,
) -> list[SearchHit]:
    hits, _ = search_ex(
        conn, query, mode=mode, limit=limit,
        query_vector=query_vector, query_vector_json=query_vector_json,
    )
    return hits


def format_table(hits: Sequence[SearchHit]) -> str:
    if not hits:
        return "(no results)"
    headers = ["score", "key", "bib_key", "year", "category", "channel", "path", "snippet"]
    rows = [
        [
            f"{h.score:.4f}",
            h.key,
            h.bib_key,
            h.year,
            h.category,
            h.channel,
            h.path,
            h.snippet.replace("\n", " ")[:80],
        ]
        for h in hits
    ]
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    lines = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))]
    lines.append("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        lines.append("  ".join(row[i].ljust(widths[i]) for i in range(len(headers))))
    return "\n".join(lines)


def dump_json(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True))


def cmd_rebuild(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    try:
        stats = rebuild_index(conn, args.library)
    finally:
        conn.close()
    if args.json:
        dump_json({"ok": True, "action": "rebuild", "stats": stats})
    else:
        print(
            f"rebuild ok: works={stats['works']} chunks={stats['chunks']} "
            f"embeddings={stats['chunk_embeddings']} invalid={stats['works_invalid']}"
        )
    return 0


def cmd_upsert_work(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    try:
        work = upsert_work(conn, args.library, args.key, args.category)
        stats = index_stats(conn)
    finally:
        conn.close()
    payload = {
        "ok": work.valid,
        "action": "upsert-work",
        "key": work.key,
        "bib_key": work.bib_key,
        "category": work.category,
        "valid": work.valid,
        "validation_errors": work.validation_errors,
        "chunks": len(work.chunks) if work.valid else 0,
        "stats": stats,
    }
    if args.json:
        dump_json(payload)
    else:
        status = "ok" if work.valid else "invalid"
        print(
            f"upsert-work {status}: key={work.key} bib_key={work.bib_key} "
            f"chunks={payload['chunks']} errors={len(work.validation_errors)}"
        )
        for err in work.validation_errors:
            print(f"  - {err}")
    return 0 if work.valid else 2


def cmd_validate(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    try:
        init_schema(conn)
        problems, notes = validate_index(conn)
        stats = index_stats(conn)
    finally:
        conn.close()
    payload = {"ok": not problems, "problems": problems, "notes": notes, "stats": stats}
    if args.json:
        dump_json(payload)
    else:
        for note in notes:
            print(f"NOTE: {note}")
        if problems:
            print(f"validate FAIL ({len(problems)} problem(s))")
            for problem in problems:
                print(f"  - {problem}")
        else:
            print(
                f"validate ok: works={stats['works']} chunks={stats['chunks']} "
                f"embeddings={stats['chunk_embeddings']}"
            )
    return 0 if not problems else 1


def cmd_search(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    try:
        init_schema(conn)
        qvec = None
        if args.query_vector:
            qvec = json.loads(args.query_vector)
        hits = search(
            conn,
            args.query,
            mode=args.mode,
            limit=args.limit,
            query_vector=qvec,
            query_vector_json=args.query_vector_file,
        )
    finally:
        conn.close()
    if args.json:
        dump_json({"ok": True, "query": args.query, "mode": args.mode, "results": [h.as_dict() for h in hits]})
    else:
        print(format_table(hits))
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    try:
        init_schema(conn)
        stats = index_stats(conn)
    finally:
        conn.close()
    if args.json:
        dump_json(stats)
    else:
        for key in sorted(stats):
            print(f"{key}: {stats[key]}")
    return 0


def cmd_export_serving(args: argparse.Namespace) -> int:
    stats = export_serving_db(args.db, args.out)
    payload = {"ok": True, "action": "export-serving", "source": str(args.db), "out": str(args.out), **stats}
    if args.json:
        dump_json(payload)
    else:
        print(
            f"export-serving: {args.db} ({stats['source_size']:,} bytes) -> "
            f"{args.out} ({stats['output_size']:,} bytes)"
        )
    return 0


def default_db_path(library: Path) -> Path:
    env = os.environ.get("ALEXANDRIA_INDEX_DB")
    if env:
        return Path(env)
    return (library / ".sc" / "alexandria-qim" / "index.db").resolve()


def _add_common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--library",
        type=Path,
        default=Path("."),
        help="library root (category dirs live here)",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="SQLite index path (default: $ALEXANDRIA_INDEX_DB or <library>/.sc/alexandria-qim/index.db)",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable JSON output")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_rebuild = sub.add_parser("rebuild", help="full rebuild from filed library")
    _add_common_flags(p_rebuild)
    p_rebuild.set_defaults(func=cmd_rebuild)

    p_upsert = sub.add_parser("upsert-work", help="replace one work's index rows")
    _add_common_flags(p_upsert)
    p_upsert.add_argument("--key", required=True)
    p_upsert.add_argument("--category", default=None)
    p_upsert.set_defaults(func=cmd_upsert_work)

    p_validate = sub.add_parser("validate", help="check schema, bib_key, stale embeddings, dims")
    _add_common_flags(p_validate)
    p_validate.set_defaults(func=cmd_validate)

    p_search = sub.add_parser("search", help="query FTS5 / vector / hybrid")
    _add_common_flags(p_search)
    p_search.add_argument("--query", required=True)
    p_search.add_argument("--mode", choices=("fts", "vector", "hybrid"), default="hybrid")
    p_search.add_argument("--limit", type=int, default=10)
    p_search.add_argument(
        "--query-vector",
        default=None,
        help="JSON float list used as the query embedding (offline vector/hybrid)",
    )
    p_search.add_argument(
        "--query-vector-file",
        type=Path,
        default=None,
        help="path to JSON list or {embedding:[...]} for the query vector",
    )
    p_search.set_defaults(func=cmd_search)

    p_stats = sub.add_parser("stats", help="index counts and meta")
    _add_common_flags(p_stats)
    p_stats.set_defaults(func=cmd_stats)

    p_export = sub.add_parser(
        "export-serving",
        help="write a copy of index.db with chunk_embeddings.embedding stripped (for the serving host)",
    )
    _add_common_flags(p_export)
    p_export.add_argument("--out", type=Path, required=True, help="output path for the stripped copy")
    p_export.set_defaults(func=cmd_export_serving)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    library = args.library.resolve()
    if args.db is None:
        args.db = default_db_path(library)
    else:
        args.db = args.db.resolve()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
