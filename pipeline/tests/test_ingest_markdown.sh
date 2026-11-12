#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
LIB="$TMP/library"
mkdir -p "$LIB"

INGEST="$ROOT/pipeline/alexandria_ingest_markdown.py"

MD="$TMP/note.md"
cat > "$MD" <<'MD'
# A Podcast Episode About Rockets

Some notes taken while listening to a podcast about rocket propulsion.

More body text so the chunker has something real to split across a heading.
MD

# --- 1. --dry-run writes NOTHING ---
out="$("$INGEST" --md "$MD" --library "$LIB" --url "https://example.test/ep1" --dry-run)"
echo "$out"
grep -q '^key: apodcastepisodeaboutrockets' <<<"$out"
grep -q '^metadata_confidence: high' <<<"$out"
grep -q '^chunk_count: [1-9]' <<<"$out"
[ -z "$(find "$LIB" -mindepth 1 2>/dev/null)" ] || { echo "dry-run wrote files"; exit 1; }

# --- 2. real ingest: filed under misc, metadata kind=informal, high confidence ---
"$INGEST" --md "$MD" --library "$LIB" --url "https://example.test/ep1"

KEY="apodcastepisodeaboutrockets"
test -f "$LIB/misc/$KEY.md"
test -f "$LIB/misc/$KEY.bib"
test -f "$LIB/misc/$KEY/metadata.json"
test -f "$LIB/misc/$KEY/chunks.jsonl"
test -f "$LIB/misc/$KEY/run_meta.json"
[ -s "$LIB/misc/$KEY/chunks.jsonl" ] || { echo "chunks.jsonl is empty"; exit 1; }
! test -e "$LIB/misc/$KEY/embeddings.jsonl" || { echo "embeddings.jsonl should not be written"; exit 1; }

grep -q '"kind": "informal"' "$LIB/misc/$KEY/metadata.json"
grep -q '"metadata_confidence": "high"' "$LIB/misc/$KEY/metadata.json"
grep -q "^@online{$KEY," "$LIB/misc/$KEY.bib"

# --- 3. validate_work_md passes on the filed work ---
"$ROOT/pipeline/alexandria_validate_work.py" --library "$LIB"

# --- 4. re-running with the same .md is idempotent: same key, no duplicate ---
"$INGEST" --md "$MD" --library "$LIB" --url "https://example.test/ep1"
misc_entries="$(find "$LIB/misc" -maxdepth 1 -name "${KEY}*" | wc -l)"
[ "$misc_entries" -eq 3 ] || { echo "expected 3 misc entries for $KEY, got $misc_entries"; find "$LIB/misc"; exit 1; }
registry_entries="$(grep -c '"key"' "$LIB/key_registry.json")"
[ "$registry_entries" -eq 1 ] || { echo "expected 1 key_registry entry, got $registry_entries"; exit 1; }

# --- 5. no --url: @misc entry type instead of @online ---
MD2="$TMP/notes2.md"
cat > "$MD2" <<'MD'
# Generated Notes on Orbital Mechanics

Body text with no URL, so this should default source to "generated".
MD
"$INGEST" --md "$MD2" --library "$LIB" --category misc
KEY2="generatednotesonorbitalmechanics"
test -f "$LIB/misc/$KEY2.bib"
grep -q "^@misc{$KEY2," "$LIB/misc/$KEY2.bib"
grep -q '"source": "generated"' "$LIB/misc/$KEY2/metadata.json"
"$ROOT/pipeline/alexandria_validate_work.py" --library "$LIB"

# --- 6. A) academic @article ---
MD_ARTICLE="$TMP/article.md"
cat > "$MD_ARTICLE" <<'MD'
# Sample Article

Body of a sample journal article for bibliographic field tests.
MD
"$INGEST" --md "$MD_ARTICLE" --library "$LIB" --kind academic --year 2023 \
  --authors 'X Y; Z W' --journal 'Aerospace' --doi '10.3390/aerospace10120979'
KEY_ARTICLE="y2023"
test -f "$LIB/misc/$KEY_ARTICLE.bib"
grep -q "^@article{$KEY_ARTICLE," "$LIB/misc/$KEY_ARTICLE.bib"
grep -q '"journal": "Aerospace"' "$LIB/misc/$KEY_ARTICLE/metadata.json"
grep -q '"doi": "10.3390/aerospace10120979"' "$LIB/misc/$KEY_ARTICLE/metadata.json"
python3 -c "
import json
m = json.loads(open('$LIB/misc/$KEY_ARTICLE/metadata.json').read())
assert m['authors'] == ['X Y', 'Z W'], m['authors']
assert m['author'] == 'X Y', m['author']
"
"$ROOT/pipeline/alexandria_validate_work.py" --library "$LIB"

# --- 7. B) academic @inproceedings ---
MD_PROC="$TMP/proc.md"
cat > "$MD_PROC" <<'MD'
# Conference Paper

Body of a sample conference paper for booktitle filing.
MD
"$INGEST" --md "$MD_PROC" --library "$LIB" --kind academic --year 2003 \
  --authors 'A; B' --booktitle '39th AIAA JPC'
KEY_PROC="a2003"
test -f "$LIB/misc/$KEY_PROC.bib"
grep -q "^@inproceedings{$KEY_PROC," "$LIB/misc/$KEY_PROC.bib"
grep -q '"booktitle": "39th AIAA JPC"' "$LIB/misc/$KEY_PROC/metadata.json"
"$ROOT/pipeline/alexandria_validate_work.py" --library "$LIB"

# --- 8. C) academic @book ---
# "Author" alone trips the role-label confidence gate; Jane Author → key author2017
MD_BOOK="$TMP/book.md"
cat > "$MD_BOOK" <<'MD'
# My Book

Body of a sample book for isbn/publisher filing.
MD
"$INGEST" --md "$MD_BOOK" --library "$LIB" --kind academic --year 2017 \
  --authors 'Jane Author' --isbn '978-1-4987-8535-8' --publisher 'CRC Press'
KEY_BOOK="author2017"
test -f "$LIB/misc/$KEY_BOOK.bib"
grep -q "^@book{$KEY_BOOK," "$LIB/misc/$KEY_BOOK.bib"
grep -q '"isbn": "978-1-4987-8535-8"' "$LIB/misc/$KEY_BOOK/metadata.json"
grep -q '"publisher": "CRC Press"' "$LIB/misc/$KEY_BOOK/metadata.json"
"$ROOT/pipeline/alexandria_validate_work.py" --library "$LIB"

# --- 9. D) academic @misc ---
MD_MISC="$TMP/misc.md"
cat > "$MD_MISC" <<'MD'
# Institutional Report

Body of an institutional report with no typed bibliographic fields.
MD
"$INGEST" --md "$MD_MISC" --library "$LIB" --kind academic --year 2020 \
  --authors 'Org'
KEY_MISC="org2020"
test -f "$LIB/misc/$KEY_MISC.bib"
grep -q "^@misc{$KEY_MISC," "$LIB/misc/$KEY_MISC.bib"
! grep -q '"journal"' "$LIB/misc/$KEY_MISC/metadata.json"
! grep -q '"booktitle"' "$LIB/misc/$KEY_MISC/metadata.json"
! grep -q '"isbn"' "$LIB/misc/$KEY_MISC/metadata.json"
"$ROOT/pipeline/alexandria_validate_work.py" --library "$LIB"

# --- 10. --authors wins over --author ---
MD_BOTH="$TMP/both.md"
cat > "$MD_BOTH" <<'MD'
# Both Author Flags

Body text for --author vs --authors precedence.
MD
"$INGEST" --md "$MD_BOTH" --library "$LIB" --kind academic --year 2019 \
  --author 'Loser Name' --authors 'Winner Person' --journal 'Test Journal'
KEY_BOTH="person2019"
python3 -c "
import json
m = json.loads(open('$LIB/misc/$KEY_BOTH/metadata.json').read())
assert m['authors'] == ['Winner Person'], m['authors']
assert m['author'] == 'Winner Person', m['author']
"
grep -q "^@article{$KEY_BOTH," "$LIB/misc/$KEY_BOTH.bib"

echo "OK"
