#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
mkdir -p "$ROOT/pipeline/logs"
TMP="$(mktemp -d "$ROOT/pipeline/logs/test-resolvers.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
FIXTURES="$ROOT/pipeline/tests/fixtures/resolvers"

PYTHONPATH="$ROOT/pipeline${PYTHONPATH:+:$PYTHONPATH}" FIXTURES="$FIXTURES" TMP="$TMP" python3 - <<'PY'
import os
from pathlib import Path

from alexandria_metadata_common import apply_metadata_precedence
from alexandria_resolvers import (
    STATUS_FOUND,
    STATUS_NOT_FOUND,
    FetchResponse,
    resolve_arxiv,
    resolve_doi,
    resolve_identifiers,
    resolve_isbn,
)

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


class FailFetcher:
    def __call__(self, url, *, headers=None, timeout=None):
        raise AssertionError(f"cache miss attempted network URL: {url}")


# DOI happy path via Crossref, including normalized shape and raw-payload cache replay.
doi_cache = tmp / "cache-doi"
doi_fetcher = FixtureFetcher([
    ("api.crossref.org/works/10.5555%2Fdemo", (200, "crossref-doi-found.payload")),
])
doi_result = resolve_doi("10.5555/DEMO", fetch=doi_fetcher, cache_dir=doi_cache)
assert doi_result["status"] == STATUS_FOUND, doi_result
assert len(doi_fetcher.calls) == 1, doi_fetcher.calls
doi_meta = doi_result["metadata"]
assert doi_meta["title"] == "Crossref Fixture Title", doi_meta
assert doi_meta["authors"] == ["Ada Lovelace", "Charles Babbage"], doi_meta
assert doi_meta["year"] == "1843", doi_meta
assert doi_meta["doi"] == "10.5555/demo", doi_meta
assert doi_meta["isbn"] == "9781491957660", doi_meta
assert doi_meta["publisher"] == "Fixture Press", doi_meta
assert doi_meta["journal"] == "Journal of Fixtures", doi_meta
assert doi_meta["pages"] == "1-9", doi_meta
assert doi_meta["resolver_source"] == "crossref", doi_meta
assert doi_meta["metadata_provenance"]["title"] == "crossref:message.title", doi_meta
cache_payloads = list(doi_cache.glob("**/*.payload"))
assert cache_payloads, "expected a cached raw DOI payload"
assert any(path.read_bytes() == fixture_body("crossref-doi-found.payload") for path in cache_payloads)
replayed = resolve_doi("10.5555/demo", fetch=FailFetcher(), cache_dir=doi_cache)
assert replayed["status"] == STATUS_FOUND, replayed
assert replayed["metadata"]["title"] == "Crossref Fixture Title", replayed
assert replayed["attempts"][0]["cache_hit"] is True, replayed

# Preferred order: DOI wins over arXiv/ISBN and lower resolvers are not called.
preferred_fetcher = FixtureFetcher([
    ("api.crossref.org/works/10.5555%2Fdemo", (200, "crossref-doi-found.payload")),
])
preferred = resolve_identifiers(
    doi="10.5555/demo",
    arxiv_id="2401.01234",
    isbn="978-1-491-95766-0",
    fetch=preferred_fetcher,
    cache_dir=tmp / "cache-preferred",
)
assert preferred["resolver_source"] == "crossref", preferred
assert not any("export.arxiv.org" in call["url"] or "openlibrary.org" in call["url"] for call in preferred_fetcher.calls), preferred_fetcher.calls

# DOI fallback via DOI content negotiation when Crossref is unavailable.
fallback_fetcher = FixtureFetcher([
    ("api.crossref.org/works/10.7777%2Ffallback", (503, "not-found.payload")),
    ("doi.org/10.7777/fallback", (200, "doi-csl-found.payload")),
])
fallback = resolve_doi("10.7777/fallback", fetch=fallback_fetcher, cache_dir=tmp / "cache-fallback")
assert fallback["status"] == STATUS_FOUND, fallback
assert [call["headers"].get("Accept") for call in fallback_fetcher.calls] == [
    "application/json",
    "application/vnd.citationstyles.csl+json",
], fallback_fetcher.calls
fallback_meta = fallback["metadata"]
assert fallback_meta["resolver_source"] == "doi-content-negotiation", fallback_meta
assert fallback_meta["title"] == "CSL Fallback Title", fallback_meta
assert fallback_meta["authors"] == ["Grace Hopper"], fallback_meta
assert fallback_meta["year"] == "1952", fallback_meta
assert fallback_meta["doi"] == "10.7777/fallback", fallback_meta
assert fallback_meta["journal"] == "Fallback Journal", fallback_meta

# arXiv happy path via recorded Atom fixture.
arxiv_fetcher = FixtureFetcher([
    ("export.arxiv.org/api/query", (200, "arxiv-found.xml")),
])
arxiv = resolve_arxiv("arXiv:2401.01234", fetch=arxiv_fetcher, cache_dir=tmp / "cache-arxiv")
assert arxiv["status"] == STATUS_FOUND, arxiv
arxiv_meta = arxiv["metadata"]
assert arxiv_meta["resolver_source"] == "arxiv", arxiv_meta
assert arxiv_meta["title"] == "ArXiv Fixture Title", arxiv_meta
assert arxiv_meta["authors"] == ["Jane Doe", "John Smith"], arxiv_meta
assert arxiv_meta["year"] == "2024", arxiv_meta
assert arxiv_meta["arxiv_id"] == "2401.01234v2", arxiv_meta
assert arxiv_meta["doi"] == "10.48550/arxiv.2401.01234", arxiv_meta
assert arxiv_meta["journal"] == "Fixture Letters 1, 2 (2024)", arxiv_meta

# ISBN happy path via recorded Open Library fixture.
isbn_fetcher = FixtureFetcher([
    ("openlibrary.org/api/books", (200, "openlibrary-isbn-found.payload")),
])
isbn = resolve_isbn("978-1-491-95766-0", fetch=isbn_fetcher, cache_dir=tmp / "cache-isbn")
assert isbn["status"] == STATUS_FOUND, isbn
isbn_meta = isbn["metadata"]
assert isbn_meta["resolver_source"] == "openlibrary", isbn_meta
assert isbn_meta["title"] == "Open Library Fixture Book", isbn_meta
assert isbn_meta["authors"] == ["Luciano Ramalho"], isbn_meta
assert isbn_meta["year"] == "2015", isbn_meta
assert isbn_meta["isbn"] == "9781491957660", isbn_meta
assert isbn_meta["publisher"] == "O'Reilly Media", isbn_meta
assert isbn_meta["pages"] == 792, isbn_meta

# 404s are not_found, distinct from unavailable.
not_found_fetcher = FixtureFetcher([
    ("api.crossref.org/works/10.404%2Fmissing", (404, "not-found.payload")),
    ("doi.org/10.404/missing", (404, "not-found.payload")),
])
missing = resolve_doi("10.404/missing", fetch=not_found_fetcher, cache_dir=tmp / "cache-404")
assert missing["status"] == STATUS_NOT_FOUND, missing
assert [attempt["status"] for attempt in missing["attempts"]] == [STATUS_NOT_FOUND, STATUS_NOT_FOUND], missing

# Timeout/network unavailability degrades to an empty resolved tier, preserving local metadata.
timeout_message = (fixtures / "timeout-unavailable.fixture").read_text(encoding="utf-8").strip()
timeout_fetcher = FixtureFetcher([
    ("api.crossref.org/works/10.5555%2Ftimeout", TimeoutError(timeout_message)),
    ("doi.org/10.5555/timeout", TimeoutError(timeout_message)),
])
resolved = resolve_identifiers(doi="10.5555/timeout", fetch=timeout_fetcher, cache_dir=tmp / "cache-timeout")
assert resolved == {}, resolved
local = apply_metadata_precedence(
    resolved=resolved,
    markdown={
        "title": "Local Metadata Title",
        "authors": ["Local Author"],
        "year": "1999",
        "doi": "10.1111/local",
        "category": "papers",
    },
)
assert local["title"] == "Local Metadata Title", local
assert local["author"] == "Local Author", local
assert local["year"] == "1999", local
assert local["doi"] == "10.1111/local", local
for field in ("title", "author", "authors", "year", "doi"):
    assert local["metadata_field_sources"][field] == "markdown", (field, local)

# A partial resolver payload only fills fields it actually has; local fields are not downgraded.
partial_fetcher = FixtureFetcher([
    ("api.crossref.org/works/10.5555%2Fpartial", (200, "crossref-doi-partial.payload")),
])
partial = resolve_identifiers(doi="10.5555/partial", fetch=partial_fetcher, cache_dir=tmp / "cache-partial")
assert partial["title"] == "Partial Resolver Title", partial
assert partial["doi"] == "10.5555/partial", partial
assert "authors" not in partial, partial
assert "year" not in partial, partial
merged = apply_metadata_precedence(
    resolved=partial,
    markdown={
        "title": "Local Title",
        "authors": ["Local Author"],
        "year": "1999",
        "isbn": "978-1-491-95766-0",
        "category": "papers",
    },
)
assert merged["title"] == "Partial Resolver Title", merged
assert merged["doi"] == "10.5555/partial", merged
assert merged["author"] == "Local Author", merged
assert merged["authors"] == ["Local Author"], merged
assert merged["year"] == "1999", merged
assert merged["isbn"] == "9781491957660", merged
assert merged["metadata_field_sources"]["title"] == "resolved", merged
assert merged["metadata_field_sources"]["doi"] == "resolved", merged
for field in ("author", "authors", "year", "isbn"):
    assert merged["metadata_field_sources"][field] == "markdown", (field, merged)
PY
