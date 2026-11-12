#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

PYTHONPATH="$ROOT/pipeline${PYTHONPATH:+:$PYTHONPATH}" python3 - <<'PY'
from alexandria_metadata_common import (
    base_key,
    first_author_lastname,
    is_author_key_resolved,
    is_low_confidence,
    metadata_confidence,
    slug_word,
    transliterate_ascii,
)


def assert_case(author, year, surname, key):
    got_surname = first_author_lastname(author)
    got_key = base_key(author, year)
    assert got_surname == surname, (author, got_surname, surname)
    assert got_key == key, (author, got_key, key)
    assert got_key == got_key.lower(), got_key
    assert is_author_key_resolved(author) is True, author


assert transliterate_ascii("José Álvarez") == "Jose Alvarez"
assert slug_word("José Álvarez") == "josealvarez"

assert_case("Ada Lovelace", "1843", "Lovelace", "lovelace1843")
assert_case("Smith, Jane", "2020", "Smith", "smith2020")
assert_case("Ada Lovelace and Charles Babbage", "1843", "Lovelace", "lovelace1843")

# Particle convention: keep contiguous surname particles attached in the key.
assert_case("Vincent van Gogh", "2024", "van Gogh", "vangogh2024")
assert_case("Anna van der Berg", "2024", "van der Berg", "vanderberg2024")
assert_case("Simone de Beauvoir", "2024", "de Beauvoir", "debeauvoir2024")
assert_case("Juan de la Cruz", "2024", "de la Cruz", "delacruz2024")
assert_case("Leonardo da Vinci", "2024", "da Vinci", "davinci2024")
assert_case("Ludwig von Mises", "2024", "von Mises", "vonmises2024")
assert_case("Maria del Carmen", "2024", "del Carmen", "delcarmen2024")

assert_case("Jane Smith Jr.", "2024", "Smith", "smith2024")
assert_case("Jane Smith Sr.", "2024", "Smith", "smith2024")
assert_case("Smith, Jane, Jr.", "2024", "Smith", "smith2024")
assert_case("Jane Smith II", "2024", "Smith", "smith2024")
assert_case("Jane Smith III", "2024", "Smith", "smith2024")
assert_case("Jane Smith IV", "2024", "Smith", "smith2024")
assert_case("Jane Smith et al.", "2024", "Smith", "smith2024")
assert_case("Smith et al.", "2024", "Smith", "smith2024")

assert_case("José Álvarez", "2024", "Alvarez", "alvarez2024")
assert_case("François L'Ecuyer", "2024", "L'Ecuyer", "lecuyer2024")
assert base_key("Ada Lovelace", "forthcoming") == "lovelacenodate"


def metadata_for(author):
    return {
        "title": "Synthetic Work",
        "author": author,
        "year": "2024",
        "source_stem": "synthetic-work",
        "doi": "10.1234/example",
        "arxiv_id": "",
        "isbn": "",
    }


normal = metadata_for("Ada Lovelace")
assert metadata_confidence(normal) == "high"
assert is_low_confidence(normal) is False


def assert_unresolved(author):
    assert is_author_key_resolved(author) is False, author
    assert first_author_lastname(author) == "unknown", author
    assert base_key(author, "2024") == "unknown2024", author
    metadata = metadata_for(author)
    assert metadata_confidence(metadata) == "low", author
    assert is_low_confidence(metadata) is True, author


assert_unresolved("AT&T Labs")
assert_unresolved("NASA")
assert_unresolved("OpenAI")
assert_unresolved("李雷")
PY
