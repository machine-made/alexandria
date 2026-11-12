# ADR 0005 — Alexandria search index (dependency-free FTS5 + cosine)

Status: Accepted (2026-07-15)
Bead: alexandria-qim · Impl: see `impl(feat): alexandria-qim build the Alexandria search index`

## Context
The citation library needed a searchable index for its consumers (the MCP server,
bead tvw) once filing metadata became trustworthy (ADR 0002–0004). The folded design
beads (fyz/5zn/z9n/0ww/4il/xso/m3d/rig) specified FTS5 keyword search plus vector
semantic search over the per-work `embeddings.jsonl` that the pipeline already
produces, named `sqlite-vec` as the vector store. sqlite-vec is not installed, and
adding it is a new dependency; the corpus is small (2 works filed, ~100 works
expected) and the index is derived, rebuildable state.

## Decision
Build a single manual module `pipeline/alexandria_index.py` — a SQLite index that is
**dependency-free** and **read-only over the library**:

- **No sqlite-vec.** FTS5 (BM25) comes from the stdlib `sqlite3`; embeddings are
  stored as float32 BLOBs in a `chunk_embeddings` table and semantic search is
  brute-force cosine in numpy. At ~100 works × ~20 chunks (≈2000 × 1024-dim
  vectors) brute force is milliseconds; a vector *index* (ANN) earns its keep only
  at far larger scale. Swapping in sqlite-vec later stays a clean, approval-gated
  change behind the same query API.
- **Schema:** `works`, `chunks`, `chunk_embeddings`, `source_files`, `bib_entries`,
  `index_meta`. Every chunk row carries `key` and `bib_key` = the Alexandria
  citation key = the `<category>/<key>.md` filename stem.
- **Harvester** walks only filed category dirs; a work whose filename-stem key,
  `.bib` `@type{key}`, and metadata key disagree — or whose `.bib` is missing/
  unparseable — is marked `valid=0` and **its chunks are skipped**, so a wrong
  citation identity is never silently indexed. `validate` surfaces missing
  `bib_key`, invalid works, dimension mismatch, and stale embeddings.
- **Queries** (FTS5 / vector / hybrid RRF) return `key`/`bib_key`/`category`/
  `title`/`year`/`snippet`/`path`/`score`. CLI: `rebuild`/`upsert-work`/`validate`/
  `search`/`stats`, human-table and `--json`. Manual only — no auto-index-after-file
  hook this cycle.

## Consequences
- The library gains keyword + semantic + hybrid search with zero new dependencies,
  portable anywhere stdlib `sqlite3` (FTS5) + numpy run.
- The index is pure derived state: `rebuild` is idempotent and never writes to any
  library file (`*.md`/`*.bib`/`metadata.json`/registry/root bib) — a bug costs a
  rebuild, not data loss, which is why the verification bar is fixtures + one real
  end-to-end rather than the filing thread's pilot gates.
- Real-library end-to-end verified: `rebuild` → 2 works / 100 chunks; queries
  "oblique shock"→song2023, "transpiration cooling"→wang2029, "boundary layer"→
  song2023 return the correct citation keys.
- Follow-up alexandria-zpt tracks optional validation strengthening (embedding/text
  desync, field-level bib↔metadata parity). The MCP server (tvw) consumes this
  index's query API next.
