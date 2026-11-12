#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

WORK="$TMP/parsed/work"
mkdir -p "$WORK"
cat > "$WORK/pdf_meta.json" <<'JSON'
{
  "schema_version": 1,
  "title": "PDF Title",
  "authors": ["PDF Author"],
  "year": "2022",
  "isbn": "978-1-491-95766-0",
  "publisher": "PDF Press",
  "pages": 321,
  "dates": {"published": "2022-05-01"},
  "info": {"/Title": "PDF Title"},
  "xmp": {}
}
JSON

cat > "$WORK/work.md" <<'MD'
# Markdown Heading

Title: Markdown Title
Author: Markdown Author
Year: 1999
Category: papers
DOI: 10.1111/MARKDOWN
MD
ALEXANDRIA_PARSED_WORK="$WORK" "$ROOT/pipeline/alexandria_metadata_from_markdown.py"
python3 - <<PY
import json
from pathlib import Path
m = json.loads(Path('$WORK/metadata.json').read_text(encoding='utf-8'))
assert m['title'] == 'PDF Title', m
assert m['author'] == 'PDF Author', m
assert m['year'] == '2022', m
assert m['isbn'] == '9781491957660', m
assert m['doi'] == '10.1111/markdown', m
assert m['category'] == 'papers', m
assert m['metadata_field_sources']['title'] == 'pdf', m
assert m['metadata_field_sources']['doi'] == 'markdown', m
PY

PYTHONPATH="$ROOT/pipeline${PYTHONPATH:+:$PYTHONPATH}" python3 - <<PY
from pathlib import Path
from alexandria_metadata_common import infer_metadata, merge_metadata, pdf_meta_path_for_work

work = Path('$WORK')
text = '''# Heuristic Rocket Heading

Author: Markdown Author
Year: 1999
Category: papers
DOI: 10.1111/MARKDOWN
arXiv:2401.01234
'''
resolved = {
    'title': 'Resolved DOI Title',
    'doi': '10.2222/RESOLVED',
    'journal': 'Resolved Journal',
}
meta = infer_metadata(text, 'work', [], pdf_meta_path=pdf_meta_path_for_work(work, 'work'), resolved_metadata=resolved)
sources = meta['metadata_field_sources']
assert meta['title'] == 'Resolved DOI Title', meta
assert meta['doi'] == '10.2222/resolved', meta
assert meta['journal'] == 'Resolved Journal', meta
assert sources['title'] == 'resolved', sources
assert sources['doi'] == 'resolved', sources
assert sources['journal'] == 'resolved', sources

assert meta['author'] == 'PDF Author', meta
assert meta['authors'] == ['PDF Author'], meta
assert meta['year'] == '2022', meta
assert meta['isbn'] == '9781491957660', meta
assert meta['publisher'] == 'PDF Press', meta
assert meta['pages'] == 321, meta
assert meta['dates'] == {'published': '2022-05-01'}, meta
for field in ('author', 'authors', 'year', 'isbn', 'publisher', 'pages', 'dates'):
    assert sources[field] == 'pdf', (field, sources)

assert meta['category'] == 'papers', meta
assert meta['arxiv_id'] == '2401.01234', meta
assert sources['category'] == 'markdown', sources
assert sources['arxiv_id'] == 'markdown', sources
assert meta['metadata_source'] == 'doi', meta
assert meta['metadata_confidence'] == 'high', meta

heuristic = infer_metadata('# Rocket Flight\n\nFirst edition published 2023. Rockets everywhere.\n', 'rocket_flight', [])
assert heuristic['title'] == 'Rocket Flight', heuristic
assert heuristic['year'] == '2023', heuristic
assert heuristic['category'] == 'propulsion', heuristic
assert heuristic['metadata_field_sources']['title'] == 'heuristic', heuristic
assert heuristic['metadata_field_sources']['year'] == 'heuristic', heuristic
assert heuristic['metadata_field_sources']['category'] == 'keyword', heuristic

# Word-boundary: morphological near-misses must NOT steal the quant shelf.
scientific = infer_metadata(
    '# Experimental Measurements\n\nThe observed response was quantified across all samples.\n',
    'experimental_measurements',
    [],
)
assert scientific['category'] == 'misc', scientific
assert scientific['metadata_field_sources']['category'] == 'heuristic', scientific

agreement = infer_metadata(
    '# Fluids Study\n\nQuantitative agreement with experiment is excellent.\n',
    'fluids_study',
    [],
)
assert agreement['category'] == 'misc', agreement

# "optimization problem" in a non-domain fluids abstract (no DOI) → NOT optimization.
fluids_opt = infer_metadata(
    '# Shock Boundary Layer\n\n'
    'An optimization problem for the mesh size is noted in the appendix.\n',
    'shock_boundary_layer',
    [],
)
assert fluids_opt['category'] == 'misc', fluids_opt
assert fluids_opt['category'] != 'optimization', fluids_opt

# Positive keyword hits (word-boundary).
rocket = infer_metadata(
    '# Launch Systems\n\nRocket propulsion systems for orbital transfer.\n',
    'launch_systems',
    [],
)
assert rocket['category'] == 'propulsion', rocket
assert rocket['metadata_field_sources']['category'] == 'keyword', rocket

quant = infer_metadata(
    '# Quant Methods\n\nA quant studies risk models.\n',
    'quant_methods',
    [],
)
assert quant['category'] == 'quant', quant
assert quant['metadata_field_sources']['category'] == 'keyword', quant

finance = infer_metadata(
    '# Quantitative Finance\n\nMethods for quantitative finance and risk modelling.\n',
    'quantitative_finance',
    [],
)
assert finance['category'] == 'quant', finance
assert finance['metadata_field_sources']['category'] == 'keyword', finance

opt_book = infer_metadata(
    '# Convex Optimization\n\nA graduate text on optimization methods.\n',
    'convex_optimization',
    [],
)
assert opt_book['category'] == 'optimization', opt_book
assert opt_book['metadata_field_sources']['category'] == 'keyword', opt_book

# DOI / arXiv / journal → papers (after keywords miss); provenance doi_default.
doi_paper = infer_metadata(
    '# Global Instability of SWBLI\n\n'
    'DOI: 10.1063/5.0160648\n'
    'Quantitative agreement with DNS.\n',
    'song_like',
    [],
)
assert doi_paper['category'] == 'papers', doi_paper
assert doi_paper['doi'] == '10.1063/5.0160648', doi_paper
assert doi_paper['metadata_field_sources']['category'] == 'doi_default', doi_paper

arxiv_paper = infer_metadata(
    '# Preprint Title\n\narXiv:2401.01234\nSome abstract without domain keywords.\n',
    'preprint_title',
    [],
)
assert arxiv_paper['category'] == 'papers', arxiv_paper
assert arxiv_paper['metadata_field_sources']['category'] == 'doi_default', arxiv_paper

journal_paper = infer_metadata(
    '# Container Only\n\nNo topical keywords here.\n',
    'container_only',
    [],
    resolved_metadata={'journal': 'Physics of Fluids'},
)
assert journal_paper['category'] == 'papers', journal_paper
assert journal_paper['metadata_field_sources']['category'] == 'doi_default', journal_paper

# No DOI / no keyword → misc.
bare = infer_metadata('# Untitled Notes\n\nNo identifiers and no domain terms.\n', 'untitled_notes', [])
assert bare['category'] == 'misc', bare
assert bare['metadata_field_sources']['category'] == 'heuristic', bare

# Keyword beats a bare DOI (propulsion keyword wins over article signal).
keyword_beats_doi = infer_metadata(
    '# Solid Rocket Motors\n\n'
    'DOI: 10.1234/example\n'
    'Rocket propulsion design.\n',
    'solid_rocket_motors',
    [],
)
assert keyword_beats_doi['category'] == 'propulsion', keyword_beats_doi
assert keyword_beats_doi['metadata_field_sources']['category'] == 'keyword', keyword_beats_doi
assert keyword_beats_doi['doi'] == '10.1234/example', keyword_beats_doi

# Fluids abstract with optimization language + DOI → papers (not optimization, not quant).
fluids_doi = infer_metadata(
    '# Oblique Shock Interaction\n\n'
    'DOI: 10.1063/5.0160648\n'
    'Quantitative agreement; quantify the growth; an optimization problem for the grid.\n',
    'oblique_shock',
    [],
)
assert fluids_doi['category'] == 'papers', fluids_doi
assert fluids_doi['metadata_field_sources']['category'] == 'doi_default', fluids_doi

merged = merge_metadata(
    {'title': 'Manual Title', 'author': 'Manual Author', 'year': '1900', 'category': 'misc'},
    'work',
    [],
    pdf_meta_path=work / 'pdf_meta.json',
)
assert merged['title'] == 'PDF Title', merged
assert merged['author'] == 'PDF Author', merged
assert merged['year'] == '2022', merged
assert merged['isbn'] == '9781491957660', merged
assert merged['category'] == 'misc', merged
assert merged['metadata_field_sources']['title'] == 'pdf', merged
assert merged['metadata_field_sources']['category'] == 'manual', merged
PY

PYTHONPATH="$ROOT/pipeline${PYTHONPATH:+:$PYTHONPATH}" python3 - <<'PY'
from alexandria_metadata_common import apply_metadata_precedence, infer_metadata

mixed = apply_metadata_precedence(
    resolved={'journal': 'Resolved Journal'},
    pdf={'title': 'PDF Title'},
    epub={'title': 'EPUB Title', 'authors': ['EPUB Author'], 'year': '2018', 'publisher': 'EPUB Press'},
    markdown={'title': 'Markdown Title', 'author': 'Markdown Author', 'year': '1999', 'category': 'papers'},
    heuristic={'category': 'misc'},
)
assert mixed['title'] == 'PDF Title', mixed
assert mixed['author'] == 'EPUB Author', mixed
assert mixed['year'] == '2018', mixed
assert mixed['category'] == 'papers', mixed
assert mixed['journal'] == 'Resolved Journal', mixed
assert mixed['metadata_field_sources']['title'] == 'pdf', mixed
assert mixed['metadata_field_sources']['author'] == 'epub', mixed
assert mixed['metadata_field_sources']['year'] == 'epub', mixed
assert mixed['metadata_field_sources']['category'] == 'markdown', mixed

opf = infer_metadata(
    '# Heuristic Heading\n\nAuthor: Markdown Author\nYear: 1999\nCategory: papers\n',
    'work',
    [{'name': 'work.epub', 'format': 'epub', 'size': 1, 'sha256': 'abc'}],
    epub_meta={'title': 'OPF Title', 'authors': ['OPF Author'], 'year': '2020'},
)
assert opf['title'] == 'OPF Title', opf
assert opf['author'] == 'OPF Author', opf
assert opf['metadata_field_sources']['title'] == 'epub', opf
assert opf['metadata_source'] == 'epub', opf
assert opf['metadata_confidence'] == 'medium', opf

identified = infer_metadata(
    '# Heading\n', 'work', [],
    epub_meta={'title': 'OPF Title', 'authors': ['OPF Author'], 'year': '2020', 'isbn': '9781234567897'},
)
assert identified['metadata_source'] == 'isbn', identified
assert identified['metadata_confidence'] == 'high', identified
PY

# ---------------------------------------------------------------------------
# Year evidence precedence + plausibility gate (alexandria-qcn)
# ---------------------------------------------------------------------------
PYTHONPATH="$ROOT/pipeline${PYTHONPATH:+:$PYTHONPATH}" python3 - <<'PY'
from datetime import datetime

from alexandria_metadata_common import (
    infer_metadata,
    is_plausible_publication_year,
    year_from_doi,
    year_from_dates,
)

NOW = datetime.now().year
# Future / implausible fixture — always beyond the plausible upper bound (now+1).
FUTURE = str(NOW + 3)

# DOI path-segment extraction: Elsevier-style volume year, not registrant.
assert year_from_doi("10.1016/j.ijheatmasstransfer.2026.129100") == "2026"
assert year_from_doi("10.1111/markdown") == ""
assert year_from_doi("") == ""
# arXiv YYMM is a submission id, never a publication year.
assert year_from_doi("10.48550/arXiv.2012.12345") == ""
assert year_from_doi("10.48550/arXiv.1901.01234") == ""
assert year_from_doi("10.48550/arxiv.2301.00001") == ""
# Multiple year-like tokens → ambiguous, skip.
assert year_from_doi("10.1234/foo.1999.2024.bar") == ""
assert year_from_doi("10.1000/journal.2010.2015.99") == ""

# Dates map → year (preferred keys, plausible only).
assert year_from_dates({"created": "2026-06-04T19:30:30+00:00"}) == "2026"
assert year_from_dates({"published": "2022-05-01", "created": "1999-01-01"}) == "2022"
assert year_from_dates({}) == ""

# Plausibility bounds: [1450, now.year+1].
assert is_plausible_publication_year("2026")
assert is_plausible_publication_year("1450")
assert not is_plausible_publication_year("1449")
assert not is_plausible_publication_year(FUTURE)  # future table-value / typo
assert not is_plausible_publication_year("nodate")
assert not is_plausible_publication_year("")

# DOI-embedded year beats heuristic bare-token (table value FUTURE J/kg/K).
table_text = (
    "# Fuzzy modelling for transpiration cooling\n\n"
    "Author: Shuaiyang Wang\n"
    "DOI: 10.1016/j.ijheatmasstransfer.2026.129100\n\n"
    f"The specific heat capacity is {FUTURE} J/kg/K in the coolant table.\n"
)
table = infer_metadata(table_text, "wang_like", [])
assert table["year"] == "2026", table
assert table["metadata_field_sources"]["year"] == "doi", table["metadata_field_sources"]
assert table["metadata_confidence"] == "high", table
assert table["doi"] == "10.1016/j.ijheatmasstransfer.2026.129100", table

# PDF dates alone (no discrete year field, no DOI year) → pdf-tier year.
dates_only = infer_metadata(
    "# Some Paper\n\nAuthor: Alice Example\n\nNo explicit year line here.\n",
    "dates_only",
    [],
    pdf_meta={
        "title": "Some Paper",
        "authors": ["Alice Example"],
        "dates": {"created": "2021-03-15T00:00:00+00:00", "modified": "2021-03-16T00:00:00+00:00"},
    },
)
assert dates_only["year"] == "2021", dates_only
assert dates_only["metadata_field_sources"]["year"] == "pdf", dates_only["metadata_field_sources"]

# When DOI year and pdf dates differ by more than 1 year, prefer dates.
dates_over_doi = infer_metadata(
    "# Paper\n\nAuthor: Bob Example\n",
    "dates_over_doi",
    [],
    pdf_meta={
        "title": "Paper",
        "authors": ["Bob Example"],
        "doi": "10.1016/j.example.2024.999",
        "dates": {"created": "2020-01-01T00:00:00+00:00"},
    },
)
assert dates_over_doi["year"] == "2020", dates_over_doi
assert dates_over_doi["metadata_field_sources"]["year"] == "pdf", dates_over_doi["metadata_field_sources"]

# DOI year still wins when within 1 year of dates (no large conflict).
doi_near_dates = infer_metadata(
    "# Paper\n\nAuthor: Bob Example\n",
    "doi_near_dates",
    [],
    pdf_meta={
        "title": "Paper",
        "authors": ["Bob Example"],
        "doi": "10.1016/j.example.2021.999",
        "dates": {"created": "2020-01-01T00:00:00+00:00"},
    },
)
assert doi_near_dates["year"] == "2021", doi_near_dates
assert doi_near_dates["metadata_field_sources"]["year"] == "doi", doi_near_dates["metadata_field_sources"]

# arXiv preprint with pdf dates → dates year (never YYMM from the DOI).
arxiv_preprint = infer_metadata(
    "# Preprint\n\nAuthor: Ada Example\narXiv:2012.12345\n",
    "arxiv_preprint",
    [],
    pdf_meta={
        "title": "Preprint",
        "authors": ["Ada Example"],
        "doi": "10.48550/arXiv.2012.12345",
        "dates": {"created": "2020-12-01T00:00:00+00:00"},
    },
)
assert arxiv_preprint["year"] == "2020", arxiv_preprint
assert arxiv_preprint["metadata_field_sources"]["year"] == "pdf", arxiv_preprint["metadata_field_sources"]

# Implausible bare-token only → nodate, low confidence (hold), never files future year.
future_only = infer_metadata(
    f"# Notes\n\nAuthor: Carol Example\n\nCapacity is {FUTURE} J/kg/K.\n",
    "future_only",
    [],
)
assert future_only["year"] == "nodate", future_only
assert future_only["metadata_confidence"] == "low", future_only
assert future_only["metadata_field_sources"].get("year") == "heuristic", future_only
assert future_only["year"] != FUTURE, future_only

# Plausible bare-token with no stronger evidence remains acceptable.
bare_ok = infer_metadata(
    "# Archive Notes\n\nAuthor: Dana Example\n\nWritten around 2018 for the survey.\n",
    "bare_ok",
    [],
)
assert bare_ok["year"] == "2018", bare_ok
assert bare_ok["metadata_field_sources"]["year"] == "heuristic", bare_ok

# Explicit markdown year-context patterns still win as heuristic (copyright/published).
copyright = infer_metadata(
    "# Book Title\n\nAuthor: Eve Example\n\nCopyright 2015 All rights reserved.\n"
    f"Some later table says {FUTURE} units.\n",
    "copyright_book",
    [],
)
assert copyright["year"] == "2015", copyright
assert copyright["metadata_field_sources"]["year"] == "heuristic", copyright

published = infer_metadata(
    "# Book Title\n\nFirst edition published 2017. Later notes mention 2020 updates.\n",
    "published_book",
    [],
)
assert published["year"] == "2017", published

# Resolved year still outranks DOI-embedded year.
resolved_year = infer_metadata(
    "# Paper\n\nDOI: 10.1016/j.example.2024.999\n",
    "resolved_year",
    [],
    resolved_metadata={"title": "Resolved", "year": "2019", "doi": "10.1016/j.example.2024.999"},
)
assert resolved_year["year"] == "2019", resolved_year
assert resolved_year["metadata_field_sources"]["year"] == "resolved", resolved_year["metadata_field_sources"]

# Explicit markdown Year: field beats heuristic; DOI still outranks markdown.
md_year = infer_metadata(
    "# Paper\n\nYear: 2010\nAuthor: Frank Example\n",
    "md_year",
    [],
)
assert md_year["year"] == "2010", md_year
assert md_year["metadata_field_sources"]["year"] == "markdown", md_year["metadata_field_sources"]

md_doi = infer_metadata(
    "# Paper\n\nYear: 2010\nDOI: 10.1016/j.example.2023.1\nAuthor: Frank Example\n",
    "md_doi",
    [],
)
assert md_doi["year"] == "2023", md_doi
assert md_doi["metadata_field_sources"]["year"] == "doi", md_doi["metadata_field_sources"]
print("OK year precedence + plausibility cases")
PY
