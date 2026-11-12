#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TMP="$(mktemp -d "$ROOT/.tmp.informal.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

assert_has() {
    local file="$1" needle="$2"
    grep -Fq "$needle" "$file"
}

assert_lacks_regex() {
    local file="$1" pattern="$2"
    ! grep -Eq "$pattern" "$file" || exit 1
}

render() {
    local metadata="$1" key="$2" output="$3"
    shift 3
    "$ROOT/pipeline/alexandria_bibtex.py" --metadata "$metadata" --key "$key" "$@" > "$output"
}

validate_library() {
    "$ROOT/pipeline/alexandria_validate_work.py" --library "$1"
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

# --- 1. Informal metadata WITH url -> @online, title+url+note, no author line ---
INFORMAL_URL="$TMP/informal_url.json"
cat > "$INFORMAL_URL" <<'JSON'
{
  "title": "A Podcast Episode About Rockets",
  "kind": "informal",
  "url": "https://example.test/episode-1",
  "source": "Rocket Talk Podcast",
  "category": "misc"
}
JSON
render "$INFORMAL_URL" podcast2026 "$TMP/podcast2026.bib"
assert_has "$TMP/podcast2026.bib" '@online{podcast2026,'
assert_has "$TMP/podcast2026.bib" 'title = {A Podcast Episode About Rockets}'
assert_has "$TMP/podcast2026.bib" 'url = {https://example.test/episode-1}'
assert_has "$TMP/podcast2026.bib" 'note = {Alexandria kind: informal; source: Rocket Talk Podcast}'
assert_lacks_regex "$TMP/podcast2026.bib" '^  author ='

# --- 2. Informal metadata WITHOUT url -> @misc ---
INFORMAL_NO_URL="$TMP/informal_no_url.json"
cat > "$INFORMAL_NO_URL" <<'JSON'
{
  "title": "Generated Notes on Orbital Mechanics",
  "kind": "informal",
  "source": "generated",
  "category": "misc"
}
JSON
render "$INFORMAL_NO_URL" notes2026 "$TMP/notes2026.bib"
assert_has "$TMP/notes2026.bib" '@misc{notes2026,'
assert_has "$TMP/notes2026.bib" 'note = {Alexandria kind: informal; source: generated}'
assert_lacks_regex "$TMP/notes2026.bib" '^  author ='

# --- 3. Academic metadata unchanged: @article still correct ---
ARTICLE="$TMP/article.json"
cat > "$ARTICLE" <<'JSON'
{
  "title": "A Small Paper",
  "author": "Ada Lovelace",
  "authors": ["Ada Lovelace"],
  "year": "1843",
  "category": "papers",
  "doi": "10.1000/lovelace.1843",
  "metadata_confidence": "high"
}
JSON
render "$ARTICLE" lovelace1843 "$TMP/lovelace1843.bib"
assert_has "$TMP/lovelace1843.bib" '@article{lovelace1843,'
assert_has "$TMP/lovelace1843.bib" 'author = {Ada Lovelace}'
assert_has "$TMP/lovelace1843.bib" 'doi = {10.1000/lovelace.1843}'

# --- 4. Academic metadata unchanged: @book still correct ---
BOOK="$TMP/book.json"
cat > "$BOOK" <<'JSON'
{
  "title": "Optimization Handbook",
  "author": "Jane Smith",
  "authors": ["Jane Smith"],
  "year": "2020",
  "category": "optimization",
  "publisher": "Example Press",
  "metadata_confidence": "medium"
}
JSON
render "$BOOK" smith2020 "$TMP/smith2020.bib"
assert_has "$TMP/smith2020.bib" '@book{smith2020,'
assert_has "$TMP/smith2020.bib" 'publisher = {Example Press}'

# --- 5. validate_work accepts an informal work whose .bib has only a title,
#        and does NOT fail on a missing image when kind==informal ---
LIB="$TMP/library"
mkdir -p "$LIB/misc/podcast2026"
cat > "$LIB/misc/podcast2026.md" <<'MD'
# A Podcast Episode About Rockets

Some body text with a missing image reference.

![](podcast2026/missing.png)
MD
cat > "$LIB/misc/podcast2026/metadata.json" <<'JSON'
{
  "key": "podcast2026",
  "title": "A Podcast Episode About Rockets",
  "kind": "informal",
  "url": "https://example.test/episode-1",
  "source": "Rocket Talk Podcast",
  "category": "misc"
}
JSON
cat > "$LIB/misc/podcast2026.bib" <<'BIB'
@online{podcast2026,
  title = {A Podcast Episode About Rockets},
  url = {https://example.test/episode-1}
}
BIB
cp "$LIB/misc/podcast2026.bib" "$LIB/alexandria.bib"
out="$(validate_library "$LIB" 2>&1)"
echo "$out"
echo "$out" | grep -Fq "WARN $LIB/misc/podcast2026.md: missing image artifact podcast2026/missing.png"
! grep -Fq 'missing required BibTeX field' <<<"$out" || exit 1
# validate_work.py exits 0 (a WARN is not a hard failure)
validate_library "$LIB" >/dev/null

# --- 6. Academic validation STILL fails on missing author / missing image ---
ACADEMIC_LIB="$TMP/academic_library"
mkdir -p "$ACADEMIC_LIB/papers/lovelace1843"
cat > "$ACADEMIC_LIB/papers/lovelace1843.md" <<'MD'
# A Small Paper

Some body text with a copied image.

![](lovelace1843/_page_0_Picture_3.jpeg)
MD
printf image > "$ACADEMIC_LIB/papers/lovelace1843/_page_0_Picture_3.jpeg"
printf pdf > "$ACADEMIC_LIB/papers/lovelace1843/lovelace1843.pdf"
pdf_sha="$(sha256sum "$ACADEMIC_LIB/papers/lovelace1843/lovelace1843.pdf" | awk '{print $1}')"
cat > "$ACADEMIC_LIB/papers/lovelace1843/metadata.json" <<JSON
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
printf '{"chunk_id":"lovelace1843:0000","text":"Body"}\n' > "$ACADEMIC_LIB/papers/lovelace1843/chunks.jsonl"
printf '{"chunk_id":"lovelace1843:0000","embedding":[0.1,0.2]}\n' > "$ACADEMIC_LIB/papers/lovelace1843/embeddings.jsonl"
printf '{"embedding_ok":true,"embedding_count":1}\n' > "$ACADEMIC_LIB/papers/lovelace1843/run_meta.json"
cat > "$ACADEMIC_LIB/papers/lovelace1843.bib" <<'BIB'
@article{lovelace1843,
  title = {A Small Paper},
  author = {Ada Lovelace},
  year = {1843},
  doi = {10.1000/lovelace.1843}
}
BIB
cp "$ACADEMIC_LIB/papers/lovelace1843.bib" "$ACADEMIC_LIB/alexandria.bib"
validate_library "$ACADEMIC_LIB"

# missing image is a hard failure for academic works
rm "$ACADEMIC_LIB/papers/lovelace1843/_page_0_Picture_3.jpeg"
assert_validation_fails "$ACADEMIC_LIB" 'missing image artifact'
printf image > "$ACADEMIC_LIB/papers/lovelace1843/_page_0_Picture_3.jpeg"

# missing author is a hard failure for academic works
cat > "$ACADEMIC_LIB/papers/lovelace1843.bib" <<'BIB'
@article{lovelace1843,
  title = {A Small Paper},
  year = {1843},
  doi = {10.1000/lovelace.1843}
}
BIB
cp "$ACADEMIC_LIB/papers/lovelace1843.bib" "$ACADEMIC_LIB/alexandria.bib"
assert_validation_fails "$ACADEMIC_LIB" 'missing required BibTeX field author'

echo "OK"
