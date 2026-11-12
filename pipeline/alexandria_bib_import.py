#!/usr/bin/env python3
"""Merge one external BibTeX entry into an existing filed Alexandria work."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alexandria_bibtex import bibtex_entry, rebuild_root_bibliography
from alexandria_metadata_common import NODATE_YEAR, UNKNOWN_AUTHOR, VALID_CATEGORIES, load_json, normalize_isbn, write_json
from alexandria_scholar_bib import BibTeXEntry, BibTeXParseError, bibtex_text_to_plain, metadata_from_entry, parse_one_entry
from alexandria_validate_work import validate_work_md

SELECTED_FIELDS = (
    "doi",
    "isbn",
    "journal",
    "booktitle",
    "publisher",
    "volume",
    "number",
    "pages",
    "year",
    "month",
    "eprint",
    "arxiv_id",
    "url",
    "title",
    "author",
    "authors",
)
PROVENANCE_FIELDS = ("metadata_source", "scholar_key", "manual_bib_source")
DIRECT_FIELD_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("journal", ("journal", "journaltitle")),
    ("booktitle", ("booktitle", "book_title")),
    ("publisher", ("publisher",)),
    ("volume", ("volume",)),
    ("number", ("number", "issue")),
    ("pages", ("pages",)),
    ("month", ("month",)),
    ("eprint", ("eprint",)),
    ("url", ("url",)),
)
ARXIV_SOURCE_FIELDS = ("archiveprefix", "archive_prefix", "eprint", "arxiv_id", "arxivid", "url", "note")
DOI_SOURCE_FIELDS = ("doi", "url", "note")
YEAR_SOURCE_FIELDS = ("year", "date", "publicationdate")
NEVER_OVERLAY_FIELDS = {
    "key",
    "category",
    "source_files",
    "source_sha256",
    "source_stem",
    "parsed_path",
    "parsed_paths",
    "parsed_artifact_path",
    "parsed_artifact_paths",
    "markdown_path",
    "source_path",
    "source_paths",
}


@dataclass(frozen=True)
class FieldOverlay:
    field: str
    imported: Any


@dataclass(frozen=True)
class FieldConflict:
    field: str
    existing: Any
    imported: Any
    action: str


@dataclass(frozen=True)
class MergePlan:
    library_root: Path
    md_path: Path
    metadata_path: Path
    bib_path: Path
    key: str
    category: str
    bibtex_source: str
    merged_metadata: dict[str, Any]
    overlays: list[FieldOverlay]
    conflicts: list[FieldConflict]
    provenance: dict[str, Any]


class FileSnapshot:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.existed = path.exists()
        self.data = path.read_bytes() if self.existed and path.is_file() else b""

    def restore(self) -> None:
        if not self.existed:
            if self.path.exists():
                self.path.unlink()
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.tmp-bib-import-restore")
        tmp.write_bytes(self.data)
        os.replace(tmp, self.path)


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(_has_value(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_has_value(item) for item in value)
    return True


def _compact_text(value: Any) -> str:
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


def _entry_has_field(entry: BibTeXEntry, *names: str) -> bool:
    return any(name.lower() in entry.fields for name in names)


def _plain_entry_field(entry: BibTeXEntry, *names: str) -> str:
    for name in names:
        value = entry.fields.get(name.lower())
        if value is None:
            continue
        text = bibtex_text_to_plain(value)
        if text:
            return text
    return ""


def _imported_metadata(entry: BibTeXEntry, bibtex_source: str) -> tuple[dict[str, Any], dict[str, Any]]:
    normalized = metadata_from_entry(entry, manual_bib_source=bibtex_source)
    imported: dict[str, Any] = {}

    if _entry_has_field(entry, "title") and _has_value(normalized.get("title")):
        imported["title"] = normalized["title"]
    if _entry_has_field(entry, "author"):
        author = normalized.get("author")
        if _has_value(author) and str(author).strip().casefold() != UNKNOWN_AUTHOR:
            imported["author"] = author
        authors = normalized.get("authors")
        if _has_value(authors):
            imported["authors"] = authors
    if _entry_has_field(entry, *YEAR_SOURCE_FIELDS):
        year = str(normalized.get("year") or "").strip()
        if year and year.casefold() != NODATE_YEAR:
            imported["year"] = year
    if _entry_has_field(entry, *DOI_SOURCE_FIELDS) and _has_value(normalized.get("doi")):
        imported["doi"] = normalized["doi"]
    if _entry_has_field(entry, "isbn") and _has_value(normalized.get("isbn")):
        imported["isbn"] = normalized["isbn"]
    if _entry_has_field(entry, *ARXIV_SOURCE_FIELDS) and _has_value(normalized.get("arxiv_id")):
        imported["arxiv_id"] = normalized["arxiv_id"]

    for field, aliases in DIRECT_FIELD_ALIASES:
        value = _plain_entry_field(entry, *aliases)
        if value:
            imported[field] = value

    imported = {field: imported[field] for field in SELECTED_FIELDS if field in imported and field not in NEVER_OVERLAY_FIELDS}
    provenance = {field: normalized[field] for field in PROVENANCE_FIELDS if _has_value(normalized.get(field))}
    return imported, provenance


def _normalize_doi_for_compare(value: str) -> str:
    value = re.sub(r"^\s*(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value, flags=re.IGNORECASE)
    return value.strip().strip(".,;:").casefold()


def _normalize_arxiv_for_compare(value: str) -> str:
    value = re.sub(r"^\s*arxiv:\s*", "", value.strip(), flags=re.IGNORECASE)
    value = re.sub(r"^\s*https?://arxiv\.org/(?:abs|pdf)/", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\.pdf\s*$", "", value, flags=re.IGNORECASE)
    return value.strip().casefold()


def _fingerprint(field: str, value: Any) -> Any:
    if field == "authors" and isinstance(value, (list, tuple, set)):
        return tuple(_compact_text(item) for item in value if _compact_text(item))
    text = _compact_text(value)
    if field == "doi":
        return _normalize_doi_for_compare(text)
    if field == "isbn":
        return normalize_isbn(text) or text.casefold()
    if field in {"arxiv_id", "eprint"}:
        return _normalize_arxiv_for_compare(text)
    return text


def _values_equal(field: str, existing: Any, imported: Any) -> bool:
    return _fingerprint(field, existing) == _fingerprint(field, imported)


def plan_merge(
    *,
    existing: dict[str, Any],
    imported: dict[str, Any],
    provenance: dict[str, Any],
    prefer_import: bool,
) -> tuple[dict[str, Any], list[FieldOverlay], list[FieldConflict]]:
    merged = dict(existing)
    overlays: list[FieldOverlay] = []
    conflicts: list[FieldConflict] = []
    for field in SELECTED_FIELDS:
        if field in NEVER_OVERLAY_FIELDS or field not in imported:
            continue
        imported_value = imported[field]
        if not _has_value(imported_value):
            continue
        existing_value = existing.get(field)
        if not _has_value(existing_value):
            merged[field] = imported_value
            overlays.append(FieldOverlay(field=field, imported=imported_value))
            continue
        if _values_equal(field, existing_value, imported_value):
            continue
        action = "prefer-import" if prefer_import else "keep-existing"
        conflicts.append(FieldConflict(field=field, existing=existing_value, imported=imported_value, action=action))
        if prefer_import:
            merged[field] = imported_value

    for field in PROVENANCE_FIELDS:
        if field in provenance:
            merged[field] = provenance[field]
    return merged, overlays, conflicts


def _resolve_work_md(work: str, library_root: Path) -> Path:
    candidate = Path(work).expanduser()
    if candidate.suffix == ".md" or candidate.exists():
        md_path = candidate.resolve()
        if not md_path.is_file():
            raise FileNotFoundError(f"filed work markdown not found: {md_path}")
        if md_path.name.endswith(".notes.md"):
            raise RuntimeError(f"notes files are not filed works: {md_path}")
        if md_path.parent.name not in VALID_CATEGORIES:
            raise RuntimeError(f"work markdown is not under a valid Alexandria category: {md_path}")
        return md_path

    key = work.strip()
    if not key or "/" in key or "\\" in key:
        raise RuntimeError(f"work must be a citation key or <category>/<key>.md path: {work!r}")
    matches = []
    for category in sorted(VALID_CATEGORIES):
        md_path = library_root / category / f"{key}.md"
        if md_path.is_file():
            matches.append(md_path)
    if not matches:
        raise FileNotFoundError(f"filed work key not found under {library_root}: {key}")
    if len(matches) > 1:
        detail = ", ".join(str(path) for path in matches)
        raise RuntimeError(f"ambiguous filed work key {key!r}: {detail}")
    return matches[0].resolve()


def resolve_work(work: str, explicit_library: Path | None) -> tuple[Path, Path, Path, Path, str, str]:
    if explicit_library is not None:
        library_root = explicit_library.resolve()
    else:
        candidate = Path(work).expanduser()
        library_root = candidate.resolve().parent.parent if candidate.suffix == ".md" or candidate.exists() else Path(".").resolve()
    md_path = _resolve_work_md(work, library_root)
    actual_root = md_path.parent.parent.resolve()
    if explicit_library is not None and actual_root != library_root:
        raise RuntimeError(f"work {md_path} is not under --library {library_root}")
    library_root = actual_root
    key = md_path.stem
    category = md_path.parent.name
    metadata_path = md_path.parent / key / "metadata.json"
    bib_path = md_path.parent / f"{key}.bib"
    if not metadata_path.is_file():
        raise FileNotFoundError(f"missing filed metadata: {metadata_path}")
    if not md_path.parent.joinpath(key).is_dir():
        raise FileNotFoundError(f"missing filed work artifact directory: {md_path.parent / key}")
    return library_root, md_path, metadata_path, bib_path, key, category


def _read_bibtex(path: Path | None) -> tuple[str, str]:
    if path is None or str(path) == "-":
        return sys.stdin.read(), "stdin"
    resolved = path.resolve()
    return resolved.read_text(encoding="utf-8"), str(resolved)


def make_plan(
    *,
    work: str,
    library: Path | None,
    bibtex_path: Path | None,
    prefer_import: bool,
) -> MergePlan:
    library_root, md_path, metadata_path, bib_path, key, category = resolve_work(work, library)
    bibtex_text, bibtex_source = _read_bibtex(bibtex_path)
    entry = parse_one_entry(bibtex_text)
    imported, provenance = _imported_metadata(entry, bibtex_source)
    existing = load_json(metadata_path)
    merged, overlays, conflicts = plan_merge(
        existing=existing,
        imported=imported,
        provenance=provenance,
        prefer_import=prefer_import,
    )
    return MergePlan(
        library_root=library_root,
        md_path=md_path,
        metadata_path=metadata_path,
        bib_path=bib_path,
        key=key,
        category=category,
        bibtex_source=bibtex_source,
        merged_metadata=merged,
        overlays=overlays,
        conflicts=conflicts,
        provenance=provenance,
    )


def _json_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def format_plan(plan: MergePlan, *, dry_run: bool) -> str:
    lines: list[str] = []
    if dry_run:
        lines.append("DRY RUN: import BibTeX into Alexandria work")
    else:
        lines.append(f"imported BibTeX into key={plan.key} category={plan.category}")
    lines.extend(
        [
            f"work: {plan.md_path}",
            f"metadata: {plan.metadata_path}",
            f"bibtex source: {plan.bibtex_source}",
            "field overlays:",
        ]
    )
    if plan.overlays:
        for overlay in plan.overlays:
            lines.append(f"- {overlay.field}: <missing> -> {_json_value(overlay.imported)}")
    else:
        lines.append("- none")
    lines.append("conflicts:")
    if plan.conflicts:
        for conflict in plan.conflicts:
            lines.append(
                f"- {conflict.field}: existing={_json_value(conflict.existing)} "
                f"imported={_json_value(conflict.imported)} action={conflict.action}"
            )
    else:
        lines.append("- none")
    lines.append("provenance:")
    if plan.provenance:
        for field in PROVENANCE_FIELDS:
            if field in plan.provenance:
                lines.append(f"- {field}: {_json_value(plan.provenance[field])}")
    else:
        lines.append("- none")
    if dry_run:
        lines.append("No files written.")
    else:
        lines.extend(
            [
                f"regenerated sidecar: {plan.bib_path}",
                f"rebuilt root bibliography: {plan.library_root / 'alexandria.bib'}",
                "validated merged work with validate_work_md",
            ]
        )
    return "\n".join(lines)


def _remove_if_exists(path: Path) -> None:
    if path.exists():
        path.unlink()


def apply_plan(plan: MergePlan) -> None:
    metadata_snapshot = FileSnapshot(plan.metadata_path)
    bib_snapshot = FileSnapshot(plan.bib_path)
    root_bib_snapshot = FileSnapshot(plan.library_root / "alexandria.bib")
    metadata_tmp = plan.metadata_path.with_name(f".{plan.metadata_path.name}.tmp-bib-import")
    bib_tmp = plan.bib_path.with_name(f".{plan.bib_path.name}.tmp-bib-import")
    try:
        _remove_if_exists(metadata_tmp)
        _remove_if_exists(bib_tmp)
        write_json(metadata_tmp, plan.merged_metadata)
        os.replace(metadata_tmp, plan.metadata_path)
        bib_tmp.write_text(bibtex_entry(plan.key, plan.merged_metadata), encoding="utf-8")
        os.replace(bib_tmp, plan.bib_path)
        rebuild_root_bibliography(plan.library_root)
        validation_errors = validate_work_md(plan.md_path)
        if validation_errors:
            raise RuntimeError("merged work failed validate_work_md: " + "; ".join(validation_errors))
    except Exception:
        _remove_if_exists(metadata_tmp)
        _remove_if_exists(bib_tmp)
        metadata_snapshot.restore()
        bib_snapshot.restore()
        root_bib_snapshot.restore()
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("work", help="filed Alexandria work key or <category>/<key>.md path")
    parser.add_argument("--bibtex", type=Path, help="one-entry BibTeX file; omit or use '-' to read stdin")
    parser.add_argument("--library", type=Path, help="Alexandria library root; defaults to work path root or current directory")
    parser.add_argument("--dry-run", action="store_true", help="print planned overlays/conflicts without writing files")
    parser.add_argument("--prefer-import", action="store_true", help="take imported values when conflicts are reported")
    args = parser.parse_args(argv)

    try:
        plan = make_plan(work=args.work, library=args.library, bibtex_path=args.bibtex, prefer_import=args.prefer_import)
        if not args.dry_run:
            apply_plan(plan)
    except (OSError, BibTeXParseError, ValueError, RuntimeError) as exc:
        print(f"alexandria_bib_import.py: error: {exc}", file=sys.stderr)
        return 1
    print(format_plan(plan, dry_run=args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
