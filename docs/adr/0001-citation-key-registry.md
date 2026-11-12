# ADR 0001 — Citation-key registry & immutability contract

Status: Accepted (2026-06-27)
Bead: alexandria-1aq · Impl: see `impl(feat): key registry & immutability contract`

## Context
Citation keys (`<surname><year>`) are the stable handle every `.bib` entry and LaTeX
`\cite{}` depends on. Two pre-existing defects (surfaced by the alexandria-znp audit, risk R1):
1. `unique_key` reserved keys only within a single category directory, but
   `rebuild_root_bibliography` merges all categories into one `alexandria.bib` — so the
   same `surnameYYYY` filed under `papers/` and `misc/` produced a DUPLICATE BibTeX key
   that breaks `bibtex`/`biber` at compile time.
2. Collision suffixes (`a`, `b`, …) were assigned by incidental filesystem-glob order, so
   the same inputs could yield different keys across runs, and a re-file could silently
   mutate a key that LaTeX documents had already cited.

## Decision
Introduce an append-only registry, `key_registry.json` at the library root, owning the
`source_sha256 → (category, key)` mapping (`pipeline/alexandria_key_registry.py`).
- **Immutable reuse:** once a source hash has a key, every future filing of those bytes
  (even under a new filename/stem) reuses the exact key; a re-file that would remap an
  existing hash raises and STOPS rather than mutating silently.
- **Global uniqueness:** key allocation reserves across ALL categories
  (`global_filed_keys()` ∪ registry keys), so cross-category collisions are impossible —
  the R1 fix. `unique_key` also now avoids `.bib` stem collisions.
- **Deterministic suffixing:** colliding new works are ordered by a stable content key
  (`source_sha256`), not filesystem order (`reserve_key_batch`).
- **Persistence:** atomic write (tmp + `os.replace`), sorted JSON for clean diffs,
  schema-versioned (`version: 1`), matching the pipeline's existing filing-safety patterns.
- `find_existing_by_sha` consults the registry first, keeping the filesystem scan as a
  legacy/compat fallback (and recording legacy hits forward into the registry).

## Consequences
- The merged `alexandria.bib` can no longer contain duplicate keys → LaTeX-safe.
- Keys are reproducible and frozen once assigned → safe to cite mid-project.
- No new dependency (stdlib only). Library is empty today, so no data migration was needed.
- `key_registry.json` becomes a tracked part of each library's state.
- Downstream beads build on this: xvm (validation gates), bro (BibTeX rendering),
  m0w (quarantine promotion).
