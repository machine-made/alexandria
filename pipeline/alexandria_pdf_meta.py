#!/usr/bin/env python3
"""Extract embedded PDF Info/XMP metadata into a durable pdf_meta.json artifact."""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from pypdf import PdfReader
except ModuleNotFoundError as exc:  # pragma: no cover - exercised only without the approved dep
    if exc.name != "pypdf":
        raise
    raise SystemExit(
        "ERROR: pypdf is required to extract embedded PDF metadata. "
        "Run this script with ./.venv/bin/python, or install the approved dependency with:\n"
        "  VIRTUAL_ENV=.venv suv pip install -r pipeline/requirements.txt"
    ) from exc

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Z0-9]+)", re.IGNORECASE)
ARXIV_RE = re.compile(r"(?:arxiv:\s*|arxiv\.org/abs/)(\d{4}\.\d{4,5}(?:v\d+)?)", re.IGNORECASE)
ISBN_RE = re.compile(r"\bISBN(?:-1[03])?\s*:?\s*([0-9Xx][0-9Xx\-\s]{8,24}[0-9Xx])")
XMP_FIELDS = (
    "dc_title",
    "dc_creator",
    "dc_description",
    "dc_identifier",
    "dc_publisher",
    "dc_relation",
    "dc_source",
    "dc_subject",
    "dc_date",
    "pdf_keywords",
    "pdf_producer",
    "xmp_create_date",
    "xmp_metadata_date",
    "xmp_modify_date",
    "xmp_creator_tool",
)
INFO_FIELD_ALIASES = {
    "title": ("/Title", "Title"),
    "author": ("/Author", "Author"),
    "doi": ("/DOI", "/Doi", "/doi", "DOI", "doi"),
    "isbn": ("/ISBN", "/Isbn", "/isbn", "ISBN", "isbn"),
    "publisher": ("/Publisher", "/Company", "Publisher", "Company"),
    "journal": ("/Journal", "/PublicationName", "/Source", "Journal", "PublicationName", "Source"),
    "pages": ("/Pages", "/PageRange", "Pages", "PageRange"),
    "year": ("/Year", "Year"),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def jsonable(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): jsonable(val) for key, val in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(item) for item in value]
    return str(value)


def compact(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        for key in ("x-default", "en", "en-US"):
            if key in value:
                text = compact(value[key])
                if text:
                    return text
        for item in value.values():
            text = compact(item)
            if text:
                return text
        return ""
    if isinstance(value, (list, tuple, set)):
        parts = [compact(item) for item in value]
        return "; ".join(part for part in parts if part)
    return re.sub(r"\s+", " ", str(value)).strip()


def flatten_text(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = compact(value)
        return [text] if text else []
    if isinstance(value, datetime):
        return [value.isoformat()]
    if isinstance(value, dict):
        rows: list[str] = []
        for key, item in value.items():
            rows.extend(flatten_text(key))
            rows.extend(flatten_text(item))
        return rows
    if isinstance(value, (list, tuple, set)):
        rows = []
        for item in value:
            rows.extend(flatten_text(item))
        return rows
    text = compact(value)
    return [text] if text else []


# Crossref Funder Registry registrant — never a scholarly work DOI.
FUNDER_DOI_PREFIX = "10.13039/"


def normalize_doi(raw: str) -> str:
    doi = raw.strip()
    # Strip trailing prose punctuation until stable: removing an unbalanced
    # paren can re-expose a sentence period ("(doi: 10.1234/x.y.)"), so a single
    # pass in a fixed order is not enough.
    while doi:
        if doi[-1] in ".,;:":
            doi = doi[:-1]
            continue
        # DOI_RE's charclass includes (), so a closing paren from surrounding
        # prose is greedily eaten. Strip a trailing unbalanced ')' only; balanced
        # paths like Lancet/Elsevier ``10.1016/S0140-6736(00)02034-9`` stay intact.
        if doi.endswith(")") and doi.count(")") > doi.count("("):
            doi = doi[:-1]
            continue
        break
    return doi.lower()


def _is_funder_doi(doi: str) -> bool:
    return bool(doi) and doi.startswith(FUNDER_DOI_PREFIX)


def extract_doi(text: str) -> str:
    """First non-funder DOI match in *text* (positional).

    Funder-registry DOIs (``10.13039/``) are never returned as a work DOI; the
    next non-funder match wins. Still first-match otherwise — no labeled-line
    preference (that lives on the markdown path only).
    """
    for match in DOI_RE.finditer(text):
        doi = normalize_doi(match.group(1))
        if doi and not _is_funder_doi(doi):
            return doi
    return ""


def normalize_isbn(raw: str) -> str:
    isbn = re.sub(r"[^0-9Xx]", "", raw).upper()
    return isbn if len(isbn) in {10, 13} else ""


def extract_isbn(text: str) -> str:
    for match in ISBN_RE.finditer(text):
        isbn = normalize_isbn(match.group(1))
        if isbn:
            return isbn
    return ""


def extract_arxiv_id(text: str) -> str:
    match = ARXIV_RE.search(text)
    return match.group(1) if match else ""


def split_authors(raw: Any) -> list[str]:
    if isinstance(raw, (list, tuple, set)):
        rows: list[str] = []
        for item in raw:
            rows.extend(split_authors(item))
        return rows
    text = compact(raw)
    if not text:
        return []
    parts = [part.strip() for part in re.split(r"\s*;\s*|\s+and\s+|\s+&\s+|\n+", text) if part.strip()]
    return parts or [text]


def first_value(*values: Any) -> str:
    for value in values:
        text = compact(value)
        if text:
            return text
    return ""


def info_lookup(info: dict[str, Any], field: str) -> str:
    for key in INFO_FIELD_ALIASES.get(field, ()):  # raw Info keys may include or omit the leading slash
        if key in info:
            text = compact(info[key])
            if text:
                return text
    return ""


def xmp_lookup(xmp: dict[str, Any], *keys: str) -> str:
    for key in keys:
        if key in xmp:
            text = compact(xmp[key])
            if text:
                return text
    return ""


def date_value(value: Any) -> str:
    text = compact(value)
    return text


def first_year(*values: Any) -> str:
    for value in values:
        for text in flatten_text(value):
            match = re.search(r"\b((?:18|19|20|21)\d{2})\b", text)
            if match:
                return match.group(1)
    return ""


def raw_info_dict(reader: PdfReader) -> dict[str, Any]:
    metadata = reader.metadata or {}
    rows = {str(key): jsonable(value) for key, value in metadata.items()}
    for attr, key in (("creation_date", "_creation_date_iso"), ("modification_date", "_modification_date_iso")):
        value = getattr(metadata, attr, None)
        if value:
            rows[key] = jsonable(value)
    return rows


def raw_xmp_dict(reader: PdfReader) -> tuple[dict[str, Any], str]:
    try:
        xmp = reader.xmp_metadata
    except Exception as exc:  # malformed XMP should not hide the Info dictionary
        return {}, f"{type(exc).__name__}: {exc}"
    if xmp is None:
        return {}, ""
    rows: dict[str, Any] = {}
    for name in XMP_FIELDS:
        try:
            value = getattr(xmp, name)
        except Exception as exc:  # keep extracting other fields if one property is malformed
            rows[f"{name}_error"] = f"{type(exc).__name__}: {exc}"
            continue
        if value not in (None, "", [], {}, ()):  # keep the raw shape, but omit empty noise
            rows[name] = jsonable(value)
    try:
        custom = xmp.custom_properties
    except Exception as exc:
        rows["custom_properties_error"] = f"{type(exc).__name__}: {exc}"
    else:
        if custom:
            rows["custom_properties"] = jsonable(custom)
    return rows, ""


def normalized_metadata(info: dict[str, Any], xmp: dict[str, Any], page_count: int) -> dict[str, Any]:
    raw_text = "\n".join(flatten_text(info) + flatten_text(xmp))
    xmp_authors = xmp.get("dc_creator")
    info_author = info_lookup(info, "author")
    authors = split_authors(xmp_authors) or split_authors(info_author)
    dates = {
        "created": first_value(info.get("_creation_date_iso"), info.get("/CreationDate"), info.get("CreationDate"), xmp.get("xmp_create_date")),
        "modified": first_value(info.get("_modification_date_iso"), info.get("/ModDate"), info.get("ModDate"), xmp.get("xmp_modify_date")),
        "metadata": date_value(xmp.get("xmp_metadata_date")),
        "published": first_value(xmp.get("dc_date"), info.get("/Date"), info.get("Date")),
    }
    dates = {key: value for key, value in dates.items() if value}
    pages = info_lookup(info, "pages") or page_count
    normalized: dict[str, Any] = {
        "pages": pages,
        "page_count": page_count,
    }
    for key, value in {
        "title": first_value(xmp_lookup(xmp, "dc_title"), info_lookup(info, "title")),
        "author": "; ".join(authors) if authors else info_author,
        "authors": authors,
        "doi": info_lookup(info, "doi") or extract_doi(raw_text),
        "isbn": normalize_isbn(info_lookup(info, "isbn")) or extract_isbn(raw_text),
        "arxiv_id": extract_arxiv_id(raw_text),
        "publisher": first_value(xmp_lookup(xmp, "dc_publisher"), info_lookup(info, "publisher")),
        "journal": first_value(xmp_lookup(xmp, "dc_source"), info_lookup(info, "journal")),
        "year": info_lookup(info, "year") or first_year(xmp.get("dc_date"), info.get("/Date"), info.get("Date")),
        "dates": dates,
    }.items():
        if value not in (None, "", [], {}):
            normalized[key] = value
    return normalized


def extract_pdf_metadata(pdf_path: Path) -> dict[str, Any]:
    pdf_path = pdf_path.resolve()
    reader = PdfReader(str(pdf_path))
    try:
        page_count = len(reader.pages)
    except Exception:
        page_count = 0
    info = raw_info_dict(reader)
    xmp, xmp_error = raw_xmp_dict(reader)
    normalized = normalized_metadata(info, xmp, page_count)
    data: dict[str, Any] = {
        "schema_version": 1,
        "extractor": "alexandria_pdf_meta.py",
        "extracted_at": utc_now(),
        "source": {
            "name": pdf_path.name,
            "size": pdf_path.stat().st_size,
            "sha256": sha256_file(pdf_path),
        },
        "info": info,
        "xmp": xmp,
    }
    if xmp_error:
        data["xmp_error"] = xmp_error
    data.update(normalized)
    return data


def write_pdf_metadata(pdf_path: Path, output_path: Path | None = None) -> Path:
    output_path = output_path or (pdf_path.parent / "pdf_meta.json")
    data = extract_pdf_metadata(pdf_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output_path


def main(argv: list[str]) -> int:
    if len(argv) not in {2, 3}:
        print("Usage: alexandria_pdf_meta.py <input.pdf> [pdf_meta.json]", file=sys.stderr)
        return 2
    pdf_path = Path(argv[1])
    if not pdf_path.is_file():
        print(f"ERROR: PDF not found: {pdf_path}", file=sys.stderr)
        return 1
    output_path = Path(argv[2]) if len(argv) == 3 else None
    written = write_pdf_metadata(pdf_path, output_path)
    print(written)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
