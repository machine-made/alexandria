#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Safe, fixture-testable bibliographic metadata resolvers.

Resolvers are deliberately opt-in: importing this module or calling its helpers does not
change the filing path.  All live I/O goes through the injectable ``fetch`` callable;
tests can pass a fixture fetcher and remain fully offline.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from alexandria_metadata_common import normalize_doi, normalize_isbn

STATUS_FOUND = "found"
STATUS_NOT_FOUND = "not_found"
STATUS_UNAVAILABLE = "unavailable"
DEFAULT_TIMEOUT_SECONDS = 10.0
USER_AGENT = "AlexandriaMetadataResolver/1.0 (safe opt-in metadata enrichment; no scraping)"
_METADATA_FIELDS = {"title", "authors", "year", "doi", "isbn", "arxiv_id", "publisher", "journal", "pages"}
_ARXIV_ID_RE = re.compile(r"(?:[A-Za-z.-]+/\d{7}|\d{4}\.\d{4,5}(?:v\d+)?)")


@dataclass(frozen=True)
class FetchResponse:
    """Small response shape accepted from injected fetchers."""

    status: int
    body: bytes | str
    headers: dict[str, str] | None = None
    url: str | None = None


FetchCallable = Callable[..., Any]


def default_cache_dir() -> Path:
    """Return the gitignored scratch cache directory for resolver payloads."""

    configured = os.environ.get("ALEXANDRIA_RESOLVER_CACHE")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parent / "logs" / "resolver_cache"


def default_fetch(url: str, *, headers: dict[str, str] | None = None, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> FetchResponse:
    """Fetch *url* with urllib.

    This is the only built-in live network boundary.  HTTP errors are returned as
    responses so callers can distinguish 404 from 5xx; transport failures are
    allowed to raise and are classified by the resolver wrapper.
    """

    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - opt-in resolver boundary
            return FetchResponse(
                status=int(getattr(response, "status", response.getcode())),
                body=response.read(),
                headers=dict(response.headers.items()),
                url=response.geturl(),
            )
    except urllib.error.HTTPError as exc:
        return FetchResponse(
            status=int(exc.code),
            body=exc.read() if exc.fp is not None else b"",
            headers=dict(exc.headers.items()) if exc.headers else {},
            url=url,
        )


def normalize_arxiv_id(raw: str) -> str:
    """Normalize a stable arXiv identifier without guessing one."""

    value = str(raw or "").strip()
    value = re.sub(r"^arxiv:\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^https?://arxiv\.org/(?:abs|pdf)/", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\.pdf$", "", value, flags=re.IGNORECASE)
    match = _ARXIV_ID_RE.search(value)
    return match.group(0) if match else ""


def resolve_doi(
    doi: str,
    *,
    fetch: FetchCallable = default_fetch,
    cache_dir: Path | str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Resolve DOI metadata via Crossref, falling back to DOI content negotiation."""

    normalized = normalize_doi(str(doi or ""))
    if not normalized:
        return _status_result(STATUS_NOT_FOUND, "doi", "")

    attempts: list[dict[str, Any]] = []
    providers = (
        (
            "crossref",
            _crossref_url(normalized),
            {
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
            _parse_crossref_payload,
        ),
        (
            "doi-content-negotiation",
            _doi_content_negotiation_url(normalized),
            {
                "Accept": "application/vnd.citationstyles.csl+json",
                "User-Agent": USER_AGENT,
            },
            _parse_csl_payload,
        ),
    )
    for resolver_source, url, headers, parser in providers:
        payload = _payload_for_identifier(
            identifier_type="doi",
            identifier=normalized,
            resolver_source=resolver_source,
            url=url,
            fetch=fetch,
            headers=headers,
            timeout=timeout,
            cache_dir=cache_dir,
        )
        attempts.append(_attempt_summary(resolver_source, payload))
        if payload["status"] != STATUS_FOUND:
            continue
        try:
            metadata = parser(payload["body"], resolver_source=resolver_source)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            attempts[-1]["status"] = STATUS_UNAVAILABLE
            attempts[-1]["error"] = f"{type(exc).__name__}: {exc}"
            continue
        if _has_bibliographic_fields(metadata):
            return _found_result("doi", normalized, resolver_source, metadata, attempts)
        attempts[-1]["status"] = STATUS_NOT_FOUND
        attempts[-1]["error"] = "payload contained no recognized bibliographic fields"
    return _combined_status_result("doi", normalized, attempts)


def resolve_arxiv(
    arxiv_id: str,
    *,
    fetch: FetchCallable = default_fetch,
    cache_dir: Path | str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Resolve arXiv metadata via the arXiv export API."""

    normalized = normalize_arxiv_id(arxiv_id)
    if not normalized:
        return _status_result(STATUS_NOT_FOUND, "arxiv_id", "")
    resolver_source = "arxiv"
    payload = _payload_for_identifier(
        identifier_type="arxiv_id",
        identifier=normalized,
        resolver_source=resolver_source,
        url=_arxiv_url(normalized),
        fetch=fetch,
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
        cache_dir=cache_dir,
    )
    attempts = [_attempt_summary(resolver_source, payload)]
    if payload["status"] != STATUS_FOUND:
        return _combined_status_result("arxiv_id", normalized, attempts)
    try:
        metadata = _parse_arxiv_payload(payload["body"], resolver_source=resolver_source)
    except (ET.ParseError, UnicodeDecodeError, ValueError) as exc:
        attempts[-1]["status"] = STATUS_UNAVAILABLE
        attempts[-1]["error"] = f"{type(exc).__name__}: {exc}"
        return _combined_status_result("arxiv_id", normalized, attempts)
    if not _has_bibliographic_fields(metadata):
        attempts[-1]["status"] = STATUS_NOT_FOUND
        attempts[-1]["error"] = "payload contained no entry"
        return _combined_status_result("arxiv_id", normalized, attempts)
    return _found_result("arxiv_id", normalized, resolver_source, metadata, attempts)


def resolve_isbn(
    isbn: str,
    *,
    fetch: FetchCallable = default_fetch,
    cache_dir: Path | str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Resolve ISBN metadata via Open Library."""

    normalized = normalize_isbn(str(isbn or ""))
    if not normalized:
        return _status_result(STATUS_NOT_FOUND, "isbn", "")
    resolver_source = "openlibrary"
    payload = _payload_for_identifier(
        identifier_type="isbn",
        identifier=normalized,
        resolver_source=resolver_source,
        url=_openlibrary_url(normalized),
        fetch=fetch,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        timeout=timeout,
        cache_dir=cache_dir,
    )
    attempts = [_attempt_summary(resolver_source, payload)]
    if payload["status"] != STATUS_FOUND:
        return _combined_status_result("isbn", normalized, attempts)
    try:
        metadata = _parse_openlibrary_payload(payload["body"], normalized, resolver_source=resolver_source)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        attempts[-1]["status"] = STATUS_UNAVAILABLE
        attempts[-1]["error"] = f"{type(exc).__name__}: {exc}"
        return _combined_status_result("isbn", normalized, attempts)
    if not _has_bibliographic_fields(metadata):
        attempts[-1]["status"] = STATUS_NOT_FOUND
        attempts[-1]["error"] = "payload contained no matching ISBN record"
        return _combined_status_result("isbn", normalized, attempts)
    return _found_result("isbn", normalized, resolver_source, metadata, attempts)


def resolve_identifiers(
    *,
    doi: str | None = None,
    arxiv_id: str | None = None,
    isbn: str | None = None,
    fetch: FetchCallable = default_fetch,
    cache_dir: Path | str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Return resolved metadata for DOI > arXiv > ISBN, or ``{}`` when absent/unavailable.

    ``not_found`` results fall through to the next stable identifier.  Any
    ``unavailable`` result returns an empty resolved tier so local metadata remains
    authoritative rather than being overwritten by partial or uncertain data.
    """

    resolvers: tuple[tuple[str | None, Callable[..., dict[str, Any]]], ...] = (
        (doi, resolve_doi),
        (arxiv_id, resolve_arxiv),
        (isbn, resolve_isbn),
    )
    for value, resolver in resolvers:
        if not value:
            continue
        result = resolver(value, fetch=fetch, cache_dir=cache_dir, timeout=timeout)
        if result.get("status") == STATUS_FOUND and isinstance(result.get("metadata"), dict):
            return dict(result["metadata"])
        if result.get("status") == STATUS_UNAVAILABLE:
            return {}
    return {}


def _crossref_url(doi: str) -> str:
    return "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")


def _doi_content_negotiation_url(doi: str) -> str:
    return "https://doi.org/" + urllib.parse.quote(doi, safe="/")


def _arxiv_url(arxiv_id: str) -> str:
    return "https://export.arxiv.org/api/query?id_list=" + urllib.parse.quote(arxiv_id, safe="")


def _openlibrary_url(isbn: str) -> str:
    bibkey = "ISBN:" + isbn
    return "https://openlibrary.org/api/books?" + urllib.parse.urlencode({"bibkeys": bibkey, "format": "json", "jscmd": "data"})


def _payload_for_identifier(
    *,
    identifier_type: str,
    identifier: str,
    resolver_source: str,
    url: str,
    fetch: FetchCallable,
    headers: dict[str, str],
    timeout: float,
    cache_dir: Path | str | None,
) -> dict[str, Any]:
    cache_path = _cache_path(cache_dir, identifier_type, identifier, resolver_source)
    if cache_path.is_file():
        return {"status": STATUS_FOUND, "body": cache_path.read_bytes(), "cache_hit": True, "cache_path": str(cache_path)}

    try:
        response = _coerce_fetch_response(fetch(url, headers=headers, timeout=timeout))
    except (TimeoutError, socket.timeout, urllib.error.URLError, ConnectionError, OSError) as exc:
        return {"status": STATUS_UNAVAILABLE, "error": f"{type(exc).__name__}: {exc}", "cache_hit": False}

    if response.status == 404:
        return {"status": STATUS_NOT_FOUND, "body": response.body, "cache_hit": False}
    if response.status >= 500:
        return {"status": STATUS_UNAVAILABLE, "body": response.body, "cache_hit": False, "error": f"HTTP {response.status}"}
    if response.status >= 400:
        return {"status": STATUS_UNAVAILABLE, "body": response.body, "cache_hit": False, "error": f"HTTP {response.status}"}

    body = _body_bytes(response.body)
    _write_cached_payload(cache_path, body)
    return {"status": STATUS_FOUND, "body": body, "cache_hit": False, "cache_path": str(cache_path)}


def _coerce_fetch_response(raw: Any) -> FetchResponse:
    if isinstance(raw, FetchResponse):
        return raw
    if isinstance(raw, bytes | str):
        return FetchResponse(status=200, body=raw)
    if isinstance(raw, tuple):
        if len(raw) == 2:
            status, body = raw
            return FetchResponse(status=int(status), body=body)
        if len(raw) == 3:
            status, body, headers = raw
            return FetchResponse(status=int(status), body=body, headers=dict(headers or {}))
    if isinstance(raw, dict):
        return FetchResponse(status=int(raw.get("status", 200)), body=raw.get("body", b""), headers=dict(raw.get("headers") or {}))
    status = getattr(raw, "status", getattr(raw, "status_code", None))
    body = getattr(raw, "body", getattr(raw, "content", None))
    if status is not None and body is not None:
        return FetchResponse(status=int(status), body=body, headers=dict(getattr(raw, "headers", {}) or {}))
    raise TypeError(f"fetch returned unsupported response shape: {type(raw).__name__}")


def _body_bytes(body: bytes | str) -> bytes:
    return body if isinstance(body, bytes) else str(body).encode("utf-8")


def _cache_path(cache_dir: Path | str | None, identifier_type: str, identifier: str, resolver_source: str) -> Path:
    root = Path(cache_dir) if cache_dir is not None else default_cache_dir()
    normalized_identifier = str(identifier).strip().lower()
    digest = hashlib.sha256(f"{identifier_type}:{normalized_identifier}".encode("utf-8")).hexdigest()[:16]
    safe_identifier = re.sub(r"[^A-Za-z0-9]+", "-", normalized_identifier).strip("-")[:48] or "identifier"
    safe_source = re.sub(r"[^A-Za-z0-9]+", "-", resolver_source).strip("-") or "resolver"
    return root / identifier_type / f"{safe_source}-{safe_identifier}-{digest}.payload"


def _write_cached_payload(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(body)
    tmp.replace(path)


def _decode_json(body: bytes | str) -> Any:
    return json.loads(_body_bytes(body).decode("utf-8"))


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip()
    return re.sub(r"\s+", " ", str(value)).strip()


def _first_text(value: Any) -> str:
    if isinstance(value, list | tuple):
        for item in value:
            text = _clean_text(item)
            if text:
                return text
        return ""
    return _clean_text(value)


def _metadata_shell(resolver_source: str) -> dict[str, Any]:
    return {"resolver_source": resolver_source, "metadata_provenance": {}}


def _set_text(metadata: dict[str, Any], key: str, value: Any, provenance: str) -> None:
    text = _first_text(value)
    if not text:
        return
    metadata[key] = text
    metadata["metadata_provenance"][key] = provenance


def _set_doi(metadata: dict[str, Any], value: Any, provenance: str) -> None:
    doi = normalize_doi(_first_text(value))
    if not doi:
        return
    metadata["doi"] = doi
    metadata["metadata_provenance"]["doi"] = provenance


def _set_isbn(metadata: dict[str, Any], value: Any, provenance: str) -> None:
    candidates = value if isinstance(value, list | tuple) else [value]
    for candidate in candidates:
        isbn = normalize_isbn(_clean_text(candidate))
        if isbn:
            metadata["isbn"] = isbn
            metadata["metadata_provenance"]["isbn"] = provenance
            return


def _set_authors(metadata: dict[str, Any], authors: list[str], provenance: str) -> None:
    cleaned = [_clean_text(author) for author in authors if _clean_text(author)]
    if cleaned:
        metadata["authors"] = cleaned
        metadata["metadata_provenance"]["authors"] = provenance


def _set_pages(metadata: dict[str, Any], value: Any, provenance: str) -> None:
    if value in (None, "", [], {}):
        return
    metadata["pages"] = value
    metadata["metadata_provenance"]["pages"] = provenance


def _year_from_date_parts(value: Any) -> str:
    if isinstance(value, dict):
        parts = value.get("date-parts")
        if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
            year = _clean_text(parts[0][0])
            return year if re.fullmatch(r"\d{4}", year) else ""
    return ""


def _year_from_text(value: Any) -> str:
    match = re.search(r"\b((?:18|19|20|21)\d{2})\b", _clean_text(value))
    return match.group(1) if match else ""


def _crossref_authors(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    authors: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = _clean_text(item.get("name"))
        if not name:
            given = _clean_text(item.get("given"))
            family = _clean_text(item.get("family"))
            name = " ".join(part for part in (given, family) if part)
        if name:
            authors.append(name)
    return authors


def _csl_authors(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    authors: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        literal = _clean_text(item.get("literal"))
        if literal:
            authors.append(literal)
            continue
        given = _clean_text(item.get("given"))
        family = _clean_text(item.get("family"))
        name = " ".join(part for part in (given, family) if part)
        if name:
            authors.append(name)
    return authors


def _parse_crossref_payload(body: bytes | str, *, resolver_source: str) -> dict[str, Any]:
    data = _decode_json(body)
    message = data.get("message") if isinstance(data, dict) else None
    if not isinstance(message, dict):
        return _metadata_shell(resolver_source)
    metadata = _metadata_shell(resolver_source)
    _set_text(metadata, "title", message.get("title"), "crossref:message.title")
    _set_authors(metadata, _crossref_authors(message.get("author")), "crossref:message.author")
    for date_key in ("published-print", "published-online", "published", "issued"):
        year = _year_from_date_parts(message.get(date_key))
        if year:
            metadata["year"] = year
            metadata["metadata_provenance"]["year"] = f"crossref:message.{date_key}.date-parts"
            break
    _set_doi(metadata, message.get("DOI"), "crossref:message.DOI")
    _set_isbn(metadata, message.get("ISBN"), "crossref:message.ISBN")
    _set_text(metadata, "publisher", message.get("publisher"), "crossref:message.publisher")
    _set_text(metadata, "journal", message.get("container-title"), "crossref:message.container-title")
    _set_pages(metadata, _clean_text(message.get("page")), "crossref:message.page")
    return metadata


def _parse_csl_payload(body: bytes | str, *, resolver_source: str) -> dict[str, Any]:
    data = _decode_json(body)
    if not isinstance(data, dict):
        return _metadata_shell(resolver_source)
    metadata = _metadata_shell(resolver_source)
    _set_text(metadata, "title", data.get("title"), "doi-content-negotiation:title")
    _set_authors(metadata, _csl_authors(data.get("author")), "doi-content-negotiation:author")
    year = _year_from_date_parts(data.get("issued")) or _year_from_text(data.get("issued"))
    if year:
        metadata["year"] = year
        metadata["metadata_provenance"]["year"] = "doi-content-negotiation:issued"
    _set_doi(metadata, data.get("DOI") or data.get("doi"), "doi-content-negotiation:DOI")
    _set_isbn(metadata, data.get("ISBN") or data.get("isbn"), "doi-content-negotiation:ISBN")
    _set_text(metadata, "publisher", data.get("publisher"), "doi-content-negotiation:publisher")
    _set_text(metadata, "journal", data.get("container-title"), "doi-content-negotiation:container-title")
    _set_pages(metadata, _clean_text(data.get("page")), "doi-content-negotiation:page")
    return metadata


def _parse_arxiv_payload(body: bytes | str, *, resolver_source: str) -> dict[str, Any]:
    root = ET.fromstring(_body_bytes(body))
    atom = "{http://www.w3.org/2005/Atom}"
    arxiv = "{http://arxiv.org/schemas/atom}"
    entry = root.find(f"{atom}entry")
    if entry is None:
        return _metadata_shell(resolver_source)
    metadata = _metadata_shell(resolver_source)
    _set_text(metadata, "title", entry.findtext(f"{atom}title"), "arxiv:entry.title")
    _set_authors(metadata, [node.findtext(f"{atom}name") or "" for node in entry.findall(f"{atom}author")], "arxiv:entry.author.name")
    year = _year_from_text(entry.findtext(f"{atom}published")) or _year_from_text(entry.findtext(f"{atom}updated"))
    if year:
        metadata["year"] = year
        metadata["metadata_provenance"]["year"] = "arxiv:entry.published"
    entry_id = _clean_text(entry.findtext(f"{atom}id"))
    arxiv_id = normalize_arxiv_id(entry_id)
    if arxiv_id:
        metadata["arxiv_id"] = arxiv_id
        metadata["metadata_provenance"]["arxiv_id"] = "arxiv:entry.id"
    _set_doi(metadata, entry.findtext(f"{arxiv}doi"), "arxiv:entry.arxiv:doi")
    _set_text(metadata, "journal", entry.findtext(f"{arxiv}journal_ref"), "arxiv:entry.arxiv:journal_ref")
    return metadata


def _parse_openlibrary_payload(body: bytes | str, isbn: str, *, resolver_source: str) -> dict[str, Any]:
    data = _decode_json(body)
    if not isinstance(data, dict) or not data:
        return _metadata_shell(resolver_source)
    wanted_key = "ISBN:" + isbn
    record = data.get(wanted_key)
    record_key = wanted_key
    if not isinstance(record, dict):
        for key, value in data.items():
            if isinstance(key, str) and normalize_isbn(key.replace("ISBN:", "")) == isbn and isinstance(value, dict):
                record = value
                record_key = key
                break
    if not isinstance(record, dict):
        return _metadata_shell(resolver_source)
    metadata = _metadata_shell(resolver_source)
    _set_text(metadata, "title", record.get("title"), "openlibrary:title")
    authors = []
    if isinstance(record.get("authors"), list):
        for author in record["authors"]:
            if isinstance(author, dict):
                authors.append(_clean_text(author.get("name")))
            else:
                authors.append(_clean_text(author))
    _set_authors(metadata, authors, "openlibrary:authors.name")
    year = _year_from_text(record.get("publish_date"))
    if year:
        metadata["year"] = year
        metadata["metadata_provenance"]["year"] = "openlibrary:publish_date"
    identifiers = record.get("identifiers") if isinstance(record.get("identifiers"), dict) else {}
    isbn_candidates: list[Any] = []
    for key in ("isbn_13", "isbn_10", "isbn"):
        value = identifiers.get(key)
        if isinstance(value, list):
            isbn_candidates.extend(value)
        elif value:
            isbn_candidates.append(value)
    isbn_candidates.append(record_key)
    _set_isbn(metadata, isbn_candidates, "openlibrary:identifiers")
    publishers = []
    if isinstance(record.get("publishers"), list):
        for publisher in record["publishers"]:
            if isinstance(publisher, dict):
                publishers.append(_clean_text(publisher.get("name")))
            else:
                publishers.append(_clean_text(publisher))
    _set_text(metadata, "publisher", publishers, "openlibrary:publishers.name")
    _set_pages(metadata, record.get("number_of_pages"), "openlibrary:number_of_pages")
    return metadata


def _has_bibliographic_fields(metadata: dict[str, Any]) -> bool:
    return any(key in metadata and metadata[key] not in (None, "", [], {}) for key in _METADATA_FIELDS)


def _attempt_summary(resolver_source: str, payload: dict[str, Any]) -> dict[str, Any]:
    summary = {"resolver_source": resolver_source, "status": payload.get("status", STATUS_UNAVAILABLE)}
    if payload.get("cache_hit"):
        summary["cache_hit"] = True
    if payload.get("cache_path"):
        summary["cache_path"] = payload["cache_path"]
    if payload.get("error"):
        summary["error"] = payload["error"]
    return summary


def _found_result(
    identifier_type: str,
    identifier: str,
    resolver_source: str,
    metadata: dict[str, Any],
    attempts: list[dict[str, Any]],
) -> dict[str, Any]:
    metadata = dict(metadata)
    metadata["resolver_source"] = resolver_source
    metadata.setdefault("metadata_provenance", {})
    return {
        "status": STATUS_FOUND,
        "identifier_type": identifier_type,
        "identifier": identifier,
        "resolver_source": resolver_source,
        "metadata": metadata,
        "attempts": attempts,
    }


def _status_result(status: str, identifier_type: str, identifier: str, *, attempts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"status": status, "identifier_type": identifier_type, "identifier": identifier}
    if attempts is not None:
        result["attempts"] = attempts
    return result


def _combined_status_result(identifier_type: str, identifier: str, attempts: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = [attempt.get("status") for attempt in attempts]
    status = STATUS_NOT_FOUND if statuses and all(item == STATUS_NOT_FOUND for item in statuses) else STATUS_UNAVAILABLE
    return _status_result(status, identifier_type, identifier, attempts=attempts)
