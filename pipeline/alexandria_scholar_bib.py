#!/usr/bin/env python3
"""Normalize one manually exported Google Scholar BibTeX entry.

This is the non-scraping Scholar fallback primitive: it reads exactly one
user-provided BibTeX entry from stdin or a file and prints Alexandria-shaped
metadata JSON to stdout. It is read-only with respect to the Alexandria library;
it never writes source PDFs, parsed Markdown, sidecars, or the root bibliography.

Parser limits are intentional and dependency-free. The reader supports one
standard BibTeX entry of the form ``@type{key, field={value}, ...}`` (or the
parenthesized variant), nested braced values, quoted values, bare values, and
simple ``#`` concatenation. It does not expand @string macros, crossref data, or
multi-entry exports; split library exports to a single entry before using it.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alexandria_metadata_common import (
    NODATE_YEAR,
    UNKNOWN_AUTHOR,
    VALID_CATEGORIES,
    authors_from_author,
    base_key,
    is_low_confidence,
)


class BibTeXParseError(ValueError):
    """Raised when the one-entry BibTeX reader cannot parse the input."""


@dataclass(frozen=True)
class BibTeXEntry:
    entry_type: str
    key: str
    fields: dict[str, str]
    raw: str


_ACCENT_MARKS = {
    "'": "\u0301",
    "`": "\u0300",
    "^": "\u0302",
    '"': "\u0308",
    "~": "\u0303",
    "=": "\u0304",
    ".": "\u0307",
    "u": "\u0306",
    "v": "\u030c",
    "H": "\u030b",
    "r": "\u030a",
    "c": "\u0327",
    "k": "\u0328",
    "b": "\u0331",
    "d": "\u0323",
}
_CHAR_COMMANDS = {
    "aa": "å",
    "AA": "Å",
    "ae": "æ",
    "AE": "Æ",
    "oe": "œ",
    "OE": "Œ",
    "o": "ø",
    "O": "Ø",
    "l": "ł",
    "L": "Ł",
    "ss": "ß",
    "LaTeX": "LaTeX",
    "TeX": "TeX",
}
_TEXT_SYMBOL_COMMANDS = {
    "textbackslash": "\\",
    "textasciitilde": "~",
    "textasciicircum": "^",
}
_ARTICLE_TYPES = {"article", "inproceedings", "proceedings", "conference", "phdthesis", "mastersthesis"}
_ARXIV_RE = re.compile(r"(?:arxiv:\s*|arxiv\.org/(?:abs|pdf)/)([A-Za-z.-]+/\d{7}|\d{4}\.\d{4,5}(?:v\d+)?)", re.IGNORECASE)
_BARE_ARXIV_RE = re.compile(r"^(?:[A-Za-z.-]+/\d{7}|\d{4}\.\d{4,5}(?:v\d+)?)$")
_DOI_RE = re.compile(r"\b(10\.\d{4,9}/\S+)", re.IGNORECASE)


def _skip_ws_and_comments(text: str, pos: int) -> int:
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
            continue
        if text[pos] == "%":
            while pos < len(text) and text[pos] not in "\r\n":
                pos += 1
            continue
        return pos
    return pos


def _matching_delimiter(opener: str) -> str:
    if opener == "{":
        return "}"
    if opener == "(":
        return ")"
    raise BibTeXParseError(f"unsupported entry delimiter: {opener!r}")


def _find_entry_end(text: str, open_pos: int, opener: str) -> int:
    closer = _matching_delimiter(opener)
    depth = 1
    in_quote = False
    escaped = False
    pos = open_pos + 1
    while pos < len(text):
        char = text[pos]
        if escaped:
            escaped = False
            pos += 1
            continue
        if char == "\\":
            escaped = True
            pos += 1
            continue
        if char == '"':
            in_quote = not in_quote
            pos += 1
            continue
        if not in_quote:
            if char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    return pos
        pos += 1
    raise BibTeXParseError("unterminated BibTeX entry")


def _find_balanced_brace(text: str, open_pos: int) -> int:
    depth = 1
    escaped = False
    pos = open_pos + 1
    while pos < len(text):
        char = text[pos]
        if escaped:
            escaped = False
            pos += 1
            continue
        if char == "\\":
            escaped = True
            pos += 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return pos
        pos += 1
    raise BibTeXParseError("unterminated braced value")


def _find_key_comma(body: str) -> int:
    depth = 0
    in_quote = False
    escaped = False
    for pos, char in enumerate(body):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_quote = not in_quote
            continue
        if in_quote:
            continue
        if char == "{":
            depth += 1
        elif char == "}" and depth:
            depth -= 1
        elif char == "," and depth == 0:
            return pos
    raise BibTeXParseError("entry is missing the comma after the citation key")


def _parse_quoted_value(text: str, pos: int) -> tuple[str, int]:
    depth = 0
    escaped = False
    start = pos + 1
    pos += 1
    while pos < len(text):
        char = text[pos]
        if escaped:
            escaped = False
            pos += 1
            continue
        if char == "\\":
            escaped = True
            pos += 1
            continue
        if char == "{":
            depth += 1
        elif char == "}" and depth:
            depth -= 1
        elif char == '"' and depth == 0:
            return text[start:pos], pos + 1
        pos += 1
    raise BibTeXParseError("unterminated quoted value")


def _parse_bare_value(text: str, pos: int) -> tuple[str, int]:
    start = pos
    while pos < len(text) and text[pos] not in ",#":
        pos += 1
    return text[start:pos].strip(), pos


def _parse_value_segment(text: str, pos: int) -> tuple[str, int]:
    pos = _skip_ws_and_comments(text, pos)
    if pos >= len(text):
        raise BibTeXParseError("missing field value")
    if text[pos] == "{":
        end = _find_balanced_brace(text, pos)
        return text[pos + 1 : end], end + 1
    if text[pos] == '"':
        return _parse_quoted_value(text, pos)
    return _parse_bare_value(text, pos)


def _parse_value(text: str, pos: int) -> tuple[str, int]:
    parts: list[str] = []
    while True:
        segment, pos = _parse_value_segment(text, pos)
        parts.append(segment)
        pos = _skip_ws_and_comments(text, pos)
        if pos >= len(text) or text[pos] != "#":
            return "".join(parts), pos
        pos = _skip_ws_and_comments(text, pos + 1)


def _parse_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    pos = 0
    while True:
        pos = _skip_ws_and_comments(text, pos)
        while pos < len(text) and text[pos] == ",":
            pos = _skip_ws_and_comments(text, pos + 1)
        if pos >= len(text):
            return fields
        match = re.match(r"[A-Za-z][A-Za-z0-9_:-]*", text[pos:])
        if not match:
            raise BibTeXParseError(f"expected field name near {text[pos:pos + 20]!r}")
        field_name = match.group(0).lower()
        pos += len(match.group(0))
        pos = _skip_ws_and_comments(text, pos)
        if pos >= len(text) or text[pos] != "=":
            raise BibTeXParseError(f"field {field_name!r} is missing '='")
        value, pos = _parse_value(text, pos + 1)
        fields[field_name] = value
        pos = _skip_ws_and_comments(text, pos)
        if pos < len(text):
            if text[pos] != ",":
                raise BibTeXParseError(f"expected comma after field {field_name!r}")
            pos += 1


def parse_one_entry(text: str) -> BibTeXEntry:
    """Parse exactly one BibTeX entry from *text*."""
    start = _skip_ws_and_comments(text, 0)
    if start >= len(text) or text[start] != "@":
        raise BibTeXParseError("expected one BibTeX entry starting with '@'")
    pos = start + 1
    match = re.match(r"[A-Za-z]+", text[pos:])
    if not match:
        raise BibTeXParseError("entry type is missing after '@'")
    entry_type = match.group(0).lower()
    if entry_type in {"comment", "preamble", "string"}:
        raise BibTeXParseError(f"@{entry_type} is not a bibliographic entry")
    pos += len(match.group(0))
    pos = _skip_ws_and_comments(text, pos)
    if pos >= len(text) or text[pos] not in "{(":
        raise BibTeXParseError("entry must use '{' or '(' after the type")
    opener = text[pos]
    end = _find_entry_end(text, pos, opener)
    tail = _skip_ws_and_comments(text, end + 1)
    if tail != len(text):
        raise BibTeXParseError("expected exactly one BibTeX entry; extra text follows the first entry")

    body = text[pos + 1 : end]
    comma = _find_key_comma(body)
    key = body[:comma].strip()
    if not key:
        raise BibTeXParseError("citation key is empty")
    fields = _parse_fields(body[comma + 1 :])
    return BibTeXEntry(entry_type=entry_type, key=key, fields=fields, raw=text[start : end + 1])


def _take_braced_text(text: str, pos: int) -> tuple[str, int]:
    end = _find_balanced_brace(text, pos)
    return text[pos + 1 : end], end + 1


def _skip_empty_group(text: str, pos: int) -> int:
    if text.startswith("{}", pos):
        return pos + 2
    return pos


def _apply_accent(accent: str, base: str) -> str:
    if not base:
        return ""
    mark = _ACCENT_MARKS.get(accent)
    if not mark:
        return base
    first = unicodedata.normalize("NFC", base[0] + mark)
    return first + base[1:]


def bibtex_text_to_plain(value: str) -> str:
    """Return readable text for the small TeX subset emitted by Scholar."""
    value = str(value or "").replace("\u00a0", " ")
    out: list[str] = []
    pos = 0
    while pos < len(value):
        char = value[pos]
        if char in "{}":
            pos += 1
            continue
        if char != "\\":
            out.append(char)
            pos += 1
            continue

        pos += 1
        if pos >= len(value):
            out.append("\\")
            break
        command_start = pos
        command_char = value[pos]
        if command_char in r"&%$#_{}":
            out.append(command_char)
            pos += 1
            continue
        if command_char in _ACCENT_MARKS:
            pos += 1
            while pos < len(value) and value[pos].isspace():
                pos += 1
            if pos < len(value) and value[pos] == "{":
                try:
                    base, pos = _take_braced_text(value, pos)
                    base = bibtex_text_to_plain(base).strip()
                except BibTeXParseError:
                    base = ""
            elif pos < len(value):
                base = value[pos]
                pos += 1
            else:
                base = ""
            out.append(_apply_accent(command_char, base))
            continue
        if command_char.isalpha():
            while pos < len(value) and value[pos].isalpha():
                pos += 1
            command = value[command_start:pos]
            if command in _CHAR_COMMANDS:
                out.append(_CHAR_COMMANDS[command])
                pos = _skip_empty_group(value, pos)
            elif command in _TEXT_SYMBOL_COMMANDS:
                out.append(_TEXT_SYMBOL_COMMANDS[command])
                pos = _skip_empty_group(value, pos)
            elif pos < len(value) and value[pos] == "{":
                try:
                    argument, pos = _take_braced_text(value, pos)
                    out.append(bibtex_text_to_plain(argument))
                except BibTeXParseError:
                    out.append(command)
            else:
                out.append(command)
            if pos < len(value) and value[pos].isspace():
                pos += 1
            continue
        out.append(command_char)
        pos += 1
    return re.sub(r"\s+", " ", "".join(out)).strip()


def _plain_fields(fields: dict[str, str]) -> dict[str, str]:
    return {name: bibtex_text_to_plain(value) for name, value in fields.items()}


def _year_from_fields(fields: dict[str, str]) -> str:
    for name in ("year", "date", "publicationdate"):
        value = fields.get(name, "")
        match = re.search(r"\d{4}", value)
        if match:
            return match.group(0)
    return NODATE_YEAR


def _normalize_doi(value: str) -> str:
    value = re.sub(r"^\s*(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value.strip(), flags=re.IGNORECASE)
    match = _DOI_RE.search(value)
    doi = match.group(1) if match else value
    return doi.strip().strip(".,;)").lower()


def _doi_from_fields(fields: dict[str, str]) -> str:
    for name in ("doi", "url", "note"):
        doi = _normalize_doi(fields.get(name, ""))
        if doi.startswith("10."):
            return doi
    return ""


def _normalize_isbn(value: str) -> str:
    isbn = re.sub(r"[^0-9Xx]", "", value).upper()
    return isbn if len(isbn) in {10, 13} else ""


def _arxiv_from_fields(fields: dict[str, str]) -> str:
    archive_prefix = fields.get("archiveprefix", "")
    candidates: list[str] = []
    if archive_prefix.lower() == "arxiv" and fields.get("eprint"):
        candidates.append(fields["eprint"])
    for name in ("arxiv_id", "arxivid", "eprint", "url", "note"):
        if fields.get(name):
            candidates.append(fields[name])
    for candidate in candidates:
        candidate = candidate.strip()
        if _BARE_ARXIV_RE.fullmatch(candidate):
            return candidate
        match = _ARXIV_RE.search(candidate)
        if match:
            return match.group(1)
    return ""


def _category_from_entry(entry_type: str, fields: dict[str, str], doi: str, arxiv_id: str) -> str:
    category = fields.get("category", "").strip().lower()
    if category in VALID_CATEGORIES:
        return category
    if doi or arxiv_id or entry_type.lower() in _ARTICLE_TYPES:
        return "papers"
    return "misc"


def metadata_from_entry(entry: BibTeXEntry, manual_bib_source: str = "google-scholar") -> dict[str, Any]:
    """Map a parsed BibTeX entry to Alexandria metadata plus provenance."""
    fields = _plain_fields(entry.fields)
    author = fields.get("author", "").strip() or UNKNOWN_AUTHOR
    year = _year_from_fields(fields)
    doi = _doi_from_fields(fields)
    isbn = _normalize_isbn(fields.get("isbn", ""))
    arxiv_id = _arxiv_from_fields(fields)
    category = _category_from_entry(entry.entry_type, fields, doi, arxiv_id)
    metadata: dict[str, Any] = {
        "key": base_key(author, year),
        "title": fields.get("title", "").strip(),
        "author": author,
        "authors": authors_from_author(author),
        "year": year,
        "category": category,
        "doi": doi,
        "isbn": isbn,
        "arxiv_id": arxiv_id,
        "metadata_source": "manual_bib",
        "scholar_key": entry.key,
        "manual_bib_source": manual_bib_source,
    }
    metadata["metadata_confidence"] = "low" if is_low_confidence(metadata) else "high" if (doi or isbn or arxiv_id) else "medium"
    return metadata


def _read_input(path: Path | None) -> str:
    if path is None or str(path) == "-":
        return sys.stdin.read()
    return path.read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Normalize one manually exported Scholar BibTeX entry to Alexandria metadata JSON.")
    parser.add_argument("bibfile", nargs="?", type=Path, help="BibTeX file containing exactly one entry; omit or use '-' for stdin")
    args = parser.parse_args(argv)

    try:
        entry = parse_one_entry(_read_input(args.bibfile))
        metadata = metadata_from_entry(entry)
    except (BibTeXParseError, OSError) as exc:
        print(f"alexandria_scholar_bib.py: error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(metadata, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
