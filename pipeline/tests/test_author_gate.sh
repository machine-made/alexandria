#!/usr/bin/env bash
# Permanent fixtures: author-identity gate.
# Table-driven: the six real bogus shapes hold; real person-authors still file;
# PDF heuristic-only identity holds (format-agnostic).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="${ROOT}/.venv/bin/python"
if [ ! -x "$PY" ]; then PY=python3; fi
export PYTHONPATH="$ROOT/pipeline${PYTHONPATH:+:$PYTHONPATH}"

"$PY" - <<'PY'
from alexandria_metadata_common import is_plausible_person_author, metadata_confidence

# --- six real filed-book shapes: must HOLD (low), ISBN must not rescue ---
HOLD_CASES = [
    ("editors2023", "Series Editors", "Advances and Innovations in Statistics and Data Science", "9783031083280"),
    ("market2023", "Quantitative Trading in the Oil Market", "Virtual Barrels", "9783031361500"),
    ("aircraft2016", "Fundamentals of Aircraft and Rocket Propulsion", "Fundamentals of Aircraft and Rocket Propulsion", "9781447167945"),
    ("detonation2017", "Pulse Detonation and Rotating Detonation Engines", "Detonation Control for Propulsion", "9783319689050"),
    ("materials2016", "A Comprehensive Survey of Energetic Materials", "Chemical Rocket Propulsion", "9783319277462"),
    ("perspective2025", "A System Perspective", "SPACE Farid Gamgami PROPULSION AND SPACESHIP DESIGN", "9783031713354"),
]
for key, author, title, isbn in HOLD_CASES:
    md = {
        "author": author,
        "authors": [author],
        "title": title,
        "isbn": isbn,
        "year": "2020",
        "source_stem": "x",
        "metadata_field_sources": {"author": "heuristic", "title": "heuristic"},
        "source_files": [{"format": "pdf"}],
    }
    conf = metadata_confidence(md)
    assert conf == "low", f"{key}: expected low, got {conf} (author={author!r})"
    assert is_plausible_person_author(author, title) is False, f"{key}: author should be implausible"

# --- real person-authors still file high/medium with identifiers ---
FILE_CASES = [
    ("Ilia Bouchouev", "Virtual Barrels", "9783031361500"),
    ("Ziming Song", "Some Quantitative Title", "9783031361501"),
    ("Ahmed F. El-Sayed", "Fundamentals of Aircraft and Rocket Propulsion", "9781447167945"),
    ("Farid Gamgami", "Space Propulsion and Spaceship Design", "9783031713354"),
    ("Anna van der Berg", "Particle Name Book", "9783031361502"),
    # Bare surname / particle forms (strict=False only) — must FILE
    ("Kobelev", "Theory of Elasticity", "9783031361503"),
    ("Eremeyev", "Theory of Elasticity", "9783031361504"),
    ("El-Sayed", "Fundamentals of Aircraft and Rocket Propulsion", "9781447167946"),
    ("van der Berg", "Particle Name Book", "9783031361505"),
]
for author, title, isbn in FILE_CASES:
    md = {
        "author": author,
        "authors": [author],
        "title": title,
        "isbn": isbn,
        "year": "2023",
        "source_stem": "x",
        "metadata_field_sources": {"author": "markdown", "title": "markdown"},
    }
    conf = metadata_confidence(md)
    assert conf in {"high", "medium"}, f"real author {author!r} -> {conf}"
    assert is_plausible_person_author(author, title) is True, f"real author not plausible: {author!r}"

# --- PDF heuristic-only (no id) → low (closes EPUB asymmetry) ---
pdf_heur = {
    "author": "Some Heading Fragment Text",
    "authors": ["Some Heading Fragment Text"],
    "title": "A Different Long Title Entirely",
    "year": "2019",
    "source_stem": "x",
    "metadata_field_sources": {"author": "heuristic", "title": "heuristic"},
    "source_files": [{"format": "pdf"}],
}
assert metadata_confidence(pdf_heur) == "low", metadata_confidence(pdf_heur)

# --- heuristic author + ISBN but real person still files (shape ok, id present) ---
real_heur = {
    "author": "Anthony Young",
    "authors": ["Anthony Young"],
    "title": "The Twenty-First Century Commercial Space Imperative",
    "isbn": "9783319426721",
    "year": "2026",
    "source_stem": "x",
    "metadata_field_sources": {"author": "heuristic", "title": "pdf"},
    "source_files": [{"format": "pdf"}],
}
assert metadata_confidence(real_heur) in {"high", "medium"}, metadata_confidence(real_heur)

# --- template/placeholder metadata HOLDS (PDF form fields, SSRN stubs) ---
assert is_plausible_person_author("Your Name", "Your Title") is False
placeholder_md = {
    "author": "Your Name",
    "authors": ["Your Name"],
    "title": "Your Title",
    "year": "2026",
    "source_stem": "ssrn-6824883",
    "metadata_field_sources": {"author": "pdf", "title": "pdf"},
    "source_files": [{"format": "pdf"}],
}
assert metadata_confidence(placeholder_md) == "low", metadata_confidence(placeholder_md)
# Real author containing placeholder substring still files (whole-value only).
assert is_plausible_person_author("Nameet Kaur", "Some Real Title") is True

print("test_author_gate: OK")
PY
