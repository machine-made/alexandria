# ADR 0003 — Category inference decision

Status: Accepted (2026-07-14)
Bead: alexandria-o7n · Impl: see `impl(fix): alexandria-o7n complete category-inference fix`

## Context
`inferred_category` chose a work's shelf from front-matter text and misfiled in two
ways, both caught by the lm9 five-PDF pilot: (1) SUBSTRING matching — `"quant" in sample`
matched *quantitative/quantify*, filing a fluid-dynamics paper (song2023) into `quant/`;
and (2) a hard `misc` fallback in the heuristic layer, so a DOI'd journal article with no
domain keyword never reached `papers/`. Category is invisible to the confidence gate, so a
wrong category still filed at HIGH confidence, and it feeds BibTeX type selection — a
class of identity error that scales to the ~80 remaining bulk PDFs.

## Decision
Category is resolved by an explicit, auditable decision that runs AFTER the precedence
ladder (ADR 0002) has merged all tiers field-wise — implemented as
`_apply_category_decision` in `alexandria_metadata_common.py`, called at the end of
`apply_metadata_precedence`:

1. An explicit category from a ladder tier (`resolved`/`pdf`/`epub`/`markdown`/`manual`)
   keeps its tier source and wins.
2. Else a topical KEYWORD hit (word-bounded, via `_keyword_category`) → its domain,
   provenance `keyword`.
3. Else an ARTICLE signal — a non-empty `doi`, `arxiv_id`, or `journal` on the merged
   record — → `papers`, provenance `doi_default`.
4. Else → `misc`, provenance `heuristic`.

Keyword matching is always word-bounded (`\bterm\b`), so morphological near-misses cannot
steal a shelf. Because the article signal is read off the MERGED metadata, a DOI/journal
supplied by a higher tier (e.g. `pdf_meta.json`) routes real articles to `papers/` even
when the Markdown alone yields no identifier. The `optimization`/`optimisation` group —
common casually in CFD abstracts — matches markdown HEADINGS only; `propulsion`/`quant`
scan the full front-matter sample. Provenance is recorded in `metadata_field_sources`
under the vocabulary `keyword` / `doi_default` (alongside the existing tier labels).

## Consequences
- Category becomes evidence-driven and explainable: `metadata_field_sources['category']`
  says exactly which path decided the shelf. A keyword hit always beats a bare DOI.
- DOI'd journal articles reach `papers/` (verified: real song2023 re-inference through
  `pdf_meta.json` → `papers`/`doi_default`) instead of silting into `misc`.
- Trade-offs, tracked as follow-ups: the `papers` default requires the `doi`/`journal`
  field to be populated, which for the current PDF flow comes from `pdf_meta.json` —
  Markdown-only sources whose DOI is a URL/mid-line shape are not extracted
  (alexandria-ihs); and the headings-only `optimization` guard can under-file body-only
  optimization papers to `papers`/`misc` (alexandria-5e4). Both err toward the safe
  generic shelf, never toward domain poisoning.
- Resolver-based container/arXiv-class enrichment (bead fvb) slots in above as a stronger
  article signal with no change to this decision order.
