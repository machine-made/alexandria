#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

DROP="$TMP/DROP_HERE"
PARSED="$TMP/parsed"
LIB="$TMP/library"
OUT="$TMP/audit"
mkdir -p "$DROP" "$PARSED/work" "$PARSED/unknown" "$LIB"

printf work-pdf > "$DROP/work.pdf"
cat > "$PARSED/work/work.md" <<'MD'
# A Small Paper

Author: Ada Lovelace
Year: 1843
Category: papers
DOI: 10.1145/3133956.3134047
MD

printf unknown-pdf > "$DROP/unknown.pdf"
cat > "$PARSED/unknown/unknown.md" <<'MD'
# unknown

OCR fragments without author or year.
MD

printf new-pdf > "$DROP/new_only.pdf"

printf mixed-pdf > "$DROP/mixed.Pdf"

printf legacy-pdf > "$DROP/legacy.pdf"
mkdir -p "$PARSED/legacy"
cat > "$PARSED/legacy/legacy.md" <<'MD'
# Legacy Metadata Paper

Author: Katherine Johnson
Year: 1969
Category: papers
DOI: 10.5555/12345678
MD
cat > "$PARSED/legacy/metadata.json" <<'JSON'
{"title":"Legacy Metadata Paper","author":"Katherine Johnson","year":"1969","category":"papers","metadata_source":"heuristic","metadata_confidence":"medium"}
JSON

printf duplicate-pdf > "$DROP/duplicate.pdf"
sha="$(sha256sum "$DROP/duplicate.pdf" | awk '{print $1}')"
mkdir -p "$LIB/papers/existingduplicate"
cat > "$LIB/papers/existingduplicate/metadata.json" <<JSON
{"source_sha256":{"existingduplicate.pdf":"$sha"}}
JSON

"$ROOT/pipeline/alexandria_audit_drop.py" --drop "$DROP" --parsed "$PARSED" --library "$LIB" --out-dir "$OUT" > "$TMP/stdout.tsv"

test -f "$OUT/drop_audit.jsonl"
test -f "$OUT/drop_audit.tsv"
grep -q $'work\t' "$OUT/drop_audit.tsv"
grep -q $'unknown\t' "$OUT/drop_audit.tsv"
grep -q $'new_only\t' "$OUT/drop_audit.tsv"
grep -q $'mixed\t' "$OUT/drop_audit.tsv"
grep -q $'legacy\t' "$OUT/drop_audit.tsv"
grep -q $'duplicate\t' "$OUT/drop_audit.tsv"

python3 - <<PY
import json
from pathlib import Path
rows=[json.loads(line) for line in Path('$OUT/drop_audit.jsonl').read_text().splitlines()]
by={row['stem']: row for row in rows}
assert by['work']['action'] == 'file'
assert by['work']['key'] == 'lovelace1843'
assert by['work']['metadata_confidence'] == 'high'
assert by['work']['doi'] == '10.1145/3133956.3134047'
assert by['unknown']['action'] == 'hold'
assert by['unknown']['metadata_confidence'] == 'low'
assert by['new_only']['action'] == 'parse-needed'
assert by['new_only']['parsed_markdown'] is False
assert by['mixed']['action'] == 'parse-needed'
assert by['mixed']['formats'] == ['pdf']
assert by['legacy']['action'] == 'file'
assert by['legacy']['metadata_confidence'] == 'high'
assert by['legacy']['doi'] == '10.5555/12345678'
assert by['duplicate']['action'] == 'duplicate'
assert by['duplicate']['parsed_markdown'] is False
assert by['duplicate']['duplicate_of'] == {'category': 'papers', 'key': 'existingduplicate'}
PY

COL_DROP="$TMP/collision_DROP_HERE"
COL_PARSED="$TMP/collision_parsed"
COL_LIB="$TMP/collision_library"
COL_OUT="$TMP/collision_audit"
mkdir -p "$COL_DROP" "$COL_PARSED/collision" "$COL_LIB/papers/lovelace1843"
mkdir -p "$COL_PARSED/collision2"
printf existing-pdf > "$COL_LIB/papers/lovelace1843/lovelace1843.pdf"
cat > "$COL_LIB/papers/lovelace1843/metadata.json" <<'JSON'
{"source_sha256":{"lovelace1843.pdf":"existing-sha"}}
JSON
touch "$COL_LIB/papers/lovelace1843.md"
printf collision-pdf > "$COL_DROP/collision.pdf"
printf collision2-pdf > "$COL_DROP/collision2.pdf"
cat > "$COL_PARSED/collision/collision.md" <<'MD'
# Another Small Paper

Author: Ada Lovelace
Year: 1843
Category: papers
MD
cat > "$COL_PARSED/collision2/collision2.md" <<'MD'
# Yet Another Small Paper

Author: Ada Lovelace
Year: 1843
Category: papers
MD

"$ROOT/pipeline/alexandria_audit_drop.py" --drop "$COL_DROP" --parsed "$COL_PARSED" --library "$COL_LIB" --out-dir "$COL_OUT" > "$TMP/collision_stdout.tsv"
python3 - <<PY
import json
from pathlib import Path
rows=[json.loads(line) for line in Path('$COL_OUT/drop_audit.jsonl').read_text().splitlines()]
by={row['stem']: row for row in rows}
assert by['collision']['action'] == 'file'
assert by['collision']['key'] == 'lovelace1843a'
assert by['collision2']['action'] == 'file'
assert by['collision2']['key'] == 'lovelace1843b'
PY

python3 - <<PY
import sys
from pathlib import Path

sys.path.insert(0, '$ROOT/pipeline')
from alexandria_metadata_common import unique_key

base = Path('$TMP/many_collisions')
base.mkdir()
candidate = 'smith2020'
(base / f'{candidate}.md').touch()
for suffix in 'abcdefghijklmnopqrstuvwxyz':
    (base / f'{candidate}{suffix}.md').touch()
assert unique_key(base, candidate) == 'smith2020aa'
PY
