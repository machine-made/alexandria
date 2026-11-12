#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRATCH_ROOT="$ROOT/.sc"
CREATED_SC=0
if [ ! -d "$SCRATCH_ROOT" ]; then
  CREATED_SC=1
  mkdir -p "$SCRATCH_ROOT"
fi
TMP="$(mktemp -d "$SCRATCH_ROOT/test-audit-bibtex.XXXXXX")"
cleanup() {
  rm -rf "$TMP"
  if [ "$CREATED_SC" -eq 1 ]; then
    rmdir "$SCRATCH_ROOT" 2>/dev/null || true
  fi
}
trap cleanup EXIT

library_manifest() {
  MANIFEST_DIR="$1" python3 - <<'PY'
import hashlib
import os
from pathlib import Path

root = Path(os.environ["MANIFEST_DIR"])
for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
    rel = path.relative_to(root).as_posix()
    if path.is_dir():
        print(f"D\t{rel}")
    elif path.is_file():
        print(f"F\t{rel}\t{hashlib.sha256(path.read_bytes()).hexdigest()}")
    elif path.is_symlink():
        print(f"L\t{rel}\t{os.readlink(path)}")
    else:
        print(f"?\t{rel}")
PY
}

LIB="$TMP/library"
OUT="$TMP/audit"
mkdir -p "$LIB/papers/smith2020" "$LIB/misc/smith2020"
cat > "$LIB/papers/smith2020.bib" <<'BIB'
@article{smith2020,
  title = {A Paper Copy},
  author = {Ada Smith},
  year = {2020}
}
BIB
cat > "$LIB/misc/smith2020.bib" <<'BIB'
@misc{smith2020,
  title = {A Misc Copy},
  author = {Ada Smith},
  year = {2020}
}
BIB
cat > "$LIB/papers/smith2020/metadata.json" <<'JSON'
{"title":"A Paper Copy","author":"Ada Smith","year":"2020","category":"papers","metadata_confidence":"medium"}
JSON
cat > "$LIB/misc/smith2020/metadata.json" <<'JSON'
{"title":"A Misc Copy","author":"Ada Smith","year":"2020","category":"misc","metadata_confidence":"medium"}
JSON

library_manifest "$LIB" > "$TMP/before.manifest"
"$ROOT/pipeline/alexandria_audit_bibtex.py" --library "$LIB" --out-dir "$OUT" > "$TMP/stdout.txt"
library_manifest "$LIB" > "$TMP/after.manifest"
diff -u "$TMP/before.manifest" "$TMP/after.manifest"

test -f "$OUT/bibtex_audit.json"
test -f "$OUT/bibtex_audit.tsv"
grep -q 'duplicate-key smith2020' "$TMP/stdout.txt"
grep -q 'smith2020' "$OUT/bibtex_audit.tsv"

REPORT="$OUT/bibtex_audit.json" python3 - <<'PY'
import json
import os
from pathlib import Path

report = json.loads(Path(os.environ["REPORT"]).read_text(encoding="utf-8"))
assert report["counts"]["works"] == 2, report["counts"]
assert report["counts"]["bib_sidecars"] == 2, report["counts"]
assert report["counts"]["keys"] == 2, report["counts"]
assert report["counts"]["duplicate_keys"] == 1, report["counts"]
assert report["counts"]["duplicate_key_occurrences"] == 1, report["counts"]
duplicates = report["library_findings"]["duplicate_keys"]
assert duplicates and duplicates[0]["key"] == "smith2020", duplicates
assert sorted(location["category"] for location in duplicates[0]["locations"]) == ["misc", "papers"], duplicates
risk_ids = {risk["risk_id"] for risk in report["synthetic_risks"]}
assert risk_ids == {"R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8"}, risk_ids
r1 = next(risk for risk in report["synthetic_risks"] if risk["risk_id"] == "R1")
assert r1["samples"][0]["duplicate_keys"] == ["lovelace1843"], r1
r7 = next(risk for risk in report["synthetic_risks"] if risk["risk_id"] == "R7")
r7_sample = r7["samples"][0]
assert r7_sample["root_has_old_title_after_rebuild"] is True, r7_sample
assert r7_sample["root_has_new_title_after_sidecar_edit_without_rebuild"] is False, r7_sample
assert r7_sample["root_still_has_deleted_sidecar_entry_without_rebuild"] is True, r7_sample
PY

EMPTY="$TMP/empty-library"
EMPTY_OUT="$TMP/empty-audit"
mkdir -p "$EMPTY"
"$ROOT/pipeline/alexandria_audit_bibtex.py" --library "$EMPTY" --out-dir "$EMPTY_OUT" > "$TMP/empty-stdout.txt"
EMPTY_REPORT="$EMPTY_OUT/bibtex_audit.json" python3 - <<'PY'
import json
import os
from pathlib import Path

report = json.loads(Path(os.environ["EMPTY_REPORT"]).read_text(encoding="utf-8"))
assert report["status"] == "clean", report["status"]
assert report["counts"]["works"] == 0, report["counts"]
assert report["counts"]["metadata_json"] == 0, report["counts"]
assert report["counts"]["bib_sidecars"] == 0, report["counts"]
assert report["counts"]["keys"] == 0, report["counts"]
assert report["counts"]["duplicate_keys"] == 0, report["counts"]
assert report["sample_failures"]["duplicate_keys"] == [], report["sample_failures"]
PY
