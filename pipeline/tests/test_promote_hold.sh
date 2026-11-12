#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TMP="$(mktemp -d "$ROOT/.tmp.promote.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
PROMOTE="$ROOT/pipeline/alexandria_promote_hold.py"
FILE_WORK="$ROOT/pipeline/alexandria_file_parsed_work.py"
VALIDATE="$ROOT/pipeline/alexandria_validate_work.py"

make_hold() {
    local base="$1" stem="$2" pdf_bytes="$3" epub_bytes="${4:-}"
    local parsed="$base/parsed" drop="$base/DROP_HERE" lib="$base/library" log="$base/file.log"
    mkdir -p "$parsed/$stem/$stem" "$drop" "$lib"
    cat > "$parsed/$stem/$stem.md" <<MD
# $stem

Held OCR body with a local Marker image.

![](_page_0_Picture_3.jpeg)
MD
    printf 'image-%s' "$stem" > "$parsed/$stem/$stem/_page_0_Picture_3.jpeg"
    printf '{"pages":["%s"]}\n' "$stem" > "$parsed/$stem/$stem/doc_meta.json"
    printf '{"chunk_id":"%s:0000","text":"Body"}\n' "$stem" > "$parsed/$stem/$stem/chunks.jsonl"
    printf '{"chunk_id":"%s:0000","embedding":[0.1,0.2]}\n' "$stem" > "$parsed/$stem/$stem/embeddings.jsonl"
    printf '{"embedding_ok":true,"embedding_count":1}\n' > "$parsed/$stem/$stem/run_meta.json"
    cat > "$parsed/$stem/metadata.json" <<JSON
{"title":"$stem","author":"unknown","year":"nodate","category":"misc","metadata_confidence":"low"}
JSON
    if [[ -n "$pdf_bytes" ]]; then
        printf '%s' "$pdf_bytes" > "$drop/$stem.pdf"
    fi
    if [[ -n "$epub_bytes" ]]; then
        printf '%s' "$epub_bytes" > "$drop/$stem.epub"
    fi
    ALEXANDRIA_PARSED_WORK="$parsed/$stem" \
    ALEXANDRIA_DROP_DIR="$drop" \
    ALEXANDRIA_LIBRARY_ROOT="$lib" \
    ALEXANDRIA_FILE_LOG="$log" \
    "$FILE_WORK" > "$base/hold.out"
    test -f "$lib/_hold/$stem/hold_reason.txt"
    test -f "$lib/_hold/$stem/metadata.json"
}

write_corrected_json() {
    local path="$1" title="$2" author="$3" year="$4" doi="$5"
    cat > "$path" <<JSON
{
  "title": "$title",
  "author": "$author",
  "authors": ["$author"],
  "year": "$year",
  "category": "papers",
  "doi": "$doi"
}
JSON
}

set_stale_weak_provenance() {
    local path="$1"
    python3 - <<PY
import json
from pathlib import Path
path = Path('$path')
metadata = json.loads(path.read_text())
metadata['metadata_field_sources'] = {'title': 'heuristic', 'author': 'manual_fallback'}
path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + '\n')
PY
}

# Explicit corrections replace stale weak provenance on an EPUB-only hold.
JSON_PROVENANCE="$TMP/json_provenance"
make_hold "$JSON_PROVENANCE" stale_json '' stale-json-epub
set_stale_weak_provenance "$JSON_PROVENANCE/library/_hold/stale_json/metadata.json"
write_corrected_json "$JSON_PROVENANCE/corrected.json" "Corrected JSON Work" "Katherine Johnson" 1961 "10.1000/corrected-json"
"$PROMOTE" "$JSON_PROVENANCE/library/_hold/stale_json" \
    --metadata "$JSON_PROVENANCE/corrected.json" --library "$JSON_PROVENANCE/library" \
    > "$JSON_PROVENANCE/promote.out"
grep -Fq 'promoted key=johnson1961 category=papers' "$JSON_PROVENANCE/promote.out"

BIB_PROVENANCE="$TMP/bib_provenance"
make_hold "$BIB_PROVENANCE" stale_bib '' stale-bib-epub
set_stale_weak_provenance "$BIB_PROVENANCE/library/_hold/stale_bib/metadata.json"
cat > "$BIB_PROVENANCE/corrected.bib" <<'BIB'
@article{corrected,
  title = {Corrected BibTeX Work},
  author = {Dorothy Vaughan},
  year = {1949},
  doi = {10.1000/corrected-bibtex}
}
BIB
"$PROMOTE" "$BIB_PROVENANCE/library/_hold/stale_bib" \
    --bibtex "$BIB_PROVENANCE/corrected.bib" --library "$BIB_PROVENANCE/library" \
    > "$BIB_PROVENANCE/promote.out"
grep -Fq 'promoted key=vaughan1949 category=papers' "$BIB_PROVENANCE/promote.out"
python3 - <<PY
import json
from pathlib import Path
json_sources = json.loads(Path('$JSON_PROVENANCE/library/papers/johnson1961/metadata.json').read_text())['metadata_field_sources']
bib_sources = json.loads(Path('$BIB_PROVENANCE/library/papers/vaughan1949/metadata.json').read_text())['metadata_field_sources']
assert json_sources['title'] == json_sources['author'] == 'corrected_json', json_sources
assert bib_sources['title'] == bib_sources['author'] == 'corrected_bibtex', bib_sources
PY

# Explicitly confirming unchanged citation fields replaces their stale weak provenance.
SAME_VALUE_PROVENANCE="$TMP/same_value_provenance"
make_hold "$SAME_VALUE_PROVENANCE" same_value '' same-value-epub
python3 - <<PY
import json
from pathlib import Path
path = Path('$SAME_VALUE_PROVENANCE/library/_hold/same_value/metadata.json')
metadata = json.loads(path.read_text())
metadata.update({
    'title': 'Orbital Mechanics Handbook',
    'author': 'Elena Garcia',
    'authors': ['Elena Garcia'],
    'year': '2018',
    'category': 'papers',
    'metadata_field_sources': {'title': 'heuristic', 'author': 'manual_fallback'},
})
path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + '\n')
PY
write_corrected_json "$SAME_VALUE_PROVENANCE/corrected.json" \
    "Orbital Mechanics Handbook" "Elena Garcia" 2018 "10.1000/same-value"
"$PROMOTE" "$SAME_VALUE_PROVENANCE/library/_hold/same_value" \
    --metadata "$SAME_VALUE_PROVENANCE/corrected.json" --library "$SAME_VALUE_PROVENANCE/library" \
    > "$SAME_VALUE_PROVENANCE/promote.out"
grep -Fq 'promoted key=garcia2018 category=papers' "$SAME_VALUE_PROVENANCE/promote.out"
python3 - <<PY
import json
from pathlib import Path
metadata = json.loads(Path('$SAME_VALUE_PROVENANCE/library/papers/garcia2018/metadata.json').read_text())
assert metadata['metadata_field_sources']['title'] == 'corrected_json', metadata['metadata_field_sources']
assert metadata['metadata_field_sources']['author'] == 'corrected_json', metadata['metadata_field_sources']
PY

# 1. Low-confidence hold + corrected metadata promotes into the library and validates.
CASE1="$TMP/case1"
make_hold "$CASE1" mystery case1-pdf
LIB1="$CASE1/library"
write_corrected_json "$CASE1/corrected.json" "A Small Paper" "Ada Lovelace" 1843 "10.1000/lovelace.1843"
held_md_sha="$(sha256sum "$LIB1/_hold/mystery/mystery.md" | awk '{print $1}')"
"$PROMOTE" "$LIB1/_hold/mystery" --metadata "$CASE1/corrected.json" --library "$LIB1" > "$CASE1/promote.out"
grep -Fq 'promoted key=lovelace1843 category=papers' "$CASE1/promote.out"
test -f "$LIB1/papers/lovelace1843.md"
test -d "$LIB1/papers/lovelace1843"
test -f "$LIB1/papers/lovelace1843.bib"
test -f "$LIB1/alexandria.bib"
! test -e "$LIB1/_hold/mystery" || exit 1
[[ "$(sha256sum "$LIB1/papers/lovelace1843.md" | awk '{print $1}')" == "$held_md_sha" ]]
"$VALIDATE" --work-md "$LIB1/papers/lovelace1843.md" > "$CASE1/validate.out"
grep -Fq 'validated 1 work(s)' "$CASE1/validate.out"

# Corrected metadata that is still low-confidence fails loudly and leaves the hold intact.
BAD="$TMP/still_low"
make_hold "$BAD" bad bad-pdf
cat > "$BAD/bad.json" <<'JSON'
{"title":"bad","author":"unknown","year":"nodate","category":"misc"}
JSON
if "$PROMOTE" "$BAD/library/_hold/bad" --metadata "$BAD/bad.json" --library "$BAD/library" > "$BAD/promote.out" 2>&1; then
    cat "$BAD/promote.out"
    exit 1
fi
grep -Fq 'still resolves to low-confidence/unknown' "$BAD/promote.out"
test -d "$BAD/library/_hold/bad"
! test -e "$BAD/library/misc/unknownnodate.md" || exit 1

# 2. Collision during promotion is resolved by the registry/global key uniquing and reported.
CASE2="$TMP/case2"
make_hold "$CASE2" collision collision-pdf
LIB2="$CASE2/library"
mkdir -p "$LIB2/papers/smith2020"
cat > "$LIB2/papers/smith2020.md" <<'MD'
# Existing Smith Paper

Existing body.
MD
cat > "$LIB2/papers/smith2020.bib" <<'BIB'
@article{smith2020,
  title = {Existing Smith Paper},
  author = {Jane Smith},
  year = {2020},
  doi = {10.1000/existing-smith}
}
BIB
write_corrected_json "$CASE2/corrected.json" "New Smith Paper" "Jane Smith" 2020 "10.1000/new-smith"
"$PROMOTE" "$LIB2/_hold/collision" --metadata "$CASE2/corrected.json" --library "$LIB2" > "$CASE2/promote.out"
grep -Fq 'promoted key=smith2020a category=papers' "$CASE2/promote.out"
test -f "$LIB2/papers/smith2020.md"
test -f "$LIB2/papers/smith2020a.md"
test -d "$LIB2/papers/smith2020a"
grep -Fq '@article{smith2020,' "$LIB2/alexandria.bib"
grep -Fq '@article{smith2020a,' "$LIB2/alexandria.bib"
"$VALIDATE" --work-md "$LIB2/papers/smith2020a.md" > "$CASE2/validate.out"
grep -Fq 'validated 1 work(s)' "$CASE2/validate.out"
python3 - <<PY
import json
from pathlib import Path
entries = json.loads(Path('$LIB2/key_registry.json').read_text())['entries']
assert any(entry['key'] == 'smith2020a' for entry in entries.values()), entries
PY

# 3. Dry-run prints the plan and changes nothing in the library.
CASE3="$TMP/case3"
make_hold "$CASE3" dry dry-pdf
LIB3="$CASE3/library"
write_corrected_json "$CASE3/corrected.json" "Dry Run Paper" "Alan Turing" 1936 "10.1000/turing.1936"
find "$LIB3" -mindepth 1 -printf '%P %y %s %T@\n' | sort > "$CASE3/before.txt"
"$PROMOTE" "$LIB3/_hold/dry" --metadata "$CASE3/corrected.json" --library "$LIB3" --dry-run > "$CASE3/dry.out"
grep -Fq 'DRY RUN: promote held Alexandria work' "$CASE3/dry.out"
grep -Fq 'planned key: turing1936' "$CASE3/dry.out"
grep -Fq 'target markdown:' "$CASE3/dry.out"
grep -Fq 'No files were moved; registry and bibliography were not touched.' "$CASE3/dry.out"
find "$LIB3" -mindepth 1 -printf '%P %y %s %T@\n' | sort > "$CASE3/after.txt"
cmp "$CASE3/before.txt" "$CASE3/after.txt"
test -d "$LIB3/_hold/dry"
! test -e "$LIB3/key_registry.json" || exit 1
! test -e "$LIB3/alexandria.bib" || exit 1
! test -e "$LIB3/papers/turing1936.md" || exit 1

# 4. Original artifacts and source sha256/size records are preserved through promotion.
CASE4="$TMP/case4"
make_hold "$CASE4" preserve preserve-pdf preserve-epub
LIB4="$CASE4/library"
cp "$LIB4/_hold/preserve/metadata.json" "$CASE4/held_metadata.before.json"
preserve_md_sha="$(sha256sum "$LIB4/_hold/preserve/preserve.md" | awk '{print $1}')"
cat > "$CASE4/corrected.bib" <<'BIB'
@article{manualkey,
  title = {Preserved Work},
  author = {Grace Hopper},
  year = {1952},
  doi = {10.1000/preserved-work}
}
BIB
"$PROMOTE" "$LIB4/_hold/preserve" --bibtex "$CASE4/corrected.bib" --library "$LIB4" > "$CASE4/promote.out"
grep -Fq 'promoted key=hopper1952 category=papers' "$CASE4/promote.out"
test -f "$LIB4/papers/hopper1952/_page_0_Picture_3.jpeg"
test -f "$LIB4/papers/hopper1952/doc_meta.json"
grep -Fq 'preserve' "$LIB4/papers/hopper1952/doc_meta.json"
[[ "$(sha256sum "$LIB4/papers/hopper1952.md" | awk '{print $1}')" == "$preserve_md_sha" ]]
python3 - <<PY
import json
from pathlib import Path
held = json.loads(Path('$CASE4/held_metadata.before.json').read_text())
promoted = json.loads(Path('$LIB4/papers/hopper1952/metadata.json').read_text())
held_rows = {row['name']: row for row in held['source_files']}
promoted_rows = {row['name']: row for row in promoted['source_files']}
assert set(held_rows) == {'preserve.pdf', 'preserve.epub'}, held_rows
assert set(promoted_rows) == set(held_rows), promoted_rows
for name, held_row in held_rows.items():
    row = promoted_rows[name]
    assert row['sha256'] == held_row['sha256'], (name, row, held_row)
    assert row['size'] == held_row['size'], (name, row, held_row)
    assert row['filed_name'] in {'hopper1952.pdf', 'hopper1952.epub'}, row
assert promoted['source_sha256'] == {name: held_rows[name]['sha256'] for name in held_rows}, promoted['source_sha256']
assert Path('$LIB4/papers/hopper1952/hopper1952.pdf').read_text() == 'preserve-pdf'
assert Path('$LIB4/papers/hopper1952/hopper1952.epub').read_text() == 'preserve-epub'
PY
"$VALIDATE" --work-md "$LIB4/papers/hopper1952.md" > "$CASE4/validate.out"
grep -Fq 'validated 1 work(s)' "$CASE4/validate.out"
