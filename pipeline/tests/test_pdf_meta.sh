#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="$ROOT/.venv/bin/python"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

[ -x "$PY" ] || { echo "missing .venv python with pypdf: $PY"; exit 1; }

PDF="$TMP/roundtrip.pdf"
META="$TMP/artifacts/pdf_meta.json"
"$PY" - "$PDF" <<'PY'
from pathlib import Path
import sys
from pypdf import PdfWriter

pdf = Path(sys.argv[1])
writer = PdfWriter()
writer.add_blank_page(width=72, height=72)
writer.add_metadata(
    {
        "/Title": "Round Trip Metadata",
        "/Author": "Ada Lovelace and Grace Hopper",
        "/Keywords": "DOI: 10.5555/ABC.DEF; ISBN 978-1-4028-9462-6; arXiv:2401.01234",
        "/Publisher": "Analytical Engine Press",
        "/Journal": "Journal of Metadata Tests",
        "/CreationDate": "D:20200102030405Z",
        "/ModDate": "D:20210102030405Z",
    }
)
with pdf.open("wb") as handle:
    writer.write(handle)
PY

"$PY" "$ROOT/pipeline/alexandria_pdf_meta.py" "$PDF" "$META" >/dev/null
python3 - <<PY
import json
from pathlib import Path
m = json.loads(Path('$META').read_text(encoding='utf-8'))
assert m['schema_version'] == 1
assert m['extractor'] == 'alexandria_pdf_meta.py'
assert m['source']['name'] == 'roundtrip.pdf'
assert m['source']['sha256']
assert m['info']['/Title'] == 'Round Trip Metadata'
assert m['title'] == 'Round Trip Metadata'
assert m['author'] == 'Ada Lovelace; Grace Hopper'
assert m['authors'] == ['Ada Lovelace', 'Grace Hopper']
assert m['doi'] == '10.5555/abc.def'
assert m['isbn'] == '9781402894626'
assert m['arxiv_id'] == '2401.01234'
assert m['publisher'] == 'Analytical Engine Press'
assert m['journal'] == 'Journal of Metadata Tests'
assert m['pages'] == 1
assert m['page_count'] == 1
assert m['dates']['created'].startswith('2020-01-02T03:04:05')
assert m['dates']['modified'].startswith('2021-01-02T03:04:05')
PY

# NO-LOSS: the structured artifact survives source deletion because it was written
# before any scrub/removal step consumes the only PDF copy.
rm "$PDF"
test -s "$META"
python3 - <<PY
import json
from pathlib import Path
m = json.loads(Path('$META').read_text(encoding='utf-8'))
assert m['title'] == 'Round Trip Metadata'
assert m['source']['sha256']
PY
