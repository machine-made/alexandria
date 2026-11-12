#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
DROP="$TMP/DROP_HERE"
PARSED="$TMP/parsed"
LIB="$TMP/library"
LOG="$TMP/drain.log"
PARSER="$TMP/parser.sh"
mkdir -p "$DROP" "$PARSED" "$LIB"
cat > "$PARSER" <<'PARSER'
#!/usr/bin/env bash
set -euo pipefail
src="$1"
out="$2"
mkdir -p "$out"
cat > "$out/$(basename "${src%.*}").md" <<MD
# A Small Paper

Author: Ada Lovelace
Year: 1843
Category: papers
MD
PARSER
chmod +x "$PARSER"
printf pdf > "$DROP/work.pdf"
ALEXANDRIA_DROP_DIR="$DROP" \
ALEXANDRIA_PARSED_DIR="$PARSED" \
ALEXANDRIA_LIBRARY_ROOT="$LIB" \
ALEXANDRIA_DRAIN_LOG="$LOG" \
ALEXANDRIA_PARSER_CMD="$PARSER" \
ALEXANDRIA_FILE_AFTER_PARSE=1 \
"$ROOT/pipeline/alexandria_drain_drop_here.sh"

test -f "$LIB/papers/lovelace1843.md"
test -f "$LIB/papers/lovelace1843/lovelace1843.pdf"
! test -e "$DROP/work.pdf" || exit 1
grep -q 'FILED work' "$LOG"

# Mixed-case source extensions must be detected and later removed after filing.
CASE_DROP="$TMP/case_DROP_HERE"
CASE_PARSED="$TMP/case_parsed"
CASE_LIB="$TMP/case_library"
CASE_LOG="$TMP/case_drain.log"
mkdir -p "$CASE_DROP" "$CASE_PARSED" "$CASE_LIB"
printf pdf > "$CASE_DROP/mixed.Pdf"
ALEXANDRIA_DROP_DIR="$CASE_DROP" \
ALEXANDRIA_PARSED_DIR="$CASE_PARSED" \
ALEXANDRIA_LIBRARY_ROOT="$CASE_LIB" \
ALEXANDRIA_DRAIN_LOG="$CASE_LOG" \
ALEXANDRIA_PARSER_CMD="$PARSER" \
ALEXANDRIA_FILE_AFTER_PARSE=1 \
"$ROOT/pipeline/alexandria_drain_drop_here.sh"

test -f "$CASE_LIB/papers/lovelace1843.md"
test -f "$CASE_LIB/papers/lovelace1843/lovelace1843.pdf"
! test -e "$CASE_DROP/mixed.Pdf" || exit 1
grep -q 'FILED mixed' "$CASE_LOG"

# Existing parsed output should still be fileable when filing is requested.
REC_DROP="$TMP/recovery_DROP_HERE"
REC_PARSED="$TMP/recovery_parsed"
REC_LIB="$TMP/recovery_library"
REC_LOG="$TMP/recovery_drain.log"
mkdir -p "$REC_DROP" "$REC_PARSED/work" "$REC_PARSED/work/work" "$REC_LIB"
cat > "$REC_PARSED/work/work.md" <<MD
# A Small Paper

Author: Ada Lovelace
Year: 1843
Category: papers
MD
printf '{"chunk_id":"lovelace1843:0000","text":"Body"}\n' > "$REC_PARSED/work/work/chunks.jsonl"
printf '{"embedding_ok":false,"embedding_count":0}\n' > "$REC_PARSED/work/work/run_meta.json"
printf pdf > "$REC_DROP/work.pdf"
ALEXANDRIA_DROP_DIR="$REC_DROP" \
ALEXANDRIA_PARSED_DIR="$REC_PARSED" \
ALEXANDRIA_LIBRARY_ROOT="$REC_LIB" \
ALEXANDRIA_DRAIN_LOG="$REC_LOG" \
ALEXANDRIA_PARSER_CMD="$PARSER" \
ALEXANDRIA_FILE_AFTER_PARSE=1 \
"$ROOT/pipeline/alexandria_drain_drop_here.sh"

test -f "$REC_LIB/papers/lovelace1843.md"
test -f "$REC_LIB/papers/lovelace1843/lovelace1843.pdf"
! test -e "$REC_DROP/work.pdf" || exit 1
grep -q 'FILE_EXISTING_PARSED work' "$REC_LOG"
grep -q 'FILED work' "$REC_LOG"

# A held low-confidence first stem should not block later actionable stems forever.
HOLD_DROP="$TMP/hold_skip_DROP_HERE"
HOLD_PARSED="$TMP/hold_skip_parsed"
HOLD_LIB="$TMP/hold_skip_library"
HOLD_LOG="$TMP/hold_skip_drain.log"
HOLD_PARSER="$TMP/hold_parser.sh"
mkdir -p "$HOLD_DROP" "$HOLD_PARSED" "$HOLD_LIB"
cat > "$HOLD_PARSER" <<'PARSER'
#!/usr/bin/env bash
set -euo pipefail
src="$1"
out="$2"
stem="$(basename "${src%.*}")"
mkdir -p "$out"
if [ "$stem" = "a_unknown" ]; then
cat > "$out/$stem.md" <<MD
# a_unknown

OCR fragments.
MD
else
cat > "$out/$stem.md" <<MD
# A Small Paper

Author: Ada Lovelace
Year: 1843
Category: papers
MD
fi
PARSER
chmod +x "$HOLD_PARSER"
printf pdf > "$HOLD_DROP/a_unknown.pdf"
printf pdf > "$HOLD_DROP/b_work.pdf"
ALEXANDRIA_DROP_DIR="$HOLD_DROP" \
ALEXANDRIA_PARSED_DIR="$HOLD_PARSED" \
ALEXANDRIA_LIBRARY_ROOT="$HOLD_LIB" \
ALEXANDRIA_DRAIN_LOG="$HOLD_LOG" \
ALEXANDRIA_PARSER_CMD="$HOLD_PARSER" \
ALEXANDRIA_FILE_AFTER_PARSE=1 \
"$ROOT/pipeline/alexandria_drain_drop_here.sh"

test -f "$HOLD_LIB/_hold/a_unknown/hold_reason.txt"
test -f "$HOLD_DROP/a_unknown.pdf"
test -f "$HOLD_DROP/b_work.pdf"

ALEXANDRIA_DROP_DIR="$HOLD_DROP" \
ALEXANDRIA_PARSED_DIR="$HOLD_PARSED" \
ALEXANDRIA_LIBRARY_ROOT="$HOLD_LIB" \
ALEXANDRIA_DRAIN_LOG="$HOLD_LOG" \
ALEXANDRIA_PARSER_CMD="$HOLD_PARSER" \
ALEXANDRIA_FILE_AFTER_PARSE=1 \
"$ROOT/pipeline/alexandria_drain_drop_here.sh"

test -f "$HOLD_LIB/papers/lovelace1843.md"
! test -e "$HOLD_DROP/b_work.pdf" || exit 1
test -f "$HOLD_DROP/a_unknown.pdf"
grep -q 'SKIP_HELD a_unknown' "$HOLD_LOG"
grep -q 'FILED b_work' "$HOLD_LOG"

# EPUB-only heuristic title/author must be held when OPF has no usable identity.
EPUB_DROP="$TMP/epub_DROP_HERE"
EPUB_PARSED="$TMP/epub_parsed"
EPUB_LIB="$TMP/epub_library"
EPUB_LOG="$TMP/epub_drain.log"
EPUB_PARSER="$TMP/epub_parser.sh"
mkdir -p "$EPUB_DROP" "$EPUB_PARSED" "$EPUB_LIB"
cat > "$EPUB_PARSER" <<'PARSER'
#!/usr/bin/env bash
set -euo pipefail
src="$1"
out="$2"
stem="$(basename "${src%.*}")"
mkdir -p "$out"
cat > "$out/$stem.md" <<'MD'
# The Indispensable Guide To

Excellent Writing And Speaking

First edition published 2016.
MD
PARSER
chmod +x "$EPUB_PARSER"
python3 - "$EPUB_DROP/heuristic_book.epub" <<'PY'
import sys
import zipfile
container = '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="content.opf"/></rootfiles></container>'
opf = '<package xmlns="http://www.idpf.org/2007/opf"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:language>en</dc:language></metadata></package>'
with zipfile.ZipFile(sys.argv[1], 'w') as archive:
    archive.writestr('META-INF/container.xml', container)
    archive.writestr('content.opf', opf)
PY
ALEXANDRIA_DROP_DIR="$EPUB_DROP" \
ALEXANDRIA_PARSED_DIR="$EPUB_PARSED" \
ALEXANDRIA_LIBRARY_ROOT="$EPUB_LIB" \
ALEXANDRIA_DRAIN_LOG="$EPUB_LOG" \
ALEXANDRIA_PARSER_CMD="$EPUB_PARSER" \
ALEXANDRIA_FILE_AFTER_PARSE=1 \
"$ROOT/pipeline/alexandria_drain_drop_here.sh"

test -f "$EPUB_LIB/_hold/heuristic_book/hold_reason.txt"
test -f "$EPUB_DROP/heuristic_book.epub"
test -f "$EPUB_PARSED/heuristic_book/epub_meta.json"
grep -q 'HELD heuristic_book' "$EPUB_LOG"
