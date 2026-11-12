# ADR 0004 — Year inference decision

Status: Accepted (2026-07-14)

## Context
`inferred_year` read a heat-capacity table value (`2029 J/kg/K`) as xu2029's
publication year and filed it at HIGH confidence: the paper's `pdf_meta.json`
had no discrete `year` (only `dates: 2026-06-04`), its DOI embeds `.2026.`, but
neither was used — the bare-token fallback grabbed the first `19xx|20xx` token in
the front matter, and `metadata_confidence` had no year-plausibility check. Year
drives the citation key (`base_key(author, year)`), so a wrong/future year both
mis-keys the work and mis-shelves its BibTeX — the same identity-plausibility
failure class as the category bug (ADR 0003).

## Decision
Year is resolved by an explicit decision after the precedence ladder (ADR 0002)
merges tiers — `_apply_year_decision` in `alexandria_metadata_common.py`, called
alongside `_apply_category_decision`:

1. An implausible candidate year is cleared first (see the gate below).
2. `resolved` ladder year wins (source `resolved`).
3. DOI-embedded year (`year_from_doi`) → source `doi`. arXiv DOIs and DOIs with
   more than one distinct year-like token yield nothing (the arXiv `YYMM` is a
   submission id, not a year; multi-token DOIs are ambiguous). When a DOI year
   and a pdf-dates year differ by more than one year, the more reliable pdf
   dates win.
4. An existing plausible pdf/epub/markdown ladder year (pdf may be
   dates-derived, see below) keeps its source.
5. A residual `dates` map → source `pdf`.
6. Otherwise `nodate`.

`pdf_meta_layer` now derives a pdf-tier year from `pdf_meta['dates']` when the
PDF carries no discrete year, so date evidence beats the heuristic bare token.
The **plausibility gate** — `is_plausible_publication_year`, bounds
`[1450, datetime.now().year + 1]` — is enforced at layer normalization, in
`inferred_year`, in `_apply_year_decision`, and in `metadata_confidence`: an
out-of-range year (table value, far-future typo, OCR garbage) never becomes the
filing year; the work holds low instead of filing a wrong year at high
confidence. Year provenance is recorded in `metadata_field_sources['year']`.

## Consequences
- A future or table-value year can no longer file at high confidence; such a
  work holds for review (`metadata_confidence == "low"`).
- Real xu2029 re-inference yields year `2026` from the DOI (verified), with the
  correct provenance — no longer a false `heuristic` label.
- DOI string parsing is treated as weaker than pdf dates on conflict, so arXiv
  `YYMM` ids and reused journal-id years cannot override reliable dates.
- The already-filed `xu2029` work keeps its wrong key until a deliberate,
  audited re-key to `xu2026` (tracked separately; the code fix does not mutate
  the library). Resolver-issued dates slot in above as an even
  stronger year source with no change to this order.
