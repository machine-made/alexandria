# ADR 0002 — Metadata precedence ladder

Status: Accepted (2026-06-27)

## Context
A filed work's metadata can come from several sources of differing authority: a resolved
identifier (DOI/Crossref/arXiv/ISBN — not yet built), the PDF's own embedded
Info/XMP metadata, identifiers/front-matter parsed from the Marker Markdown, and pure
heuristics over the text. Previously `infer_metadata` mixed these ad hoc with `or` chains,
so a weak heuristic could mask a stronger source and the origin of each field was invisible.

## Decision
Resolve metadata FIELD-WISE through a fixed, documented ladder
(`apply_metadata_precedence` in `alexandria_metadata_common.py`):

1. resolved DOI/arXiv/ISBN metadata  (highest authority)
2. PDF XMP/Info (`pdf_meta.json`)
3. parsed Markdown identifiers / front-matter
4. heuristic fallback
5. manual / default fallback

For each field, the first tier supplying a non-empty value wins; lower tiers never override
it. The merged record carries `metadata_field_sources` — a per-field map of which tier
supplied the value — so provenance is auditable. `metadata_source` reports `pdf` when a PDF
field was used. The embedded PDF metadata is captured into `pdf_meta.json` while the
decrypted source is still present (before the workdir scrub) so deleting the source can never
lose the only structured copy.

## Consequences
- Stronger sources deterministically beat weaker ones; metadata quality improves as the
  resolver tier (oeh) comes online — it slots in at tier 1 with no further ladder changes.
- Per-field provenance (`metadata_field_sources`) makes filing decisions explainable.
- The ladder logic is stdlib-only and importable without pypdf; only the extractor
  (`alexandria_pdf_meta.py`) needs the dependency, so the stdlib test harness still runs.
