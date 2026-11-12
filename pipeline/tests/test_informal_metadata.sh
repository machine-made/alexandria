#!/usr/bin/env bash
# Permanent fixtures for alexandria-smq — informal metadata kind.
# Test informal kind handling in metadata_confidence and informal_key.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="${ROOT}/.venv/bin/python"
if [ ! -x "$PY" ]; then PY=python3; fi
export PYTHONPATH="$ROOT/pipeline${PYTHONPATH:+:$PYTHONPATH}"

"$PY" - <<'PY'
from alexandria_metadata_common import metadata_confidence, informal_key

# --- informal kind: title + source → high confidence ---
print("Testing informal+title+source cases...")
INFORMAL_HIGH_CASES = [
    ("podcast_ep", {"kind": "informal", "title": "Why AI Matters", "source": "Lex Fridman Podcast"}),
    ("blog_post", {"kind": "informal", "title": "Notes on Deep Learning", "url": "https://example.com/notes"}),
    ("notes_url", {"kind": "informal", "title": "Research Notes", "source_url": "https://notion.so/page"}),
    ("show_episode", {"kind": "informal", "title": "ML Trends 2025", "show": "Tech Digest"}),
    ("generated_source", {"kind": "informal", "title": "Synthesis", "source": "generated"}),
    ("generated_origin", {"kind": "informal", "title": "AI Overview", "origin": "generated"}),
]
for key, md in INFORMAL_HIGH_CASES:
    conf = metadata_confidence(md)
    assert conf == "high", f"{key}: expected high, got {conf} (metadata={md!r})"
print("  ✓ All informal+title+source cases: high")

# --- informal kind: title but no source → low confidence ---
print("Testing informal+title, no source cases...")
INFORMAL_LOW_CASES = [
    ("no_source", {"kind": "informal", "title": "Some Title"}),
    ("empty_source", {"kind": "informal", "title": "Another Title", "source": ""}),
    ("title_only", {"kind": "informal", "title": "Title Here", "url": None}),
]
for key, md in INFORMAL_LOW_CASES:
    conf = metadata_confidence(md)
    assert conf == "low", f"{key}: expected low, got {conf} (metadata={md!r})"
print("  ✓ All informal+title, no source cases: low")

# --- informal kind: no title → low confidence ---
print("Testing informal, no title cases...")
INFORMAL_NO_TITLE_CASES = [
    ("no_title", {"kind": "informal", "source": "Some Source"}),
    ("empty_title", {"kind": "informal", "title": "", "source": "Source"}),
]
for key, md in INFORMAL_NO_TITLE_CASES:
    conf = metadata_confidence(md)
    assert conf == "low", f"{key}: expected low, got {conf} (metadata={md!r})"
print("  ✓ All informal, no title cases: low")

# --- academic path unchanged (kind absent or "academic") ---
print("Testing academic path (kind absent or 'academic')...")

# Known-good academic case: real author + title + ISBN → should file high/medium
good_academic = {
    "kind": "academic",
    "author": "Ahmed F. El-Sayed",
    "title": "Fundamentals of Aircraft and Rocket Propulsion",
    "isbn": "9781447167945",
    "year": "2023",
    "source_stem": "x",
    "metadata_field_sources": {"author": "markdown", "title": "markdown"},
}
conf = metadata_confidence(good_academic)
assert conf in {"high", "medium"}, f"academic good case: expected high/medium, got {conf}"
print("  ✓ Good academic case (kind='academic'): high/medium")

# Known-low academic case: heuristic author + no ID → should hold low
low_academic = {
    "kind": "academic",
    "author": "Series Editors",
    "title": "Advances and Innovations",
    "year": "2020",
    "source_stem": "x",
    "metadata_field_sources": {"author": "heuristic", "title": "heuristic"},
    "source_files": [{"format": "pdf"}],
}
conf = metadata_confidence(low_academic)
assert conf == "low", f"academic low case: expected low, got {conf}"
print("  ✓ Low academic case (kind='academic'): low")

# Absent kind defaults to academic path
no_kind = {
    "author": "John Smith",
    "title": "Real Title",
    "isbn": "9783031361500",
    "year": "2023",
    "source_stem": "x",
    "metadata_field_sources": {"author": "markdown", "title": "markdown"},
}
conf = metadata_confidence(no_kind)
assert conf in {"high", "medium"}, f"no kind case: expected high/medium, got {conf}"
print("  ✓ No kind specified: treated as academic path (high/medium)")

# --- informal_key: title with spaces/punctuation → clean slug ---
print("Testing informal_key slugging...")
KEY_CASES = [
    ("title_spaces", {"title": "Why AI Matters"}, "whyaimatters"),
    ("title_punctuation", {"title": "Notes on Deep Learning!"}, "notesondeeplearning"),
    ("title_mixed", {"title": "Python's Best Practices – 2025"}, "pythonsbestpractices2025"),
    ("title_unicode", {"title": "Café Notes on José"}, "cafenotesonjose"),
    ("explicit_key_wins", {"key": "my_custom_key", "title": "Ignored Title"}, "my_custom_key"),
    ("key_with_spaces", {"key": "my custom key"}, "mycustomkey"),
    ("source_stem_fallback", {"source_stem": "research_notes_2025"}, "research_notes_2025"),
    ("all_empty", {"title": "", "source_stem": ""}, "note"),
    ("none_values", {"key": None, "title": None}, "note"),
]
for name, md, expected_slug in KEY_CASES:
    result = informal_key(md)
    assert result == expected_slug, f"{name}: expected {expected_slug!r}, got {result!r}"
print("  ✓ All informal_key cases: correct slugs")

print("\ntest_informal_metadata: OK")
PY
