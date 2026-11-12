#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

write_work() {
    local parsed_root="$1" stem="$2" author="$3" year="$4" category="$5" title="$6"
    mkdir -p "$parsed_root/$stem"
    cat > "$parsed_root/$stem/$stem.md" <<MD
# $title

Author: $author
Year: $year
Category: $category
MD
    python3 - "$parsed_root/$stem/metadata.json" "$title" "$author" "$year" "$category" <<'PY'
import json
import sys
path, title, author, year, category = sys.argv[1:]
with open(path, "w", encoding="utf-8") as handle:
    json.dump({"title": title, "author": author, "year": year, "category": category}, handle, sort_keys=True)
    handle.write("\n")
PY
}

file_work() {
    local parsed_root="$1" drop_dir="$2" lib_dir="$3" stem="$4" log_file="$5"
    ALEXANDRIA_PARSED_WORK="$parsed_root/$stem" \
    ALEXANDRIA_DROP_DIR="$drop_dir" \
    ALEXANDRIA_LIBRARY_ROOT="$lib_dir" \
    ALEXANDRIA_FILE_LOG="$log_file" \
    "$ROOT/pipeline/alexandria_file_parsed_work.py"
}

# SAME-SOURCE RETRY and DUPLICATE UNDER NEW STEM: byte-identical input reuses one key.
RETRY_PARSED="$TMP/retry_parsed"
RETRY_DROP="$TMP/retry_DROP_HERE"
RETRY_LIB="$TMP/retry_library"
RETRY_LOG="$TMP/retry.log"
mkdir -p "$RETRY_PARSED" "$RETRY_DROP" "$RETRY_LIB"
write_work "$RETRY_PARSED" original "Ada Lovelace" 1843 papers "Stable Key Paper"
printf 'identical source bytes' > "$RETRY_DROP/original.pdf"
file_work "$RETRY_PARSED" "$RETRY_DROP" "$RETRY_LIB" original "$RETRY_LOG"
test -f "$RETRY_LIB/papers/lovelace1843.md"

printf 'identical source bytes' > "$RETRY_DROP/original.pdf"
file_work "$RETRY_PARSED" "$RETRY_DROP" "$RETRY_LIB" original "$RETRY_LOG"
! test -e "$RETRY_LIB/papers/lovelace1843a.md" || exit 1
! test -e "$RETRY_LIB/papers/lovelace1843a" || exit 1

write_work "$RETRY_PARSED" renamed "Ada Lovelace" 1843 papers "Stable Key Paper"
printf 'identical source bytes' > "$RETRY_DROP/renamed.pdf"
file_work "$RETRY_PARSED" "$RETRY_DROP" "$RETRY_LIB" renamed "$RETRY_LOG"
! test -e "$RETRY_LIB/papers/lovelace1843a.md" || exit 1
! test -e "$RETRY_LIB/papers/lovelace1843a" || exit 1
python3 - <<PY
import json
from pathlib import Path
registry = json.loads(Path('$RETRY_LIB/key_registry.json').read_text())
entries = registry['entries']
assert registry['version'] == 1
assert len(entries) == 1, entries
entry = next(iter(entries.values()))
assert entry['category'] == 'papers', entry
assert entry['key'] == 'lovelace1843', entry
result = json.loads(Path('$RETRY_PARSED/renamed/file_result.json').read_text())
assert result['key'] == 'lovelace1843', result
metadata = json.loads(Path('$RETRY_LIB/papers/lovelace1843/metadata.json').read_text())
assert {row['name'] for row in metadata['source_files']} == {'original.pdf', 'renamed.pdf'}
PY

# CROSS-CATEGORY UNIQUENESS: same base_key in different categories gets global suffixes.
CROSS_PARSED="$TMP/cross_parsed"
CROSS_DROP="$TMP/cross_DROP_HERE"
CROSS_LIB="$TMP/cross_library"
CROSS_LOG="$TMP/cross.log"
mkdir -p "$CROSS_PARSED" "$CROSS_DROP" "$CROSS_LIB"
write_work "$CROSS_PARSED" papers_copy "Jane Smith" 2020 papers "Papers Copy"
printf 'papers unique bytes' > "$CROSS_DROP/papers_copy.pdf"
file_work "$CROSS_PARSED" "$CROSS_DROP" "$CROSS_LIB" papers_copy "$CROSS_LOG"
write_work "$CROSS_PARSED" misc_copy "Jane Smith" 2020 misc "Misc Copy"
printf 'misc unique bytes' > "$CROSS_DROP/misc_copy.pdf"
file_work "$CROSS_PARSED" "$CROSS_DROP" "$CROSS_LIB" misc_copy "$CROSS_LOG"
test -f "$CROSS_LIB/papers/smith2020.md"
test -f "$CROSS_LIB/misc/smith2020a.md"
! test -e "$CROSS_LIB/misc/smith2020.md" || exit 1
python3 - <<PY
import json
from pathlib import Path
entries = json.loads(Path('$CROSS_LIB/key_registry.json').read_text())['entries']
keys = sorted({entry['key'] for entry in entries.values()})
assert keys == ['smith2020', 'smith2020a'], keys
root_bib = Path('$CROSS_LIB/alexandria.bib').read_text(encoding='utf-8')
assert root_bib.count('@') == 2, root_bib
assert '@article{smith2020,' in root_bib, root_bib
assert '@misc{smith2020a,' in root_bib, root_bib
assert '@misc{smith2020,' not in root_bib, root_bib
PY

# DETERMINISTIC SUFFIXING: batch allocation is independent of discovery order.
PYTHONPATH="$ROOT/pipeline${PYTHONPATH:+:$PYTHONPATH}" python3 - <<PY
from pathlib import Path
from alexandria_key_registry import KeyRegistry

low = '0' * 64
high = 'f' * 64

def allocate(name, requests):
    root = Path('$TMP') / name
    dirs = {'papers': root / 'papers', 'misc': root / 'misc'}
    for path in dirs.values():
        path.mkdir(parents=True)
    registry = KeyRegistry.load(root)
    results = registry.reserve_key_batch(requests, category_dirs=dirs)
    return {request['source_sha256']: result for request, result in zip(requests, results)}

forward = [
    {'source_sha256': high, 'category': 'misc', 'base_key': 'smith2020'},
    {'source_sha256': low, 'category': 'papers', 'base_key': 'smith2020'},
]
reverse = list(reversed(forward))
forward_map = allocate('deterministic_forward', forward)
reverse_map = allocate('deterministic_reverse', reverse)
assert forward_map == reverse_map, (forward_map, reverse_map)
assert forward_map[low] == ('papers', 'smith2020'), forward_map
assert forward_map[high] == ('misc', 'smith2020a'), forward_map

immut = KeyRegistry.load(Path('$TMP') / 'immutability_direct')
immut.record_existing([low], 'papers', 'smith2020', 'smith2020')
try:
    immut.record_existing([low], 'misc', 'smith2020a', 'smith2020')
except RuntimeError as exc:
    assert 'registry immutability violation' in str(exc), exc
else:
    raise AssertionError('registry allowed a sha->key mutation')
PY

# IMMUTABILITY: a re-file containing source hashes already mapped to different keys stops.
IMM_PARSED="$TMP/immut_parsed"
IMM_DROP="$TMP/immut_DROP_HERE"
IMM_LIB="$TMP/immut_library"
IMM_LOG="$TMP/immut.log"
mkdir -p "$IMM_PARSED" "$IMM_DROP" "$IMM_LIB"
write_work "$IMM_PARSED" combo "Jane Smith" 2020 papers "Conflicting Sources"
printf 'already filed pdf bytes' > "$IMM_DROP/combo.pdf"
printf 'already filed epub bytes' > "$IMM_DROP/combo.epub"
pdf_sha="$(sha256sum "$IMM_DROP/combo.pdf" | awk '{print $1}')"
epub_sha="$(sha256sum "$IMM_DROP/combo.epub" | awk '{print $1}')"
PYTHONPATH="$ROOT/pipeline${PYTHONPATH:+:$PYTHONPATH}" python3 - <<PY
from pathlib import Path
from alexandria_key_registry import KeyRegistry
registry = KeyRegistry.load(Path('$IMM_LIB'))
registry.record_existing(['$pdf_sha'], 'papers', 'smith2020', 'smith2020')
registry.record_existing(['$epub_sha'], 'papers', 'smith2020a', 'smith2020')
PY
if file_work "$IMM_PARSED" "$IMM_DROP" "$IMM_LIB" combo "$IMM_LOG" > "$TMP/immut.out" 2>&1; then
    cat "$TMP/immut.out"
    exit 1
fi
grep -q 'conflicting registry entries for source hashes' "$TMP/immut.out"
test -f "$IMM_DROP/combo.pdf"
test -f "$IMM_DROP/combo.epub"
