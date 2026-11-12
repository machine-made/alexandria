#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Audit Alexandria DROP_HERE without moving or deleting files."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from alexandria_library import default_library
from alexandria_metadata_common import (
    base_key,
    find_existing_by_sha,
    infer_metadata,
    iter_source_paths,
    is_low_confidence,
    load_json,
    metadata_confidence,
    metadata_sha_set,
    metadata_source,
    merge_metadata,
    source_files_for_stem,
    unique_key,
)


def drop_stems(drop_dir: Path) -> list[str]:
    return sorted({path.stem for path in iter_source_paths(drop_dir)})


def metadata_for_stem(parsed_dir: Path, drop_dir: Path, stem: str) -> tuple[dict[str, Any] | None, bool]:
    work = parsed_dir / stem
    md = work / f"{stem}.md"
    source_files = source_files_for_stem(drop_dir, stem)
    if not md.is_file():
        return None, False
    text = md.read_text(encoding="utf-8", errors="replace")
    inferred = infer_metadata(text, stem, source_files)
    metadata_path = work / "metadata.json"
    if metadata_path.is_file():
        metadata = merge_metadata(load_json(metadata_path), stem, source_files)
        for key in ("doi", "isbn", "arxiv_id"):
            if not metadata.get(key) and inferred.get(key):
                metadata[key] = inferred[key]
        metadata["metadata_source"] = metadata_source(metadata)
        metadata["metadata_confidence"] = metadata_confidence(metadata)
    else:
        metadata = inferred
    return metadata, True


def audit_stem(
    drop_dir: Path,
    parsed_dir: Path,
    library_root: Path,
    stem: str,
    reserved_keys: dict[str, set[str]] | None = None,
) -> dict[str, Any]:
    source_files = source_files_for_stem(drop_dir, stem)
    metadata, parsed_markdown = metadata_for_stem(parsed_dir, drop_dir, stem)
    if metadata is None:
        source_sha256 = {row["name"]: row["sha256"] for row in source_files}
        duplicate = find_existing_by_sha(library_root, set(source_sha256.values()))
        return {
            "stem": stem,
            "formats": sorted({row["format"] for row in source_files}),
            "source_files": source_files,
            "source_sha256": source_sha256,
            "parsed_markdown": False,
            "metadata_present": False,
            "title": "",
            "author": "",
            "year": "",
            "category": duplicate[0] if duplicate else "",
            "key": duplicate[1] if duplicate else "",
            "doi": "",
            "isbn": "",
            "arxiv_id": "",
            "metadata_confidence": "",
            "action": "duplicate" if duplicate else "parse-needed",
            "duplicate_of": {"category": duplicate[0], "key": duplicate[1]} if duplicate else {},
        }
    sha_values = metadata_sha_set(metadata)
    duplicate = find_existing_by_sha(library_root, sha_values)
    if duplicate:
        action = "duplicate"
        category, key = duplicate
    elif not source_files:
        action = "blocked"
        category = str(metadata.get("category", ""))
        key = base_key(str(metadata.get("author", "unknown")), str(metadata.get("year", "nodate")))
    elif is_low_confidence(metadata):
        action = "hold"
        category = str(metadata.get("category", ""))
        key = base_key(str(metadata.get("author", "unknown")), str(metadata.get("year", "nodate")))
    else:
        action = "file"
        category = str(metadata.get("category") or "misc").strip().lower()
        category_dir = library_root / category
        reserved = reserved_keys.setdefault(category, set()) if reserved_keys is not None else set()
        key = unique_key(
            category_dir,
            base_key(str(metadata.get("author", "unknown")), str(metadata.get("year", "nodate"))),
            reserved,
        )
        reserved.add(key)
    return {
        "stem": stem,
        "formats": sorted({row["format"] for row in source_files}),
        "source_files": source_files,
        "source_sha256": metadata.get("source_sha256", {}),
        "parsed_markdown": parsed_markdown,
        "metadata_present": (parsed_dir / stem / "metadata.json").is_file(),
        "title": metadata.get("title", ""),
        "author": metadata.get("author", ""),
        "year": metadata.get("year", ""),
        "category": category,
        "key": key,
        "doi": metadata.get("doi", ""),
        "isbn": metadata.get("isbn", ""),
        "arxiv_id": metadata.get("arxiv_id", ""),
        "metadata_confidence": metadata.get("metadata_confidence", ""),
        "action": action,
        "duplicate_of": {"category": duplicate[0], "key": duplicate[1]} if duplicate else {},
    }


def write_outputs(rows: list[dict[str, Any]], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl = out_dir / "drop_audit.jsonl"
    jsonl.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    fields = [
        "stem",
        "action",
        "metadata_confidence",
        "key",
        "category",
        "title",
        "author",
        "year",
        "doi",
        "isbn",
        "arxiv_id",
        "formats",
    ]
    with (out_dir / "drop_audit.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            flat = dict(row)
            flat["formats"] = ",".join(row.get("formats", []))
            writer.writerow(flat)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--drop", type=Path, default=Path("DROP_HERE"))
    parser.add_argument("--parsed", type=Path, default=Path("pipeline/parsed"))
    parser.add_argument("--library", type=Path, default=default_library())
    parser.add_argument("--out-dir", type=Path, default=Path("pipeline/audit"))
    args = parser.parse_args()
    drop_dir = args.drop.resolve()
    parsed_dir = args.parsed.resolve()
    library_root = args.library.resolve()
    reserved_keys: dict[str, set[str]] = {}
    rows = [audit_stem(drop_dir, parsed_dir, library_root, stem, reserved_keys) for stem in drop_stems(drop_dir)]
    write_outputs(rows, args.out_dir.resolve())
    with (args.out_dir.resolve() / "drop_audit.tsv").open("r", encoding="utf-8") as handle:
        print(handle.read(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
