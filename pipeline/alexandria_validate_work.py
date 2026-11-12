#!/usr/bin/env python3
"""Validate filed Alexandria works."""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from alexandria_audit_bibtex import iter_bib_sidecars, root_bibliography_finding
from alexandria_bibtex import bibtex_type
from alexandria_key_registry import KeyRegistry
from alexandria_metadata_common import (
    HTML_SRC_RE,
    MARKDOWN_IMAGE_RE,
    VALID_CATEGORIES,
    jsonl_count,
    load_json,
    metadata_sha_set,
    normalize_doi,
    normalize_isbn,
    sha256_file,
)
from alexandria_scholar_bib import BibTeXEntry, BibTeXParseError, bibtex_text_to_plain, parse_one_entry

REQUIRED_BIB_FIELDS: dict[str, tuple[tuple[str, ...], ...]] = {
    "article": (("title",), ("author",), ("year",), ("journal", "doi", "eprint")),
    "inproceedings": (("title",), ("author",), ("year",), ("booktitle",)),
    "book": (("title",), ("author",), ("year",), ("publisher", "isbn")),
    "misc": (("title",), ("author",)),
    "online": (("title",), ("url", "doi", "eprint")),
}


def is_ignorable_target(target: str) -> bool:
    lowered = target.strip().lower()
    return (
        not lowered
        or lowered.startswith("#")
        or lowered.startswith("/")
        or re.match(r"^[a-z][a-z0-9+.-]*:", lowered) is not None
    )


def image_targets(text: str) -> list[str]:
    targets = [match.group(2) for match in MARKDOWN_IMAGE_RE.finditer(text)]
    targets.extend(match.group(2) for match in HTML_SRC_RE.finditer(text))
    return targets


def compact_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, dict):
        for key in ("x-default", "en", "en-US"):
            if key in value:
                text = compact_text(value[key])
                if text:
                    return text
        for item in value.values():
            text = compact_text(item)
            if text:
                return text
        return ""
    if isinstance(value, (list, tuple, set)):
        return "; ".join(text for item in value if (text := compact_text(item)))
    return " ".join(str(value).split())


def first_metadata_text(metadata: dict[str, Any], *keys: str) -> str:
    for key in keys:
        if key not in metadata:
            continue
        text = compact_text(metadata[key])
        if text:
            return text
    return ""


def plain_bib_text(value: str) -> str:
    return compact_text(bibtex_text_to_plain(value))


def bib_field_text(entry: BibTeXEntry, *names: str) -> str:
    for name in names:
        value = entry.fields.get(name.lower())
        if value is None:
            continue
        text = plain_bib_text(value)
        if text:
            return text
    return ""


def required_field_label(group: tuple[str, ...]) -> str:
    return group[0] if len(group) == 1 else "-or-".join(group)


def normalize_doi_for_match(value: str) -> str:
    value = re.sub(r"^\s*(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value, flags=re.IGNORECASE)
    return normalize_doi(value)


def normalize_arxiv_for_match(value: str) -> str:
    value = value.strip()
    value = re.sub(r"^\s*arxiv:\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^\s*https?://arxiv\.org/(?:abs|pdf)/", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\.pdf\s*$", "", value, flags=re.IGNORECASE)
    return value.strip().casefold()


def identifier_fingerprint(value: str, kind: str) -> str:
    if kind == "doi":
        return normalize_doi_for_match(value)
    if kind == "isbn":
        return normalize_isbn(value) or re.sub(r"\s+", "", value).casefold()
    if kind == "arxiv":
        return normalize_arxiv_for_match(value)
    return value.casefold()


def validate_required_bib_fields(
    bib_path: Path,
    entry: BibTeXEntry,
    entry_type: str,
    *,
    informal: bool = False,
) -> list[str]:
    errors: list[str] = []
    if informal and entry_type == "misc":
        groups: tuple[tuple[str, ...], ...] = (("title",),)
    else:
        groups = REQUIRED_BIB_FIELDS.get(entry_type, ())
    for group in groups:
        if any(bib_field_text(entry, name) for name in group):
            continue
        errors.append(f"{bib_path}: @{entry_type} missing required BibTeX field {required_field_label(group)}")
    return errors


def validate_identifier_matches(bib_path: Path, entry: BibTeXEntry, metadata: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    checks = (
        ("doi", "DOI", bib_field_text(entry, "doi"), first_metadata_text(metadata, "doi")),
        ("isbn", "ISBN", bib_field_text(entry, "isbn"), first_metadata_text(metadata, "isbn")),
        ("arxiv", "arXiv eprint", bib_field_text(entry, "eprint", "arxiv_id", "arxivid"), first_metadata_text(metadata, "eprint", "arxiv_id")),
    )
    for kind, label, bib_value, metadata_value in checks:
        if not bib_value or not metadata_value:
            continue
        if identifier_fingerprint(bib_value, kind) != identifier_fingerprint(metadata_value, kind):
            errors.append(f"{bib_path}: {label} mismatch vs metadata: bib={bib_value!r} metadata={metadata_value!r}")
    return errors


def validate_bib_sidecar(bib_path: Path, key: str, metadata: dict[str, Any], metadata_path: Path) -> list[str]:
    errors: list[str] = []
    try:
        entry = parse_one_entry(bib_path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, BibTeXParseError) as exc:
        return [f"{bib_path}: invalid BibTeX sidecar: {exc}"]

    metadata_key = first_metadata_text(metadata, "key")
    if metadata_key and metadata_key != key:
        errors.append(f"{metadata_path}: metadata key {metadata_key!r} != markdown stem {key!r}")
    if entry.key != key:
        errors.append(f"{bib_path}: BibTeX key {entry.key!r} != markdown stem {key!r}")
    if metadata_key and entry.key != metadata_key:
        errors.append(f"{bib_path}: BibTeX key {entry.key!r} != metadata key {metadata_key!r}")

    expected_type = bibtex_type(metadata)
    if entry.entry_type != expected_type:
        errors.append(f"{bib_path}: BibTeX entry type @{entry.entry_type} != expected @{expected_type}")
    informal = str(metadata.get("kind") or "") == "informal"
    errors.extend(validate_required_bib_fields(bib_path, entry, expected_type, informal=informal))
    errors.extend(validate_identifier_matches(bib_path, entry, metadata))
    return errors


def validate_key_registry_identity(md_path: Path, metadata: dict[str, Any]) -> list[str]:
    library_root = md_path.parent.parent
    try:
        registry = KeyRegistry.load(library_root)
    except (OSError, ValueError) as exc:
        return [f"{library_root / 'key_registry.json'}: invalid key registry: {exc}"]
    if not registry.path.is_file():
        return []
    sha_values = metadata_sha_set(metadata)
    if not sha_values:
        return []
    try:
        reserved = registry.lookup_by_sha(sha_values)
    except RuntimeError as exc:
        return [f"{registry.path}: {exc}"]
    if reserved is None:
        return []
    actual = (md_path.parent.name, md_path.stem)
    if reserved != actual:
        return [
            f"{md_path}: key registry identity mismatch: source_sha256 maps to "
            f"{reserved[0]}/{reserved[1]}, but work is {actual[0]}/{actual[1]}"
        ]
    return []


def validate_library_bibliography(library_root: Path) -> list[str]:
    bib_paths = iter_bib_sidecars(library_root)
    finding = root_bibliography_finding(library_root, bib_paths)
    if not finding:
        return []
    sample = finding.get("sample") or finding.get("issue") or "root bibliography differs from sidecars"
    return [f"{library_root / 'alexandria.bib'}: {finding['issue']} ({sample})"]


def validate_work_md(md_path: Path) -> list[str]:
    errors: list[str] = []
    key = md_path.stem
    category_dir = md_path.parent
    work_dir = category_dir / key
    text = md_path.read_text(encoding="utf-8", errors="replace") if md_path.is_file() else ""
    if len(text.strip()) < 10:
        errors.append(f"{md_path}: markdown is empty or too short")
    meta_path = work_dir / "metadata.json"
    if not meta_path.is_file():
        errors.append(f"{work_dir}: missing metadata.json")
        metadata = {}
    else:
        metadata = load_json(meta_path)
        if metadata.get("metadata_confidence") == "low":
            errors.append(f"{work_dir}: low-confidence metadata in normal library")
    bib = category_dir / f"{key}.bib"
    if not bib.is_file():
        errors.append(f"{bib}: missing BibTeX sidecar")
    else:
        errors.extend(validate_bib_sidecar(bib, key, metadata, meta_path))
    if isinstance(metadata, dict):
        errors.extend(validate_key_registry_identity(md_path, metadata))
    informal = isinstance(metadata, dict) and str(metadata.get("kind") or "") == "informal"
    for target in image_targets(text):
        if is_ignorable_target(target):
            continue
        candidate = category_dir / target if "/" in target else work_dir / target
        if not candidate.is_file():
            msg = f"{md_path}: missing image artifact {target}"
            if informal:
                print(f"WARN {msg}")
            else:
                errors.append(msg)
    chunks = work_dir / "chunks.jsonl"
    embeddings = work_dir / "embeddings.jsonl"
    chunk_count = jsonl_count(chunks)
    embedding_count = jsonl_count(embeddings)
    if chunks.exists() and chunk_count == 0:
        errors.append(f"{chunks}: chunks.jsonl is empty")
    if embeddings.exists() and embedding_count != chunk_count:
        errors.append(f"{embeddings}: embedding count {embedding_count} != chunk count {chunk_count}")
    if (chunks.exists() or embeddings.exists()) and not (work_dir / "run_meta.json").is_file():
        errors.append(f"{work_dir}: missing run_meta.json")
    source_files = metadata.get("source_files") if isinstance(metadata, dict) else []
    if isinstance(source_files, list):
        for row in source_files:
            if not isinstance(row, dict):
                continue
            filed_name = row.get("filed_name")
            if not filed_name:
                continue
            source_copy = work_dir / str(filed_name)
            if not source_copy.is_file():
                errors.append(f"{source_copy}: missing source copy")
                continue
            expected_size = row.get("size")
            if isinstance(expected_size, int) and source_copy.stat().st_size != expected_size:
                errors.append(f"{source_copy}: source copy size mismatch")
            expected_sha = row.get("sha256")
            if expected_sha and sha256_file(source_copy) != str(expected_sha):
                errors.append(f"{source_copy}: source copy sha256 mismatch")
    return errors


def iter_work_markdown(library_root: Path) -> list[Path]:
    paths: list[Path] = []
    for category in sorted(VALID_CATEGORIES):
        category_dir = library_root / category
        if not category_dir.is_dir():
            continue
        for md in sorted(category_dir.glob("*.md")):
            if md.name.endswith(".notes.md"):
                continue
            paths.append(md)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, default=Path("."))
    parser.add_argument("--work-md", type=Path, action="append", default=[])
    args = parser.parse_args()
    work_paths = [path.resolve() for path in args.work_md] if args.work_md else iter_work_markdown(args.library.resolve())
    errors: list[str] = []
    for library_root in sorted({path.parent.parent for path in work_paths} or {args.library.resolve()}):
        errors.extend(validate_library_bibliography(library_root))
    for md_path in work_paths:
        errors.extend(validate_work_md(md_path))
    if errors:
        for error in errors:
            print(error)
        return 1
    print(f"validated {len(work_paths)} work(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
