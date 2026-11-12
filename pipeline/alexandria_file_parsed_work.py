#!/usr/bin/env python3
"""File one parsed Alexandria work using environment-provided paths."""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from alexandria_library import default_library
from alexandria_bibtex import bibtex_entry, rebuild_root_bibliography
from alexandria_key_registry import KeyRegistry
from alexandria_metadata_common import (
    VALID_CATEGORIES,
    base_key,
    find_existing_by_sha,
    global_filed_keys,
    iter_source_paths,
    is_low_confidence,
    is_local_marker_image,
    jsonl_count,
    load_json,
    marker_image_targets,
    merge_metadata,
    metadata_sha_set,
    sha256_file,
    source_file_rows,
    source_files_for_stem,
    source_sha256_map,
    write_json,
)


def log(path: Path, message: str) -> None:
    from datetime import datetime, timezone

    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{stamp}] {message}\n")
    print(f"[{stamp}] {message}")


def artifact_root(parsed_work: Path) -> Path:
    nested = parsed_work / parsed_work.name
    if nested.is_dir():
        return nested
    return parsed_work


def should_copy_artifact(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    if path.name == "metadata.json":
        return False
    if path.name == "file_result.json":
        return False
    if path.suffix.lower() == ".md":
        return False
    return True


def copy_artifacts(parsed_work: Path, work_dir: Path, *, overwrite: bool = True) -> None:
    root = artifact_root(parsed_work)
    for src in sorted(root.rglob("*")):
        rel = src.relative_to(root)
        dst = work_dir / rel
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
            continue
        if should_copy_artifact(src):
            if dst.exists() and not overwrite:
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def rewrite_local_image_links(text: str, key: str) -> str:
    from alexandria_metadata_common import HTML_SRC_RE, MARKDOWN_IMAGE_RE

    def rewrite_markdown(match):
        prefix, target, suffix = match.groups()
        if is_local_marker_image(target):
            return f"{prefix}{key}/{target}{suffix}"
        return match.group(0)

    def rewrite_html_src(match):
        prefix, target, suffix = match.groups()
        if is_local_marker_image(target):
            return f"{prefix}{key}/{target}{suffix}"
        return match.group(0)

    text = MARKDOWN_IMAGE_RE.sub(rewrite_markdown, text)
    return HTML_SRC_RE.sub(rewrite_html_src, text)


def missing_marker_images(text: str, parsed_work: Path) -> list[str]:
    root = artifact_root(parsed_work)
    missing: list[str] = []
    for target in marker_image_targets(text):
        if not (root / target).is_file():
            missing.append(target)
    return missing


def validate_parsed_artifacts(parsed_work: Path) -> None:
    root = artifact_root(parsed_work)
    chunks = root / "chunks.jsonl"
    embeddings = root / "embeddings.jsonl"
    run_meta = root / "run_meta.json"
    chunk_count = jsonl_count(chunks)
    embedding_count = jsonl_count(embeddings)
    if chunks.is_file() and chunk_count == 0:
        raise RuntimeError(f"{chunks}: chunks.jsonl is empty")
    if embeddings.is_file() and not chunks.is_file():
        raise RuntimeError(f"{embeddings}: embeddings.jsonl exists without chunks.jsonl")
    if embeddings.is_file() and embedding_count != chunk_count:
        raise RuntimeError(f"{embeddings}: embedding count {embedding_count} != chunk count {chunk_count}")
    if (chunks.is_file() or embeddings.is_file()) and not run_meta.is_file():
        raise RuntimeError(f"{root}: missing run_meta.json")


def source_paths(drop_dir: Path, stem: str) -> list[Path]:
    return iter_source_paths(drop_dir, stem)


def write_result(parsed_work: Path, result: dict[str, object]) -> None:
    (parsed_work / "file_result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def hold_work(
    *,
    parsed_work: Path,
    drop_dir: Path,
    library_root: Path,
    metadata: dict[str, object],
    reason: str,
    log_file: Path,
) -> None:
    stem = parsed_work.name
    hold_dir = library_root / "_hold" / stem
    tmp_dir = library_root / "_hold" / f".{stem}.tmp-hold"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True)
    (tmp_dir / "hold_reason.txt").write_text(reason + "\n", encoding="utf-8")
    write_json(tmp_dir / "metadata.json", metadata)
    md_src = parsed_work / f"{stem}.md"
    if md_src.is_file():
        shutil.copy2(md_src, tmp_dir / md_src.name)
    parsed_copy = tmp_dir / "parsed"
    shutil.copytree(parsed_work, parsed_copy, dirs_exist_ok=True)
    for src in source_paths(drop_dir, stem):
        shutil.copy2(src, tmp_dir / src.name)
    if hold_dir.exists():
        shutil.rmtree(hold_dir)
    tmp_dir.rename(hold_dir)
    write_result(parsed_work, {"status": "held", "hold_dir": str(hold_dir), "reason": reason})
    log(log_file, f"HELD {stem} reason={reason}")


def resolve_key(library_root: Path, metadata: dict[str, object]) -> tuple[str, str]:
    sha_values = metadata_sha_set(metadata)
    category = str(metadata.get("category") or "misc").strip().lower()
    if category not in VALID_CATEGORIES:
        category = "misc"
    category_dir = library_root / category
    category_dir.mkdir(parents=True, exist_ok=True)
    candidate = base_key(str(metadata.get("author", "unknown")), str(metadata.get("year", "nodate")))

    registry = KeyRegistry.load(library_root)
    existing = registry.lookup_by_sha(sha_values)
    if existing:
        registry.record_existing(sha_values, existing[0], existing[1], candidate)
        return existing

    legacy_existing = find_existing_by_sha(library_root, sha_values)
    if legacy_existing:
        registry.record_existing(sha_values, legacy_existing[0], legacy_existing[1], candidate)
        return legacy_existing

    reserved = global_filed_keys(library_root) | registry.reserved_keys()
    return registry.reserve_key(sha_values, category, candidate, category_dir=category_dir, reserved_keys=reserved)


def copy_sources_to_stage(source_list: list[Path], stage_dir: Path, key: str, metadata: dict[str, object]) -> None:
    source_rows = source_file_rows(metadata.get("source_files"))
    by_name = {str(row.get("name")): row for row in source_rows}
    for src in source_list:
        dst = stage_dir / f"{key}.{src.suffix.lower().lstrip('.')}"
        if dst.exists() and sha256_file(dst) != sha256_file(src):
            raise RuntimeError(f"refusing to overwrite existing source copy with different content: {dst}")
        tmp = dst.with_name(f".{dst.name}.tmp-filing")
        if tmp.exists():
            tmp.unlink()
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
        if dst.stat().st_size != src.stat().st_size:
            raise RuntimeError(f"size mismatch while filing {src}")
        if src.name in by_name:
            by_name[src.name]["filed_name"] = dst.name
    metadata["source_files"] = sorted(by_name.values(), key=lambda row: str(row.get("name", "")).lower())
    metadata["source_sha256"] = source_sha256_map(source_file_rows(metadata.get("source_files")))


def preserve_existing_source_records(
    metadata: dict[str, object], existing_metadata: dict[str, object], stage_dir: Path
) -> None:
    rows_by_name: dict[str, dict[str, object]] = {}
    for row in source_file_rows(existing_metadata.get("source_files")):
        name = row.get("name")
        if not name:
            continue
        filed_name = row.get("filed_name")
        if filed_name and not (stage_dir / str(filed_name)).is_file():
            continue
        rows_by_name[str(name)] = row
    for row in source_file_rows(metadata.get("source_files")):
        name = row.get("name")
        if name:
            rows_by_name[str(name)] = row
    source_rows = sorted(rows_by_name.values(), key=lambda row: str(row.get("name", "")).lower())
    metadata["source_files"] = source_rows
    metadata["source_sha256"] = source_sha256_map(source_rows)


def install_staged_work(stage_dir: Path, work_dir: Path, md_tmp: Path, md_dst: Path, bib_tmp: Path, bib_dst: Path) -> None:
    if work_dir.exists():
        raise RuntimeError(f"refusing to replace existing work directory: {work_dir}")
    stage_dir.rename(work_dir)
    md_tmp.replace(md_dst)
    bib_tmp.replace(bib_dst)


def main() -> int:
    parsed_work = Path(os.environ["ALEXANDRIA_PARSED_WORK"]).resolve()
    library_root = Path(os.environ.get("ALEXANDRIA_LIBRARY_ROOT") or default_library()).resolve()
    drop_dir = Path(os.environ.get("ALEXANDRIA_DROP_DIR", str(library_root / "DROP_HERE"))).resolve()
    log_file = Path(os.environ.get("ALEXANDRIA_FILE_LOG", str(library_root / "pipeline" / "logs" / "file-parsed.log"))).resolve()

    stem = parsed_work.name
    md_src = parsed_work / f"{stem}.md"
    meta_src = parsed_work / "metadata.json"
    if not md_src.is_file() or md_src.stat().st_size == 0:
        raise FileNotFoundError(f"missing parsed markdown: {md_src}")
    if not meta_src.is_file():
        raise FileNotFoundError(f"missing metadata: {meta_src}")

    try:
        metadata = merge_metadata(load_json(meta_src), stem, source_files_for_stem(drop_dir, stem))
    except ValueError as exc:
        raise ValueError(f"invalid metadata JSON: {meta_src}") from exc
    write_json(meta_src, metadata)

    existing_by_sha = find_existing_by_sha(library_root, metadata_sha_set(metadata))
    if is_low_confidence(metadata) and not existing_by_sha:
        hold_work(
            parsed_work=parsed_work,
            drop_dir=drop_dir,
            library_root=library_root,
            metadata=metadata,
            reason="low-confidence metadata",
            log_file=log_file,
        )
        return 0

    sources = source_paths(drop_dir, stem)
    if not sources:
        hold_work(
            parsed_work=parsed_work,
            drop_dir=drop_dir,
            library_root=library_root,
            metadata=metadata,
            reason="missing source files in DROP_HERE",
            log_file=log_file,
        )
        return 0

    text = md_src.read_text(encoding="utf-8", errors="replace")
    missing_images = missing_marker_images(text, parsed_work)
    if missing_images:
        raise RuntimeError(f"missing local Marker image artifacts: {', '.join(sorted(missing_images))}")
    validate_parsed_artifacts(parsed_work)

    category, key = resolve_key(library_root, metadata)
    category_dir = library_root / category
    category_dir.mkdir(parents=True, exist_ok=True)
    work_dir = category_dir / key
    stage_dir = category_dir / f".{key}.tmp-filing"
    md_dst = category_dir / f"{key}.md"
    md_tmp = category_dir / f".{key}.md.tmp"
    bib_dst = category_dir / f"{key}.bib"
    bib_tmp = category_dir / f".{key}.bib.tmp"
    existing_metadata: dict[str, object] = {}
    existing_meta_path = work_dir / "metadata.json"
    if existing_meta_path.is_file():
        existing_metadata = load_json(existing_meta_path)

    if work_dir.exists():
        partial_install = not md_dst.is_file() or not bib_dst.is_file()
        existing_stem = str(existing_metadata.get("source_stem") or stem)
        metadata = merge_metadata(existing_metadata, existing_stem, source_files_for_stem(drop_dir, stem))
        if partial_install:
            copy_artifacts(parsed_work, work_dir, overwrite=False)
        copy_sources_to_stage(sources, work_dir, key, metadata)
        preserve_existing_source_records(metadata, existing_metadata, work_dir)
        write_json(work_dir / "metadata.json", metadata)
        if partial_install and not md_dst.is_file():
            md_tmp.write_text(rewrite_local_image_links(text, key), encoding="utf-8")
            md_tmp.replace(md_dst)
        if partial_install and not bib_dst.is_file():
            bib_tmp.write_text(bibtex_entry(key, metadata), encoding="utf-8")
            bib_tmp.replace(bib_dst)
        for src in sources:
            src.unlink()
        rebuild_root_bibliography(library_root)
        write_result(parsed_work, {"status": "filed", "key": key, "category": category})
        log(log_file, f"FILED {stem} key={key} category={category}")
        return 0

    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True)

    try:
        if work_dir.exists():
            shutil.copytree(work_dir, stage_dir, dirs_exist_ok=True)
        copy_artifacts(parsed_work, stage_dir)
        copy_sources_to_stage(sources, stage_dir, key, metadata)
        preserve_existing_source_records(metadata, existing_metadata, stage_dir)
        write_json(stage_dir / "metadata.json", metadata)
        md_tmp.write_text(rewrite_local_image_links(text, key), encoding="utf-8")
        bib_tmp.write_text(bibtex_entry(key, metadata), encoding="utf-8")
        install_staged_work(stage_dir, work_dir, md_tmp, md_dst, bib_tmp, bib_dst)
    except Exception:
        if stage_dir.exists():
            shutil.rmtree(stage_dir)
        for tmp in (md_tmp, bib_tmp):
            if tmp.exists():
                tmp.unlink()
        raise

    for src in sources:
        src.unlink()
    rebuild_root_bibliography(library_root)
    write_result(parsed_work, {"status": "filed", "key": key, "category": category})
    log(log_file, f"FILED {stem} key={key} category={category}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
