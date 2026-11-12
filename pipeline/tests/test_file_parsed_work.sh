#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
PARSED="$TMP/parsed"
DROP="$TMP/DROP_HERE"
LIB="$TMP/library"
LOG="$TMP/file.log"
mkdir -p "$PARSED/work" "$DROP" "$LIB"
cat > "$PARSED/work/work.md" <<'MD'
# Test Title

Body with a local Marker image:

![](_page_0_Picture_3.jpeg)

HTML image:

<img src="_page_1_Figure_2.png" />

Do not rewrite:

![](assets/existing.jpeg)
![](https://example.test/remote.jpeg)
[page anchor](#page-14-0)
MD
mkdir -p "$PARSED/work/work"
printf image > "$PARSED/work/work/_page_0_Picture_3.jpeg"
printf image > "$PARSED/work/work/_page_1_Figure_2.png"
printf '{"pages":[]}\n' > "$PARSED/work/work/doc_meta.json"
printf marker-err > "$PARSED/work/work/_alexandria_marker.err"
printf marker-out > "$PARSED/work/work/_alexandria_marker.out"
printf ollama-log > "$PARSED/work/work/_alexandria_ollama.log"
printf chunk-log > "$PARSED/work/work/_alexandria_chunk.log"
printf embed-log > "$PARSED/work/work/_alexandria_embed.log"
printf '{"chunk_id":"lovelace1843:0000","text":"Body"}\n' > "$PARSED/work/work/chunks.jsonl"
printf '{"chunk_id":"lovelace1843:0000","embedding":[0.1,0.2]}\n' > "$PARSED/work/work/embeddings.jsonl"
printf '{"embedding_ok":true,"embedding_count":1}\n' > "$PARSED/work/work/embed_summary.json"
printf '{"embedding_ok":true,"embedding_count":1}\n' > "$PARSED/work/work/run_meta.json"
cat > "$PARSED/work/metadata.json" <<JSON
{"title":"Test Title","author":"Ada Lovelace","year":"1843","category":"papers"}
JSON
printf pdf > "$DROP/work.pdf"
printf epub > "$DROP/work.epub"
ALEXANDRIA_PARSED_WORK="$PARSED/work" \
ALEXANDRIA_DROP_DIR="$DROP" \
ALEXANDRIA_LIBRARY_ROOT="$LIB" \
ALEXANDRIA_FILE_LOG="$LOG" \
"$ROOT/pipeline/alexandria_file_parsed_work.py"

test -f "$LIB/papers/lovelace1843.md"
test -f "$LIB/papers/lovelace1843/lovelace1843.pdf"
test -f "$LIB/papers/lovelace1843/lovelace1843.epub"
test -f "$LIB/papers/lovelace1843/metadata.json"
test -f "$LIB/papers/lovelace1843/chunks.jsonl"
test -f "$LIB/papers/lovelace1843/embeddings.jsonl"
test -f "$LIB/papers/lovelace1843/embed_summary.json"
test -f "$LIB/papers/lovelace1843/run_meta.json"
test -f "$LIB/papers/lovelace1843/_page_0_Picture_3.jpeg"
test -f "$LIB/papers/lovelace1843/_page_1_Figure_2.png"
test -f "$LIB/papers/lovelace1843/doc_meta.json"
test -f "$LIB/papers/lovelace1843/_alexandria_marker.err"
test -f "$LIB/papers/lovelace1843/_alexandria_marker.out"
test -f "$LIB/papers/lovelace1843/_alexandria_ollama.log"
test -f "$LIB/papers/lovelace1843/_alexandria_chunk.log"
test -f "$LIB/papers/lovelace1843/_alexandria_embed.log"
! test -f "$LIB/papers/lovelace1843/work.md" || exit 1
grep -q 'lovelace1843:0000' "$LIB/papers/lovelace1843/chunks.jsonl"
python3 - <<PY
from pathlib import Path
text = Path("$LIB/papers/lovelace1843.md").read_text(encoding="utf-8")
assert "![](lovelace1843/_page_0_Picture_3.jpeg)" in text
assert '<img src="lovelace1843/_page_1_Figure_2.png" />' in text
assert "![](assets/existing.jpeg)" in text
assert "![](https://example.test/remote.jpeg)" in text
assert "[page anchor](#page-14-0)" in text
assert "![](_page_0_Picture_3.jpeg)" not in text
assert 'src="_page_1_Figure_2.png"' not in text
PY
! test -e "$DROP/work.pdf" || exit 1
! test -e "$DROP/work.epub" || exit 1
grep -q 'FILED work key=lovelace1843 category=papers' "$LOG"

# Re-filing the same source should reuse the same key instead of creating keya.
cat > "$PARSED/work/work.md" <<'MD'
# Changed Title

This retry output should not replace the canonical filed Markdown for the same source.
MD
printf pdf > "$DROP/work.pdf"
ALEXANDRIA_PARSED_WORK="$PARSED/work" \
ALEXANDRIA_DROP_DIR="$DROP" \
ALEXANDRIA_LIBRARY_ROOT="$LIB" \
ALEXANDRIA_FILE_LOG="$LOG" \
"$ROOT/pipeline/alexandria_file_parsed_work.py"
test -f "$LIB/papers/lovelace1843.md"
test -f "$LIB/papers/lovelace1843/lovelace1843.epub"
grep -q 'Test Title' "$LIB/papers/lovelace1843.md"
! grep -q 'Changed Title' "$LIB/papers/lovelace1843.md" || exit 1
! test -e "$LIB/papers/lovelace1843a.md" || exit 1
! test -e "$LIB/papers/lovelace1843a" || exit 1
python3 - <<PY
import json
from pathlib import Path
m=json.loads(Path('$LIB/papers/lovelace1843/metadata.json').read_text())
names={row['name'] for row in m['source_files']}
assert names == {'work.pdf', 'work.epub'}
assert 'work.pdf' in m['source_sha256']
assert 'work.epub' in m['source_sha256']
filed={row['name']: row.get('filed_name') for row in m['source_files']}
assert filed == {'work.epub': 'lovelace1843.epub', 'work.pdf': 'lovelace1843.pdf'}
PY

# A byte-identical source already filed before source_sha256 existed must reuse the same key.
L_PARSED="$TMP/legacy_sha_parsed"
L_DROP="$TMP/legacy_sha_DROP_HERE"
L_LIB="$TMP/legacy_sha_library"
L_LOG="$TMP/legacy_sha.log"
mkdir -p "$L_PARSED/work" "$L_DROP" "$L_LIB/papers/lovelace1843"
cat > "$L_PARSED/work/work.md" <<'MD'
# Test Title

Author: Ada Lovelace
Year: 1843
Category: papers
MD
cat > "$L_PARSED/work/metadata.json" <<'JSON'
{"title":"Test Title","author":"Ada Lovelace","year":"1843","category":"papers"}
JSON
printf pdf > "$L_LIB/papers/lovelace1843/lovelace1843.pdf"
cat > "$L_LIB/papers/lovelace1843/metadata.json" <<'JSON'
{"title":"Test Title","author":"Ada Lovelace","year":"1843","category":"papers"}
JSON
touch "$L_LIB/papers/lovelace1843.md"
printf pdf > "$L_DROP/work.pdf"
ALEXANDRIA_PARSED_WORK="$L_PARSED/work" \
ALEXANDRIA_DROP_DIR="$L_DROP" \
ALEXANDRIA_LIBRARY_ROOT="$L_LIB" \
ALEXANDRIA_FILE_LOG="$L_LOG" \
"$ROOT/pipeline/alexandria_file_parsed_work.py"
test -f "$L_LIB/papers/lovelace1843.md"
test -f "$L_LIB/papers/lovelace1843.bib"
! test -e "$L_LIB/papers/lovelace1843a.md" || exit 1
! test -e "$L_LIB/papers/lovelace1843a" || exit 1
! test -e "$L_DROP/work.pdf" || exit 1

# A partially backfilled legacy work must still match later byte-identical source formats.
LF_PARSED="$TMP/legacy_format_parsed"
LF_DROP="$TMP/legacy_format_DROP_HERE"
LF_LIB="$TMP/legacy_format_library"
mkdir -p "$LF_PARSED/work" "$LF_DROP" "$LF_LIB/papers/lovelace1843"
cat > "$LF_PARSED/work/work.md" <<'MD'
# Test Title

Author: Ada Lovelace
Year: 1843
Category: papers
MD
cat > "$LF_PARSED/work/metadata.json" <<'JSON'
{"title":"Test Title","author":"Ada Lovelace","year":"1843","category":"papers"}
JSON
printf pdf > "$LF_LIB/papers/lovelace1843/lovelace1843.pdf"
printf epub > "$LF_LIB/papers/lovelace1843/lovelace1843.epub"
cat > "$LF_LIB/papers/lovelace1843/metadata.json" <<'JSON'
{"title":"Test Title","author":"Ada Lovelace","year":"1843","category":"papers"}
JSON
touch "$LF_LIB/papers/lovelace1843.md"
printf epub > "$LF_DROP/work.epub"
ALEXANDRIA_PARSED_WORK="$LF_PARSED/work" \
ALEXANDRIA_DROP_DIR="$LF_DROP" \
ALEXANDRIA_LIBRARY_ROOT="$LF_LIB" \
"$ROOT/pipeline/alexandria_file_parsed_work.py"
printf pdf > "$LF_DROP/work.pdf"
ALEXANDRIA_PARSED_WORK="$LF_PARSED/work" \
ALEXANDRIA_DROP_DIR="$LF_DROP" \
ALEXANDRIA_LIBRARY_ROOT="$LF_LIB" \
"$ROOT/pipeline/alexandria_file_parsed_work.py"
! test -e "$LF_LIB/papers/lovelace1843a.md" || exit 1
! test -e "$LF_LIB/papers/lovelace1843a" || exit 1
! test -e "$LF_DROP/work.pdf" || exit 1

# Inconsistent chunk and embedding artifacts must block filing before source deletion.
C_PARSED="$TMP/chunk_bad_parsed"
C_DROP="$TMP/chunk_bad_DROP_HERE"
C_LIB="$TMP/chunk_bad_library"
C_LOG="$TMP/chunk_bad.log"
mkdir -p "$C_PARSED/work/work" "$C_DROP" "$C_LIB"
cat > "$C_PARSED/work/work.md" <<'MD'
# Test Title

Author: Ada Lovelace
Year: 1843
Category: papers
MD
cat > "$C_PARSED/work/metadata.json" <<'JSON'
{"title":"Test Title","author":"Ada Lovelace","year":"1843","category":"papers"}
JSON
printf '{"chunk_id":"lovelace1843:0000","text":"Body"}\n' > "$C_PARSED/work/work/chunks.jsonl"
printf '{"chunk_id":"lovelace1843:0000","embedding":[0.1]}\n{"chunk_id":"lovelace1843:0001","embedding":[0.2]}\n' > "$C_PARSED/work/work/embeddings.jsonl"
printf '{"embedding_ok":true,"embedding_count":2}\n' > "$C_PARSED/work/work/run_meta.json"
printf pdf > "$C_DROP/work.pdf"
if ALEXANDRIA_PARSED_WORK="$C_PARSED/work" \
    ALEXANDRIA_DROP_DIR="$C_DROP" \
    ALEXANDRIA_LIBRARY_ROOT="$C_LIB" \
    ALEXANDRIA_FILE_LOG="$C_LOG" \
    "$ROOT/pipeline/alexandria_file_parsed_work.py" > "$TMP/chunk_bad.out" 2>&1; then
    cat "$TMP/chunk_bad.out"
    exit 1
fi
grep -q 'embedding count 2 != chunk count 1' "$TMP/chunk_bad.out"
test -f "$C_DROP/work.pdf"
! test -e "$C_LIB/papers/lovelace1843.md" || exit 1

# Pipeline control state must not be copied into filed artifact folders.
R_PARSED="$TMP/result_state_parsed"
R_DROP="$TMP/result_state_DROP_HERE"
R_LIB="$TMP/result_state_library"
mkdir -p "$R_PARSED/work" "$R_DROP" "$R_LIB"
cat > "$R_PARSED/work/work.md" <<'MD'
# Test Title

Author: Ada Lovelace
Year: 1843
Category: papers
MD
cat > "$R_PARSED/work/metadata.json" <<'JSON'
{"title":"Test Title","author":"Ada Lovelace","year":"1843","category":"papers"}
JSON
cat > "$R_PARSED/work/file_result.json" <<'JSON'
{"status":"held"}
JSON
printf pdf > "$R_DROP/work.pdf"
ALEXANDRIA_PARSED_WORK="$R_PARSED/work" \
ALEXANDRIA_DROP_DIR="$R_DROP" \
ALEXANDRIA_LIBRARY_ROOT="$R_LIB" \
"$ROOT/pipeline/alexandria_file_parsed_work.py"
! test -e "$R_LIB/papers/lovelace1843/file_result.json" || exit 1

# A partial source-pair match must not overwrite an existing different same-extension file.
M_PARSED="$TMP/mismatch_parsed"
M_DROP="$TMP/mismatch_DROP_HERE"
M_LIB="$TMP/mismatch_library"
M_LOG="$TMP/mismatch.log"
mkdir -p "$M_PARSED/work" "$M_DROP" "$M_LIB/papers/lovelace1843"
cat > "$M_PARSED/work/work.md" <<'MD'
# Test Title

Author: Ada Lovelace
Year: 1843
Category: papers
MD
cat > "$M_PARSED/work/metadata.json" <<'JSON'
{"title":"Test Title","author":"Ada Lovelace","year":"1843","category":"papers"}
JSON
printf oldpdf > "$M_LIB/papers/lovelace1843/lovelace1843.pdf"
printf sameepub > "$M_LIB/papers/lovelace1843/lovelace1843.epub"
old_pdf_sha="$(sha256sum "$M_LIB/papers/lovelace1843/lovelace1843.pdf" | awk '{print $1}')"
same_epub_sha="$(sha256sum "$M_LIB/papers/lovelace1843/lovelace1843.epub" | awk '{print $1}')"
cat > "$M_LIB/papers/lovelace1843/metadata.json" <<JSON
{"title":"Test Title","author":"Ada Lovelace","year":"1843","category":"papers","source_sha256":{"old.pdf":"$old_pdf_sha","old.epub":"$same_epub_sha"}}
JSON
printf newpdf > "$M_DROP/work.pdf"
printf sameepub > "$M_DROP/work.epub"
if ALEXANDRIA_PARSED_WORK="$M_PARSED/work" \
    ALEXANDRIA_DROP_DIR="$M_DROP" \
    ALEXANDRIA_LIBRARY_ROOT="$M_LIB" \
    ALEXANDRIA_FILE_LOG="$M_LOG" \
    "$ROOT/pipeline/alexandria_file_parsed_work.py" > "$TMP/mismatch.out" 2>&1; then
    cat "$TMP/mismatch.out"
    exit 1
fi
grep -q 'refusing to overwrite existing source copy with different content' "$TMP/mismatch.out"
grep -q oldpdf "$M_LIB/papers/lovelace1843/lovelace1843.pdf"
test -f "$M_DROP/work.pdf"
test -f "$M_DROP/work.epub"

# A retry after an interrupted first install must finish the missing top-level files.
P_PARSED="$TMP/partial_parsed"
P_DROP="$TMP/partial_DROP_HERE"
P_LIB="$TMP/partial_library"
P_LOG="$TMP/partial.log"
mkdir -p "$P_PARSED/work" "$P_DROP" "$P_LIB/papers/lovelace1843"
cat > "$P_PARSED/work/work.md" <<'MD'
# Test Title

Author: Ada Lovelace
Year: 1843
Category: papers
MD
cat > "$P_PARSED/work/metadata.json" <<'JSON'
{"title":"Test Title","author":"Ada Lovelace","year":"1843","category":"papers"}
JSON
printf pdf > "$P_LIB/papers/lovelace1843/lovelace1843.pdf"
printf pdf > "$P_DROP/work.pdf"
p_sha="$(sha256sum "$P_DROP/work.pdf" | awk '{print $1}')"
cat > "$P_LIB/papers/lovelace1843/metadata.json" <<JSON
{"title":"Test Title","author":"Ada Lovelace","year":"1843","category":"papers","source_sha256":{"work.pdf":"$p_sha"}}
JSON
ALEXANDRIA_PARSED_WORK="$P_PARSED/work" \
ALEXANDRIA_DROP_DIR="$P_DROP" \
ALEXANDRIA_LIBRARY_ROOT="$P_LIB" \
ALEXANDRIA_FILE_LOG="$P_LOG" \
"$ROOT/pipeline/alexandria_file_parsed_work.py"
test -f "$P_LIB/papers/lovelace1843.md"
test -f "$P_LIB/papers/lovelace1843.bib"
! test -e "$P_DROP/work.pdf" || exit 1

# Backfilling a missing BibTeX sidecar must not replace existing parsed artifacts.
B_PARSED="$TMP/backfill_parsed"
B_DROP="$TMP/backfill_DROP_HERE"
B_LIB="$TMP/backfill_library"
mkdir -p "$B_PARSED/work/work" "$B_DROP" "$B_LIB/papers/lovelace1843"
cat > "$B_PARSED/work/work.md" <<'MD'
# Test Title

Author: Ada Lovelace
Year: 1843
Category: papers
MD
cat > "$B_PARSED/work/metadata.json" <<'JSON'
{"title":"Test Title","author":"Ada Lovelace","year":"1843","category":"papers"}
JSON
printf '{"chunk_id":"new"}\n' > "$B_PARSED/work/work/chunks.jsonl"
printf '{"embedding_ok":false}\n' > "$B_PARSED/work/work/run_meta.json"
printf oldchunk > "$B_LIB/papers/lovelace1843/chunks.jsonl"
printf pdf > "$B_LIB/papers/lovelace1843/lovelace1843.pdf"
printf canonical > "$B_LIB/papers/lovelace1843.md"
printf pdf > "$B_DROP/work.pdf"
b_sha="$(sha256sum "$B_DROP/work.pdf" | awk '{print $1}')"
cat > "$B_LIB/papers/lovelace1843/metadata.json" <<JSON
{"title":"Test Title","author":"Ada Lovelace","year":"1843","category":"papers","source_sha256":{"work.pdf":"$b_sha"}}
JSON
ALEXANDRIA_PARSED_WORK="$B_PARSED/work" \
ALEXANDRIA_DROP_DIR="$B_DROP" \
ALEXANDRIA_LIBRARY_ROOT="$B_LIB" \
"$ROOT/pipeline/alexandria_file_parsed_work.py"
test -f "$B_LIB/papers/lovelace1843.bib"
grep -q oldchunk "$B_LIB/papers/lovelace1843/chunks.jsonl"

# Low-confidence metadata should be held, not normally filed, and the source stays.
H_PARSED="$TMP/hold_parsed"
H_DROP="$TMP/hold_DROP_HERE"
H_LIB="$TMP/hold_library"
H_LOG="$TMP/hold.log"
mkdir -p "$H_PARSED/unknown" "$H_DROP" "$H_LIB"
cat > "$H_PARSED/unknown/unknown.md" <<'MD'
# unknown

OCR fragments without usable metadata.
MD
cat > "$H_PARSED/unknown/metadata.json" <<'JSON'
{"title":"unknown","author":"unknown","year":"nodate","category":"misc","metadata_confidence":"low"}
JSON
printf pdf > "$H_DROP/unknown.pdf"
ALEXANDRIA_PARSED_WORK="$H_PARSED/unknown" \
ALEXANDRIA_DROP_DIR="$H_DROP" \
ALEXANDRIA_LIBRARY_ROOT="$H_LIB" \
ALEXANDRIA_FILE_LOG="$H_LOG" \
"$ROOT/pipeline/alexandria_file_parsed_work.py"
test -f "$H_LIB/_hold/unknown/hold_reason.txt"
test -f "$H_LIB/_hold/unknown/unknown.pdf"
test -f "$H_LIB/_hold/unknown/metadata.json"
test -f "$H_DROP/unknown.pdf"
! test -e "$H_LIB/misc/unknownnodate.md" || exit 1
grep -q 'HELD unknown' "$H_LOG"
