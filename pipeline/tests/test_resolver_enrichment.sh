#!/usr/bin/env bash
# Offline tests for opt-in DOI resolver enrichment (alexandria-fvb).
# All network I/O goes through an injectable fixture fetcher — zero real network.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
mkdir -p "$ROOT/pipeline/logs"
TMP="$(mktemp -d "$ROOT/pipeline/logs/test-resolver-enrichment.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
FIXTURES="$ROOT/pipeline/tests/fixtures/resolvers"

# --- (a) flag OFF on the filing path: no enrichment, metadata byte-identical ---
WORK_OFF="$TMP/parsed/doi_paper_off"
mkdir -p "$WORK_OFF"
cat > "$WORK_OFF/doi_paper_off.md" <<'MD'
# Local Paper Title

Author: Local Author
Year: 1999
Category: papers

DOI: 10.5555/demo
MD
# Explicitly unset so a parent env cannot leak into the suite.
env -u ALEXANDRIA_RESOLVE_ENRICH \
  ALEXANDRIA_PARSED_WORK="$WORK_OFF" \
  PYTHONPATH="$ROOT/pipeline${PYTHONPATH:+:$PYTHONPATH}" \
  "$ROOT/pipeline/alexandria_metadata_from_markdown.py"
python3 - <<PY
import json
from pathlib import Path
m = json.loads(Path("$WORK_OFF/metadata.json").read_text(encoding="utf-8"))
assert m["title"] == "Local Paper Title", m
assert m["author"] == "Local Author", m
assert m["year"] == "1999", m
assert m["doi"] == "10.5555/demo", m
assert not m.get("journal"), m
assert m.get("publisher", "") in ("", None) or not str(m.get("publisher", "")).strip(), m
baseline = Path("$TMP/baseline_off.json")
baseline.write_text(json.dumps(m, indent=2, sort_keys=True) + "\n", encoding="utf-8")
# Re-run with flag unset again — must be byte-identical.
PY
env -u ALEXANDRIA_RESOLVE_ENRICH \
  ALEXANDRIA_PARSED_WORK="$WORK_OFF" \
  PYTHONPATH="$ROOT/pipeline${PYTHONPATH:+:$PYTHONPATH}" \
  "$ROOT/pipeline/alexandria_metadata_from_markdown.py"
cmp -s "$WORK_OFF/metadata.json" "$TMP/baseline_off.json"
echo "PASS (a) flag-off filing path: byte-identical, no journal"

PYTHONPATH="$ROOT/pipeline${PYTHONPATH:+:$PYTHONPATH}" FIXTURES="$FIXTURES" TMP="$TMP" python3 - <<'PY'
import json
import os
from pathlib import Path

from alexandria_bibtex import bibtex_entry
from alexandria_metadata_common import (
    ENRICH_ALLOWLIST,
    IDENTITY_FIELDS,
    enrich_metadata_from_doi,
    resolve_enrichment_enabled,
)
from alexandria_resolvers import FetchResponse, STATUS_FOUND

fixtures = Path(os.environ["FIXTURES"])
tmp = Path(os.environ["TMP"])


def fixture_body(name: str) -> bytes:
    return (fixtures / name).read_bytes()


class FixtureFetcher:
    def __init__(self, routes):
        self.routes = list(routes)
        self.calls = []

    def __call__(self, url, *, headers=None, timeout=None):
        self.calls.append({"url": url, "headers": headers or {}, "timeout": timeout})
        for needle, response in self.routes:
            if needle in url:
                if isinstance(response, BaseException):
                    raise response
                status, fixture_name = response
                return FetchResponse(status=status, body=fixture_body(fixture_name), headers={})
        raise AssertionError(f"unexpected network URL: {url}")


class BoomFetcher:
    def __init__(self):
        self.calls = []

    def __call__(self, url, *, headers=None, timeout=None):
        self.calls.append(url)
        raise AssertionError(f"fetch must not be called: {url}")


class RaisingFetcher:
    def __init__(self, exc: BaseException):
        self.exc = exc
        self.calls = []

    def __call__(self, url, *, headers=None, timeout=None):
        self.calls.append(url)
        raise self.exc


# --- unit: flag semantics ---
assert resolve_enrichment_enabled({}) is False
assert resolve_enrichment_enabled({"ALEXANDRIA_RESOLVE_ENRICH": ""}) is False
assert resolve_enrichment_enabled({"ALEXANDRIA_RESOLVE_ENRICH": "0"}) is False
assert resolve_enrichment_enabled({"ALEXANDRIA_RESOLVE_ENRICH": "1"}) is True
assert resolve_enrichment_enabled({"ALEXANDRIA_RESOLVE_ENRICH": "true"}) is True
assert resolve_enrichment_enabled({"ALEXANDRIA_RESOLVE_ENRICH": "YES"}) is True

# --- (a) flag off → resolver never called, metadata byte-identical ---
base = {
    "title": "Local Paper Title",
    "author": "Local Author",
    "authors": ["Local Author"],
    "year": "1999",
    "doi": "10.5555/demo",
    "isbn": "",
    "arxiv_id": "",
    "category": "papers",
    "publisher": "Local Publisher",
    "journal": "",
    "pages": "",
    "metadata_field_sources": {
        "title": "markdown",
        "author": "markdown",
        "authors": "markdown",
        "year": "markdown",
        "doi": "markdown",
        "category": "markdown",
        "publisher": "pdf",
    },
}
boom = BoomFetcher()
before = json.dumps(base, sort_keys=True)
out = enrich_metadata_from_doi(dict(base), fetch=boom, enabled=False)
assert boom.calls == [], boom.calls
assert json.dumps(out, sort_keys=True) == before
# env-driven default off
os.environ.pop("ALEXANDRIA_RESOLVE_ENRICH", None)
boom2 = BoomFetcher()
out2 = enrich_metadata_from_doi(dict(base), fetch=boom2)
assert boom2.calls == [], boom2.calls
assert out2.get("journal") in ("", None) or not str(out2.get("journal", "")).strip()
print("PASS (a) flag-off helper: fetch never called, metadata unchanged")

# --- (b) flag on + DOI fixture → journal filled; key/identity/existing unchanged ---
fetcher = FixtureFetcher([
    ("api.crossref.org/works/10.5555%2Fdemo", (200, "crossref-doi-found.payload")),
])
meta_b = dict(base)
meta_b["metadata_field_sources"] = dict(base["metadata_field_sources"])
out_b = enrich_metadata_from_doi(
    meta_b,
    fetch=fetcher,
    enabled=True,
    cache_dir=tmp / "cache-enrich-b",
)
assert len(fetcher.calls) >= 1, fetcher.calls
assert out_b["journal"] == "Journal of Fixtures", out_b
assert out_b["pages"] == "1-9", out_b
# existing non-empty publisher must NOT be overwritten by Fixture Press
assert out_b["publisher"] == "Local Publisher", out_b
# identity fields unchanged
for field in IDENTITY_FIELDS:
    assert out_b.get(field) == base.get(field), (field, out_b.get(field), base.get(field))
assert out_b["metadata_field_sources"]["journal"] == "resolved", out_b
assert out_b["metadata_field_sources"]["publisher"] == "pdf", out_b  # not rewritten
# BibTeX gains journal={...}
bib = bibtex_entry("local1999", out_b)
assert "journal = {Journal of Fixtures}" in bib, bib
assert "title = {Local Paper Title}" in bib, bib
assert "publisher = {Local Publisher}" in bib, bib
print("PASS (b) flag-on DOI fixture: journal filled, identity + existing preserved")

# Allowlist-only: resolver title/authors/year must not land even when local identity empty
sparse = {
    "title": "",
    "author": "",
    "authors": [],
    "year": "",
    "doi": "10.5555/demo",
    "isbn": "",
    "arxiv_id": "",
    "category": "",
    "journal": "",
    "publisher": "",
    "pages": "",
    "metadata_field_sources": {},
}
fetcher_sparse = FixtureFetcher([
    ("api.crossref.org/works/10.5555%2Fdemo", (200, "crossref-doi-found.payload")),
])
out_s = enrich_metadata_from_doi(
    sparse,
    fetch=fetcher_sparse,
    enabled=True,
    cache_dir=tmp / "cache-enrich-sparse",
)
assert out_s["journal"] == "Journal of Fixtures", out_s
assert out_s["publisher"] == "Fixture Press", out_s
assert out_s["pages"] == "1-9", out_s
assert out_s["title"] == "", out_s
assert out_s["author"] == "", out_s
assert out_s["authors"] == [], out_s
assert out_s["year"] == "", out_s
assert out_s["doi"] == "10.5555/demo", out_s
assert out_s["category"] == "", out_s
assert out_s["isbn"] == "", out_s
assert out_s["arxiv_id"] == "", out_s
for field in ENRICH_ALLOWLIST:
    assert field not in IDENTITY_FIELDS
print("PASS (b2) identity fields never filled even when empty")

# --- (c) flag on + resolver raises → un-enriched metadata, no exception ---
raising = RaisingFetcher(TimeoutError("simulated network timeout"))
meta_c = dict(base)
meta_c["metadata_field_sources"] = dict(base["metadata_field_sources"])
out_c = enrich_metadata_from_doi(
    meta_c,
    fetch=raising,
    enabled=True,
    cache_dir=tmp / "cache-enrich-c",
)
assert raising.calls, "expected resolve attempt before failure"
assert out_c["journal"] in ("", None) or not str(out_c.get("journal", "")).strip(), out_c
assert out_c["publisher"] == "Local Publisher", out_c
for field in IDENTITY_FIELDS:
    assert out_c.get(field) == base.get(field), (field, out_c.get(field))
print("PASS (c) resolver raises: metadata un-enriched, no exception")

# No DOI → no fetch even when enabled
no_doi = {"title": "X", "doi": "", "journal": ""}
boom3 = BoomFetcher()
out_nd = enrich_metadata_from_doi(no_doi, fetch=boom3, enabled=True)
assert boom3.calls == [], boom3.calls
assert out_nd == no_doi
print("PASS (c2) no DOI: fetch never called")

# Status not_found is non-fatal and leaves metadata alone
nf_fetcher = FixtureFetcher([
    ("api.crossref.org/works/10.404%2Fmissing", (404, "not-found.payload")),
    ("doi.org/10.404/missing", (404, "not-found.payload")),
])
meta_nf = {
    "title": "Missing DOI Paper",
    "author": "Someone",
    "authors": ["Someone"],
    "year": "2001",
    "doi": "10.404/missing",
    "journal": "",
    "publisher": "",
    "pages": "",
    "category": "papers",
}
out_nf = enrich_metadata_from_doi(
    meta_nf,
    fetch=nf_fetcher,
    enabled=True,
    cache_dir=tmp / "cache-enrich-nf",
)
assert not out_nf.get("journal"), out_nf
assert out_nf["title"] == "Missing DOI Paper", out_nf
print("PASS (c3) not_found: un-enriched, non-fatal")
PY

# --- (c) filing path with flag ON but injected failure via helper contract already
# covered; also confirm flag-ON main path still exits 0 when DOI empty (no network).
WORK_EMPTY="$TMP/parsed/no_doi"
mkdir -p "$WORK_EMPTY"
cat > "$WORK_EMPTY/no_doi.md" <<'MD'
# No DOI Paper

Author: Nobody
Year: 2000
Category: misc
MD
ALEXANDRIA_RESOLVE_ENRICH=1 \
  ALEXANDRIA_PARSED_WORK="$WORK_EMPTY" \
  PYTHONPATH="$ROOT/pipeline${PYTHONPATH:+:$PYTHONPATH}" \
  "$ROOT/pipeline/alexandria_metadata_from_markdown.py"
python3 - <<PY
import json
from pathlib import Path
m = json.loads(Path("$WORK_EMPTY/metadata.json").read_text(encoding="utf-8"))
assert m["title"] == "No DOI Paper", m
assert m["doi"] == "", m
assert not m.get("journal"), m
print("PASS (c4) flag-on filing path with no DOI still files")
PY

echo "ALL RESOLVER ENRICHMENT TESTS PASSED"
