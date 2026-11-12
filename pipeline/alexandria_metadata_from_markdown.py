#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Extract conservative metadata from one parsed Markdown work."""
from __future__ import annotations

import json
import lzma
import os
import zipfile
import zlib
from pathlib import Path
from xml.etree import ElementTree

from alexandria_epub_meta import write_epub_metadata
from alexandria_metadata_common import (
    enrich_metadata_from_doi,
    infer_metadata,
    pdf_meta_path_for_work,
    sha256_file,
    source_files_for_stem,
    write_json,
)


def epub_metadata_for_work(work: Path, drop_dir: Path | None, stem: str) -> dict[str, object]:
    if drop_dir is None or not drop_dir.is_dir():
        return {}
    sources = sorted(
        (path for path in drop_dir.iterdir() if path.is_file() and path.stem == stem and path.suffix.lower() == ".epub"),
        key=lambda path: path.name.lower(),
    )
    if not sources:
        return {}
    source = sources[0]
    nested = work / stem
    artifact = (nested if nested.is_dir() else work) / "epub_meta.json"
    source_sha = sha256_file(source)
    if artifact.is_file():
        try:
            cached = json.loads(artifact.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cached = {}
        cached_source = cached.get("source") if isinstance(cached, dict) else None
        if isinstance(cached_source, dict) and cached_source.get("sha256") == source_sha:
            return cached
    try:
        write_epub_metadata(source, artifact)
        return json.loads(artifact.read_text(encoding="utf-8"))
    except (
        OSError,
        ValueError,
        zipfile.BadZipFile,
        ElementTree.ParseError,
        KeyError,
        NotImplementedError,
        RuntimeError,
        zlib.error,
        lzma.LZMAError,
    ):
        return {}


def main() -> int:
    work = Path(os.environ["ALEXANDRIA_PARSED_WORK"]).resolve()
    stem = work.name
    drop_dir = Path(os.environ["ALEXANDRIA_DROP_DIR"]).resolve() if os.environ.get("ALEXANDRIA_DROP_DIR") else None
    md = work / f"{stem}.md"
    text = md.read_text(encoding="utf-8", errors="replace")
    source_files = source_files_for_stem(drop_dir, stem)
    epub_meta = epub_metadata_for_work(work, drop_dir, stem)
    metadata = infer_metadata(
        text,
        stem,
        source_files,
        pdf_meta_path=pdf_meta_path_for_work(work, stem),
        epub_meta=epub_meta,
    )
    # Opt-in additive DOI gap-fill (ALEXANDRIA_RESOLVE_ENRICH); off by default.
    metadata = enrich_metadata_from_doi(metadata)
    write_json(work / "metadata.json", metadata)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
