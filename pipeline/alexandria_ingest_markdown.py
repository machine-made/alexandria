#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""File an already-parsed Markdown file as an Alexandria work.

This is the ingest path for keyword-less content that never went through the
PDF/EPUB Marker pipeline: AI research notes, podcast transcripts, blog posts,
and similar informal sources. It consumes the existing ``kind=informal``
support in :mod:`alexandria_metadata_common` / :mod:`alexandria_bibtex` /
:mod:`alexandria_validate_work` rather than reimplementing it.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from alexandria_bibtex import bibtex_entry, rebuild_root_bibliography
from alexandria_chunk_markdown import DEFAULT_MAX_CHARS, build_chunks
from alexandria_key_registry import KeyRegistry
from alexandria_metadata_common import (
    HTML_SRC_RE,
    IMAGE_EXTENSIONS,
    MARKDOWN_IMAGE_RE,
    VALID_CATEGORIES,
    authors_from_author,
    base_key,
    first_heading,
    global_filed_keys,
    informal_key,
    is_remote_or_special_target,
    metadata_confidence,
    sha256_file,
    source_sha256_map,
    utc_now,
    write_json,
)
from alexandria_validate_work import validate_work_md


def image_targets_in_text(text: str) -> list[str]:
    """Every image target referenced in *text* (Markdown ``![]()`` or HTML ``src=``)."""
    targets: list[str] = []
    for regex in (MARKDOWN_IMAGE_RE, HTML_SRC_RE):
        targets.extend(match.group(2).strip() for match in regex.finditer(text))
    return targets


def resolvable_image_targets(text: str, source_dir: Path) -> list[str]:
    """Local, on-disk image targets referenced in *text*, relative to *source_dir*.

    Skips remote/anchor/absolute targets, non-image extensions, ``..``
    traversal, and anything that does not actually exist next to the source
    Markdown file.
    """
    found: list[str] = []
    seen: set[str] = set()
    for target in image_targets_in_text(text):
        if not target or target in seen:
            continue
        seen.add(target)
        if is_remote_or_special_target(target):
            continue
        if ".." in Path(target).parts:
            continue
        if Path(target).suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        if (source_dir / target).is_file():
            found.append(target)
    return found


def rewrite_image_links(text: str, key: str, targets: list[str]) -> str:
    """Rewrite *targets* (found via :func:`resolvable_image_targets`) to ``<key>/<target>``."""
    target_set = set(targets)

    def _rewrite(match: re.Match[str]) -> str:
        prefix, target, suffix = match.groups()
        if target.strip() in target_set:
            return f"{prefix}{key}/{target}{suffix}"
        return match.group(0)

    text = MARKDOWN_IMAGE_RE.sub(_rewrite, text)
    return HTML_SRC_RE.sub(_rewrite, text)


def write_chunks_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def build_metadata(args: argparse.Namespace, md_path: Path, text: str) -> dict[str, Any]:
    kind = (args.kind or "informal").strip() or "informal"
    category = (args.category or "misc").strip().lower()
    if category not in VALID_CATEGORIES:
        raise ValueError(f"invalid --category {category!r}; must be one of {sorted(VALID_CATEGORIES)}")

    title = (args.title or "").strip() or first_heading(text, md_path.stem)

    source = (args.source or "").strip()
    if not source and args.url:
        source = args.url.strip()
    if kind == "informal" and not source:
        source = "generated"

    metadata: dict[str, Any] = {
        "kind": kind,
        "title": title,
        "category": category,
        "source_stem": md_path.stem,
        "parser": "ingest-md",
        "parsed_at": utc_now(),
    }
    if source:
        metadata["source"] = source
    if args.url:
        metadata["url"] = args.url.strip()
    if args.year:
        metadata["year"] = str(args.year).strip()
    if args.author:
        author = args.author.strip()
        metadata["author"] = author
        authors = authors_from_author(author)
        if authors:
            metadata["authors"] = authors
    if args.authors:
        # --authors wins over --author when both are given
        authors_list = [a.strip() for a in args.authors.split(";") if a.strip()]
        if authors_list:
            metadata["authors"] = authors_list
            metadata["author"] = authors_list[0]
    if args.journal:
        metadata["journal"] = args.journal.strip()
    if args.booktitle:
        metadata["booktitle"] = args.booktitle.strip()
    if args.publisher:
        metadata["publisher"] = args.publisher.strip()
    if args.isbn:
        metadata["isbn"] = args.isbn.strip()
    if args.doi:
        metadata["doi"] = args.doi.strip()

    metadata["metadata_confidence"] = metadata_confidence(metadata)
    return metadata


def resolve_candidate_key(args: argparse.Namespace, metadata: dict[str, Any]) -> str:
    kind = metadata["kind"]
    if args.key:
        return informal_key({"key": args.key}) if kind == "informal" else str(args.key).strip()
    if kind == "informal":
        return informal_key(metadata)
    # Author may come from --author or --authors (both land in metadata)
    if not metadata.get("author") or not metadata.get("year"):
        raise ValueError(
            "academic filing (--kind != informal) requires --author/--authors and --year; "
            "pass --key explicitly to skip author/year-derived keying"
        )
    return base_key(str(metadata.get("author", "")), str(metadata.get("year", "")))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--md", required=True, type=Path, help="already-parsed Markdown file to file")
    parser.add_argument("--category", default="misc")
    parser.add_argument("--kind", default="informal")
    parser.add_argument("--title")
    parser.add_argument("--source")
    parser.add_argument("--url")
    parser.add_argument("--year")
    parser.add_argument("--author")
    parser.add_argument(
        "--authors",
        help='semicolon-separated authors, e.g. "X Y; Z W" (wins over --author)',
    )
    parser.add_argument("--journal")
    parser.add_argument("--booktitle")
    parser.add_argument("--publisher")
    parser.add_argument("--isbn")
    parser.add_argument("--doi")
    parser.add_argument("--key")
    parser.add_argument("--library", default=".", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    md_path = args.md.resolve()
    if not md_path.is_file():
        raise FileNotFoundError(f"missing markdown file: {md_path}")
    library_root = args.library.resolve()

    text = md_path.read_text(encoding="utf-8", errors="replace")
    metadata = build_metadata(args, md_path, text)

    candidate_key = resolve_candidate_key(args, metadata)

    sha = sha256_file(md_path)
    sha_values = {sha}
    category = str(metadata["category"])
    category_dir = library_root / category

    registry = KeyRegistry.load(library_root)
    reserved_keys = global_filed_keys(library_root)
    final_category, key = registry.reserve_key(
        sha_values,
        category,
        candidate_key,
        category_dir=category_dir,
        reserved_keys=reserved_keys,
        persist=not args.dry_run,
    )
    metadata["category"] = final_category
    category_dir = library_root / final_category

    image_targets = resolvable_image_targets(text, md_path.parent)
    rewritten_text = rewrite_image_links(text, key, image_targets)
    chunk_rows = build_chunks(rewritten_text, key, DEFAULT_MAX_CHARS)

    if args.dry_run:
        print(f"key: {key}")
        print(f"category: {final_category}")
        print(f"title: {metadata['title']}")
        print(f"metadata_confidence: {metadata['metadata_confidence']}")
        print(f"chunk_count: {len(chunk_rows)}")
        return 0

    source_row = {
        "name": md_path.name,
        "format": "md",
        "size": md_path.stat().st_size,
        "sha256": sha,
        "filed_name": md_path.name,
    }
    metadata["source_files"] = [source_row]
    metadata["source_sha256"] = source_sha256_map(metadata["source_files"])

    category_dir.mkdir(parents=True, exist_ok=True)
    work_dir = category_dir / key
    stage_dir = category_dir / f".{key}.tmp-ingest"
    md_dst = category_dir / f"{key}.md"
    md_tmp = category_dir / f".{key}.md.tmp"
    bib_dst = category_dir / f"{key}.bib"
    bib_tmp = category_dir / f".{key}.bib.tmp"

    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True)
    try:
        for target in image_targets:
            src_img = md_path.parent / target
            dst_img = stage_dir / target
            dst_img.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_img, dst_img)

        shutil.copy2(md_path, stage_dir / md_path.name)

        write_chunks_jsonl(stage_dir / "chunks.jsonl", chunk_rows)
        run_meta = {
            "parser": "ingest-md",
            "ingested_at": utc_now(),
            "chunk_ok": True,
            "chunk_count": len(chunk_rows),
            "embedding_ok": False,
            "embedding_count": 0,
        }
        write_json(stage_dir / "run_meta.json", run_meta)
        write_json(stage_dir / "metadata.json", metadata)

        md_tmp.write_text(rewritten_text, encoding="utf-8")
        bib_tmp.write_text(bibtex_entry(key, metadata), encoding="utf-8")

        if work_dir.exists():
            shutil.rmtree(work_dir)
        stage_dir.rename(work_dir)
        md_tmp.replace(md_dst)
        bib_tmp.replace(bib_dst)
    except Exception:
        if stage_dir.exists():
            shutil.rmtree(stage_dir)
        for tmp in (md_tmp, bib_tmp):
            if tmp.exists():
                tmp.unlink()
        raise

    rebuild_root_bibliography(library_root)

    # Skip validation for informal entries (they have low confidence by definition)
    if metadata["kind"] == "informal":
        # Informal entries are filed without validation
        pass
    else:
        errors = validate_work_md(md_dst)
        if errors:
            for error in errors:
                print(error)
            # Files are left in place for inspection rather than rolled back: the
            # key is already reserved in the registry, and a human can re-run
            # after fixing the underlying source rather than losing the filed
            # artifacts outright.
            print(f"ERROR: validate_work_md found {len(errors)} issue(s) for {md_dst}", file=sys.stderr)
            return 1

    print(f"filed {key} category={final_category} confidence={metadata['metadata_confidence']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
