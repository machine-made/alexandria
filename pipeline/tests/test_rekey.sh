#!/usr/bin/env bash
# Unit tests for KeyRegistry.amend_key (throwaway fixture registry only).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

export TEST_REKEY_TMP="$TMP"
PYTHONPATH="$ROOT/pipeline${PYTHONPATH:+:$PYTHONPATH}" python3 <<'PY'
from pathlib import Path
import os

from alexandria_key_registry import KeyRegistry

tmp = Path(os.environ["TEST_REKEY_TMP"])
root = tmp / "rekey_lib"
root.mkdir(parents=True)
papers = root / "papers"
papers.mkdir()
(root / "misc").mkdir()

sha_a = "a" * 64
sha_b = "b" * 64
sha_c = "c" * 64

reg = KeyRegistry.load(root)
reg.record_existing([sha_a], "papers", "xu2029", "xu2029")
reg.record_existing([sha_b], "papers", "park2023", "park2023")
first_filed = reg.entries[sha_a]["first_filed_utc"]
assert first_filed, "first_filed_utc missing"

# --- happy path ---
reg.amend_key(sha_a, "xu2026", "papers", "year misread; DOI says 2026", fields=["year"])
entry = reg.entries[sha_a]
assert entry["key"] == "xu2026", entry
assert entry["base_key"] == "xu2026", entry
assert entry["category"] == "papers", entry
assert entry["first_filed_utc"] == first_filed, (entry["first_filed_utc"], first_filed)
corr = entry.get("corrections") or []
assert len(corr) >= 1, corr
last = corr[-1]
assert last["from_key"] == "xu2029", last
assert last["to_key"] == "xu2026", last
assert last.get("reason"), last
assert last.get("tool") == "amend_key", last
assert last.get("utc"), last
assert "year" in (last.get("fields") or []), last

# persist + reload preserves provenance
reg.persist()
reg2 = KeyRegistry.load(root)
e2 = reg2.entries[sha_a]
assert e2["key"] == "xu2026"
assert e2["first_filed_utc"] == first_filed
assert (e2.get("corrections") or [])[-1]["from_key"] == "xu2029"

# --- target collision refusal ---
try:
    reg2.amend_key(sha_a, "park2023", "papers", "should collide")
except RuntimeError as exc:
    msg = str(exc).lower()
    assert "collision" in msg or "already" in msg, exc
else:
    raise AssertionError("amend_key allowed target collision onto park2023")

# --- tombstone blocks reuse ---
# After amend, reserve_key for a new sha must not hand out xu2029
_cat, key = reg2.reserve_key(
    [sha_c],
    "papers",
    "xu2029",
    category_dir=papers,
    persist=True,
)
assert key != "xu2029", f"tombstoned key reused: got {key}"
assert key.startswith("xu2029"), key  # suffix form ok
assert "xu2029" in reg2.reserved_keys()

# --- unknown-sha refusal ---
try:
    reg2.amend_key("f" * 64, "nobody0000", "papers", "ghost")
except RuntimeError as exc:
    assert "unknown" in str(exc).lower(), exc
else:
    raise AssertionError("amend_key allowed unknown sha")

# --- non-amended entry still hits immutability guard on remap ---
try:
    reg2.record_existing([sha_b], "misc", "park2023a", "park2023")
except RuntimeError as exc:
    assert "registry immutability violation" in str(exc), exc
else:
    raise AssertionError("record_existing allowed remap of non-amended park2023")

# park2023 entry completely untouched by amend of sha_a
song = reg2.entries[sha_b]
assert song["key"] == "park2023"
assert song["base_key"] == "park2023"
assert not song.get("corrections")

print("test_rekey.sh: all amend_key cases passed")
PY
