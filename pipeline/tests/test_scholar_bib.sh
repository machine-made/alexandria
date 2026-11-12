#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TMP="$(mktemp -d "$ROOT/.tmp_scholar_bib.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
export PYTHONDONTWRITEBYTECODE=1

ENTRY="$TMP/scholar-entry.bib"
cat > "$ENTRY" <<'BIB'
@article{smith2020google,
  title={A {Google Scholar} Paper: Caf{\'e} \& Co.},
  author={Jane Smith and John Doe},
  year={2020},
  doi={https://doi.org/10.X/Y}
}
BIB

"$ROOT/pipeline/alexandria_scholar_bib.py" "$ENTRY" > "$TMP/smith.json"
python3 - "$TMP/smith.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)

assert data["key"] == "smith2020", data
assert data["scholar_key"] == "smith2020google", data
assert data["manual_bib_source"] == "google-scholar", data
assert data["title"] == "A Google Scholar Paper: Café & Co.", data
assert data["author"] == "Jane Smith and John Doe", data
assert data["authors"] == ["Jane Smith", "John Doe"], data
assert data["year"] == "2020", data
assert data["doi"] == "10.x/y", data
assert data["category"] == "papers", data
assert data["metadata_confidence"] == "high", data
PY

printf '%s\n' '@misc{mystery, title={Only {A} Title}}' \
  | "$ROOT/pipeline/alexandria_scholar_bib.py" > "$TMP/missing.json"
python3 - "$TMP/missing.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)

assert data["key"] == "unknownnodate", data
assert data["scholar_key"] == "mystery", data
assert data["title"] == "Only A Title", data
assert data["author"] == "unknown", data
assert data["authors"] == [], data
assert data["year"] == "nodate", data
assert data["doi"] == "", data
assert data["isbn"] == "", data
assert data["arxiv_id"] == "", data
assert data["metadata_confidence"] == "low", data
PY

printf '%s\n' '@article{gye2024scholar, title={Held Author}, author={李雷}, year={2024}}' \
  | "$ROOT/pipeline/alexandria_scholar_bib.py" > "$TMP/held.json"
python3 - "$TMP/held.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)

assert data["key"] == "unknown2024", data
assert data["scholar_key"] == "gye2024scholar", data
assert data["author"] == "李雷", data
assert data["authors"] == ["李雷"], data
assert data["metadata_confidence"] == "low", data
PY
