#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
DROP="$TMP/DROP_HERE"
PARSED="$TMP/parsed"
LOG="$TMP/drain.log"
PARSER="$TMP/parser.sh"
mkdir -p "$DROP" "$PARSED"
cat > "$PARSER" <<'PARSER'
#!/usr/bin/env bash
set -euo pipefail
src="$1"
out="$2"
mkdir -p "$out"
printf 'parsed %s\n' "$(basename "$src")" > "$out/$(basename "${src%.*}").md"
PARSER
chmod +x "$PARSER"
printf pdf > "$DROP/work.pdf"
printf epub > "$DROP/work.epub"
ALEXANDRIA_DROP_DIR="$DROP" \
ALEXANDRIA_PARSED_DIR="$PARSED" \
ALEXANDRIA_DRAIN_LOG="$LOG" \
ALEXANDRIA_PARSER_CMD="$PARSER" \
"$ROOT/pipeline/alexandria_drain_drop_here.sh"

test -f "$PARSED/work/work.md"
grep -q 'parsed work.pdf' "$PARSED/work/work.md"
test -f "$DROP/work.pdf"
test -f "$DROP/work.epub"
grep -q 'PARSED work source=work.pdf' "$LOG"

# Without a parser command the drain stops with a clear message; it has no built-in parser.
printf pdf > "$DROP/other.pdf"
if env -u ALEXANDRIA_PARSER_CMD \
    ALEXANDRIA_DROP_DIR="$DROP" ALEXANDRIA_PARSED_DIR="$TMP/parsed2" ALEXANDRIA_DRAIN_LOG="$TMP/drain2.log" \
    "$ROOT/pipeline/alexandria_drain_drop_here.sh" > "$TMP/noparser.out" 2>&1; then
    echo "drain without ALEXANDRIA_PARSER_CMD must fail"; exit 1
fi
grep -q 'set ALEXANDRIA_PARSER_CMD' "$TMP/noparser.out"
