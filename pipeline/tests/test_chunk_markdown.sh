#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
ART="$TMP/artifact"
mkdir -p "$ART"
cat > "$ART/doc.md" <<'MD'
# Main Title

Intro paragraph for the whole work.

## Chapter One

First paragraph has enough content to become part of a chunk.

Second paragraph continues the same chapter with more content.

| A | B |
|---|---|
| 1 | 2 |

## Chapter Two

Final paragraph.
MD
ALEXANDRIA_ARTIFACT_DIR="$ART" ALEXANDRIA_CHUNK_MAX_CHARS=140 "$ROOT/pipeline/alexandria_chunk_markdown.py"
python3 - <<PY
import json
from pathlib import Path
rows=[json.loads(line) for line in Path('$ART/chunks.jsonl').read_text().splitlines()]
assert rows, 'no chunks emitted'
assert [r['chunk_index'] for r in rows] == list(range(len(rows)))
assert len({r['chunk_id'] for r in rows}) == len(rows)
assert all(r['work_id'] == 'artifact' for r in rows)
assert any(r['heading_path'] == ['Main Title', 'Chapter One'] for r in rows)
assert any('| A | B |' in r['text'] for r in rows), 'table should be preserved in a chunk'
first = Path('$ART/chunks.jsonl').read_text()
ALEXANDRIA_ARTIFACT_DIR = '$ART'
PY
cp "$ART/chunks.jsonl" "$TMP/first.jsonl"
ALEXANDRIA_ARTIFACT_DIR="$ART" ALEXANDRIA_CHUNK_MAX_CHARS=140 "$ROOT/pipeline/alexandria_chunk_markdown.py"
cmp "$TMP/first.jsonl" "$ART/chunks.jsonl"
ALEXANDRIA_ARTIFACT_DIR="$ART" ALEXANDRIA_WORK_ID="lovelace1843" ALEXANDRIA_CHUNK_MAX_CHARS=140 "$ROOT/pipeline/alexandria_chunk_markdown.py"
python3 - <<PY
import json
from pathlib import Path
rows=[json.loads(line) for line in Path('$ART/chunks.jsonl').read_text().splitlines()]
assert all(r['work_id'] == 'lovelace1843' for r in rows)
assert all(r['chunk_id'].startswith('lovelace1843:') for r in rows)
PY
