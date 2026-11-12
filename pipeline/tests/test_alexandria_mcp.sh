#!/usr/bin/env bash
# Deterministic, network-free suite for pipeline/alexandria_mcp.py
# Drives the server over real stdio JSON-RPC against the fixture index.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="$ROOT/.venv/bin/python"
MCP="$ROOT/pipeline/alexandria_mcp.py"
INDEX="$ROOT/pipeline/alexandria_index.py"
FIX="$ROOT/pipeline/tests/fixtures/index_lib"
TMP="$(mktemp -d "$ROOT/.tmp.alexandria-mcp.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

export PYTHONPATH="$ROOT/pipeline${PYTHONPATH:+:$PYTHONPATH}"

# Copy fixture library; keep query_vectors.json outside the library tree for the index.
LIB="$TMP/lib"
cp -a "$FIX" "$LIB"
QVEC_JSON="$FIX/query_vectors.json"
rm -f "$LIB/query_vectors.json"

DB="$TMP/index.db"
echo "== rebuild fixture index =="
"$PY" "$INDEX" rebuild --library "$LIB" --db "$DB" --json > "$TMP/rebuild.json"
"$PY" - "$TMP/rebuild.json" <<'PY'
import json, sys
stats = json.load(open(sys.argv[1]))["stats"]
assert stats["works"] >= 3, stats
assert stats["chunks"] >= 5, stats
print(f"fixture index ok: works={stats['works']} chunks={stats['chunks']}")
PY

# ---------------------------------------------------------------------------
# JSON-RPC driver (embedded): initialize → tools/list → tools/call
# ---------------------------------------------------------------------------
drive_mcp() {
    local reqs_file="$1"
    shift
    ALEXANDRIA_INDEX_DB="$DB" "$@" "$PY" - "$MCP" "$reqs_file" <<'PY'
import json, os, subprocess, sys, time, select

mcp_path, reqs_path = sys.argv[1], sys.argv[2]
requests = json.load(open(reqs_path, encoding="utf-8"))

env = os.environ.copy()
env["PYTHONPATH"] = os.path.dirname(mcp_path) + (
    (os.pathsep + env["PYTHONPATH"]) if env.get("PYTHONPATH") else ""
)

proc = subprocess.Popen(
    [sys.executable, mcp_path],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    env=env,
    bufsize=1,
)

def send(msg):
    line = json.dumps(msg, separators=(",", ":"))
    assert "\n" not in line
    proc.stdin.write(line + "\n")
    proc.stdin.flush()

def recv(timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            err = proc.stderr.read() if proc.stderr else ""
            raise SystemExit(f"server exited early code={proc.returncode} stderr={err!r}")
        r, _, _ = select.select([proc.stdout], [], [], 0.1)
        if not r:
            continue
        chunk = proc.stdout.readline()
        if not chunk:
            continue
        buf = chunk.strip()
        if not buf:
            continue
        return json.loads(buf)
    err = ""
    try:
        proc.kill()
        err = proc.stderr.read() if proc.stderr else ""
    except Exception:
        pass
    raise SystemExit(f"timeout waiting for response; stderr={err!r}")

responses = []
for item in requests:
    kind = item.get("kind", "request")
    msg = item["msg"]
    send(msg)
    if kind == "notification":
        continue
    responses.append(recv())

try:
    proc.stdin.close()
except Exception:
    pass
try:
    proc.wait(timeout=2)
except Exception:
    proc.kill()

print(json.dumps(responses, indent=2, ensure_ascii=False))
if proc.returncode not in (0, None) and proc.returncode != 0:
    err = proc.stderr.read() if proc.stderr else ""
    if not responses:
        raise SystemExit(f"server failed: code={proc.returncode} stderr={err!r}")
PY
}

PROTOCOL="2025-03-26"

make_init_sequence() {
    local out="$1"
    cat > "$out" <<EOF
[
  {
    "kind": "request",
    "msg": {
      "jsonrpc": "2.0",
      "id": 1,
      "method": "initialize",
      "params": {
        "protocolVersion": "$PROTOCOL",
        "capabilities": {},
        "clientInfo": {"name": "test_alexandria_mcp", "version": "0.0.1"}
      }
    }
  },
  {
    "kind": "notification",
    "msg": {
      "jsonrpc": "2.0",
      "method": "notifications/initialized"
    }
  },
  {
    "kind": "request",
    "msg": {
      "jsonrpc": "2.0",
      "id": 2,
      "method": "tools/list"
    }
  }
]
EOF
}

echo "== missing DB → clear error (no traceback) =="
MISSING_DB="$TMP/no-such-index.db"
set +e
ERR_OUT="$("$PY" "$MCP" --db "$MISSING_DB" 2>&1 >/dev/null)"
RC=$?
set -e
if [ "$RC" -eq 0 ]; then
    echo "FAIL: expected non-zero exit for missing DB"
    exit 1
fi
echo "$ERR_OUT" | grep -qi "not found\|unreadable\|ALEXANDRIA_INDEX_DB\|rebuild" || {
    echo "FAIL: missing-DB error not actionable: $ERR_OUT"
    exit 1
}
if echo "$ERR_OUT" | grep -q "Traceback"; then
    echo "FAIL: missing DB produced a traceback: $ERR_OUT"
    exit 1
fi
echo "missing DB error ok"

echo "== session 1: tools/list + search (parse bib_key for chain) =="
REQS1="$TMP/reqs_s1.json"
make_init_sequence "$REQS1"
"$PY" - "$REQS1" <<'PY'
import json, sys
path = sys.argv[1]
reqs = json.load(open(path))
reqs.append({
    "kind": "request",
    "msg": {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "search",
            "arguments": {
                "query": "transpiration cooling",
                "limit": 5,
                "mode": "fts",
            },
        },
    },
})
reqs.append({
    "kind": "request",
    "msg": {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "search",
            "arguments": {
                "query": "transpiration cooling",
                "limit": 5,
                "mode": "fts",
                "verbose": True,
            },
        },
    },
})
# Multi-level heading_path: cool2021 chunk 1 under "2.3 Estimator variance"
reqs.append({
    "kind": "request",
    "msg": {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {
            "name": "search",
            "arguments": {
                "query": "porous media",
                "limit": 3,
                "mode": "fts",
            },
        },
    },
})
reqs.append({
    "kind": "request",
    "msg": {
        "jsonrpc": "2.0",
        "id": 6,
        "method": "tools/call",
        "params": {
            "name": "get_chunk",
            "arguments": {"key": "cool2021", "ordinal": 1},
        },
    },
})
json.dump(reqs, open(path, "w"), indent=2)
PY

RESP1="$TMP/responses_s1.json"
drive_mcp "$REQS1" > "$RESP1"

CHAIN_KEY_FILE="$TMP/chain_bib_key.txt"
"$PY" - "$RESP1" "$CHAIN_KEY_FILE" <<'PY'
import json, sys
sys.path.insert(0, "pipeline")
import alexandria_mcp as mcp

REQUIRED = set(mcp.SEARCH_HIT_REQUIRED)
VERBOSE_EXTRA = set(mcp.SEARCH_HIT_VERBOSE_EXTRA)
DROPPED_BY_DEFAULT = VERBOSE_EXTRA | {"doi", "isbn", "arxiv_id"}

responses = json.load(open(sys.argv[1], encoding="utf-8"))
by_id = {r["id"]: r for r in responses if "id" in r}
assert set(by_id) >= {1, 2, 3, 4, 5, 6}, sorted(by_id)

init = by_id[1]
assert "result" in init, init
# Handshake payload shape, not just presence of a result: a server that answered
# initialize with an empty object would otherwise satisfy the check above.
assert "serverInfo" in init["result"] or "protocolVersion" in init["result"], init["result"]

tools = by_id[2]["result"]["tools"]
names = sorted(t["name"] for t in tools)
assert names == ["bib", "get_chunk", "get_work", "search"], names
for t in tools:
    assert all(c.isalnum() or c in "._-" for c in t["name"]), t["name"]
gc = next(t for t in tools if t["name"] == "get_chunk")
assert "lean" in gc["description"].lower(), gc["description"]
assert "verbose" in gc["description"].lower(), gc["description"]

def structured(resp):
    result = resp["result"]
    if result.get("isError"):
        return None, result
    sc = result.get("structuredContent")
    if sc is not None:
        return sc, result
    content = result.get("content") or []
    for block in content:
        if block.get("type") == "text":
            return json.loads(block["text"]), result
    raise AssertionError(f"no structured/text content: {result}")

search_payload, raw = structured(by_id[3])
assert search_payload and search_payload.get("ok"), (search_payload, raw)
results = search_payload["results"]
assert results, "expected at least one FTS hit for 'transpiration cooling'"
for hit in results:
    missing = REQUIRED - set(hit)
    assert not missing, (missing, hit)
    leaked = DROPPED_BY_DEFAULT & set(hit)
    assert not leaked, (leaked, hit)
    assert hit["bib_key"] and str(hit["bib_key"]).strip(), hit
    assert hit["text"] and str(hit["text"]).strip(), hit
    assert isinstance(hit["heading"], str), hit

top_key = results[0]["bib_key"]
assert top_key == "cool2021", f"expected cool2021 for transpiration cooling, got {top_key}"
assert results[0]["heading"] == "Abstract", results[0]["heading"]

open(sys.argv[2], "w", encoding="utf-8").write(top_key)
print(f"session1 ok: parsed bib_key={top_key!r}")

verbose_payload, vraw = structured(by_id[4])
assert verbose_payload and verbose_payload.get("ok"), (verbose_payload, vraw)
vresults = verbose_payload["results"]
assert vresults, "expected verbose FTS hits"
for hit in vresults:
    missing = (REQUIRED | VERBOSE_EXTRA) - set(hit)
    assert not missing, (missing, hit)
    assert isinstance(hit["authors"], list), hit
    assert "channel" in hit["score_breakdown"], hit["score_breakdown"]
assert vresults[0]["authors"] == ["Cara Cool"], vresults[0]["authors"]
print("verbose search ok")

deep_payload, draw = structured(by_id[5])
assert deep_payload and deep_payload.get("ok"), (deep_payload, draw)
deep_results = deep_payload["results"]
assert deep_results, "expected an FTS hit for 'porous media'"
deep_hit = deep_results[0]
assert deep_hit["bib_key"] == "cool2021", deep_hit
assert deep_hit["chunk_id"] == "cool_src:0001:eeeeeeeeeeeeeeee", deep_hit
assert deep_hit["heading"] == "2.3 Estimator variance", deep_hit["heading"]
assert deep_hit["heading"] != "Transpiration Cooling", deep_hit["heading"]
assert deep_hit["heading"] != deep_hit["title"], (deep_hit["heading"], deep_hit["title"])
print("multi-level heading_path (search) ok")

deep_chunk, craw = structured(by_id[6])
assert deep_chunk and deep_chunk.get("ok"), (deep_chunk, craw)
assert deep_chunk["chunk_id"] == "cool_src:0001:eeeeeeeeeeeeeeee", deep_chunk
assert deep_chunk["heading_path"] == [
    "Transpiration Cooling",
    "2. Methods",
    "2.3 Estimator variance",
], deep_chunk["heading_path"]
print("multi-level heading_path (get_chunk) ok")
PY

PARSED_KEY="$(cat "$CHAIN_KEY_FILE")"
if [ -z "$PARSED_KEY" ] || [ -z "${PARSED_KEY// /}" ]; then
    echo "FAIL: session1 produced empty/blank bib_key"
    exit 1
fi

echo "== session 2: get_work/bib chained from parsed bib_key (no hardcoded key) =="
REQS2="$TMP/reqs_s2.json"
make_init_sequence "$REQS2"
"$PY" - "$REQS2" "$PARSED_KEY" <<'PY'
import json, sys
path, chain_key = sys.argv[1], sys.argv[2]
assert chain_key.strip(), chain_key
reqs = json.load(open(path))
reqs.extend([
    {
        "kind": "request",
        "msg": {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "get_work",
                "arguments": {"key": chain_key},
            },
        },
    },
    {
        "kind": "request",
        "msg": {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "bib",
                "arguments": {"key": chain_key},
            },
        },
    },
    {
        "kind": "request",
        "msg": {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "get_work",
                "arguments": {"key": "definitely-not-a-real-key-zzzz"},
            },
        },
    },
    {
        "kind": "request",
        "msg": {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {
                "name": "get_chunk",
                "arguments": {"key": chain_key, "ordinal": 0},
            },
        },
    },
    {
        "kind": "request",
        "msg": {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "get_work",
                "arguments": {"key": "alpha2020"},
            },
        },
    },
    {
        "kind": "request",
        "msg": {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {
                "name": "get_chunk",
                "arguments": {"key": chain_key, "ordinal": 0, "verbose": True},
            },
        },
    },
])
assert reqs[3]["msg"]["params"]["arguments"]["key"] == chain_key
assert reqs[4]["msg"]["params"]["arguments"]["key"] == chain_key
json.dump(reqs, open(path, "w"), indent=2)
PY

RESP2="$TMP/responses_s2.json"
drive_mcp "$REQS2" > "$RESP2"

"$PY" - "$RESP2" "$PARSED_KEY" <<'PY'
import json, sys

chain_key = sys.argv[2]
responses = json.load(open(sys.argv[1], encoding="utf-8"))
by_id = {r["id"]: r for r in responses if "id" in r}
assert set(by_id) >= {1, 2, 3, 4, 5, 6, 7, 8}, sorted(by_id)

def structured(resp):
    result = resp["result"]
    if result.get("isError"):
        return None, result
    sc = result.get("structuredContent")
    if sc is not None:
        return sc, result
    content = result.get("content") or []
    for block in content:
        if block.get("type") == "text":
            return json.loads(block["text"]), result
    raise AssertionError(f"no structured/text content: {result}")

work, raw = structured(by_id[3])
assert work and work.get("ok"), (work, raw)
assert work["bib_key"] == chain_key, (work["bib_key"], chain_key)
assert work["authors"] == ["Cara Cool"], work["authors"]
assert "bibtex" in work and work["bibtex"].strip().startswith("@"), work.get("bibtex", "")[:40]

bib, raw = structured(by_id[4])
assert bib and bib.get("ok"), (bib, raw)
assert bib["bib_key"] == chain_key == work["bib_key"], (bib["bib_key"], work["bib_key"], chain_key)
assert bib.get("bibtex", "").strip().startswith("@"), bib
assert bib.get("bib_path"), bib

print(f"round-trip ok: search→get_work→bib bib_key={chain_key} (chained, not hardcoded)")

unknown = by_id[5]["result"]
assert unknown.get("isError") is True, unknown
text = " ".join(b.get("text", "") for b in unknown.get("content") or [])
assert "not found" in text.lower() or "not_found" in text.lower(), text

chunk, raw = structured(by_id[6])
assert chunk and chunk.get("ok"), (chunk, raw)
assert chunk["bib_key"] == chain_key, chunk
assert chunk.get("text"), chunk
assert chunk.get("chunk_id"), chunk
assert "heading_path" in chunk and isinstance(chunk["heading_path"], list), chunk
lean_chunk_keys = {"ok", "bib_key", "chunk_id", "heading_path", "text"}
assert set(chunk) == lean_chunk_keys, set(chunk)

alpha, raw = structured(by_id[7])
assert alpha and alpha.get("ok"), (alpha, raw)
assert alpha["bib_key"] == "alpha2020", alpha
assert alpha.get("doi") == "10.1000/alpha.2020", alpha
assert alpha["authors"] == ["Ada Alpha"], alpha["authors"]

chunk_v, raw = structured(by_id[8])
assert chunk_v and chunk_v.get("ok"), (chunk_v, raw)
assert chunk_v["bib_key"] == chain_key, chunk_v
for field in ("key", "title", "authors", "year", "category", "path", "chunk_ordinal"):
    assert field in chunk_v, (field, chunk_v)
assert chunk_v["authors"] == ["Cara Cool"], chunk_v["authors"]
assert chunk_v.get("text"), chunk_v
assert "heading_path" in chunk_v, chunk_v

print("session2 chain + enrichment ok")
PY

echo "== limit clamp/cap: 0->1, 1000->100, 100->at most 100 =="
REQS_LIMIT="$TMP/reqs_limit.json"
make_init_sequence "$REQS_LIMIT"
"$PY" - "$REQS_LIMIT" <<'PY'
import json, sys
path = sys.argv[1]
reqs = json.load(open(path))
for rid, lim in ((3, 0), (4, 1000), (5, 100)):
    reqs.append({
        "kind": "request",
        "msg": {
            "jsonrpc": "2.0",
            "id": rid,
            "method": "tools/call",
            "params": {
                "name": "search",
                "arguments": {
                    "query": "transpiration cooling",
                    "limit": lim,
                    "mode": "fts",
                },
            },
        },
    })
json.dump(reqs, open(path, "w"), indent=2)
PY
RESP_LIMIT="$TMP/responses_limit.json"
drive_mcp "$REQS_LIMIT" > "$RESP_LIMIT"
"$PY" - "$RESP_LIMIT" <<'PY'
import json, sys

def structured(resp):
    result = resp["result"]
    if result.get("isError"):
        return None, result
    sc = result.get("structuredContent")
    if sc is not None:
        return sc, result
    content = result.get("content") or []
    for block in content:
        if block.get("type") == "text":
            return json.loads(block["text"]), result
    raise AssertionError(f"no structured/text content: {result}")

responses = json.load(open(sys.argv[1], encoding="utf-8"))
by_id = {r["id"]: r for r in responses if "id" in r}

zero_payload, raw = structured(by_id[3])
assert zero_payload and zero_payload.get("ok"), (zero_payload, raw)
assert len(zero_payload["results"]) == 1, len(zero_payload["results"])
print(f"limit=0 clamped to 1: got {len(zero_payload['results'])} results")

huge_payload, raw = structured(by_id[4])
assert huge_payload and huge_payload.get("ok"), (huge_payload, raw)
assert len(huge_payload["results"]) <= 100, len(huge_payload["results"])
print(f"limit=1000 clamped to 100: got {len(huge_payload['results'])} results")

normal_payload, raw = structured(by_id[5])
assert normal_payload and normal_payload.get("ok"), (normal_payload, raw)
assert len(normal_payload["results"]) <= 100, len(normal_payload["results"])
print(f"limit=100: got {len(normal_payload['results'])} results")
print("limit clamping ok")
PY

echo "== vector search offline via ALEXANDRIA_QUERY_VECTOR =="
REQS_VEC="$TMP/reqs_vec.json"
make_init_sequence "$REQS_VEC"
"$PY" - "$REQS_VEC" <<'PY'
import json, sys
path = sys.argv[1]
reqs = json.load(open(path))
reqs.append({
    "kind": "request",
    "msg": {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "search",
            "arguments": {
                "query": "oblique",
                "limit": 3,
                "mode": "vector",
            },
        },
    },
})
reqs.append({
    "kind": "request",
    "msg": {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "search",
            "arguments": {
                "query": "oblique",
                "limit": 3,
                "mode": "vector",
                "verbose": True,
            },
        },
    },
})
json.dump(reqs, open(path, "w"), indent=2)
PY

OBLIQUE_VEC="$("$PY" - "$QVEC_JSON" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
print(json.dumps(data["oblique"]))
PY
)"

RESP_VEC="$TMP/responses_vec.json"
ALEXANDRIA_QUERY_VECTOR="$OBLIQUE_VEC" drive_mcp "$REQS_VEC" > "$RESP_VEC"

"$PY" - "$RESP_VEC" <<'PY'
import json, sys

def structured(resp):
    result = resp["result"]
    if result.get("isError"):
        return None, result
    sc = result.get("structuredContent")
    if sc is not None:
        return sc, result
    content = result.get("content") or []
    for block in content:
        if block.get("type") == "text":
            return json.loads(block["text"]), result
    raise AssertionError(f"no structured/text content: {result}")

responses = json.load(open(sys.argv[1], encoding="utf-8"))
by_id = {r["id"]: r for r in responses if "id" in r}
vec_payload, raw = structured(by_id[3])
assert vec_payload and vec_payload.get("ok"), (vec_payload, raw)
vec_hits = vec_payload["results"]
assert vec_hits, "vector mode returned no hits"
for hit in vec_hits:
    assert hit.get("bib_key") and str(hit["bib_key"]).strip(), hit
    assert "text" in hit and hit["text"], "text should always be attached"
    assert "score_breakdown" not in hit, "score_breakdown must be verbose-only"

vec_payload_v, raw_v = structured(by_id[4])
assert vec_payload_v and vec_payload_v.get("ok"), (vec_payload_v, raw_v)
vec_hits_v = vec_payload_v["results"]
assert vec_hits_v, "verbose vector mode returned no hits"
for hit in vec_hits_v:
    assert hit["score_breakdown"].get("channel") == "vector", hit
print("vector search ok")
PY

echo "== boilerplate filter: roman numeral unit + junk/backfill/leaf/blocklist =="
"$PY" - <<'PY'
import sys
sys.path.insert(0, "pipeline")
import alexandria_mcp as mcp

must_be = [
    "viii** ■ Contents",
    "x** Contents",
    "x **Contents",
    "xiv** ■ List of Figures",
    "xxx List of Figures",
    "xvi About the Authors",
    "E.9. Cited References.",
    "B.4. Cited References.",
    "H.17. Cited References.",
    "ACKNOWLEDGEMENT",
]
must_not = [
    "CD Contents",
    "MD Index",
    "DVD Contents",
    "Mix Index",
    "Mill Index",
    "Civil Copyright",
    "Disk Index",
    "Chapter References",
    "Author Notes",
]
for leaf in must_be:
    assert mcp._is_boilerplate_chunk([leaf]), f"expected boilerplate: {leaf!r}"
for leaf in must_not:
    assert not mcp._is_boilerplate_chunk([leaf]), f"expected NOT boilerplate: {leaf!r}"
print(f"roman numeral + blocklist regex: {len(must_be)+len(must_not)}/{len(must_be)+len(must_not)} passed")
PY

REQS_BOIL="$TMP/reqs_boil.json"
make_init_sequence "$REQS_BOIL"
"$PY" - "$REQS_BOIL" <<'PY'
import json, sys
path = sys.argv[1]
reqs = json.load(open(path))
# zirconium: 2 junk + 3 real, limit=3 must backfill
reqs.append({
    "kind": "request",
    "msg": {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "search",
            "arguments": {
                "query": "zirconium coolant matrix",
                "limit": 3,
                "mode": "fts",
            },
        },
    },
})
reqs.append({
    "kind": "request",
    "msg": {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "search",
            "arguments": {
                "query": "zirconium coolant matrix",
                "limit": 3,
                "mode": "fts",
                "verbose": True,
            },
        },
    },
})
# control: no boilerplate
reqs.append({
    "kind": "request",
    "msg": {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {
            "name": "search",
            "arguments": {
                "query": "porous media",
                "limit": 3,
                "mode": "fts",
            },
        },
    },
})
# survival: glossary/preface/notes
reqs.append({
    "kind": "request",
    "msg": {
        "jsonrpc": "2.0",
        "id": 6,
        "method": "tools/call",
        "params": {
            "name": "search",
            "arguments": {
                "query": "phlogiston capacitor bound",
                "limit": 3,
                "mode": "fts",
            },
        },
    },
})
# leaf-only invariant
reqs.append({
    "kind": "request",
    "msg": {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {
            "name": "search",
            "arguments": {
                "query": "quasar-silver orthography",
                "limit": 5,
                "mode": "fts",
            },
        },
    },
})
# blocklist coverage
reqs.append({
    "kind": "request",
    "msg": {
        "jsonrpc": "2.0",
        "id": 8,
        "method": "tools/call",
        "params": {
            "name": "search",
            "arguments": {
                "query": "aetherium flux lattice",
                "limit": 5,
                "mode": "fts",
            },
        },
    },
})
json.dump(reqs, open(path, "w"), indent=2)
PY

RESP_BOIL="$TMP/responses_boil.json"
drive_mcp "$REQS_BOIL" > "$RESP_BOIL"

"$PY" - "$DB" <<'PY'
import sys
sys.path.insert(0, "pipeline")
from pathlib import Path
import alexandria_index as index
import alexandria_mcp as mcp

conn = mcp.open_index_readonly(Path(sys.argv[1]))
raw_hits, _ = index.search_ex(conn, "zirconium coolant matrix", mode="fts", limit=3, allow_rebuild=False)
raw_ids = [h.chunk_id for h in raw_hits]
assert raw_ids.count("cool_src:0002:ffffffffffffffff") == 1, raw_ids
assert raw_ids.count("cool_src:0003:gggggggggggggggg") == 1, raw_ids
print(f"raw top-3 (pre-filter) includes both junk chunks: {raw_ids}")
conn.close()
PY

"$PY" - "$RESP_BOIL" <<'PY'
import json, sys

responses = json.load(open(sys.argv[1], encoding="utf-8"))
by_id = {r["id"]: r for r in responses if "id" in r}
assert set(by_id) >= {1, 2, 3, 4, 5, 6, 7, 8}, sorted(by_id)

def structured(resp):
    result = resp["result"]
    if result.get("isError"):
        return None, result
    sc = result.get("structuredContent")
    if sc is not None:
        return sc, result
    content = result.get("content") or []
    for block in content:
        if block.get("type") == "text":
            return json.loads(block["text"]), result
    raise AssertionError(f"no structured/text content: {result}")

JUNK_CHUNK_IDS = {"cool_src:0002:ffffffffffffffff", "cool_src:0003:gggggggggggggggg"}
JUNK_HEADINGS = {"References", "xxvi** ■ Contents"}

payload, raw = structured(by_id[3])
assert payload and payload.get("ok"), (payload, raw)
results = payload["results"]
assert len(results) == 3, f"expected 3 backfilled real hits, got {len(results)}: {results}"
result_ids = {h["chunk_id"] for h in results}
result_headings = {h["heading"] for h in results}
assert not (result_ids & JUNK_CHUNK_IDS), f"junk chunk leaked into results: {results}"
assert not (result_headings & JUNK_HEADINGS), f"junk heading leaked into results: {result_headings}"
assert "Appendix B. Derivation" in result_headings, result_headings
assert payload.get("filtered_boilerplate") == 2, payload.get("filtered_boilerplate")
print(f"fts filter+backfill ok: headings={sorted(result_headings)} filtered_boilerplate=2")

vpayload, vraw = structured(by_id[4])
assert vpayload and vpayload.get("ok"), (vpayload, vraw)
vresults = vpayload["results"]
assert len(vresults) == 3, f"verbose backfill mismatch: {vresults}"
vresult_ids = {h["chunk_id"] for h in vresults}
assert vresult_ids == result_ids, (vresult_ids, result_ids)
assert vpayload.get("filtered_boilerplate") == 2, vpayload.get("filtered_boilerplate")
for hit in vresults:
    assert "score_breakdown" in hit, hit
print("verbose filter ok: identical to lean, richer fields")

control_payload, craw = structured(by_id[5])
assert control_payload and control_payload.get("ok"), (control_payload, craw)
assert "filtered_boilerplate" not in control_payload, control_payload
print("filtered_boilerplate correctly absent when nothing was filtered")

survive_payload, sraw = structured(by_id[6])
assert survive_payload and survive_payload.get("ok"), (survive_payload, sraw)
survive_results = survive_payload["results"]
survive_headings = {h["heading"] for h in survive_results}
assert survive_headings == {"Glossary", "Preface", "Notes"}, survive_headings
assert "filtered_boilerplate" not in survive_payload, survive_payload
print(f"survival ok: {sorted(survive_headings)} unfiltered (audit-excluded from blocklist)")

ancestor_payload, _ = structured(by_id[7])
assert ancestor_payload and ancestor_payload.get("ok"), ancestor_payload
ancestor_results = ancestor_payload["results"]
ancestor_ids = {h["chunk_id"] for h in ancestor_results}
ancestor_headings = {h["heading"] for h in ancestor_results}
assert "cool_src:0010:nnnnnnnnnnnnnnnn" in ancestor_ids, ancestor_results
assert "Style of citations in this field" in ancestor_headings, ancestor_headings
assert "filtered_boilerplate" not in ancestor_payload, ancestor_payload
print("leaf-only filter ok: 'References' ancestor did not filter real leaf")

numbering_payload, _ = structured(by_id[8])
assert numbering_payload and numbering_payload.get("ok"), numbering_payload
numbering_results = numbering_payload["results"]
numbering_ids = {h["chunk_id"] for h in numbering_results}
numbering_headings = {h["heading"] for h in numbering_results}
assert "cool_src:0011:oooooooooooooooo" not in numbering_ids, numbering_results
assert "cool_src:0012:pppppppppppppppp" not in numbering_ids, numbering_results
assert "E.9. Cited References." not in numbering_headings, numbering_headings
assert "List of Figures" not in numbering_headings, numbering_headings
assert numbering_payload.get("filtered_boilerplate", 0) >= 2, numbering_payload
print("blocklist coverage ok: numbered prefix and keyword chunks filtered")
PY

echo "== get_chunk JSON guard + _last_heading + missing_chunk_bodies =="
"$PY" - "$DB" <<'PY'
import shutil, sqlite3, sys
from pathlib import Path
sys.path.insert(0, "pipeline")
import alexandria_mcp as mcp

assert mcp._last_heading(None) == ""
assert mcp._last_heading("not-a-list") == ""
assert mcp._last_heading({"a": 1}) == ""
assert mcp._last_heading([]) == ""
assert mcp._last_heading(["A", "B"]) == "B"
print("_last_heading defensive ok")

db = Path(sys.argv[1])
corrupt = db.parent / "index_bad_hp.db"
shutil.copy2(db, corrupt)
rw = sqlite3.connect(corrupt)
rw.execute(
    "UPDATE chunks SET heading_path = ? WHERE chunk_id = ?",
    ("{not-json", "cool_src:0000:dddddddddddddddd"),
)
rw.commit()
rw.close()
conn = mcp.open_index_readonly(corrupt)
out = mcp.tool_get_chunk(conn, chunk_id="cool_src:0000:dddddddddddddddd")
conn.close()
assert out.get("ok") is True, out
assert out.get("heading_path") == [], out
print("get_chunk JSON guard ok")

conn = mcp.open_index_readonly(db)
real_bodies = mcp._chunk_bodies

def drop_one(conn_, ids):
    bodies = real_bodies(conn_, ids)
    if bodies:
        del bodies[next(iter(bodies))]
    return bodies

mcp._chunk_bodies = drop_one
try:
    payload = mcp.tool_search(conn, "transpiration cooling", mode="fts", limit=3)
finally:
    mcp._chunk_bodies = real_bodies
conn.close()
assert payload.get("ok"), payload
degraded = payload.get("degraded") or []
assert any("missing_chunk_bodies:" in n for n in degraded), degraded
print(f"missing_chunk_bodies degraded ok: {degraded}")
PY

echo "== read-only open proven (writes fail) =="
"$PY" - "$DB" <<'PY'
import sqlite3, sys
from pathlib import Path
import alexandria_mcp as mcp

db = Path(sys.argv[1])
conn = mcp.open_index_readonly(db)
raised = None
try:
    conn.execute("CREATE TABLE _ro_probe (x INTEGER)")
    conn.commit()
except sqlite3.OperationalError as exc:
    raised = exc
assert raised is not None, "expected OperationalError on write through RO connection"
msg = str(raised).lower()
assert "readonly" in msg or "read-only" in msg or "read only" in msg, raised
raised2 = None
try:
    conn.execute("INSERT INTO works (key) VALUES ('x')")
except sqlite3.OperationalError as exc:
    raised2 = exc
assert raised2 is not None, "expected OperationalError on INSERT"
print(f"read-only ok: {raised}")
conn.close()
PY

echo "== include_markdown path containment (poisoned md_path) =="
"$PY" - "$DB" <<'PY'
import sqlite3, sys, shutil
from pathlib import Path
import alexandria_mcp as mcp

src = Path(sys.argv[1])
db = Path(sys.argv[1]).parent / "index_poison_md.db"
shutil.copy2(src, db)

def poison(md_path_value: str) -> None:
    rw = sqlite3.connect(db)
    rw.execute("UPDATE works SET md_path = ? WHERE key = 'cool2021'", (md_path_value,))
    rw.commit()
    rw.close()

def assert_contained(label: str, md_path_value: str) -> None:
    poison(md_path_value)
    conn = mcp.open_index_readonly(db)
    out = mcp.tool_get_work(conn, "cool2021", include_markdown=True)
    conn.close()
    assert out.get("ok") is True, out
    md = out.get("markdown") or ""
    assert "root:" not in md.lower() and "daemon" not in md.lower(), (
        f"{label}: leaked file content: {md[:200]!r}"
    )
    err = out.get("markdown_error") or ""
    assert err, f"{label}: expected markdown_error, got {out}"
    assert "escape" in err.lower(), (
        f"{label}: expected escape error, got: {err}"
    )
    print(f"  containment ok ({label}): {err}")

assert_contained("absolute /etc/passwd", "/etc/passwd")
assert_contained("dotdot walk", "../../etc/passwd")
print("path containment ok")
PY

echo "== adversarial: blank/whitespace bib_key fail-closed on all four tools + vector =="
OBLIQUE_VEC="$("$PY" - "$QVEC_JSON" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
print(json.dumps(data["oblique"]))
PY
)"
ALEXANDRIA_QUERY_VECTOR="$OBLIQUE_VEC" "$PY" - "$DB" <<'PY'
import shutil, sqlite3, sys
from pathlib import Path
import alexandria_mcp as mcp

src = Path(sys.argv[1])
db = Path(sys.argv[1]).parent / "index_corrupt.db"
shutil.copy2(src, db)

def corrupt_chunks(value: str) -> None:
    rw = sqlite3.connect(db)
    rw.execute("UPDATE chunks SET bib_key = ?", (value,))
    rw.execute("UPDATE chunks_fts SET bib_key = ?", (value,))
    rw.execute("UPDATE chunk_embeddings SET bib_key = ?", (value,))
    rw.commit()
    got = rw.execute("SELECT bib_key FROM chunks LIMIT 1").fetchone()[0]
    assert got == value, (got, value)
    got_fts = rw.execute("SELECT bib_key FROM chunks_fts LIMIT 1").fetchone()[0]
    assert got_fts == value, (got_fts, value)
    got_emb = rw.execute("SELECT bib_key FROM chunk_embeddings LIMIT 1").fetchone()[0]
    assert got_emb == value, (got_emb, value)
    rw.close()

def corrupt_works(value: str) -> None:
    rw = sqlite3.connect(db)
    rw.execute("UPDATE works SET bib_key = ?", (value,))
    rw.execute("UPDATE bib_entries SET bib_key = ?", (value,))
    rw.commit()
    got_w = rw.execute("SELECT bib_key FROM works LIMIT 1").fetchone()[0]
    assert got_w == value, (got_w, value)
    got_b = rw.execute("SELECT bib_key FROM bib_entries LIMIT 1").fetchone()[0]
    assert got_b == value, (got_b, value)
    rw.close()

def expect_fail(label: str, fn) -> None:
    try:
        out = fn()
    except RuntimeError as exc:
        if "bib_key" not in str(exc):
            raise AssertionError(
                f"{label}: RuntimeError message missing 'bib_key': {exc}"
            ) from exc
        print(f"  {label}: FAIL-CLOSED ({type(exc).__name__}: {exc})")
        return
    except Exception as exc:
        raise AssertionError(
            f"{label}: wrong exception {type(exc).__name__} "
            f"(want RuntimeError with bib_key): {exc}"
        ) from exc
    raise AssertionError(
        f"{label}: LEAKED ok={out.get('ok')!r} bib_key={out.get('bib_key')!r} full={out!r}"
    )

for value, tag in (("", "empty"), ("   ", "whitespace")):
    shutil.copy2(src, db)
    corrupt_chunks(value)
    conn = mcp.open_index_readonly(db)
    expect_fail(
        f"search/{tag}",
        lambda: mcp.tool_search(conn, "cooling", mode="fts", limit=3),
    )
    expect_fail(
        f"search_vector/{tag}",
        lambda: mcp.tool_search(conn, "oblique", mode="vector", limit=3),
    )
    row = conn.execute("SELECT key, chunk_ordinal FROM chunks LIMIT 1").fetchone()
    expect_fail(
        f"get_chunk/{tag}",
        lambda r=row: mcp.tool_get_chunk(conn, key=r["key"], ordinal=r["chunk_ordinal"]),
    )
    conn.close()

    shutil.copy2(src, db)
    corrupt_works(value)
    conn = mcp.open_index_readonly(db)
    k = conn.execute("SELECT key FROM works LIMIT 1").fetchone()["key"]
    expect_fail(f"get_work/{tag}", lambda: mcp.tool_get_work(conn, k))
    expect_fail(f"bib/{tag}", lambda: mcp.tool_bib(conn, k))
    conn.close()

print("adversarial bib_key: FAIL-CLOSED (fts+vector+get_chunk+get_work+bib)")
PY

echo "== protocol edges: version echo, ping, bad arguments, unknown method, notification silence =="
REQS_EDGE="$TMP/reqs_edge.json"
"$PY" - "$REQS_EDGE" "$PROTOCOL" <<'PY'
import json, sys
path, protocol = sys.argv[1], sys.argv[2]
def req(i, method, params=None):
    msg = {"jsonrpc": "2.0", "id": i, "method": method}
    if params is not None:
        msg["params"] = params
    return {"kind": "request", "msg": msg}
def call(i, args):
    return req(i, "tools/call", {"name": "search", "arguments": args})
init = {"protocolVersion": protocol, "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}
reqs = [
    req(1, "initialize", init),
    {"kind": "notification", "msg": {"jsonrpc": "2.0", "method": "notifications/initialized"}},
    req(2, "ping"),
    req(3, "initialize", {**init, "protocolVersion": "1999-01-01"}),
    call(4, {}),
    call(5, {"query": "oblique", "limit": "x"}),
    call(6, {"query": "oblique", "mode": "fts", "extra": 1}),
    call(7, {"query": "oblique", "mode": "sideways"}),
    req(8, "no/such/method"),
]
json.dump(reqs, open(path, "w"))
PY
drive_mcp "$REQS_EDGE" > "$TMP/edge_out.json"
"$PY" - "$TMP/edge_out.json" "$PROTOCOL" <<'PY'
import json, sys
out = json.load(open(sys.argv[1]))
by_id = {r["id"]: r for r in out}
assert [r["id"] for r in out] == [1, 2, 3, 4, 5, 6, 7, 8], [r["id"] for r in out]  # notification got no reply
assert by_id[1]["result"]["protocolVersion"] == sys.argv[2], by_id[1]
assert by_id[1]["result"]["serverInfo"]["name"] == "alexandria", by_id[1]
assert "tools" in by_id[1]["result"]["capabilities"], by_id[1]
assert by_id[2]["result"] == {}, by_id[2]
assert by_id[3]["result"]["protocolVersion"] != "1999-01-01", by_id[3]
def message(r):
    assert r["result"]["isError"] is True, r
    return " ".join(b["text"] for b in r["result"]["content"])
assert "'query' is a required property" in message(by_id[4]), by_id[4]
assert "is not of type 'integer'" in message(by_id[5]), by_id[5]
assert "Additional properties are not allowed" in message(by_id[6]), by_id[6]
assert "is not one of" in message(by_id[7]), by_id[7]
assert by_id[8]["error"]["code"] == -32601, by_id[8]
print("protocol edges ok")
PY

echo "== BLAS thread default (CPU) =="
env -u OPENBLAS_NUM_THREADS "$PY" -c 'import os, alexandria_mcp; assert os.environ["OPENBLAS_NUM_THREADS"] == "2", os.environ.get("OPENBLAS_NUM_THREADS")'
OPENBLAS_NUM_THREADS=5 "$PY" -c 'import os, alexandria_mcp; assert os.environ["OPENBLAS_NUM_THREADS"] == "5"'
echo "blas threads default ok, overridable"

echo "ALL MCP SERVER CHECKS PASSED"
echo "test_alexandria_mcp.sh OK"
