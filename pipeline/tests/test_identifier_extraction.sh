#!/usr/bin/env bash
# Identifier extraction shapes + reference-section guard.
# Fully offline; exercises identifier_lines / extract_* / markdown_metadata_layer / infer_metadata.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export PYTHONPATH="$ROOT/pipeline${PYTHONPATH:+:$PYTHONPATH}"

python3 - <<'PY'
from alexandria_metadata_common import (
    extract_arxiv_id,
    extract_doi,
    extract_doi_from_identifier_lines,
    extract_isbn,
    identifier_lines,
    infer_metadata,
    markdown_metadata_layer,
    normalize_doi,
)

# --- Prefix forms still work (regression) ---
prefix = """# Rocket Book

DOI: 10.1201/9781003226901
ISBN 978-1-032-22690-1
arXiv:2401.01234
"""
ids = identifier_lines(prefix)
assert extract_doi(ids) == "10.1201/9781003226901", ids
assert extract_isbn(ids) == "9781032226901", ids
assert extract_arxiv_id(ids) == "2401.01234", ids

# --- URL-form DOI (Marker shape) ---
url_doi = """# A Marker Paper

Alice Author
2024

https://doi.org/10.1234/marker.example

## Abstract
Body text.
"""
ids = identifier_lines(url_doi)
assert "https://doi.org/10.1234/marker.example" in ids, ids
assert extract_doi(ids) == "10.1234/marker.example", ids
layer = markdown_metadata_layer(url_doi)
assert layer.get("doi") == "10.1234/marker.example", layer

dx_url = """# Paper

https://dx.doi.org/10.5555/dx.form
"""
assert extract_doi(identifier_lines(dx_url)) == "10.5555/dx.form"

http_url = """# Paper

http://doi.org/10.5555/http.form
"""
assert extract_doi(identifier_lines(http_url)) == "10.5555/http.form"

# --- Mid-line doi: ---
mid_doi = """# Paper

Available at doi: 10.5555/midline.work

## Introduction
"""
ids = identifier_lines(mid_doi)
assert extract_doi(ids) == "10.5555/midline.work", ids

# --- Bare 10.x DOI ---
bare = """# Paper

10.9999/bare.doi.here

## Abstract
"""
assert extract_doi(identifier_lines(bare)) == "10.9999/bare.doi.here"

# --- arXiv URL form (same gate) ---
arxiv_url = """# Preprint

https://arxiv.org/abs/2401.12345

## Abstract
"""
ids = identifier_lines(arxiv_url)
assert extract_arxiv_id(ids) == "2401.12345", ids
layer = markdown_metadata_layer(arxiv_url)
assert layer.get("arxiv_id") == "2401.12345", layer

# Mid-line arXiv:
mid_arxiv = """# Preprint

See arXiv: 2402.54321 for the full text.
"""
assert extract_arxiv_id(identifier_lines(mid_arxiv)) == "2402.54321"

# Mid-line ISBN
mid_isbn = """# Book

Published under ISBN 978-1-491-95766-0 by O'Reilly.
"""
assert extract_isbn(identifier_lines(mid_isbn)) == "9781491957660"

# --- HARD INVARIANT: References section poison never leaks ---
refs_only = """# My Paper

Author: Jane Doe
Year: 2024

## References

DOI: 10.9999/reference
Smith J. https://doi.org/10.9999/reference.
"""
assert identifier_lines(refs_only) == "", repr(identifier_lines(refs_only))
assert extract_doi(identifier_lines(refs_only)) == ""
layer = markdown_metadata_layer(refs_only)
assert not layer.get("doi"), layer
meta = infer_metadata(refs_only, "my_paper", [])
assert meta.get("doi") == "", meta

# Own DOI in front matter wins; References DOI is ignored (first-match + stop).
own_plus_refs = """# My Paper

https://doi.org/10.1111/own.work

## References

DOI: 10.9999/someone.else
https://doi.org/10.9999/someone.else
"""
ids = identifier_lines(own_plus_refs)
assert extract_doi(ids) == "10.1111/own.work", ids
assert "10.9999" not in ids, ids

# Bibliography stop heading
biblio = """# Essay

## Bibliography

10.9999/biblio.only
"""
assert extract_doi(identifier_lines(biblio)) == ""

# --- Conservative-by-design: stop section before DOI ---
after_abstract = """# Paper

## Abstract

https://doi.org/10.5555/after.abstract
"""
assert extract_doi(identifier_lines(after_abstract)) == ""

after_intro = """# Paper

## Introduction

doi: 10.5555/after.intro
"""
assert extract_doi(identifier_lines(after_intro)) == ""

# --- Conservative-by-design: past 220-line front-matter bound ---
# front_matter_lines(text, 220) only considers the first 220 raw lines.
padding = "\n".join(f"padding line {i}" for i in range(230))
past_bound = f"""# Paper

{padding}
https://doi.org/10.5555/past.bound
"""
assert extract_doi(identifier_lines(past_bound)) == ""

# --- End-to-end: Marker URL DOI → papers / doi_default (not misc) ---
marker = """# Global Instability of SWBLI

Alice Author

https://doi.org/10.1063/5.0160648

Quantitative agreement with DNS without domain keywords in title.
"""
meta = infer_metadata(marker, "song_like_url", [])
assert meta["doi"] == "10.1063/5.0160648", meta
assert meta["category"] == "papers", meta
assert meta["metadata_field_sources"]["category"] == "doi_default", meta
assert meta["metadata_field_sources"]["doi"] == "markdown", meta

# arXiv URL alone also routes to papers
meta_arx = infer_metadata(
    "# Preprint Only\n\nhttps://arxiv.org/abs/2401.12345\nNo topical keywords.\n",
    "preprint_url",
    [],
)
assert meta_arx["arxiv_id"] == "2401.12345", meta_arx
assert meta_arx["category"] == "papers", meta_arx
assert meta_arx["metadata_field_sources"]["category"] == "doi_default", meta_arx

# =============================================================================
# Round 2 — wrong-DOI selection (AC-6) + funder + unbalanced paren
# =============================================================================

OWN = "10.1016/j.actaastro.2026.03.001"
COMPANION = "10.9999/companion.other"
DATASET = "10.5281/zenodo.999999"
FUNDER = "10.13039/501100001665"
LANCET = "10.1016/s0140-6736(00)02034-9"
SONG = "10.1063/5.0160648"

# --- Noise-before-own: companion URL then labeled own DOI ---
noise_companion = f"""# Paper

Companion article: https://doi.org/{COMPANION}

DOI: {OWN}
"""
# extract_doi stays first-match (pdf_meta contract)
assert extract_doi(identifier_lines(noise_companion)) == COMPANION, extract_doi(identifier_lines(noise_companion))
# markdown path prefers line-initial labeled DOI
layer = markdown_metadata_layer(noise_companion)
assert layer.get("doi") == OWN, layer
assert extract_doi_from_identifier_lines(identifier_lines(noise_companion)) == OWN

# --- Dataset-before-own ---
noise_dataset = f"""# Paper

Data available at https://doi.org/{DATASET}

DOI: {OWN}
"""
assert extract_doi(identifier_lines(noise_dataset)) == DATASET
assert markdown_metadata_layer(noise_dataset).get("doi") == OWN

# --- Cite-as prose then labeled own DOI ---
cite_then_own = f"""# Paper

Cite as: Phys. Fluids 35, 084121 (2023); doi: 10.9999/cite.as.noise Submitted: 4 June 2023

DOI: {OWN}
"""
assert markdown_metadata_layer(cite_then_own).get("doi") == OWN

# --- Funder mid-line + own URL ---
funder_plus_own = f"""# Paper

This work was funded by the ANR (DOI: {FUNDER}).

https://doi.org/{OWN}
"""
ids = identifier_lines(funder_plus_own)
# first-match without funder filter would return funder (and unbalanced ')' before normalize fix)
assert markdown_metadata_layer(funder_plus_own).get("doi") == OWN, markdown_metadata_layer(funder_plus_own)
assert extract_doi_from_identifier_lines(ids) == OWN

# --- Funder-only front matter → empty, not the funder id ---
funder_only = f"""# Paper

This work was funded by the ANR (DOI: {FUNDER}).

## Abstract
"""
assert markdown_metadata_layer(funder_only).get("doi") in (None, ""), markdown_metadata_layer(funder_only)
assert extract_doi_from_identifier_lines(identifier_lines(funder_only)) == ""
meta_f = infer_metadata(funder_only, "funder_only", [])
assert meta_f.get("doi") == "", meta_f

# --- normalize_doi: unbalanced trailing ')' stripped; Lancet balanced path kept ---
assert normalize_doi("10.13039/501100001665)") == "10.13039/501100001665"
assert normalize_doi("10.1016/S0140-6736(00)02034-9") == LANCET
assert normalize_doi("10.1016/S0140-6736(00)02034-9)") == LANCET  # one extra outer paren
# extract_doi skips funder-registry DOIs (10.13039/) — never a work DOI
funder_prose = f"funded by the ANR (DOI: {FUNDER})."
assert extract_doi(funder_prose) == ""
funder_then_work = f"funded by the ANR (DOI: {FUNDER}). Work DOI: {OWN}"
assert extract_doi(funder_then_work) == OWN

# --- park2023 real AIP boilerplate: own DOI only mid-prose, no labeled line ---
song_shape = f"""# Global Instability of SWBLI

This article may be downloaded for personal use only. Any other use requires prior permission of the author and AIP Publishing. This article may be downloaded for personal use only and may be found at https://doi.org/{SONG}.

Physics of Fluids 35, 084121 (2023) https://doi.org/{SONG}

Cite as: Phys. Fluids **35**, 084121 (2023); doi: {SONG} Submitted: 4 June 2023 · Accepted: 1 August 2023 · Published Online: 15 August 2023

## Abstract
Body.
"""
assert markdown_metadata_layer(song_shape).get("doi") == SONG, markdown_metadata_layer(song_shape)
meta_song = infer_metadata(song_shape, "park2023_shape", [])
assert meta_song["doi"] == SONG, meta_song
assert meta_song["category"] == "papers", meta_song

# --- HARD: extract_doi first-match contract unchanged (pdf_meta path) ---
# Multi-DOI text with labeled DOI second: extract_doi returns the FIRST hit, not labeled.
pdf_like = f"Companion article: https://doi.org/{COMPANION}\nDOI: {OWN}\n"
assert extract_doi(pdf_like) == COMPANION, extract_doi(pdf_like)
# Markdown selection disagrees on purpose:
assert extract_doi_from_identifier_lines(identifier_lines("# T\n\n" + pdf_like)) == OWN

print("OK identifier extraction shapes + reference-section guard + wrong-DOI selection")
PY
