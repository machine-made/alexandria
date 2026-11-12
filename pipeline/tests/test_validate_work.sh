#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TMP="$(mktemp -d "$ROOT/.tmp.validate.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

validate_library() {
    "$ROOT/pipeline/alexandria_validate_work.py" --library "$1"
}

sync_root_bib() {
    local lib="$1"
    cat "$lib/papers/lovelace1843.bib" > "$lib/alexandria.bib"
}

write_lovelace_bib() {
    local lib="$1"
    local entry_type="${2:-article}"
    local bib_key="${3:-lovelace1843}"
    local doi_field
    if [[ $# -ge 4 ]]; then
        doi_field="$4"
    else
        doi_field='  doi = {10.1000/lovelace.1843}'
    fi
    cat > "$lib/papers/lovelace1843.bib" <<BIB
@$entry_type{$bib_key,
  title = {A Small Paper},
  author = {Ada Lovelace},
  year = {1843}${doi_field:+,
$doi_field}
}
BIB
}

setup_valid_library() {
    local lib="$1"
    rm -rf "$lib"
    mkdir -p "$lib/papers/lovelace1843"
    cat > "$lib/papers/lovelace1843.md" <<'MD'
# A Small Paper

Some body text with a copied image.

![](lovelace1843/_page_0_Picture_3.jpeg)
MD
    printf image > "$lib/papers/lovelace1843/_page_0_Picture_3.jpeg"
    printf pdf > "$lib/papers/lovelace1843/lovelace1843.pdf"
    local pdf_sha
    pdf_sha="$(sha256sum "$lib/papers/lovelace1843/lovelace1843.pdf" | awk '{print $1}')"
    cat > "$lib/papers/lovelace1843/metadata.json" <<JSON
{
  "key": "lovelace1843",
  "title": "A Small Paper",
  "author": "Ada Lovelace",
  "authors": ["Ada Lovelace"],
  "year": "1843",
  "category": "papers",
  "doi": "10.1000/lovelace.1843",
  "metadata_confidence": "medium",
  "source_sha256": {"work.pdf": "$pdf_sha"},
  "source_files": [{"name": "work.pdf", "sha256": "$pdf_sha", "size": 3, "filed_name": "lovelace1843.pdf"}]
}
JSON
    printf '{"chunk_id":"lovelace1843:0000","text":"Body"}\n' > "$lib/papers/lovelace1843/chunks.jsonl"
    printf '{"chunk_id":"lovelace1843:0000","embedding":[0.1,0.2]}\n' > "$lib/papers/lovelace1843/embeddings.jsonl"
    printf '{"embedding_ok":true,"embedding_count":1}\n' > "$lib/papers/lovelace1843/run_meta.json"
    write_lovelace_bib "$lib"
    sync_root_bib "$lib"
}

assert_validation_fails() {
    local lib="$1"
    local needle="$2"
    local out="$TMP/$(basename "$lib").out"
    if validate_library "$lib" > "$out" 2>&1; then
        cat "$out"
        exit 1
    fi
    grep -Fq "$needle" "$out"
}

LIB="$TMP/library"
setup_valid_library "$LIB"
validate_library "$LIB"

printf bad > "$LIB/papers/lovelace1843/lovelace1843.pdf"
assert_validation_fails "$LIB" 'source copy sha256 mismatch'
printf pdf > "$LIB/papers/lovelace1843/lovelace1843.pdf"

# A recompressed source copy is valid when metadata records what is stored; the original sha stays.
python3 - "$LIB/papers/lovelace1843" <<'PY'
import hashlib, json, sys
d = sys.argv[1]
open(f"{d}/lovelace1843.pdf", "wb").write(b"p")
meta = json.load(open(f"{d}/metadata.json"))
row = meta["source_files"][0]
row["stored_sha256"] = hashlib.sha256(b"p").hexdigest()
row["stored_size"] = 1
json.dump(meta, open(f"{d}/metadata.json", "w"))
PY
validate_library "$LIB"
printf q > "$LIB/papers/lovelace1843/lovelace1843.pdf"
assert_validation_fails "$LIB" 'source copy sha256 mismatch'
python3 - "$LIB/papers/lovelace1843" <<'PY'
import json, sys
d = sys.argv[1]
meta = json.load(open(f"{d}/metadata.json"))
row = meta["source_files"][0]
del row["stored_sha256"], row["stored_size"]
json.dump(meta, open(f"{d}/metadata.json", "w"))
PY
printf pdf > "$LIB/papers/lovelace1843/lovelace1843.pdf"

rm "$LIB/papers/lovelace1843/_page_0_Picture_3.jpeg"
assert_validation_fails "$LIB" 'missing image artifact'

KEY_MISMATCH="$TMP/key_mismatch"
setup_valid_library "$KEY_MISMATCH"
write_lovelace_bib "$KEY_MISMATCH" article wrongkey '  doi = {10.1000/lovelace.1843}'
sync_root_bib "$KEY_MISMATCH"
assert_validation_fails "$KEY_MISMATCH" "BibTeX key 'wrongkey' != markdown stem 'lovelace1843'"

WRONG_TYPE="$TMP/wrong_type"
setup_valid_library "$WRONG_TYPE"
write_lovelace_bib "$WRONG_TYPE" book lovelace1843 '  doi = {10.1000/lovelace.1843}'
sync_root_bib "$WRONG_TYPE"
assert_validation_fails "$WRONG_TYPE" 'BibTeX entry type @book != expected @article'

MISSING_FIELD="$TMP/missing_field"
setup_valid_library "$MISSING_FIELD"
write_lovelace_bib "$MISSING_FIELD" article lovelace1843 ''
sync_root_bib "$MISSING_FIELD"
assert_validation_fails "$MISSING_FIELD" 'missing required BibTeX field journal-or-doi-or-eprint'

DOI_MISMATCH="$TMP/doi_mismatch"
setup_valid_library "$DOI_MISMATCH"
write_lovelace_bib "$DOI_MISMATCH" article lovelace1843 '  doi = {10.1000/wrong-doi}'
sync_root_bib "$DOI_MISMATCH"
assert_validation_fails "$DOI_MISMATCH" 'DOI mismatch vs metadata'

STALE_ROOT="$TMP/stale_root"
setup_valid_library "$STALE_ROOT"
printf '%s\n' '@article{lovelace1843,' '  title = {Stale Root Title},' '  author = {Ada Lovelace},' '  year = {1843},' '  doi = {10.1000/lovelace.1843}' '}' > "$STALE_ROOT/alexandria.bib"
assert_validation_fails "$STALE_ROOT" 'stale-or-out-of-sync-root-bibliography'

DUP_ROOT="$TMP/duplicate_root"
setup_valid_library "$DUP_ROOT"
cat "$DUP_ROOT/papers/lovelace1843.bib" "$DUP_ROOT/papers/lovelace1843.bib" > "$DUP_ROOT/alexandria.bib"
assert_validation_fails "$DUP_ROOT" 'stale-or-out-of-sync-root-bibliography'

REGISTRY_MUTATION="$TMP/registry_mutation"
setup_valid_library "$REGISTRY_MUTATION"
registry_sha="$(sha256sum "$REGISTRY_MUTATION/papers/lovelace1843/lovelace1843.pdf" | awk '{print $1}')"
cat > "$REGISTRY_MUTATION/key_registry.json" <<JSON
{
  "version": 1,
  "entries": {
    "$registry_sha": {
      "key": "lovelace1843a",
      "category": "papers",
      "base_key": "lovelace1843",
      "first_filed_utc": "2024-01-01T00:00:00+00:00"
    }
  }
}
JSON
assert_validation_fails "$REGISTRY_MUTATION" 'key registry identity mismatch'
