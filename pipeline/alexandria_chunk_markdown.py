#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Create deterministic JSONL chunks from one Marker Markdown artifact."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

DEFAULT_MAX_CHARS = 2800
OVERLAP_CHARS = 350


def artifact_dir() -> Path:
    return Path(os.environ["ALEXANDRIA_ARTIFACT_DIR"]).resolve()


def markdown_path(root: Path) -> Path:
    explicit = os.environ.get("ALEXANDRIA_MARKDOWN_PATH")
    if explicit:
        return Path(explicit).resolve()
    candidates = sorted(p for p in root.glob("*.md") if p.is_file())
    if not candidates:
        raise FileNotFoundError(f"no Markdown file in {root}")
    return candidates[0]


def split_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    in_table = False
    for line in text.splitlines():
        is_table = line.strip().startswith("|") and "|" in line.strip()[1:]
        if is_table:
            if current and not in_table:
                blocks.append("\n".join(current).strip())
                current = []
            in_table = True
            current.append(line)
            continue
        if in_table:
            blocks.append("\n".join(current).strip())
            current = []
            in_table = False
        if line.strip():
            current.append(line)
        elif current:
            blocks.append("\n".join(current).strip())
            current = []
    if current:
        blocks.append("\n".join(current).strip())
    return [b for b in blocks if b]


def chunks_from_blocks(blocks: list[str], max_chars: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for block in blocks:
        if len(block) > max_chars:
            if current:
                chunks.append(current.strip())
                current = ""
            start = 0
            while start < len(block):
                end = min(len(block), start + max_chars)
                chunks.append(block[start:end].strip())
                if end == len(block):
                    break
                start = max(0, end - OVERLAP_CHARS)
            continue
        candidate = block if not current else current + "\n\n" + block
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                chunks.append(current.strip())
            current = block
    if current:
        chunks.append(current.strip())
    return chunks


def update_heading_path(path: list[str], line: str) -> list[str]:
    match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
    if not match:
        return path
    level = len(match.group(1))
    heading = re.sub(r"<[^>]+>", "", match.group(2)).strip(" *_")
    new_path = path[: level - 1]
    new_path.append(heading)
    return new_path


def build_chunks(text: str, work_id: str, max_chars: int) -> list[dict[str, object]]:
    heading_path: list[str] = []
    section_lines: list[str] = []
    sections: list[tuple[list[str], str]] = []

    def flush() -> None:
        body = "\n".join(section_lines).strip()
        if body:
            sections.append((heading_path.copy(), body))

    for line in text.splitlines():
        if re.match(r"^#{1,6}\s+", line):
            flush()
            heading_path = update_heading_path(heading_path, line)
            section_lines = [line]
        else:
            section_lines.append(line)
    flush()

    rows: list[dict[str, object]] = []
    index = 0
    for path, section in sections:
        for chunk_text in chunks_from_blocks(split_blocks(section), max_chars):
            digest = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()[:16]
            rows.append(
                {
                    "chunk_id": f"{work_id}:{index:04d}:{digest}",
                    "work_id": work_id,
                    "chunk_index": index,
                    "heading_path": path,
                    "char_count": len(chunk_text),
                    "token_estimate": max(1, len(chunk_text) // 4),
                    "text": chunk_text,
                }
            )
            index += 1
    return rows


def main() -> int:
    root = artifact_dir()
    md = markdown_path(root)
    text = md.read_text(encoding="utf-8", errors="replace")
    work_id = os.environ.get("ALEXANDRIA_WORK_ID") or root.name
    max_chars = int(os.environ.get("ALEXANDRIA_CHUNK_MAX_CHARS", str(DEFAULT_MAX_CHARS)))
    rows = build_chunks(text, work_id, max_chars)
    out = Path(os.environ.get("ALEXANDRIA_CHUNKS_JSONL", str(root / "chunks.jsonl"))).resolve()
    out.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
