#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Audit Alexandria BibTeX sidecars and generator risk samples read-only."""
from __future__ import annotations

import argparse
import csv
import json
import re
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from alexandria_library import default_library
from alexandria_bibtex import bibtex_entry, bibtex_type, clean_value, rebuild_root_bibliography
from alexandria_metadata_common import (
    NODATE_YEAR,
    UNKNOWN_AUTHOR,
    VALID_CATEGORIES,
    base_key,
    first_author_lastname,
    load_json,
    metadata_confidence,
    unique_key,
)

BIB_ENTRY_RE = re.compile(r"@\s*([A-Za-z]+)\s*\{\s*([^,\s]+)", re.MULTILINE)
BIB_FIELD_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_-]*)\s*=", re.MULTILINE)
CORE_FIELDS = ("title", "author", "year")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def is_unknown_author(author: object) -> bool:
    return not str(author or "").strip() or str(author).strip().casefold() == UNKNOWN_AUTHOR


def is_nodate_year(year: object) -> bool:
    return not str(year or "").strip() or str(year).strip().casefold() == NODATE_YEAR


def iter_metadata_paths(library_root: Path) -> list[Path]:
    paths: list[Path] = []
    for category in sorted(VALID_CATEGORIES):
        category_dir = library_root / category
        if category_dir.is_dir():
            paths.extend(sorted(category_dir.glob("*/metadata.json")))
    return paths


def iter_bib_sidecars(library_root: Path) -> list[Path]:
    paths: list[Path] = []
    for category in sorted(VALID_CATEGORIES):
        category_dir = library_root / category
        if category_dir.is_dir():
            paths.extend(sorted(category_dir.glob("*.bib")))
    return paths


def discovered_works(library_root: Path, metadata_paths: list[Path], bib_paths: list[Path]) -> list[dict[str, str]]:
    keys: set[tuple[str, str]] = set()
    for metadata_path in metadata_paths:
        keys.add((metadata_path.parent.parent.name, metadata_path.parent.name))
    for bib_path in bib_paths:
        keys.add((bib_path.parent.name, bib_path.stem))
    for category in sorted(VALID_CATEGORIES):
        category_dir = library_root / category
        if not category_dir.is_dir():
            continue
        for md_path in sorted(category_dir.glob("*.md")):
            if md_path.is_file():
                keys.add((category, md_path.stem))
    return [{"category": category, "key": key} for category, key in sorted(keys)]


def parse_bib_sidecar(path: Path, library_root: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    match = BIB_ENTRY_RE.search(text)
    entry_type = match.group(1).lower() if match else ""
    bib_key = match.group(2) if match else ""
    fields = sorted({field.casefold() for field in BIB_FIELD_RE.findall(text)})
    missing_fields = [field for field in CORE_FIELDS if field not in fields]
    row: dict[str, Any] = {
        "path": display_path(path, library_root),
        "category": path.parent.name,
        "file_key": path.stem,
        "bib_key": bib_key,
        "entry_type": entry_type,
        "fields": fields,
        "missing_fields": missing_fields,
        "parse_error": "" if match else "no BibTeX entry found",
    }
    if bib_key and bib_key != path.stem:
        row["key_mismatch"] = True
    else:
        row["key_mismatch"] = False
    return row


def parse_metadata(path: Path, library_root: Path) -> dict[str, Any]:
    row: dict[str, Any] = {
        "path": display_path(path, library_root),
        "category": path.parent.parent.name,
        "key": path.parent.name,
        "title": "",
        "author": "",
        "year": "",
        "metadata_confidence": "",
        "computed_base_key": "",
        "missing_fields": [],
        "low_confidence": False,
        "parse_error": "",
    }
    try:
        metadata = load_json(path)
    except (OSError, ValueError) as exc:
        row["parse_error"] = str(exc)
        row["missing_fields"] = list(CORE_FIELDS)
        row["low_confidence"] = True
        return row

    title = str(metadata.get("title") or "").strip()
    author = str(metadata.get("author") or "").strip()
    year = str(metadata.get("year") or "").strip()
    missing_fields: list[str] = []
    if not title:
        missing_fields.append("title")
    if is_unknown_author(author):
        missing_fields.append("author")
    if is_nodate_year(year):
        missing_fields.append("year")
    confidence = str(metadata.get("metadata_confidence") or metadata_confidence(metadata))
    row.update(
        {
            "title": title,
            "author": author,
            "year": year,
            "metadata_confidence": confidence,
            "computed_base_key": base_key(author or UNKNOWN_AUTHOR, year or NODATE_YEAR),
            "missing_fields": missing_fields,
            "low_confidence": confidence == "low",
        }
    )
    return row


def duplicate_key_findings(bib_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in bib_rows:
        key = str(row.get("bib_key") or "")
        if not key:
            continue
        by_key[key].append(
            {
                "path": str(row["path"]),
                "category": str(row["category"]),
                "file_key": str(row["file_key"]),
                "entry_type": str(row["entry_type"]),
            }
        )
    return [
        {"key": key, "count": len(locations), "locations": locations}
        for key, locations in sorted(by_key.items())
        if len(locations) > 1
    ]


def expected_root_bibliography_text(library_root: Path) -> str:
    entries: list[str] = []
    for category in sorted(VALID_CATEGORIES):
        category_dir = library_root / category
        if not category_dir.is_dir():
            continue
        for bib in sorted(category_dir.glob("*.bib")):
            entries.append(bib.read_text(encoding="utf-8", errors="replace").strip())
    return "\n\n".join(entry for entry in entries if entry) + ("\n" if entries else "")


def first_diff_line(left: str, right: str) -> str:
    left_lines = left.splitlines()
    right_lines = right.splitlines()
    limit = max(len(left_lines), len(right_lines))
    for index in range(limit):
        left_line = left_lines[index] if index < len(left_lines) else "<missing>"
        right_line = right_lines[index] if index < len(right_lines) else "<missing>"
        if left_line != right_line:
            return f"line {index + 1}: actual={left_line!r} expected={right_line!r}"
    return "byte content differs"


def root_bibliography_finding(library_root: Path, bib_paths: list[Path]) -> dict[str, Any] | None:
    root_bib = library_root / "alexandria.bib"
    if not bib_paths and not root_bib.exists():
        return None
    expected = expected_root_bibliography_text(library_root)
    if not root_bib.is_file():
        return {
            "path": display_path(root_bib, library_root),
            "issue": "missing-root-bibliography",
            "sidecar_count": len(bib_paths),
            "sample": "sidecars exist but alexandria.bib has not been rebuilt",
        }
    actual = root_bib.read_text(encoding="utf-8", errors="replace")
    if actual == expected:
        return None
    return {
        "path": display_path(root_bib, library_root),
        "issue": "stale-or-out-of-sync-root-bibliography",
        "sidecar_count": len(bib_paths),
        "actual_bytes": len(actual.encode("utf-8")),
        "expected_bytes": len(expected.encode("utf-8")),
        "sample": first_diff_line(actual, expected),
    }


def field_counter(rows: list[dict[str, Any]]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for row in rows:
        for field in row.get("missing_fields", []):
            counter[str(field)] += 1
    return counter


def synthetic_cross_category_collision(scratch_dir: Path) -> dict[str, Any]:
    root = scratch_dir / "cross-category"
    papers = root / "papers"
    misc = root / "misc"
    papers.mkdir(parents=True)
    misc.mkdir(parents=True)
    candidate = base_key("Ada Lovelace", "1843")
    papers_key = unique_key(papers, candidate)
    misc_key = unique_key(misc, candidate)
    papers_entry = bibtex_entry(
        papers_key,
        {
            "title": "Papers Copy",
            "author": "Ada Lovelace",
            "authors": ["Ada Lovelace"],
            "year": "1843",
            "category": "papers",
            "doi": "",
            "isbn": "",
            "arxiv_id": "",
            "metadata_confidence": "medium",
        },
    )
    misc_entry = bibtex_entry(
        misc_key,
        {
            "title": "Misc Copy",
            "author": "Ada Lovelace",
            "authors": ["Ada Lovelace"],
            "year": "1843",
            "category": "misc",
            "doi": "",
            "isbn": "",
            "arxiv_id": "",
            "metadata_confidence": "medium",
        },
    )
    (papers / f"{papers_key}.bib").write_text(papers_entry, encoding="utf-8")
    (misc / f"{misc_key}.bib").write_text(misc_entry, encoding="utf-8")
    root_bib = rebuild_root_bibliography(root)
    root_keys = [match.group(2) for match in BIB_ENTRY_RE.finditer(root_bib.read_text(encoding="utf-8"))]
    duplicates = sorted(key for key, count in Counter(root_keys).items() if count > 1)
    return {
        "risk_id": "R1",
        "title": "Cross-category key collision",
        "generator_calls": ["base_key", "unique_key", "bibtex_entry", "rebuild_root_bibliography"],
        "samples": [
            {
                "input": {"author": "Ada Lovelace", "year": "1843", "categories": ["papers", "misc"]},
                "base_key": candidate,
                "papers_unique_key": papers_key,
                "misc_unique_key": misc_key,
                "merged_root_keys": root_keys,
                "duplicate_keys": duplicates,
            }
        ],
        "summary": "unique_key is category-local; rebuilt root bibliography can contain the same BibTeX key twice.",
    }


def synthetic_author_key_risk() -> dict[str, Any]:
    cases = [
        ("org_author_ampersand", "AT&T Labs", "2024"),
        ("name_particle", "van der Berg", "2024"),
        ("et_al_literal", "Smith et al.", "2024"),
        ("diacritics", "José Álvarez", "2024"),
    ]
    samples = []
    for label, author, year in cases:
        samples.append(
            {
                "case": label,
                "author": author,
                "year": year,
                "first_author_lastname": first_author_lastname(author),
                "base_key": base_key(author, year),
            }
        )
    return {
        "risk_id": "R2",
        "title": "Author surname normalization mis-keys edge cases",
        "generator_calls": ["first_author_lastname", "base_key"],
        "samples": samples,
        "summary": "Organization names, particles, literal et al., and diacritics collapse to surprising key stems.",
    }


def synthetic_year_regex_risk() -> dict[str, Any]:
    year = "catalog 1234; published 2024"
    return {
        "risk_id": "R3",
        "title": "base_key takes the first 4-digit run in year",
        "generator_calls": ["base_key"],
        "samples": [
            {
                "author": "Ada Lovelace",
                "year": year,
                "base_key": base_key("Ada Lovelace", year),
            }
        ],
        "summary": "Any earlier 4-digit run in the year field wins over the intended publication year.",
    }


def synthetic_bibtex_type_risk() -> dict[str, Any]:
    fixtures = [
        {
            "case": "paper_without_category_or_ids",
            "metadata": {
                "title": "Paper-like Work",
                "author": "Ada Lovelace",
                "year": "1843",
                "category": "misc",
                "doi": "",
                "isbn": "",
                "arxiv_id": "",
            },
        },
        {
            "case": "book_without_isbn",
            "metadata": {
                "title": "Book Without ISBN",
                "author": "Ada Lovelace",
                "year": "1843",
                "category": "propulsion",
                "doi": "",
                "isbn": "",
                "arxiv_id": "",
            },
        },
        {
            "case": "book_filed_as_papers_without_isbn",
            "metadata": {
                "title": "Book Filed As Papers",
                "author": "Ada Lovelace",
                "year": "1843",
                "category": "papers",
                "doi": "",
                "isbn": "",
                "arxiv_id": "",
            },
        },
    ]
    samples = []
    for fixture in fixtures:
        samples.append(
            {
                "case": fixture["case"],
                "category": fixture["metadata"]["category"],
                "doi": fixture["metadata"]["doi"],
                "isbn": fixture["metadata"]["isbn"],
                "arxiv_id": fixture["metadata"]["arxiv_id"],
                "bibtex_type": bibtex_type(fixture["metadata"]),
            }
        )
    return {
        "risk_id": "R4",
        "title": "bibtex_type relies on sparse identifiers/category",
        "generator_calls": ["bibtex_type"],
        "samples": samples,
        "summary": "Without ISBN/DOI/arXiv, type selection falls back to misc or category=papers article.",
    }


def synthetic_missing_fields_risk() -> dict[str, Any]:
    metadata = {
        "title": "",
        "author": UNKNOWN_AUTHOR,
        "authors": [],
        "year": NODATE_YEAR,
        "category": "misc",
        "doi": "",
        "isbn": "",
        "arxiv_id": "",
        "metadata_confidence": "low",
    }
    entry = bibtex_entry("unknownnodate", metadata)
    return {
        "risk_id": "R5",
        "title": "Missing author/year/title fields are silently omitted",
        "generator_calls": ["bibtex_entry"],
        "samples": [
            {
                "key": "unknownnodate",
                "metadata": {"title": "", "author": UNKNOWN_AUTHOR, "year": NODATE_YEAR},
                "bibtex_entry": entry.strip(),
                "emitted_fields": sorted({field.casefold() for field in BIB_FIELD_RE.findall(entry)}),
            }
        ],
        "summary": "A low-confidence entry with no title/author/year emits only the key and confidence note.",
    }


def synthetic_identifier_coverage_risk() -> dict[str, Any]:
    metadata = {
        "title": "Known Work With Missing Identifiers",
        "author": "Ada Lovelace",
        "authors": ["Ada Lovelace"],
        "year": "1843",
        "category": "papers",
        "doi": "",
        "isbn": "",
        "arxiv_id": "",
        "metadata_confidence": "medium",
    }
    entry = bibtex_entry("lovelace1843", metadata)
    fields = sorted({field.casefold() for field in BIB_FIELD_RE.findall(entry)})
    return {
        "risk_id": "R6",
        "title": "Identifiers are emitted only if already present",
        "generator_calls": ["bibtex_entry"],
        "samples": [
            {
                "input_identifiers": {"doi": "", "isbn": "", "arxiv_id": ""},
                "emitted_fields": fields,
                "has_doi": "doi" in fields,
                "has_isbn": "isbn" in fields,
                "has_eprint": "eprint" in fields,
            }
        ],
        "summary": "The BibTeX generator does not resolve or enrich missing DOI/ISBN/arXiv fields.",
    }


def synthetic_root_staleness_risk(scratch_dir: Path) -> dict[str, Any]:
    root = scratch_dir / "root-staleness"
    misc = root / "misc"
    misc.mkdir(parents=True)
    key = "stale2020"
    old_entry = bibtex_entry(
        key,
        {
            "title": "Old Title",
            "author": "Ada Lovelace",
            "authors": ["Ada Lovelace"],
            "year": "2020",
            "category": "misc",
            "doi": "",
            "isbn": "",
            "arxiv_id": "",
            "metadata_confidence": "medium",
        },
    )
    sidecar = misc / f"{key}.bib"
    sidecar.write_text(old_entry, encoding="utf-8")
    root_bib = rebuild_root_bibliography(root)
    root_after_rebuild = root_bib.read_text(encoding="utf-8")
    new_entry = old_entry.replace("Old Title", "New Title")
    sidecar.write_text(new_entry, encoding="utf-8")
    root_after_sidecar_edit = root_bib.read_text(encoding="utf-8")
    sidecar.unlink()
    root_after_sidecar_delete = root_bib.read_text(encoding="utf-8")
    return {
        "risk_id": "R7",
        "title": "Root alexandria.bib is rebuilt only on demand",
        "generator_calls": ["bibtex_entry", "rebuild_root_bibliography"],
        "samples": [
            {
                "key": key,
                "root_has_old_title_after_rebuild": "Old Title" in root_after_rebuild,
                "root_has_new_title_after_sidecar_edit_without_rebuild": "New Title" in root_after_sidecar_edit,
                "root_still_has_deleted_sidecar_entry_without_rebuild": f"@misc{{{key}," in root_after_sidecar_delete,
            }
        ],
        "summary": "Editing/deleting sidecars leaves the root bibliography stale until rebuild_root_bibliography is called again.",
    }


def synthetic_clean_value_risk() -> dict[str, Any]:
    raw = r"\\ path & 50% $x_y# ~ ^ {raw} café"
    return {
        "risk_id": "R8",
        "title": "clean_value escapes TeX specials but leaves raw unicode",
        "generator_calls": ["clean_value"],
        "samples": [{"raw": raw, "clean_value": clean_value(raw)}],
        "summary": "Backslashes and TeX specials are escaped, braces are stripped, and unicode text is passed through.",
    }


def synthetic_risk_demonstrations(out_dir: Path) -> list[dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix=".bibtex-risk-", dir=out_dir) as scratch:
        scratch_dir = Path(scratch)
        return [
            synthetic_cross_category_collision(scratch_dir),
            synthetic_author_key_risk(),
            synthetic_year_regex_risk(),
            synthetic_bibtex_type_risk(),
            synthetic_missing_fields_risk(),
            synthetic_identifier_coverage_risk(),
            synthetic_root_staleness_risk(scratch_dir),
            synthetic_clean_value_risk(),
        ]


def audit_library(library_root: Path, out_dir: Path) -> dict[str, Any]:
    metadata_paths = iter_metadata_paths(library_root)
    bib_paths = iter_bib_sidecars(library_root)
    works = discovered_works(library_root, metadata_paths, bib_paths)
    metadata_rows = [parse_metadata(path, library_root) for path in metadata_paths]
    bib_rows = [parse_bib_sidecar(path, library_root) for path in bib_paths]
    duplicate_keys = duplicate_key_findings(bib_rows)
    metadata_missing = field_counter(metadata_rows)
    bib_missing = field_counter(bib_rows)
    key_mismatches = [row for row in bib_rows if row.get("key_mismatch")]
    invalid_bib = [row for row in bib_rows if row.get("parse_error")]
    low_or_missing_metadata = [
        row for row in metadata_rows if row.get("low_confidence") or row.get("missing_fields") or row.get("parse_error")
    ]
    bib_missing_rows = [row for row in bib_rows if row.get("missing_fields") or row.get("parse_error")]
    root_finding = root_bibliography_finding(library_root, bib_paths)
    issue_count = (
        len(duplicate_keys)
        + len(low_or_missing_metadata)
        + len(bib_missing_rows)
        + len(key_mismatches)
        + len(invalid_bib)
        + (1 if root_finding else 0)
    )
    counts = {
        "works": len(works),
        "metadata_json": len(metadata_rows),
        "bib_sidecars": len(bib_rows),
        "keys": sum(1 for row in bib_rows if row.get("bib_key")),
        "unique_keys": len({row["bib_key"] for row in bib_rows if row.get("bib_key")}),
        "duplicate_keys": len(duplicate_keys),
        "duplicate_key_occurrences": sum(max(0, int(row["count"]) - 1) for row in duplicate_keys),
        "low_confidence_metadata": sum(1 for row in metadata_rows if row.get("low_confidence")),
        "metadata_missing_title": metadata_missing["title"],
        "metadata_missing_author": metadata_missing["author"],
        "metadata_missing_year": metadata_missing["year"],
        "bib_missing_title": bib_missing["title"],
        "bib_missing_author": bib_missing["author"],
        "bib_missing_year": bib_missing["year"],
        "sidecar_key_mismatches": len(key_mismatches),
        "invalid_bib_sidecars": len(invalid_bib),
        "root_bibliography_issues": 1 if root_finding else 0,
    }
    samples: dict[str, Any] = {
        "duplicate_keys": duplicate_keys[:10],
        "metadata_low_or_missing": low_or_missing_metadata[:10],
        "bib_missing_fields": bib_missing_rows[:10],
        "sidecar_key_mismatches": key_mismatches[:10],
        "root_bibliography": [root_finding] if root_finding else [],
    }
    return {
        "schema": "alexandria-bibtex-audit-v1",
        "generated_at": utc_now(),
        "library_root": library_root.as_posix(),
        "status": "clean" if issue_count == 0 else "issues",
        "counts": counts,
        "works": works,
        "bib_sidecars": bib_rows,
        "metadata": metadata_rows,
        "library_findings": {
            "duplicate_keys": duplicate_keys,
            "metadata_low_or_missing": low_or_missing_metadata,
            "bib_missing_fields": bib_missing_rows,
            "sidecar_key_mismatches": key_mismatches,
            "root_bibliography": [root_finding] if root_finding else [],
        },
        "sample_failures": samples,
        "synthetic_risks": synthetic_risk_demonstrations(out_dir),
    }


def tsv_rows(report: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for duplicate in report["library_findings"]["duplicate_keys"]:
        rows.append(
            {
                "section": "library",
                "risk_id": "duplicate-key",
                "key": str(duplicate["key"]),
                "path": ",".join(location["path"] for location in duplicate["locations"]),
                "summary": f"BibTeX key appears {duplicate['count']} times",
                "details": json.dumps(duplicate, sort_keys=True),
            }
        )
    for row in report["library_findings"]["metadata_low_or_missing"]:
        rows.append(
            {
                "section": "library",
                "risk_id": "metadata-low-or-missing",
                "key": str(row["key"]),
                "path": str(row["path"]),
                "summary": "metadata is low-confidence or missing core fields",
                "details": json.dumps(
                    {
                        "missing_fields": row.get("missing_fields", []),
                        "metadata_confidence": row.get("metadata_confidence", ""),
                        "parse_error": row.get("parse_error", ""),
                    },
                    sort_keys=True,
                ),
            }
        )
    for row in report["library_findings"]["bib_missing_fields"]:
        rows.append(
            {
                "section": "library",
                "risk_id": "bib-missing-fields",
                "key": str(row.get("bib_key") or row.get("file_key") or ""),
                "path": str(row["path"]),
                "summary": "BibTeX sidecar is missing core fields or is unparsable",
                "details": json.dumps(
                    {"missing_fields": row.get("missing_fields", []), "parse_error": row.get("parse_error", "")},
                    sort_keys=True,
                ),
            }
        )
    for row in report["library_findings"]["sidecar_key_mismatches"]:
        rows.append(
            {
                "section": "library",
                "risk_id": "sidecar-key-mismatch",
                "key": str(row.get("bib_key") or ""),
                "path": str(row["path"]),
                "summary": "BibTeX entry key differs from sidecar filename stem",
                "details": json.dumps({"file_key": row.get("file_key"), "bib_key": row.get("bib_key")}, sort_keys=True),
            }
        )
    for row in report["library_findings"]["root_bibliography"]:
        rows.append(
            {
                "section": "library",
                "risk_id": "root-bibliography-stale",
                "key": "alexandria.bib",
                "path": str(row["path"]),
                "summary": str(row["issue"]),
                "details": json.dumps(row, sort_keys=True),
            }
        )
    for risk in report["synthetic_risks"]:
        rows.append(
            {
                "section": "synthetic",
                "risk_id": str(risk["risk_id"]),
                "key": str(risk["title"]),
                "path": "",
                "summary": str(risk["summary"]),
                "details": json.dumps(risk["samples"], sort_keys=True),
            }
        )
    return rows


def write_outputs(report: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "bibtex_audit.json"
    tsv_path = out_dir / "bibtex_audit.tsv"
    report["outputs"] = {"json": json_path.as_posix(), "tsv": tsv_path.as_posix()}
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    fields = ["section", "risk_id", "key", "path", "summary", "details"]
    with tsv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(tsv_rows(report))
    return json_path, tsv_path


def print_summary(report: dict[str, Any]) -> None:
    print("Alexandria BibTeX audit")
    print(f"library: {report['library_root']}")
    print(f"status: {report['status']}")
    print("counts:")
    for key in sorted(report["counts"]):
        print(f"  {key}: {report['counts'][key]}")
    print("sample failures:")
    duplicate_keys = report["sample_failures"]["duplicate_keys"]
    if duplicate_keys:
        for duplicate in duplicate_keys:
            locations = ", ".join(location["path"] for location in duplicate["locations"])
            print(f"  duplicate-key {duplicate['key']}: {locations}")
    else:
        print("  duplicate-key: none")
    for row in report["sample_failures"]["metadata_low_or_missing"][:3]:
        print(f"  metadata {row['path']}: missing={','.join(row.get('missing_fields', [])) or '-'} confidence={row.get('metadata_confidence') or '-'}")
    for row in report["sample_failures"]["bib_missing_fields"][:3]:
        print(f"  bib {row['path']}: key={row.get('bib_key') or '-'} missing={','.join(row.get('missing_fields', [])) or '-'}")
    for row in report["sample_failures"]["root_bibliography"]:
        print(f"  root {row['path']}: {row['issue']} ({row['sample']})")
    print("synthetic generator risk samples:")
    for risk in report["synthetic_risks"]:
        print(f"  {risk['risk_id']}: {risk['summary']}")
    outputs = report.get("outputs", {})
    if outputs:
        print(f"wrote: {outputs.get('json')} {outputs.get('tsv')}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, default=default_library(), help="Alexandria library root to audit")
    parser.add_argument("--out-dir", type=Path, default=Path("pipeline/audit"), help="directory for JSON/TSV reports")
    args = parser.parse_args()

    library_root = args.library.resolve()
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    report = audit_library(library_root, out_dir)
    write_outputs(report, out_dir)
    print_summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
