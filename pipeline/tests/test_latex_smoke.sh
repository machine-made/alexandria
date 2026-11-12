#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TMP="$(mktemp -d "$ROOT/.tmp.latex_smoke.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
SMOKE="$ROOT/pipeline/alexandria_latex_smoke.py"
PYTHON="$(command -v python3)"

assert_has() {
    local file="$1" needle="$2"
    grep -Fq "$needle" "$file"
}

assert_nonempty_file() {
    local file="$1"
    [[ -s "$file" ]] || { echo "missing or empty file: $file"; exit 1; }
}

write_minimal_bib() {
    local bib="$1"
    cat > "$bib" <<'BIB'
@book{skiptest2024,
  title = {Skip Test},
  author = {Ada Lovelace},
  year = {2024},
  publisher = {Example Press}
}
BIB
}

MINIMAL_BIB="$TMP/minimal.bib"
write_minimal_bib "$MINIMAL_BIB"
FAKEBIN="$TMP/fakebin"
mkdir -p "$FAKEBIN"
SKIP_OUT="$TMP/skip.out"
PATH="$FAKEBIN" "$PYTHON" "$SMOKE" --bib "$MINIMAL_BIB" > "$SKIP_OUT" 2>&1
assert_has "$SKIP_OUT" "skipped: no LaTeX toolchain"

if ! command -v biber >/dev/null 2>&1 || ! command -v pdflatex >/dev/null 2>&1; then
    echo "skipped: no LaTeX toolchain for compile assertions"
    exit 0
fi

LIB="$TMP/library"
mkdir -p "$LIB/papers/curie1903" "$LIB/optimization/chatelet1740"
cat > "$LIB/papers/curie1903.bib" <<'BIB'
@article{curie1903,
  title = {Café Measurements},
  author = {Marie Curie and Frédéric Joliot-Curie},
  year = {1903},
  journal = {Journal des expériences}
}
BIB
cat > "$LIB/optimization/chatelet1740.bib" <<'BIB'
@book{chatelet1740,
  title = {Institutions de physique},
  author = {Émilie du Châtelet},
  year = {1740},
  publisher = {Prault}
}
BIB

SUCCESS_OUT="$TMP/success.out"
( cd "$TMP" && "$SMOKE" --library "$LIB" --keep ) > "$SUCCESS_OUT" 2>&1
assert_has "$LIB/alexandria.bib" "@article{curie1903,"
assert_has "$LIB/alexandria.bib" "@book{chatelet1740,"
assert_has "$SUCCESS_OUT" "latex smoke succeeded: 2 entries resolved"
PDF_PATH="$(awk -F': ' '/^pdf: / {print $2}' "$SUCCESS_OUT" | tail -n 1)"
assert_nonempty_file "$PDF_PATH"

BROKEN_BIB="$TMP/broken.bib"
cat > "$BROKEN_BIB" <<'BIB'
@article{brokenkey,
  title = {Broken Entry},
  author = {Ada Lovelace}
  year = {1843},
  journal = {Journal of Bad Bib}
}
BIB
BROKEN_OUT="$TMP/broken.out"
if "$SMOKE" --bib "$BROKEN_BIB" > "$BROKEN_OUT" 2>&1; then
    cat "$BROKEN_OUT"
    exit 1
fi
assert_has "$BROKEN_OUT" "latex smoke failed"
assert_has "$BROKEN_OUT" "brokenkey"

EMPTY_BIB="$TMP/empty.bib"
: > "$EMPTY_BIB"
EMPTY_OUT="$TMP/empty.out"
if "$SMOKE" --bib "$EMPTY_BIB" > "$EMPTY_OUT" 2>&1; then
    cat "$EMPTY_OUT"
    exit 1
fi
assert_has "$EMPTY_OUT" "empty bibliography"
