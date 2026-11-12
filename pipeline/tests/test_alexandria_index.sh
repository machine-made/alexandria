#!/usr/bin/env bash
# Fixture + real-library regression suite for pipeline/alexandria_index.py
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
INDEX="$ROOT/pipeline/alexandria_index.py"
FIX="$ROOT/pipeline/tests/fixtures/index_lib"
TMP="$(mktemp -d "$ROOT/.tmp.alexandria-index.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

export PYTHONPATH="$ROOT/pipeline${PYTHONPATH:+:$PYTHONPATH}"

DB="$TMP/index.db"
LIB="$TMP/lib"
# Copy fixture library so upsert / missing-bib mutations stay local.
cp -a "$FIX" "$LIB"
# Drop non-library helper file from the copy (not a category).
rm -f "$LIB/query_vectors.json"

run_index() {
    # Flags live on the subcommand: <cmd> --library ... --db ... [args]
    local cmd="$1"
    shift
    python3 "$INDEX" "$cmd" --library "$LIB" --db "$DB" "$@"
}

assert_json_field() {
    local file="$1" field="$2" expected="$3"
    python3 - "$file" "$field" "$expected" <<'PY'
import json, sys
path, field, expected = sys.argv[1], sys.argv[2], sys.argv[3]
data = json.loads(open(path, encoding="utf-8").read())
cur = data
for part in field.split("."):
    if part.isdigit():
        cur = cur[int(part)]
    else:
        cur = cur[part]
got = str(cur)
if got != expected:
    raise SystemExit(f"expected {field}={expected!r}, got {got!r} in {path}")
PY
}

echo "== schema columns + uniqueness =="
run_index rebuild --json > "$TMP/rebuild1.json"
python3 - "$DB" <<'PY'
import sqlite3, sys
db = sys.argv[1]
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row
required = {
    "works": {"key","bib_key","category","title","authors","year","doi","isbn","arxiv_id","md_path","bib_path","source_hashes"},
    "chunks": {"chunk_id","key","bib_key","category","title","year","chunk_ordinal","text","text_hash","md_path"},
    "chunk_embeddings": {"chunk_id","key","bib_key","embedding","embedding_dim","embedding_model","text_hash"},
    "source_files": {"key","name","sha256"},
    "bib_entries": {"key","bib_key","entry_type","bib_path"},
    "index_meta": {"meta_key","meta_value"},
}
for table, cols in required.items():
    have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    missing = cols - have
    assert not missing, (table, missing)
# uniqueness: chunk_id primary, works.key primary
assert conn.execute("SELECT chunk_id FROM chunks GROUP BY chunk_id HAVING COUNT(*)>1").fetchone() is None, "duplicate chunk_id"
n = conn.execute("SELECT COUNT(*) n FROM chunks").fetchone()["n"]
assert n >= 5, n
# every chunk has key + bib_key
bad = conn.execute("SELECT COUNT(*) n FROM chunks WHERE key='' OR bib_key='' OR key IS NULL OR bib_key IS NULL").fetchone()["n"]
assert bad == 0, bad
# FTS5 present
assert conn.execute("SELECT COUNT(*) n FROM chunks_fts").fetchone()["n"] == n
print(f"schema ok: chunks={n}")
PY

echo "== harvester: filed categories only; missing .bib is error =="
# DROP_HERE / _hold / .sc should be ignored even if present under LIB
mkdir -p "$LIB/DROP_HERE" "$LIB/_hold/fake" "$LIB/.sc/noise"
printf '# ignore me\n' > "$LIB/DROP_HERE/noise2020.md"
printf '# hold\n' > "$LIB/_hold/fake/hold2020.md"
# missing-bib work
mkdir -p "$LIB/papers/nobib2022"
printf '# No Bib\n' > "$LIB/papers/nobib2022.md"
cat > "$LIB/papers/nobib2022/metadata.json" <<'JSON'
{"title":"No Bib","author":"N. Obib","authors":["N. Obib"],"year":"2022","category":"papers"}
JSON
printf '%s\n' '{"chunk_id":"nobib:0000:ffff","chunk_index":0,"text":"orphan without bib"}' > "$LIB/papers/nobib2022/chunks.jsonl"
run_index rebuild --json > "$TMP/rebuild_nobib.json"
python3 - "$DB" <<'PY'
import sqlite3, sys, json
conn = sqlite3.connect(sys.argv[1])
conn.row_factory = sqlite3.Row
keys = {r["key"] for r in conn.execute("SELECT key FROM works")}
assert "noise2020" not in keys and "hold2020" not in keys, keys
row = conn.execute("SELECT valid, validation_errors, bib_key FROM works WHERE key='nobib2022'").fetchone()
assert row is not None, "nobib2022 should be recorded"
assert int(row["valid"]) == 0
errs = json.loads(row["validation_errors"])
assert any("missing .bib" in e for e in errs), errs
# invalid works must not contribute chunks
n = conn.execute("SELECT COUNT(*) n FROM chunks WHERE key='nobib2022'").fetchone()["n"]
assert n == 0, n
print("harvester refusal ok")
PY

echo "== key mismatch refusal =="
mkdir -p "$LIB/papers/badkey2023"
printf '# Bad Key\n' > "$LIB/papers/badkey2023.md"
cat > "$LIB/papers/badkey2023.bib" <<'BIB'
@article{wrongkey,
  title = {Bad Key},
  author = {X. Wrong},
  year = {2023}
}
BIB
cat > "$LIB/papers/badkey2023/metadata.json" <<'JSON'
{"title":"Bad Key","author":"X. Wrong","authors":["X. Wrong"],"year":"2023","category":"papers","key":"also_wrong"}
JSON
printf '%s\n' '{"chunk_id":"bad:0000:1111","chunk_index":0,"text":"should not index"}' > "$LIB/papers/badkey2023/chunks.jsonl"
run_index rebuild --json > "$TMP/rebuild_badkey.json"
python3 - "$DB" <<'PY'
import sqlite3, sys, json
conn = sqlite3.connect(sys.argv[1])
conn.row_factory = sqlite3.Row
row = conn.execute("SELECT valid, validation_errors FROM works WHERE key='badkey2023'").fetchone()
assert row is not None
assert int(row["valid"]) == 0
errs = " | ".join(json.loads(row["validation_errors"]))
assert "disagrees" in errs or "wrongkey" in errs or "also_wrong" in errs, errs
assert conn.execute("SELECT COUNT(*) n FROM chunks WHERE key='badkey2023'").fetchone()["n"] == 0
print("key mismatch refusal ok")
PY

echo "== FTS5 exact search =="
# rebuild clean fixture-only lib without the synthetic bad works for ranking tests
rm -rf "$LIB"
cp -a "$FIX" "$LIB"
rm -f "$LIB/query_vectors.json"
run_index rebuild --json > "$TMP/rebuild_clean.json"
run_index search --mode fts --query "oblique shock" --json --limit 5 > "$TMP/fts_oblique.json"
assert_json_field "$TMP/fts_oblique.json" "results.0.key" "alpha2020"
assert_json_field "$TMP/fts_oblique.json" "results.0.bib_key" "alpha2020"
python3 - "$TMP/fts_oblique.json" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
assert data["results"], data
for r in data["results"]:
    for field in ("key","bib_key","category","title","year","snippet","path","score"):
        assert field in r and r[field] is not None, (field, r)
    assert r["bib_key"]
print("fts result shape ok")
PY
run_index search --mode fts --query "transpiration cooling" --json --limit 5 > "$TMP/fts_cool.json"
assert_json_field "$TMP/fts_cool.json" "results.0.key" "cool2021"
# no-result
run_index search --mode fts --query "zzznomatchxyz123" --json --limit 5 > "$TMP/fts_empty.json"
python3 - "$TMP/fts_empty.json" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
assert data["results"] == []
print("fts empty ok")
PY

echo "== vector cosine search (offline query vector) =="
run_index search --mode vector --query "oblique" --query-vector '[1.0,0.0,0.0,0.0]' --json --limit 3 > "$TMP/vec_oblique.json"
assert_json_field "$TMP/vec_oblique.json" "results.0.key" "alpha2020"
run_index search --mode vector --query "cooling" --query-vector '[0.0,0.0,1.0,0.0]' --json --limit 3 > "$TMP/vec_cool.json"
assert_json_field "$TMP/vec_cool.json" "results.0.key" "cool2021"

echo "== hybrid ranker =="
run_index search --mode hybrid --query "boundary layer" --query-vector '[1.0,0.0,0.0,0.0]' --json --limit 5 > "$TMP/hyb.json"
python3 - "$TMP/hyb.json" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
assert data["results"]
top_keys = [r["key"] for r in data["results"]]
assert "alpha2020" in top_keys, top_keys
for r in data["results"]:
    assert r["bib_key"]
print("hybrid ok", top_keys[:3])
PY

echo "== dimension mismatch detection =="
python3 - "$DB" <<'PY'
import sqlite3, sys, struct
conn = sqlite3.connect(sys.argv[1])
# inject a 2-float blob while claiming dim=4
blob = struct.pack("<ff", 1.0, 0.0)
conn.execute(
    "UPDATE chunk_embeddings SET embedding=?, embedding_dim=4 WHERE chunk_id=(SELECT chunk_id FROM chunk_embeddings LIMIT 1)",
    (blob,),
)
conn.commit()
PY
set +e
run_index validate --json > "$TMP/val_dim.json"
val_rc=$?
set -e
test "$val_rc" -ne 0
python3 - "$TMP/val_dim.json" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
assert data["ok"] is False
joined = " ".join(data["problems"])
assert "blob length" in joined or "dimension" in joined.lower(), data["problems"]
print("dimension mismatch validate ok")
PY
# restore clean index
run_index rebuild --json > "$TMP/rebuild_after_dim.json"

echo "== stale embedding (text-hash) detection =="
python3 - "$DB" <<'PY'
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
conn.execute(
    "UPDATE chunk_embeddings SET text_hash='deadbeef'*4 WHERE chunk_id=(SELECT chunk_id FROM chunk_embeddings LIMIT 1)"
)
conn.commit()
PY
set +e
run_index validate --json > "$TMP/val_stale.json"
val_rc=$?
set -e
test "$val_rc" -ne 0
python3 - "$TMP/val_stale.json" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
assert data["ok"] is False
assert any("stale embedding" in p for p in data["problems"]), data["problems"]
print("stale embedding validate ok")
PY
run_index rebuild --json > /dev/null

echo "== idempotent rebuild =="
run_index rebuild --json > "$TMP/id1.json"
python3 - "$DB" "$TMP/snap_a.json" <<'PY'
import json, sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
conn.row_factory = sqlite3.Row
chunks = [dict(r) for r in conn.execute(
    "SELECT chunk_id, key, bib_key, text_hash, chunk_ordinal FROM chunks ORDER BY chunk_id"
)]
embs = [dict(r) for r in conn.execute(
    "SELECT chunk_id, embedding_dim, text_hash, length(embedding) AS blob_len FROM chunk_embeddings ORDER BY chunk_id"
)]
json.dump({"chunks": chunks, "embs": embs}, open(sys.argv[2], "w"), sort_keys=True, indent=2)
PY
run_index rebuild --json > "$TMP/id2.json"
python3 - "$DB" "$TMP/snap_b.json" <<'PY'
import json, sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
conn.row_factory = sqlite3.Row
chunks = [dict(r) for r in conn.execute(
    "SELECT chunk_id, key, bib_key, text_hash, chunk_ordinal FROM chunks ORDER BY chunk_id"
)]
embs = [dict(r) for r in conn.execute(
    "SELECT chunk_id, embedding_dim, text_hash, length(embedding) AS blob_len FROM chunk_embeddings ORDER BY chunk_id"
)]
json.dump({"chunks": chunks, "embs": embs}, open(sys.argv[2], "w"), sort_keys=True, indent=2)
PY
diff -u "$TMP/snap_a.json" "$TMP/snap_b.json"
echo "idempotent rebuild ok"

echo "== upsert-work replaces stale chunks =="
# mutate alpha chunk text on disk then upsert
python3 - <<PY
import json
from pathlib import Path
p = Path("$LIB/papers/alpha2020/chunks.jsonl")
rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
rows[0]["text"] = "UPDATED oblique shock boundary layer content for upsert test."
rows[0]["chunk_id"] = "alpha_src:0000:updatedupdated00"
p.write_text("".join(json.dumps(r, sort_keys=True)+"\\n" for r in rows))
# keep embedding keyed to new chunk_id
ep = Path("$LIB/papers/alpha2020/embeddings.jsonl")
erows = [json.loads(l) for l in ep.read_text().splitlines() if l.strip()]
erows[0]["chunk_id"] = rows[0]["chunk_id"]
ep.write_text("".join(json.dumps(e, separators=(",",":"))+"\\n" for e in erows))
PY
run_index upsert-work --key alpha2020 --category papers --json > "$TMP/upsert.json"
python3 - "$DB" <<'PY'
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
rows = list(conn.execute("SELECT chunk_id, text FROM chunks WHERE key='alpha2020' ORDER BY chunk_ordinal"))
assert any("UPDATED oblique" in r[1] for r in rows), rows
assert any(r[0] == "alpha_src:0000:updatedupdated00" for r in rows), rows
# old first chunk id should be gone
old = conn.execute("SELECT COUNT(*) FROM chunks WHERE chunk_id='alpha_src:0000:aaaaaaaaaaaaaaaa'").fetchone()[0]
assert old == 0, old
print("upsert replaces stale chunks ok")
PY

echo "== validate detects missing bib_key =="
python3 - "$DB" <<'PY'
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
conn.execute("UPDATE chunks SET bib_key='' WHERE chunk_id=(SELECT chunk_id FROM chunks LIMIT 1)")
conn.commit()
PY
set +e
run_index validate --json > "$TMP/val_bib.json"
val_rc=$?
set -e
test "$val_rc" -ne 0
python3 - "$TMP/val_bib.json" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
assert data["ok"] is False
assert any("missing bib_key" in p for p in data["problems"]), data["problems"]
print("missing bib_key validate ok")
PY

echo "== stats CLI =="
run_index rebuild --json > /dev/null
run_index stats --json > "$TMP/stats.json"
python3 - "$TMP/stats.json" <<'PY'
import json, sys
s = json.load(open(sys.argv[1]))
assert s["works"] >= 3
assert s["chunks"] >= 5
assert s["chunk_embeddings"] >= 5
assert s["schema_version"]
print("stats ok", s["works"], s["chunks"])
PY

echo "== human table search (smoke) =="
run_index search --mode fts --query "portfolio" --limit 3 | tee "$TMP/table.txt" | grep -q beta2020

echo "== scriptorium layout: vectors/<embedder-id>/<category>/<key>.jsonl =="
SLIB="$TMP/slib"
cp -a "$LIB" "$SLIB"
python3 - "$SLIB" <<'PY'
import json, shutil, sys
from pathlib import Path
root = Path(sys.argv[1])
vec = root / "vectors" / "fixture-mini"
for emb in sorted(root.glob("*/*/embeddings.jsonl")):
    dst = vec / emb.parent.parent.name / f"{emb.parent.name}.jsonl"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(emb), str(dst))
(vec / "embedder.json").write_text(json.dumps({"id": "fixture-mini", "runtime_tag": "fixture-mini", "dim": 4}))
(root / "scriptorium.toml").write_text('format_version = 1\nactive_embedder = "fixture-mini"\n')
PY
SDB="$TMP/sindex.db"
python3 "$INDEX" rebuild --library "$SLIB" --db "$SDB" --json > "$TMP/srebuild.json"
python3 - "$TMP/srebuild.json" "$TMP/rebuild_clean.json" <<'PY'
import json, sys
new, legacy = (json.load(open(p))["stats"] for p in sys.argv[1:3])
for field in ("works", "chunks", "chunk_embeddings", "works_invalid"):
    assert new[field] == legacy[field], (field, new, legacy)
print("scriptorium layout indexes like the legacy layout", {k: new[k] for k in ("works", "chunks", "chunk_embeddings")})
PY
# A vector set from another model must make the work invalid, not mix in.
sed -i 's/"model":"fixture-mini"/"model":"other-model"/' "$SLIB/vectors/fixture-mini/papers/alpha2020.jsonl"
python3 "$INDEX" rebuild --library "$SLIB" --db "$SDB" --json > "$TMP/smixed.json"
python3 - "$TMP/smixed.json" <<'PY'
import json, sys
stats = json.load(open(sys.argv[1]))["stats"]
assert stats["works_invalid"] >= 1, stats
print("foreign embedder tag rejected")
PY
python3 "$INDEX" upsert-work --library "$SLIB" --db "$SDB" --key alpha2020 --json > "$TMP/supsert.json" || true
python3 - "$TMP/supsert.json" <<'PY'
import json, sys
out = json.load(open(sys.argv[1]))
assert out["valid"] is False and any("active embedder" in e for e in out["validation_errors"]), out
print("upsert-work reports the embedder mismatch")
PY

echo "== ALEXANDRIA_LIBRARY is the default --library =="
ALEXANDRIA_LIBRARY="$SLIB" python3 "$INDEX" rebuild --db "$TMP/envlib.db" --json > "$TMP/envlib.json"
python3 - "$TMP/envlib.json" <<'PY'
import json, sys
stats = json.load(open(sys.argv[1]))["stats"]
assert stats["works"] == 3 and stats["library_root"].endswith("/slib"), stats
print("ALEXANDRIA_LIBRARY default ok")
PY

echo "== export-serving: strips vectors, keeps the sidecar stamp valid, sets the serving library root =="
python3 "$INDEX" export-serving --library "$SLIB" --db "$SDB" --out "$TMP/serving.db" --library-root /srv/library --json > "$TMP/export.json"
python3 - "$TMP/export.json" "$SDB" "$TMP/serving.db" <<'PY'
import json, sys
import alexandria_index as idx
out = json.load(open(sys.argv[1]))
assert out["ok"] and out["output_size"] > 0, out
from pathlib import Path
src, dst = idx.connect(Path(sys.argv[2])), idx.connect(Path(sys.argv[3]))
assert idx.get_meta(dst, "library_root") == "/srv/library"
assert idx.get_meta(src, "library_root") != "/srv/library"
assert dst.execute("SELECT MAX(LENGTH(embedding)) FROM chunk_embeddings").fetchone()[0] == 0
assert idx._index_stamp(src) == idx._index_stamp(dst), "stripped copy changes the sidecar stamp"
assert dst.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == src.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
print("export-serving ok")
PY

echo "ALL ALEXANDRIA INDEX TESTS PASSED"
