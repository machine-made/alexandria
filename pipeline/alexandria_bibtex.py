#!/usr/bin/env python3
"""Generate Alexandria BibTeX sidecars from metadata."""
from __future__ import annotations

import argparse
import os
import re
import secrets
import stat
import unicodedata
from pathlib import Path
from typing import Any

from alexandria_metadata_common import NODATE_YEAR, VALID_CATEGORIES, is_unknown_author_value, load_json

# Deterministic entry-type mapping, in precedence order:
# 1. source-type/booktitle/proceedings cues -> @inproceedings
# 2. explicit source-type book cues -> @book; explicit source-type article cues -> @article
# 3. identifiers/containers: isbn -> @book; doi/arXiv/journal/category=papers -> @article
# 4. Alexandria book-like subject categories -> @book; otherwise @misc
BOOKLIKE_CATEGORIES = {"optimization", "propulsion", "quant"}
SOURCE_TYPE_KEYS = ("source_type", "sourceType", "publication_type", "publicationType", "type", "entry_type")
PROCEEDINGS_SOURCE_TYPES = {
    "conference",
    "conference-paper",
    "inproceedings",
    "proceeding",
    "proceedings",
    "proceedings-article",
}
BOOK_SOURCE_TYPES = {"book", "ebook", "edited-book", "e-book", "monograph", "textbook"}
ARTICLE_SOURCE_TYPES = {
    "article",
    "doi",
    "journal-article",
    "paper",
    "posted-content",
    "preprint",
    "report-paper",
}
TECHREPORT_SOURCE_TYPES = {
    "nasa-technical-note",
    "technical-note",
    "technical-report",
    "techreport",
    "tech-report",
}
VALUE_REPLACEMENTS = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}
ASCII_TRANSLATIONS = str.maketrans(
    {
        "–": "-",
        "—": "-",
        "−": "-",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "…": "...",
        "×": "x",
        "≤": "<=",
        "≥": ">=",
    }
)
ACRONYM_RE = re.compile(r"(?<![A-Za-z\\{])([A-Z][A-Z0-9]{1,})(?![A-Za-z\\}])")
FIELD_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("month", ("month",)),
    ("journal", ("journal", "journaltitle")),
    ("booktitle", ("booktitle", "book_title")),
    ("publisher", ("publisher",)),
    ("institution", ("institution", "organization")),
    ("volume", ("volume",)),
    ("number", ("number", "issue")),
    ("pages", ("pages",)),
    ("url", ("url",)),
    ("doi", ("doi",)),
    ("isbn", ("isbn",)),
)


def _compact_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, dict):
        for key in ("x-default", "en", "en-US"):
            if key in value:
                text = _compact_text(value[key])
                if text:
                    return text
        for item in value.values():
            text = _compact_text(item)
            if text:
                return text
        return ""
    if isinstance(value, (list, tuple, set)):
        return "; ".join(text for item in value if (text := _compact_text(item)))
    return " ".join(str(value).split())


def _ascii_text(text: str) -> str:
    text = text.translate(ASCII_TRANSLATIONS)
    decomposed = unicodedata.normalize("NFKD", text)
    without_marks = "".join(char for char in decomposed if not unicodedata.combining(char))
    return without_marks.encode("ascii", "ignore").decode("ascii")


def clean_value(value: object, *, ascii_tex: bool = False) -> str:
    """Return a BibTeX-safe field value.

    The default path keeps UTF-8 characters for modern biber/biblatex while escaping
    BibTeX-special ASCII characters.  ``ascii_tex=True`` is an explicit opt-in for
    ASCII-only output, using stdlib transliteration before the same BibTeX escaping.
    """

    text = _compact_text(value)
    text = text.replace("{", "").replace("}", "")
    if ascii_tex:
        text = _ascii_text(text)
    return "".join(VALUE_REPLACEMENTS.get(char, char) for char in text)


def protect_title_acronyms(title: str) -> str:
    """Brace all-caps title tokens without wrapping the complete title."""

    return ACRONYM_RE.sub(r"{\1}", title)


def clean_title(value: object, *, ascii_tex: bool = False) -> str:
    return protect_title_acronyms(clean_value(value, ascii_tex=ascii_tex))


def _first_metadata_value(metadata: dict[str, Any], *keys: str) -> object:
    for key in keys:
        if key not in metadata:
            continue
        value = metadata[key]
        if _compact_text(value):
            return value
    return ""


def _has_metadata_value(metadata: dict[str, Any], *keys: str) -> bool:
    return bool(_compact_text(_first_metadata_value(metadata, *keys)))


def _normalized_source_types(metadata: dict[str, Any]) -> set[str]:
    source_types: set[str] = set()
    for key in SOURCE_TYPE_KEYS:
        value = metadata.get(key)
        values = value if isinstance(value, (list, tuple, set)) else (value,)
        for item in values:
            text = _compact_text(item).lower()
            if not text:
                continue
            source_type = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
            if source_type:
                source_types.add(source_type)
    return source_types


def bibtex_type(metadata: dict[str, Any]) -> str:
    if str(metadata.get("kind") or "") == "informal":
        if _has_metadata_value(metadata, "url", "source_url"):
            return "online"
        return "misc"
    source_types = _normalized_source_types(metadata)
    category = _compact_text(metadata.get("category")).casefold()
    if source_types & PROCEEDINGS_SOURCE_TYPES or _has_metadata_value(metadata, "booktitle", "book_title", "proceedings"):
        return "inproceedings"
    if source_types & BOOK_SOURCE_TYPES:
        return "book"
    if source_types & ARTICLE_SOURCE_TYPES:
        return "article"
    if source_types & TECHREPORT_SOURCE_TYPES:
        return "techreport"
    if _has_metadata_value(metadata, "isbn"):
        return "book"
    if _has_metadata_value(metadata, "doi", "arxiv_id", "eprint", "journal") or category == "papers":
        return "article"
    if category in BOOKLIKE_CATEGORIES:
        return "book"
    return "misc"


def _normalize_biber_author_name(name: str) -> str:
    """Make a single author field value biber-valid.

    BibTeX/biber parse each name as ``Last, First`` (at most one comma). A
    value with ≥2 commas is almost always a packed multi-author string from
    NASA reports and similar sources. Split on commas, pair tokens as
    (surname, initials), and rejoin with `` and ``. Odd/unpairable token
    counts fall back to the original string (no crash).
    """
    if name.count(",") < 2:
        return name
    tokens = [token.strip() for token in name.split(",") if token.strip()]
    if len(tokens) < 2 or len(tokens) % 2 != 0:
        return name
    pairs = [f"{tokens[i]}, {tokens[i + 1]}" for i in range(0, len(tokens), 2)]
    return " and ".join(pairs)


def bibtex_author(metadata: dict[str, Any], *, ascii_tex: bool = False) -> str:
    authors = metadata.get("authors")
    if isinstance(authors, list) and authors:
        cleaned_authors = [
            _normalize_biber_author_name(clean_value(author, ascii_tex=ascii_tex))
            for author in authors
        ]
        return " and ".join(author for author in cleaned_authors if author)
    author = clean_value(metadata.get("author", ""), ascii_tex=ascii_tex)
    if is_unknown_author_value(author):
        return ""
    return _normalize_biber_author_name(author)


def _append_field(fields: list[tuple[str, str]], name: str, value: object, *, ascii_tex: bool = False, title: bool = False) -> None:
    cleaned = clean_title(value, ascii_tex=ascii_tex) if title else clean_value(value, ascii_tex=ascii_tex)
    if cleaned:
        fields.append((name, cleaned))


def bibtex_entry(key: str, metadata: dict[str, Any], *, ascii_tex: bool = False) -> str:
    entry_type = bibtex_type(metadata)
    is_informal = str(metadata.get("kind") or "") == "informal"
    fields: list[tuple[str, str]] = []
    _append_field(fields, "title", metadata.get("title", ""), ascii_tex=ascii_tex, title=True)
    if not is_informal:
        author = bibtex_author(metadata, ascii_tex=ascii_tex)
        if author:
            fields.append(("author", author))
    year = clean_value(metadata.get("year", ""), ascii_tex=ascii_tex)
    if year and year.casefold() != NODATE_YEAR:
        fields.append(("year", year))
    for field_name, aliases in FIELD_ALIASES:
        _append_field(fields, field_name, _first_metadata_value(metadata, *aliases), ascii_tex=ascii_tex)
    eprint = _first_metadata_value(metadata, "eprint", "arxiv_id")
    if _compact_text(eprint):
        _append_field(fields, "eprint", eprint, ascii_tex=ascii_tex)
        archive_prefix = _first_metadata_value(metadata, "archivePrefix", "archive_prefix")
        if not _compact_text(archive_prefix) and _has_metadata_value(metadata, "arxiv_id"):
            archive_prefix = "arXiv"
        _append_field(fields, "archivePrefix", archive_prefix, ascii_tex=ascii_tex)
        _append_field(
            fields,
            "primaryClass",
            _first_metadata_value(metadata, "primaryClass", "primary_class", "arxiv_primary_class"),
            ascii_tex=ascii_tex,
        )
    if is_informal:
        source = clean_value(
            _first_metadata_value(metadata, "source", "origin", "show", "url", "source_url"),
            ascii_tex=ascii_tex,
        )
        note = "Alexandria kind: informal"
        if source:
            note = f"{note}; source: {source}"
        fields.append(("note", note))
    else:
        confidence = clean_value(metadata.get("metadata_confidence", ""), ascii_tex=ascii_tex)
        if confidence.casefold() == "low":
            fields.append(("note", "Alexandria metadata confidence: low"))
    lines = [f"@{entry_type}{{{key},"]
    for idx, (name, value) in enumerate(fields):
        suffix = "," if idx < len(fields) - 1 else ""
        lines.append(f"  {name} = {{{value}}}{suffix}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def rebuild_root_bibliography(library_root: Path) -> Path:
    entries: list[str] = []
    for category in sorted(VALID_CATEGORIES):
        category_dir = library_root / category
        if not category_dir.is_dir():
            continue
        for bib in sorted(category_dir.glob("*.bib")):
            entries.append(bib.read_text(encoding="utf-8").strip())
    output = library_root / "alexandria.bib"
    content = "\n\n".join(entry for entry in entries if entry) + ("\n" if entries else "")
    output_mode = stat.S_IMODE(output.stat().st_mode) if output.exists() else None
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    while True:
        temporary = output.parent / f".{output.name}.{secrets.token_hex(8)}.tmp"
        try:
            temporary_fd = os.open(temporary, flags, 0o666)
            break
        except FileExistsError:
            continue
    directory_fd: int | None = None
    try:
        with os.fdopen(temporary_fd, "w", encoding="utf-8") as handle:
            if output_mode is not None:
                os.fchmod(handle.fileno(), output_mode)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        directory_fd = os.open(output.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        os.replace(temporary, output)
        os.fsync(directory_fd)
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
        temporary.unlink(missing_ok=True)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, help="metadata.json to render")
    parser.add_argument("--key", help="BibTeX/Alexandria key")
    parser.add_argument("--output", type=Path, help="write BibTeX here instead of stdout")
    parser.add_argument("--rebuild-root", type=Path, help="rebuild alexandria.bib under this library root")
    parser.add_argument("--ascii-tex", action="store_true", help="opt in to ASCII-only/TeX-command-compatible values")
    args = parser.parse_args()

    if args.rebuild_root:
        rebuild_root_bibliography(args.rebuild_root.resolve())
        return 0
    if not args.metadata or not args.key:
        parser.error("--metadata and --key are required unless --rebuild-root is used")
    metadata = load_json(args.metadata.resolve())
    entry = bibtex_entry(args.key, metadata, ascii_tex=args.ascii_tex)
    if args.output:
        args.output.write_text(entry, encoding="utf-8")
    else:
        print(entry, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
