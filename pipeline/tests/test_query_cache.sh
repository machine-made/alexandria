#!/usr/bin/env bash
# Query-embedding cache: exact hits skip the embedder, nothing breaks when the cache does.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TMP="$(mktemp -d "$ROOT/.tmp.query-cache.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
export PYTHONPATH="$ROOT/pipeline${PYTHONPATH:+:$PYTHONPATH}"

cp -a "$ROOT/pipeline/tests/fixtures/index_lib" "$TMP/lib"
rm -f "$TMP/lib/query_vectors.json"
python3 "$ROOT/pipeline/alexandria_index.py" rebuild --library "$TMP/lib" --db "$TMP/index.db" --json > /dev/null

python3 - "$TMP" <<'PY'
import os, sqlite3, sys, time
from pathlib import Path
import numpy as np
import alexandria_index as ai
import alexandria_query_cache as qc

tmp = Path(sys.argv[1])
os.environ.pop(qc.ENV, None)

# --- the cache itself
path = tmp / "unit.sqlite"
vec = np.arange(8, dtype=np.float32) / 7
assert qc.get(path, "m", "text") is None
qc.put(path, "m", "text", vec)
assert np.array_equal(qc.get(path, "m", "text"), vec), "round trip changes the vector"
assert qc.get(path, "other-model", "text") is None, "the model is part of the key"
assert qc.get(path, "m", "text ") is None, "the key is the exact text"
raw = sqlite3.connect(path).execute("SELECT key FROM q").fetchone()[0]
assert "text" not in raw and len(raw) == 64, "only a hash of the query is stored"
assert b"Instruct" not in path.read_bytes()

# non-finite or empty vectors are not cached
qc.put(path, "m", "bad", np.array([1.0, np.nan], dtype=np.float32)); assert qc.get(path, "m", "bad") is None
qc.put(path, "m", "empty", np.array([], dtype=np.float32)); assert qc.get(path, "m", "empty") is None

# least recently used goes first
old_max, qc.MAX_ENTRIES = qc.MAX_ENTRIES, 3
lru = tmp / "lru.sqlite"
for name in "abcd":
    qc.put(lru, "m", name, vec); time.sleep(0.01)
    if name == "c":
        assert qc.get(lru, "m", "a") is not None  # touching a keeps it
time.sleep(0.01)
qc.put(lru, "m", "e", vec)
left = {n for n in "abcde" if qc.get(lru, "m", n) is not None}
assert len(left) == 3 and "e" in left and "a" in left, left
qc.MAX_ENTRIES = old_max

# a broken cache never raises
bad = tmp / "bad.sqlite"; bad.write_bytes(b"this is not a database" * 100)
assert qc.get(bad, "m", "x") is None
qc.put(bad, "m", "x", vec)
assert qc.get(tmp / "no_such_dir" / "c.sqlite", "m", "x") is None
qc.put(tmp / "no_such_dir" / "c.sqlite", "m", "x", vec)

# off and custom path
os.environ[qc.ENV] = "off"; assert qc.cache_path(str(tmp / "index.db")) is None
os.environ[qc.ENV] = str(tmp / "custom.sqlite"); assert qc.cache_path(str(tmp / "index.db")) == tmp / "custom.sqlite"
os.environ.pop(qc.ENV)
assert qc.cache_path(str(tmp / "index.db")) == tmp / "index.db.query_cache.sqlite"
assert qc.cache_path("") is None

# --- through the search path: the second identical query does not call the embedder
calls = []
def fake_embed(text, model, timeout=60):
    calls.append(text)
    return np.asarray([len(text) % 7 + 1, 2.0, 3.0, 4.0], dtype=np.float32)
ai.embed_query_ollama = fake_embed
conn = ai.connect(tmp / "index.db")
a = ai.resolve_query_vector(conn, "alpha beta")
b = ai.resolve_query_vector(conn, "alpha beta")
assert len(calls) == 1 and np.array_equal(a, b), calls
ai.resolve_query_vector(conn, "gamma")
assert len(calls) == 2
assert (tmp / "index.db.query_cache.sqlite").is_file(), "the cache sits next to the index"
os.environ[qc.ENV] = "off"
ai.resolve_query_vector(conn, "alpha beta")
assert len(calls) == 3, "ALEXANDRIA_QUERY_CACHE=off embeds every time"
print("query cache ok")
PY
echo "ALL QUERY CACHE TESTS PASSED"
