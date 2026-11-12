#!/usr/bin/env python3
"""stdio MCP server over the Alexandria search index (read-only).

Exposes four tools — search, get_work, get_chunk, bib — so agents can retrieve
library material and cite it. Every search hit carries a non-empty ``bib_key``;
a claim without one did not come from the library.

Result contract (search hit)
----------------------------
Lean default (agents pay context for every field; get_work/bib already own
identity, so a search hit carries only what's needed to decide and cite):
  bib_key, title, chunk_id, heading, text, score
``text`` is the full chunk body, always present — never gated behind a flag.
``heading`` is the last element of the chunk's heading_path (its immediate
section), not the document title.

verbose=true restores the old fat card (bib_key, key, title, authors, year,
category, path, chunk_id, snippet, score, score_breakdown, doi/isbn/arxiv_id
when non-empty) plus text and heading — for debugging ranking, not the common
path.

score_breakdown is ``{channel, fts_score?, vector_score?}``. authors/doi/isbn/
arxiv_id are enriched from the ``works`` table (SearchHit lacks them).

Boilerplate filter: ``search`` hides chunks whose own leaf heading (a
References/Contents/Index/etc. section — see ``_is_boilerplate_chunk``) is
front/back matter, over-fetching from the index so a dropped-junk result can
still be backfilled up to the caller's ``limit``. When anything was dropped,
the payload carries ``filtered_boilerplate`` (an int count); the key is
omitted when nothing was filtered, so the lean envelope stays lean.
``filtered_boilerplate`` is a diagnostic — junk chunks skipped while
assembling this result page, not a count of hits you would otherwise have
seen. This filter applies identically whether ``verbose`` is set or not,
and has no disable flag — ``get_chunk``/``get_work(include_markdown=True)``
still reach every chunk, so nothing becomes unreachable by it.

Config: reuses ``alexandria_index.default_db_path()`` / ``ALEXANDRIA_INDEX_DB``.
No write path. Interpreter: project ``.venv/bin/python`` (needs numpy only; the
MCP stdio protocol is spoken directly, no SDK).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any

# Allow ``python pipeline/alexandria_mcp.py`` from the library root.
_PIPELINE = Path(__file__).resolve().parent
if str(_PIPELINE) not in sys.path:
    sys.path.insert(0, str(_PIPELINE))

# OpenBLAS starts one thread per core and they spin between calls. A search is one
# matrix-vector product, so two threads give the same results (measured: identical top-8 ids
# and scores on 60 queries) at about 1/8 of the CPU. Must be set before numpy loads.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "2")

import alexandria_index as index  # noqa: E402

SERVER_NAME = "alexandria"
SERVER_VERSION = "0.1.0"

# Clients often reach this server over SSH, whose wrapper can keep stdin open after the
# client is gone — so a dead session never produces EOF and the process would live forever.
# Self-exit on inactivity is the only reliable reaper: request activity is the one signal
# that distinguishes an orphan from an idle-but-live session.
IDLE_EXIT_SECONDS = int(os.environ.get("ALEXANDRIA_IDLE_EXIT_SECONDS", "3600"))
_LAST_ACTIVITY = time.monotonic()


class _ConnHolder:
    """Mutable box around the live connection so the tool handlers can pick up a reopened
    connection after a deploy without every closure needing `nonlocal`. `db_path` is the
    resolved path the connection was (re)opened against."""

    def __init__(self, conn: sqlite3.Connection, db_path: Path) -> None:
        self.conn = conn
        self.db_path = db_path
        self.last_checked = 0.0


def _live_conn(holder: _ConnHolder) -> sqlite3.Connection:
    """Return the current connection, reopening it first if a deploy replaced index.db
    underneath it (new inode — the old handle would keep silently reading the pre-deploy
    data forever, see alexandria_index.connection_is_stale). Throttled the same way as the
    vector-cache re-check: this must not stat() on every request.
    """
    now = time.monotonic()
    if now - holder.last_checked >= index.RELOAD_CHECK_SECONDS:
        holder.last_checked = now
        if index.connection_is_stale(holder.conn):
            stale_conn = holder.conn
            holder.conn = open_index_readonly(holder.db_path)
            stale_conn.close()
            # Vector cache is keyed on db_path too, but drop it explicitly rather than
            # waiting out its own throttle — the sqlite handle and the mmap should never
            # disagree about which deploy they're serving.
            index.invalidate_vector_cache(holder.db_path)
    return holder.conn


# --- Result contract keys (asserted by tests) ---------------------------------
# Lean default hit: enough to decide relevance and cite (bib_key); identity/
# BibTeX are get_work's/bib's job, not search's.
SEARCH_HIT_REQUIRED = (
    "bib_key",
    "title",
    "chunk_id",
    "heading",
    "text",
    "score",
)

# Fields restored (in addition to the lean set) when verbose=true.
SEARCH_HIT_VERBOSE_EXTRA = (
    "key",
    "authors",
    "year",
    "category",
    "path",
    "snippet",
    "score_breakdown",
)


def resolve_db_path(library: Path | None = None) -> Path:
    """Same resolution as the index CLI — no new config surface."""
    return index.default_db_path(library or Path("."))


def open_index_readonly(db_path: Path) -> sqlite3.Connection:
    """Open the index DB read-only. Clear errors, never create/write."""
    db_path = db_path.expanduser()
    if not db_path.is_file():
        raise FileNotFoundError(
            f"Alexandria index DB not found or unreadable: {db_path}\n"
            "Build it with:\n"
            "  .venv/bin/python pipeline/alexandria_index.py rebuild --library .\n"
            "Or set ALEXANDRIA_INDEX_DB to an existing index path."
        )
    try:
        # URI mode=ro refuses writes; do not use index.connect (it mkdir + RW).
        conn = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise OSError(
            f"Cannot open Alexandria index DB read-only at {db_path}: {exc}\n"
            "Check permissions and that the file is a valid SQLite database."
        ) from exc
    conn.row_factory = sqlite3.Row
    # Seed connection_is_stale's inode baseline right at open time, not on the first
    # request — a deploy landing before the first tool call would otherwise "first-check"
    # against the already-replaced file and never be noticed for this connection.
    index.connection_is_stale(conn)
    return conn


def _require_bib_key(value: Any, context: str) -> str:
    """Single validity gate for citation keys: non-empty after strip.

    Every tool return path that carries a ``bib_key`` must go through this.
    Blank or whitespace-only keys fail closed (raise) — never returned as ok.
    """
    key = str(value if value is not None else "").strip()
    if not key:
        raise RuntimeError(f"missing or blank bib_key ({context})")
    return key


def _safe_under_root(library_root: str, md_path: str) -> Path:
    """Resolve ``md_path`` and require it stays under ``library_root``.

    Absolute paths and ``..`` walks that escape the root raise ValueError.
    """
    root = Path(library_root).expanduser().resolve()
    raw = Path(md_path)
    candidate = raw if raw.is_absolute() else (root / raw)
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"markdown path escapes library root: {md_path!r}"
        ) from exc
    return candidate


def _parse_authors(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(a) for a in raw]
    text = str(raw).strip()
    if not text:
        return []
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [str(a) for a in data]
    except json.JSONDecodeError:
        pass
    return [text]


def _load_work_enrichment(conn: sqlite3.Connection, keys: set[str]) -> dict[str, dict[str, Any]]:
    if not keys:
        return {}
    placeholders = ",".join("?" for _ in keys)
    rows = conn.execute(
        f"""
        SELECT key, authors, doi, isbn, arxiv_id
        FROM works
        WHERE key IN ({placeholders})
        """,
        tuple(keys),
    ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        out[str(row["key"])] = {
            "authors": _parse_authors(row["authors"]),
            "doi": str(row["doi"] or ""),
            "isbn": str(row["isbn"] or ""),
            "arxiv_id": str(row["arxiv_id"] or ""),
        }
    return out


def _chunk_bodies(
    conn: sqlite3.Connection, chunk_ids: list[str]
) -> dict[str, tuple[str, list[str]]]:
    """Batch-fetch (text, heading_path) for a set of chunk_ids in one query.

    Hits already arrive as a batch from the ranker — this stays a single
    ``IN (...)`` round trip rather than one query per hit.
    """
    if not chunk_ids:
        return {}
    placeholders = ",".join("?" for _ in chunk_ids)
    rows = conn.execute(
        f"SELECT chunk_id, text, heading_path FROM chunks WHERE chunk_id IN ({placeholders})",
        tuple(chunk_ids),
    ).fetchall()
    out: dict[str, tuple[str, list[str]]] = {}
    for r in rows:
        raw_hp = r["heading_path"]
        try:
            heading_path = json.loads(raw_hp) if raw_hp else []
        except json.JSONDecodeError:
            heading_path = []
        out[str(r["chunk_id"])] = (str(r["text"] or ""), heading_path)
    return out


def _last_heading(heading_path: list[str] | Any) -> str:
    """Immediate section of a chunk. heading_path[0] is the document title
    (duplicates ``title``), so only the last element is useful here.

    Non-list input (corrupt JSON that decoded to a dict/str/number) is
    treated as empty — never raises, never treats a bare string as a path.
    """
    if not isinstance(heading_path, list) or not heading_path:
        return ""
    return str(heading_path[-1])


# --- Boilerplate filter (query-time only; index/chunking are untouched) -------
#
# A References/Contents/Index section contains nearly every technical term in
# the book, so it lexically and semantically matches almost any query while
# carrying zero usable claims. This filter hides those chunks from `search`
# results without deleting or reranking anything at the index layer.
#
# Kept deliberately conservative: a false positive here silently hides real
# scholarship, which is far worse than one wasted result slot. Do NOT add
# "appendix", "introduction", "summary", "abstract", or "conclusion" — those
# headings carry real content and only their *leaf* form ever reaches this
# match (see below), so an "Appendix" ancestor never triggers it either.
#
# Audited index-wide (58,941 chunks) and REJECTED by evidence, not taste —
# do not re-add without re-running that audit:
#   - "glossary" (17 chunks): definitional content is exactly what an agent
#     wants for "what is X" queries. Real example: "**quantum mechanics:** a
#     theory of matter based on the concept of elementary particles
#     possessing wave properties...".
#   - "preface" (151 chunks): routinely carries the author's thesis/framing
#     in prose. Real example ('Virtual Barrels'): substantive narrative on
#     the financialization of oil trading, fully quotable.
#   - "notes" (315 chunks): genuinely MIXED — some are endnote/citation soup,
#     but some are substantive (an 'Optimization: 100 Examples' Notes chunk
#     explains counterexamples and defines the first-order extremum
#     condition). The operating criterion here is "contains no claims of its
#     own"; "notes" fails that test in both directions, so it stays out —
#     one wasted slot beats silently hiding real scholarship.
# Proper roman-numeral grammar for page-number stamps. The old
# [ivxlcdm]{1,6} letter-soup accepted any word from those letters ("civil",
# "mill", "dvd") and English false friends that happen to be valid classical
# numerals ("cd"=400, "md"=1500, "mix"=1009). The classical form is
#   m{0,3}(?:cm|cd|d?c{0,3})(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})
# but page stamps use the 1–399 range only (no M/D), which also rejects those
# false friends. (?=[ivxlc]) forces a non-empty match so the all-optional
# body cannot silently eat nothing.
_ROMAN_PAGE = r"(?=[ivxlc])c{0,3}(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})"
_BOILERPLATE_LEAF_RE = re.compile(
    # Optional leading page/section-number token, e.g. "e.9." or "7.1"
    # (digit-bearing numbering scheme) or "xxvi" (roman page stamp). Not a
    # a blocklist term (e.g. "Author Notes") do NOT get eaten.
    r"^(?:(?:" + _ROMAN_PAGE + r"|[a-z]{0,2}\.?\d+(?:\.\d+)*)\.?\s+)?"
    r"(?:"
    r"references"
    r"|bibliography"
    r"|works cited"
    r"|cited references"
    r"|contents"
    r"|table of contents"
    r"|index"
    r"|copyright"
    r"|acknowledge?ments?"
    r"|list of (?:figures|tables)"
    r"|about the authors?"
    r"|further reading"
    r")"
    r"\.?$"  # optional trailing period
)

# Markdown emphasis/bullet glyphs observed in real leaf headings (e.g. the
# real chunk 'xxvi** ■ Contents') that must be stripped before matching.
_LEADING_JUNK_RE = re.compile(r"^[^a-zA-Z]+")
_MD_EMPHASIS_RE = re.compile(r"[*_■•·◦►▪]+")
_TRAILING_PUNCT_RE = re.compile(r"[\s.:;,-]+$")
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_leaf_heading(leaf: str) -> str:
    """Normalize a leaf heading for boilerplate matching.

    Strips leading non-alphabetic characters (roman-numeral / arabic page
    number prefixes), markdown emphasis and bullet glyphs, trailing
    punctuation, collapses whitespace, and lowercases. Example:
    'xxvi** ■ Contents' -> 'contents'.
    """
    s = leaf.strip()
    s = _LEADING_JUNK_RE.sub("", s)
    s = _MD_EMPHASIS_RE.sub(" ", s)
    s = _TRAILING_PUNCT_RE.sub("", s)
    s = _WHITESPACE_RE.sub(" ", s).strip()
    return s.lower()


def _is_boilerplate_chunk(heading_path: list[str]) -> bool:
    """True iff the chunk's own immediate section (the LEAF of heading_path,
    never an ancestor) is front/back matter.

    Matching only the leaf is deliberate: a legitimate section can live
    *under* an ancestor called "Appendix" (e.g. "Appendix B. Derivation"), or
    inside a book whose title contains "Index" — only the chunk's own
    section tells you what the chunk IS. Matching ancestors would silently
    delete real content that merely lives near boilerplate-sounding titles.
    """
    leaf = _last_heading(heading_path)
    if not leaf:
        return False
    normalized = _normalize_leaf_heading(leaf)
    if not normalized:
        return False
    return bool(_BOILERPLATE_LEAF_RE.match(normalized))


# Over-fetch multiplier + cap applied before the boilerplate filter so a
# caller's `limit` is still backfilled to full after dropping junk. This is
# nearly free for the vector channel: it scans the whole matrix regardless of
# k, so the marginal cost of a larger k is only in top-k selection, not the
# scan. FTS pays a similarly small marginal cost for a larger LIMIT.
_OVERFETCH_MULTIPLIER = 3
_OVERFETCH_CAP = 300


def hit_to_result(
    hit: index.SearchHit,
    enrichment: dict[str, Any] | None,
    *,
    verbose: bool = False,
    full_text: str = "",
    heading_path: list[str] | None = None,
) -> dict[str, Any]:
    """Map a SearchHit + works-table enrichment to the MCP result contract.

    Lean by default: bib_key/title/chunk_id/heading/text/score. Everything
    else (key, authors, year, category, path, snippet, score_breakdown,
    doi/isbn/arxiv_id) is identity/debug detail that get_work and bib already
    own, or ranking detail nobody needs to act on a hit — gated behind
    verbose=true so the common path isn't paying for it.
    """
    enr = enrichment or {}
    bib_key = _require_bib_key(hit.bib_key, f"search hit chunk_id={hit.chunk_id}")
    heading = _last_heading(heading_path or [])

    result: dict[str, Any] = {
        "bib_key": bib_key,
        "title": hit.title,
        "chunk_id": hit.chunk_id,
        "heading": heading,
        "text": full_text,
        "score": hit.score,
    }
    if not verbose:
        return result

    score_breakdown: dict[str, Any] = {"channel": hit.channel}
    if hit.fts_score is not None:
        score_breakdown["fts_score"] = hit.fts_score
    if hit.vector_score is not None:
        score_breakdown["vector_score"] = hit.vector_score

    result["key"] = hit.key
    result["authors"] = enr.get("authors", [])
    result["year"] = hit.year
    result["category"] = hit.category
    result["path"] = hit.path
    result["snippet"] = hit.snippet
    result["score_breakdown"] = score_breakdown
    for field in ("doi", "isbn", "arxiv_id"):
        val = enr.get(field) or ""
        if val:
            result[field] = val
    return result


def tool_search(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int = 8,
    mode: str = "hybrid",
    verbose: bool = False,
) -> dict[str, Any]:
    if not query or not str(query).strip():
        raise ValueError("query must be a non-empty string")
    limit = max(1, min(int(limit), 100))
    mode = str(mode or "hybrid").lower().strip()
    if mode not in {"fts", "vector", "hybrid"}:
        raise ValueError("mode must be one of: fts, vector, hybrid")

    # Over-fetch so the boilerplate filter below can drop junk and still
    # backfill up to the caller's `limit` real hits, when enough exist. This
    # is nearly free: the vector channel scans the whole matrix regardless of
    # k (cost is top-k selection, not the scan), and FTS's LIMIT cost scales
    # the same way. allow_rebuild=False: this server is read-only, serving
    # boxes must never rebuild the vector index (see
    # alexandria_index.load_vector_index).
    fetch_limit = min(limit * _OVERFETCH_MULTIPLIER, _OVERFETCH_CAP)
    hits, notes = index.search_ex(
        conn, str(query).strip(), mode=mode, limit=fetch_limit, allow_rebuild=False
    )
    # text/heading_path needed up front (not just for verbose) since the
    # boilerplate filter matches on heading_path[-1] before truncation.
    bodies = _chunk_bodies(conn, [h.chunk_id for h in hits])

    kept_hits = []
    filtered_count = 0
    for h in hits:
        _, heading_path = bodies.get(h.chunk_id, ("", []))
        if _is_boilerplate_chunk(heading_path):
            filtered_count += 1
            continue
        kept_hits.append(h)
        if len(kept_hits) >= limit:
            break
    hits = kept_hits

    # Enrichment (authors/doi/isbn/arxiv_id) only feeds the verbose card — skip
    # the works-table round trip entirely on the lean common path.
    enrichment: dict[str, dict[str, Any]] = {}
    if verbose:
        keys = {h.key for h in hits}
        enrichment = _load_work_enrichment(conn, keys)

    results = []
    missing_bodies = 0
    for h in hits:
        if h.chunk_id not in bodies:
            missing_bodies += 1
        body_text, heading_path = bodies.get(h.chunk_id, ("", []))
        results.append(
            hit_to_result(
                h,
                enrichment.get(h.key),
                verbose=verbose,
                full_text=body_text,
                heading_path=heading_path,
            )
        )
    payload: dict[str, Any] = {"ok": True, "query": query, "mode": mode, "results": results}
    # No flag to disable this filter: nothing becomes unreachable by it — get_chunk
    # (by chunk_id or key+ordinal) and get_work(include_markdown=True) still reach
    # every chunk in the library. A blocklist that turns out wrong gets corrected
    # in the code, not flagged around by callers.
    if filtered_count:
        payload["filtered_boilerplate"] = filtered_count
    if missing_bodies:
        notes = list(notes)
        notes.append(
            f"missing_chunk_bodies: {missing_bodies} chunk(s) from ranker absent in database"
        )
    if notes:
        payload["degraded"] = notes
    return payload


def tool_get_work(
    conn: sqlite3.Connection,
    key: str,
    *,
    include_markdown: bool = False,
) -> dict[str, Any]:
    key = str(key or "").strip()
    if not key:
        raise ValueError("key must be a non-empty string")
    row = conn.execute(
        """
        SELECT key, bib_key, category, title, authors, year, doi, isbn, arxiv_id,
               md_path, bib_path, work_dir, valid, validation_errors
        FROM works WHERE key = ? OR bib_key = ?
        """,
        (key, key),
    ).fetchone()
    if row is None:
        return {"ok": False, "error": "not_found", "message": f"work not found: {key}"}

    bib = conn.execute(
        "SELECT bib_text, bib_path, entry_type FROM bib_entries WHERE key = ?",
        (row["key"],),
    ).fetchone()

    library_root = index.get_meta(conn, "library_root") or ""
    md_path = str(row["md_path"] or "")
    bib_path = str((bib["bib_path"] if bib else None) or row["bib_path"] or "")
    bib_key = _require_bib_key(row["bib_key"], f"get_work key={row['key']}")

    out: dict[str, Any] = {
        "ok": True,
        "bib_key": bib_key,
        "key": str(row["key"]),
        "title": str(row["title"] or ""),
        "authors": _parse_authors(row["authors"]),
        "year": str(row["year"] or ""),
        "category": str(row["category"] or ""),
        "md_path": md_path,
        "bib_path": bib_path,
        "work_dir": str(row["work_dir"] or ""),
        "valid": bool(int(row["valid"])),
        "bibtex": str(bib["bib_text"]) if bib else "",
        "entry_type": str(bib["entry_type"]) if bib else "",
    }
    for field in ("doi", "isbn", "arxiv_id"):
        val = str(row[field] or "")
        if val:
            out[field] = val

    if include_markdown:
        text = ""
        if not md_path:
            out["markdown"] = ""
        elif not library_root:
            out["markdown_error"] = "library_root missing; cannot resolve markdown path"
            out["markdown"] = ""
        else:
            try:
                candidate = _safe_under_root(library_root, md_path)
            except ValueError as exc:
                out["markdown_error"] = str(exc)
                out["markdown"] = ""
            else:
                if candidate.is_file():
                    text = candidate.read_text(encoding="utf-8", errors="replace")
                else:
                    out["markdown_error"] = f"markdown file missing: {candidate}"
                out["markdown"] = text
    return out


def tool_get_chunk(
    conn: sqlite3.Connection,
    *,
    chunk_id: str | None = None,
    key: str | None = None,
    ordinal: int | None = None,
    verbose: bool = False,
) -> dict[str, Any]:
    row = None
    if chunk_id:
        row = conn.execute(
            """
            SELECT c.chunk_id, c.key, c.bib_key, c.category, c.title, c.year,
                   c.authors, c.chunk_ordinal, c.text, c.md_path AS path,
                   c.heading_path
            FROM chunks c WHERE c.chunk_id = ?
            """,
            (str(chunk_id),),
        ).fetchone()
    elif key is not None and ordinal is not None:
        row = conn.execute(
            """
            SELECT c.chunk_id, c.key, c.bib_key, c.category, c.title, c.year,
                   c.authors, c.chunk_ordinal, c.text, c.md_path AS path,
                   c.heading_path
            FROM chunks c WHERE c.key = ? AND c.chunk_ordinal = ?
            """,
            (str(key).strip(), int(ordinal)),
        ).fetchone()
    else:
        raise ValueError("provide chunk_id, or key + ordinal")

    if row is None:
        label = chunk_id or f"{key}#{ordinal}"
        return {"ok": False, "error": "not_found", "message": f"chunk not found: {label}"}

    bib_key = _require_bib_key(row["bib_key"], f"get_chunk chunk_id={row['chunk_id']}")
    raw_hp = row["heading_path"]
    try:
        heading_path = json.loads(raw_hp) if raw_hp else []
    except json.JSONDecodeError:
        heading_path = []
    if not isinstance(heading_path, list):
        heading_path = []

    # Lean default: get_chunk is already a targeted lookup, so the fields worth
    # keeping are the ones a targeted lookup needs — full section context
    # (heading_path) plus the text itself. Identity metadata is verbose-only;
    # get_work/bib own identity.
    out: dict[str, Any] = {
        "ok": True,
        "bib_key": bib_key,
        "chunk_id": str(row["chunk_id"]),
        "heading_path": heading_path,
        "text": str(row["text"] or ""),
    }
    if not verbose:
        return out

    enr = _load_work_enrichment(conn, {str(row["key"])}).get(str(row["key"]), {})
    authors = enr.get("authors") or _parse_authors(row["authors"])
    out["key"] = str(row["key"])
    out["title"] = str(row["title"] or "")
    out["authors"] = authors
    out["year"] = str(row["year"] or "")
    out["category"] = str(row["category"] or "")
    out["path"] = str(row["path"] or "")
    out["chunk_ordinal"] = int(row["chunk_ordinal"])
    for field in ("doi", "isbn", "arxiv_id"):
        val = enr.get(field) or ""
        if val:
            out[field] = val
    return out


def tool_bib(conn: sqlite3.Connection, key: str) -> dict[str, Any]:
    key = str(key or "").strip()
    if not key:
        raise ValueError("key must be a non-empty string")
    row = conn.execute(
        """
        SELECT b.key, b.bib_key, b.entry_type, b.bib_path, b.bib_text
        FROM bib_entries b
        WHERE b.key = ? OR b.bib_key = ?
        """,
        (key, key),
    ).fetchone()
    if row is None:
        # Fall back to works.bib_path on disk via works table
        work = conn.execute(
            "SELECT key, bib_key, bib_path FROM works WHERE key = ? OR bib_key = ?",
            (key, key),
        ).fetchone()
        if work is None:
            return {"ok": False, "error": "not_found", "message": f"bib entry not found: {key}"}
        # not_found does not cite — omit bib_key (and key). Hint path in the message only.
        bib_path = str(work["bib_path"] or "")
        msg = f"bib entry not found for key: {key}"
        if bib_path:
            msg = f"{msg} (bib_path={bib_path})"
        return {"ok": False, "error": "not_found", "message": msg}
    bib_key = _require_bib_key(row["bib_key"], f"bib key={row['key']}")
    return {
        "ok": True,
        "bib_key": bib_key,
        "key": str(row["key"]),
        "entry_type": str(row["entry_type"] or ""),
        "bib_path": str(row["bib_path"] or ""),
        "bibtex": str(row["bib_text"] or ""),
    }


def _tool_error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _tool_ok(payload: dict[str, Any]) -> dict[str, Any]:
    """Structured content plus the same JSON as text, for clients that read only text."""
    return {
        "content": [{"type": "text", "text": json.dumps(payload, indent=2)}],
        "structuredContent": payload,
        "isError": False,
    }


def _tool(name: str, description: str, inputSchema: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "description": description, "inputSchema": inputSchema}


def tool_definitions() -> list[dict[str, Any]]:
    # Names: [A-Za-z0-9._-] only; no dots needed — client namespaces by server.
    return [
        _tool(
            name="search",
            description=(
                "Search the Alexandria library index (FTS / vector / hybrid). "
                "Every hit includes bib_key for \\cite{bib_key} and full chunk text."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Free-text query"},
                    "limit": {
                        "type": "integer",
                        "minimum": 0,
                        "default": 8,
                        "description": (
                            "Max hits (default 8). Clamped to [1, 100] "
                            "inside the tool (0→1, values above 100→100)."
                        ),
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["fts", "vector", "hybrid"],
                        "default": "hybrid",
                        "description": "fts | vector | hybrid (default hybrid)",
                    },
                    "verbose": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Return the full metadata card (authors, year, "
                            "category, path, snippet, score_breakdown, ...) "
                            "in addition to text/heading. Debugging only."
                        ),
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
        _tool(
            name="get_work",
            description=(
                "Fetch one work by citation key: metadata, paths, BibTeX, "
                "optional full markdown."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Work key or bib_key (e.g. wang2029)",
                    },
                    "include_markdown": {
                        "type": "boolean",
                        "default": False,
                        "description": "Include full markdown body when available",
                    },
                },
                "required": ["key"],
                "additionalProperties": False,
            },
        ),
        _tool(
            name="get_chunk",
            description=(
                "Fetch one chunk by chunk_id, or by key + ordinal. "
                "Returns chunk text, section heading hierarchy, and bib_key "
                "(for citation). Lean default; verbose restores full metadata."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "chunk_id": {
                        "type": "string",
                        "description": "Chunk id from a search hit",
                    },
                    "key": {
                        "type": "string",
                        "description": "Work key (use with ordinal)",
                    },
                    "ordinal": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Chunk ordinal within the work (use with key)",
                    },
                    "verbose": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Return the full metadata card (key, title, "
                            "authors, year, category, path, chunk_ordinal, "
                            "doi/isbn/arxiv_id) in addition to the lean fields."
                        ),
                    },
                },
                "additionalProperties": False,
            },
        ),
        _tool(
            name="bib",
            description=(
                "Return the BibTeX entry and path for a citation key "
                "(for direct \\cite{bib_key} / bibliography use)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Work key or bib_key",
                    },
                },
                "required": ["key"],
                "additionalProperties": False,
            },
        ),
    ]


_TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
}


def _validate_arguments(schema: dict[str, Any], args: dict[str, Any]) -> str | None:
    """Check ``args`` against a tool's inputSchema; return a message or None.
    Covers exactly what the schemas use: required, type, enum, minimum, additionalProperties."""
    props = schema.get("properties", {})
    for name in schema.get("required", []):
        if name not in args:
            return f"'{name}' is a required property"
    for name, value in args.items():
        spec = props.get(name)
        if spec is None:
            if schema.get("additionalProperties") is False:
                return f"Additional properties are not allowed ('{name}' was unexpected)"
            continue
        check = _TYPE_CHECKS.get(spec.get("type", ""))
        if check is not None and not check(value):
            return f"{value!r} is not of type '{spec['type']}'"
        if "enum" in spec and value not in spec["enum"]:
            return f"{value!r} is not one of {spec['enum']}"
        if "minimum" in spec and value < spec["minimum"]:
            return f"{value!r} is less than the minimum of {spec['minimum']}"
    return None


def call_tool(holder: _ConnHolder, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    global _LAST_ACTIVITY
    _LAST_ACTIVITY = time.monotonic()
    args = arguments or {}
    schema = next((t["inputSchema"] for t in tool_definitions() if t["name"] == name), None)
    if schema is not None:
        problem = _validate_arguments(schema, args)
        if problem:
            return _tool_error(f"Input validation error: {problem}")
    conn = _live_conn(holder)
    try:
        if name == "search":
            payload = tool_search(
                conn,
                str(args.get("query", "")),
                limit=int(args.get("limit", 8)),
                mode=str(args.get("mode", "hybrid")),
                verbose=bool(args.get("verbose", False)),
            )
            return _tool_ok(payload)

        if name == "get_work":
            payload = tool_get_work(
                conn,
                str(args.get("key", "")),
                include_markdown=bool(args.get("include_markdown", False)),
            )
            if not payload.get("ok"):
                return _tool_error(payload.get("message", "not found"))
            return _tool_ok(payload)

        if name == "get_chunk":
            payload = tool_get_chunk(
                conn,
                chunk_id=args.get("chunk_id"),
                key=args.get("key"),
                ordinal=args.get("ordinal"),
                verbose=bool(args.get("verbose", False)),
            )
            if not payload.get("ok"):
                return _tool_error(payload.get("message", "not found"))
            return _tool_ok(payload)

        if name == "bib":
            payload = tool_bib(conn, str(args.get("key", "")))
            if not payload.get("ok"):
                return _tool_error(payload.get("message", "not found"))
            return _tool_ok(payload)

        return _tool_error(f"unknown tool: {name}")
    except ValueError as exc:
        return _tool_error(str(exc))
    except Exception as exc:  # noqa: BLE001 — surface as tool error, keep server alive
        return _tool_error(f"{type(exc).__name__}: {exc}")



SUPPORTED_PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")


class _RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def handle_request(holder: _ConnHolder, method: str, params: dict[str, Any]) -> dict[str, Any]:
    if method == "initialize":
        asked = params.get("protocolVersion")
        return {
            "protocolVersion": asked if asked in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0],
            "capabilities": {"experimental": {}, "tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": tool_definitions()}
    if method == "tools/call":
        if not isinstance(params.get("name"), str):
            raise _RpcError(-32602, "tools/call needs a string 'name'")
        return call_tool(holder, params["name"], params.get("arguments"))
    raise _RpcError(-32601, f"Method not found: {method}")


def _idle_watchdog() -> None:
    """Exit when no request arrives for IDLE_EXIT_SECONDS. Disabled when the value is <= 0."""
    if IDLE_EXIT_SECONDS <= 0:
        return
    while True:
        time.sleep(min(60, IDLE_EXIT_SECONDS))
        if time.monotonic() - _LAST_ACTIVITY >= IDLE_EXIT_SECONDS:
            # os._exit: nothing to flush, and a normal exit can block on the open stdin.
            os._exit(0)


def run_stdio(conn: sqlite3.Connection, db_path: Path) -> None:
    """Newline-delimited JSON-RPC over stdin/stdout. Stdout carries protocol only; anything
    else that prints goes to stderr. Notifications get no reply."""
    global _LAST_ACTIVITY
    # Resolved so this matches the path string alexandria_index reports back via
    # PRAGMA database_list / _conn_db_path — that's what the vector cache is keyed on.
    holder = _ConnHolder(conn, db_path.resolve())
    out = sys.stdout
    sys.stdout = sys.stderr
    threading.Thread(target=_idle_watchdog, daemon=True).start()

    def reply(obj: dict[str, Any]) -> None:
        out.write(json.dumps(obj, separators=(",", ":")) + "\n")
        out.flush()

    try:
        while True:
            line = sys.stdin.readline()
            if not line:
                break
            if not line.strip():
                continue
            _LAST_ACTIVITY = time.monotonic()
            try:
                msg = json.loads(line)
            except ValueError:
                reply({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
                continue
            if not isinstance(msg, dict) or not isinstance(msg.get("method"), str):
                if isinstance(msg, dict) and "id" in msg:
                    continue  # a response from the client; this server sends no requests
                reply({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}})
                continue
            if "id" not in msg:
                continue  # notification
            params = msg.get("params")
            try:
                if params is not None and not isinstance(params, dict):
                    raise _RpcError(-32602, "params must be an object")
                result = handle_request(holder, msg["method"], params or {})
                reply({"jsonrpc": "2.0", "id": msg["id"], "result": result})
            except _RpcError as exc:
                reply({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": exc.code, "message": str(exc)}})
    finally:
        holder.conn.close()  # close whichever connection ended up live (may have been reopened)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Alexandria MCP server (stdio, read-only over the search index)",
    )
    parser.add_argument(
        "--library",
        type=Path,
        default=Path("."),
        help="library root used only to resolve the default DB path "
        "(overridden by ALEXANDRIA_INDEX_DB)",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="index DB path (default: ALEXANDRIA_INDEX_DB or "
        "<library>/.sc/alexandria-qim/index.db)",
    )
    args = parser.parse_args(argv)

    if args.db is not None:
        db_path = args.db.expanduser()
    else:
        db_path = resolve_db_path(args.library)

    try:
        conn = open_index_readonly(db_path)
    except (FileNotFoundError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    # run_stdio owns closing conn (or its reopened replacement — see _ConnHolder).
    run_stdio(conn, db_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
