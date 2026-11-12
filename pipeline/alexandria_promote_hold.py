#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Promote a corrected Alexandria hold entry into the canonical library."""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from alexandria_bibtex import bibtex_entry, rebuild_root_bibliography
from alexandria_file_parsed_work import (
    copy_artifacts,
    copy_sources_to_stage,
    install_staged_work,
    missing_marker_images,
    preserve_existing_source_records,
    resolve_key,
    source_paths,
    validate_parsed_artifacts,
)
from alexandria_key_registry import KeyRegistry
from alexandria_metadata_common import (
    PRECEDENCE_FIELDS,
    VALID_CATEGORIES,
    base_key,
    find_existing_by_sha,
    global_filed_keys,
    is_low_confidence,
    load_json,
    merge_metadata,
    metadata_sha_set,
    sha256_file,
    source_file_rows,
    source_sha256_map,
    write_json,
)
from alexandria_scholar_bib import BibTeXParseError, metadata_from_entry, parse_one_entry
from alexandria_validate_work import validate_work_md

HELD_SOURCE_KEYS = {"source_files", "source_sha256", "source_stem"}
REGISTRY_OWNED_KEYS = {"key"}
CORRECTED_JSON_SOURCE = "corrected_json"
CORRECTED_BIBTEX_SOURCE = "corrected_bibtex"


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
        tmp = self.path.with_name(f".{self.path.name}.tmp-promote-restore")
        tmp.write_bytes(self.data)
        os.replace(tmp, self.path)


def _overlay_metadata(base: dict[str, Any], overlay: dict[str, Any], source: str) -> None:
    field_sources = dict(base.get("metadata_field_sources") or {})
    for key, value in overlay.items():
        if key in HELD_SOURCE_KEYS or key in REGISTRY_OWNED_KEYS or key == "metadata_field_sources":
            continue
        if key in PRECEDENCE_FIELDS or base.get(key) != value:
            field_sources[key] = source
        base[key] = value
    base["metadata_field_sources"] = field_sources


def _metadata_from_bibtex(path: Path) -> dict[str, Any]:
    try:
        entry = parse_one_entry(path.read_text(encoding="utf-8"))
    except (OSError, BibTeXParseError) as exc:
        raise ValueError(f"invalid corrected BibTeX: {path}: {exc}") from exc
    return metadata_from_entry(entry, manual_bib_source=str(path))


def corrected_metadata(hold_dir: Path, metadata_path: Path | None, bibtex_path: Path | None) -> dict[str, Any]:
    stem = hold_dir.name
    held_metadata = load_json(hold_dir / "metadata.json")
    held_source_rows = source_file_rows(held_metadata.get("source_files"))
    if not held_source_rows:
        raise RuntimeError(f"{hold_dir / 'metadata.json'}: held metadata has no source_files records")

    merged_input = dict(held_metadata)
    if bibtex_path is not None:
        _overlay_metadata(merged_input, _metadata_from_bibtex(bibtex_path), CORRECTED_BIBTEX_SOURCE)
    if metadata_path is not None:
        _overlay_metadata(merged_input, load_json(metadata_path), CORRECTED_JSON_SOURCE)

    metadata = merge_metadata(merged_input, stem, held_source_rows)
    if is_low_confidence(metadata):
        raise RuntimeError("corrected metadata still resolves to low-confidence/unknown; refusing to promote")
    return metadata


def artifact_source_for_hold(hold_dir: Path) -> Path:
    stem = hold_dir.name
    parsed = hold_dir / "parsed"
    if not parsed.is_dir():
        raise FileNotFoundError(f"missing held parsed artifact copy: {parsed}")
    nested = parsed / stem
    if nested.is_dir():
        return nested
    return parsed


def verify_held_sources(hold_dir: Path, metadata: dict[str, Any]) -> list[Path]:
    stem = hold_dir.name
    rows = source_file_rows(metadata.get("source_files"))
    if not rows:
        raise RuntimeError(f"{hold_dir / 'metadata.json'}: no source_files records to preserve")
    rows_by_name = {str(row.get("name") or ""): row for row in rows if row.get("name")}
    sources = source_paths(hold_dir, stem)
    source_names = {path.name for path in sources}
    row_names = set(rows_by_name)
    missing = sorted(row_names - source_names)
    extra = sorted(source_names - row_names)
    if missing:
        raise RuntimeError(f"held source copy missing for metadata source_files: {', '.join(missing)}")
    if extra:
        raise RuntimeError(f"held source copy lacks metadata source_files record: {', '.join(extra)}")

    declared_sha_map = metadata.get("source_sha256")
    row_sha_map = source_sha256_map(rows)
    if isinstance(declared_sha_map, dict):
        for name, sha in row_sha_map.items():
            declared = declared_sha_map.get(name)
            if declared and str(declared) != sha:
                raise RuntimeError(f"held source_sha256 mismatch for {name}: {declared} != {sha}")

    for source in sources:
        row = rows_by_name[source.name]
        expected_size = row.get("size")
        if isinstance(expected_size, int) and source.stat().st_size != expected_size:
            raise RuntimeError(f"held source size mismatch for {source.name}: {source.stat().st_size} != {expected_size}")
        expected_sha = row.get("sha256")
        if expected_sha and sha256_file(source) != str(expected_sha):
            raise RuntimeError(f"held source sha256 mismatch for {source.name}")
    return sources


def planned_key(library_root: Path, metadata: dict[str, Any]) -> tuple[str, str]:
    """Plan a registry-owned key without persisting dry-run state."""
    sha_values = metadata_sha_set(metadata)
    category = str(metadata.get("category") or "misc").strip().lower()
    if category not in VALID_CATEGORIES:
        category = "misc"
    category_dir = library_root / category
    candidate = base_key(str(metadata.get("author", "unknown")), str(metadata.get("year", "nodate")))

    registry = KeyRegistry.load(library_root)
    existing = registry.lookup_by_sha(sha_values)
    if existing:
        registry.record_existing(sha_values, existing[0], existing[1], candidate, persist=False)
        return existing

    legacy_existing = find_existing_by_sha(library_root, sha_values)
    if legacy_existing:
        registry.record_existing(sha_values, legacy_existing[0], legacy_existing[1], candidate, persist=False)
        return legacy_existing

    reserved = global_filed_keys(library_root) | registry.reserved_keys()
    return registry.reserve_key(sha_values, category, candidate, category_dir=category_dir, reserved_keys=reserved, persist=False)


def format_plan(*, hold_dir: Path, library_root: Path, category: str, key: str, dry_run: bool) -> str:
    category_dir = library_root / category
    lines = []
    if dry_run:
        lines.append("DRY RUN: promote held Alexandria work")
        lines.append(f"planned key: {key}")
    else:
        lines.append(f"promoted key={key} category={category}")
    lines.extend(
        [
            f"category: {category}",
            f"target markdown: {category_dir / f'{key}.md'}",
            f"target work dir: {category_dir / key}",
            f"target bib: {category_dir / f'{key}.bib'}",
            "actions:",
            f"- copy held artifacts and source copies from {hold_dir}",
            "- generate BibTeX sidecar via bibtex_entry",
            "- rebuild root bibliography via rebuild_root_bibliography",
            f"- clear hold entry {hold_dir}",
        ]
    )
    if dry_run:
        lines.append("No files were moved; registry and bibliography were not touched.")
    return "\n".join(lines)


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _rollback_install(
    *,
    registry_snapshot: FileSnapshot,
    root_bib_snapshot: FileSnapshot,
    category_dir: Path,
    category_dir_existed: bool,
    stage_dir: Path,
    work_dir: Path,
    md_tmp: Path,
    md_dst: Path,
    bib_tmp: Path,
    bib_dst: Path,
) -> None:
    for path in (stage_dir, work_dir):
        if path.exists():
            _remove_path(path)
    for path in (md_tmp, md_dst, bib_tmp, bib_dst):
        if path.exists():
            path.unlink()
    registry_snapshot.restore()
    root_bib_snapshot.restore()
    if not category_dir_existed:
        try:
            category_dir.rmdir()
        except OSError:
            pass


def promote_hold(
    *,
    hold_dir: Path,
    library_root: Path,
    metadata_path: Path | None,
    bibtex_path: Path | None,
    dry_run: bool = False,
) -> tuple[str, str]:
    hold_dir = hold_dir.resolve()
    library_root = library_root.resolve()
    if hold_dir.parent.name != "_hold":
        raise RuntimeError(f"held work path must be _hold/<stem>: {hold_dir}")
    if not hold_dir.is_dir():
        raise FileNotFoundError(f"held work directory not found: {hold_dir}")
    if not (hold_dir / "metadata.json").is_file():
        raise FileNotFoundError(f"missing held metadata: {hold_dir / 'metadata.json'}")

    stem = hold_dir.name
    md_src = hold_dir / f"{stem}.md"
    if not md_src.is_file() or md_src.stat().st_size == 0:
        raise FileNotFoundError(f"missing held markdown: {md_src}")

    metadata = corrected_metadata(hold_dir, metadata_path, bibtex_path)
    sources = verify_held_sources(hold_dir, metadata)
    artifact_source = artifact_source_for_hold(hold_dir)
    text = md_src.read_text(encoding="utf-8", errors="replace")
    missing_images = missing_marker_images(text, artifact_source)
    if missing_images:
        raise RuntimeError(f"missing local Marker image artifacts: {', '.join(sorted(missing_images))}")
    validate_parsed_artifacts(artifact_source)

    if dry_run:
        return planned_key(library_root, metadata)

    registry_snapshot = FileSnapshot(library_root / "key_registry.json")
    root_bib_snapshot = FileSnapshot(library_root / "alexandria.bib")
    category = str(metadata.get("category") or "misc").strip().lower()
    if category not in VALID_CATEGORIES:
        category = "misc"
    category_dir = library_root / category
    category_dir_existed = category_dir.exists()

    category, key = resolve_key(library_root, metadata)
    category_dir = library_root / category
    work_dir = category_dir / key
    stage_dir = category_dir / f".{key}.tmp-promote"
    md_dst = category_dir / f"{key}.md"
    md_tmp = category_dir / f".{key}.md.tmp-promote"
    bib_dst = category_dir / f"{key}.bib"
    bib_tmp = category_dir / f".{key}.bib.tmp-promote"

    existing_targets = [path for path in (work_dir, md_dst, bib_dst) if path.exists()]
    if existing_targets:
        registry_snapshot.restore()
        root_bib_snapshot.restore()
        if not category_dir_existed:
            try:
                category_dir.rmdir()
            except OSError:
                pass
        detail = ", ".join(str(path) for path in existing_targets)
        raise RuntimeError(f"target already exists after registry resolution: {detail}")

    try:
        if stage_dir.exists():
            shutil.rmtree(stage_dir)
        for tmp in (md_tmp, bib_tmp):
            if tmp.exists():
                tmp.unlink()
        stage_dir.mkdir(parents=True)

        copy_artifacts(artifact_source, stage_dir)
        copy_sources_to_stage(sources, stage_dir, key, metadata)
        preserve_existing_source_records(metadata, load_json(hold_dir / "metadata.json"), stage_dir)
        metadata["key"] = key
        write_json(stage_dir / "metadata.json", metadata)
        shutil.copy2(md_src, md_tmp)
        bib_tmp.write_text(bibtex_entry(key, metadata), encoding="utf-8")
        install_staged_work(stage_dir, work_dir, md_tmp, md_dst, bib_tmp, bib_dst)
        rebuild_root_bibliography(library_root)
        validation_errors = validate_work_md(md_dst)
        if validation_errors:
            raise RuntimeError("promoted work failed validate_work_md: " + "; ".join(validation_errors))
        shutil.rmtree(hold_dir)
    except Exception:
        _rollback_install(
            registry_snapshot=registry_snapshot,
            root_bib_snapshot=root_bib_snapshot,
            category_dir=category_dir,
            category_dir_existed=category_dir_existed,
            stage_dir=stage_dir,
            work_dir=work_dir,
            md_tmp=md_tmp,
            md_dst=md_dst,
            bib_tmp=bib_tmp,
            bib_dst=bib_dst,
        )
        raise

    return category, key


def infer_library_root(hold_dir: Path, explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit.resolve()
    resolved_hold = hold_dir.resolve()
    if resolved_hold.parent.name == "_hold":
        return resolved_hold.parent.parent.resolve()
    return Path(".").resolve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("held_work", type=Path, help="held work directory, normally <library>/_hold/<stem>")
    parser.add_argument("--metadata", type=Path, help="corrected Alexandria metadata JSON")
    parser.add_argument("--bibtex", type=Path, help="corrected one-entry BibTeX file")
    parser.add_argument("--library", type=Path, help="Alexandria library root; defaults to the parent of _hold")
    parser.add_argument("--dry-run", action="store_true", help="print planned key/paths/actions without changing disk state")
    args = parser.parse_args(argv)

    if args.metadata is None and args.bibtex is None:
        parser.error("at least one of --metadata or --bibtex is required")

    hold_dir = args.held_work.resolve()
    library_root = infer_library_root(hold_dir, args.library)
    try:
        category, key = promote_hold(
            hold_dir=hold_dir,
            library_root=library_root,
            metadata_path=args.metadata.resolve() if args.metadata else None,
            bibtex_path=args.bibtex.resolve() if args.bibtex else None,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(format_plan(hold_dir=hold_dir, library_root=library_root, category=category, key=key, dry_run=args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
