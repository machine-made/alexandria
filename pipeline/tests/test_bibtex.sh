#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TMP="$(mktemp -d "$ROOT/.tmp.bibtex.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

assert_has() {
    local file="$1" needle="$2"
    grep -Fq "$needle" "$file"
}

assert_lacks_regex() {
    local file="$1" pattern="$2"
    ! grep -Eq "$pattern" "$file" || exit 1
}

render() {
    local metadata="$1" key="$2" output="$3"
    shift 3
    "$ROOT/pipeline/alexandria_bibtex.py" --metadata "$metadata" --key "$key" "$@" > "$output"
}

ARTICLE="$TMP/article.json"
cat > "$ARTICLE" <<'JSON'
{
  "title": "A Small Paper",
  "author": "Ada Lovelace and Charles Babbage",
  "authors": ["Ada Lovelace", "Charles Babbage"],
  "year": "1843",
  "month": "October",
  "category": "papers",
  "journal": "Journal of Analytical Engines",
  "volume": "12",
  "number": "3",
  "pages": "1-10",
  "url": "https://example.test/lovelace",
  "doi": "10.1145/3133956.3134047",
  "isbn": "",
  "arxiv_id": "",
  "metadata_confidence": "high"
}
JSON
render "$ARTICLE" lovelace1843 "$TMP/lovelace1843.bib"
assert_has "$TMP/lovelace1843.bib" '@article{lovelace1843,'
assert_has "$TMP/lovelace1843.bib" 'title = {A Small Paper}'
assert_has "$TMP/lovelace1843.bib" 'author = {Ada Lovelace and Charles Babbage}'
assert_has "$TMP/lovelace1843.bib" 'year = {1843}'
assert_has "$TMP/lovelace1843.bib" 'month = {October}'
assert_has "$TMP/lovelace1843.bib" 'journal = {Journal of Analytical Engines}'
assert_has "$TMP/lovelace1843.bib" 'volume = {12}'
assert_has "$TMP/lovelace1843.bib" 'number = {3}'
assert_has "$TMP/lovelace1843.bib" 'pages = {1-10}'
assert_has "$TMP/lovelace1843.bib" 'url = {https://example.test/lovelace}'
assert_has "$TMP/lovelace1843.bib" 'doi = {10.1145/3133956.3134047}'

BOOK="$TMP/book.json"
cat > "$BOOK" <<'JSON'
{
  "title": "Optimization Handbook",
  "author": "Jane Smith",
  "authors": ["Jane Smith"],
  "year": "2020",
  "category": "optimization",
  "publisher": "Example Press",
  "metadata_confidence": "medium"
}
JSON
render "$BOOK" smith2020 "$TMP/smith2020.bib"
assert_has "$TMP/smith2020.bib" '@book{smith2020,'
assert_has "$TMP/smith2020.bib" 'publisher = {Example Press}'

PROC="$TMP/proceedings.json"
cat > "$PROC" <<'JSON'
{
  "title": "Conference Rocket Results",
  "author": "Grace Hopper",
  "authors": ["Grace Hopper"],
  "year": "1952",
  "category": "papers",
  "booktitle": "Proceedings of the Rocket Conference",
  "pages": "22-29",
  "metadata_confidence": "high"
}
JSON
render "$PROC" hopper1952 "$TMP/hopper1952.bib"
assert_has "$TMP/hopper1952.bib" '@inproceedings{hopper1952,'
assert_has "$TMP/hopper1952.bib" 'booktitle = {Proceedings of the Rocket Conference}'

ARXIV="$TMP/arxiv.json"
cat > "$ARXIV" <<'JSON'
{
  "title": "NASA CFD Preprint",
  "author": "Katherine Johnson",
  "authors": ["Katherine Johnson"],
  "year": "2024",
  "category": "papers",
  "arxiv_id": "2401.01234v2",
  "primary_class": "cs.LG",
  "metadata_confidence": "high"
}
JSON
render "$ARXIV" johnson2024 "$TMP/johnson2024.bib"
assert_has "$TMP/johnson2024.bib" '@article{johnson2024,'
assert_has "$TMP/johnson2024.bib" 'title = {{NASA} {CFD} Preprint}'
assert_has "$TMP/johnson2024.bib" 'eprint = {2401.01234v2}'
assert_has "$TMP/johnson2024.bib" 'archivePrefix = {arXiv}'
assert_has "$TMP/johnson2024.bib" 'primaryClass = {cs.LG}'

DOI_ONLY="$TMP/doi_only.json"
cat > "$DOI_ONLY" <<'JSON'
{
  "title": "DOI Only Work",
  "author": "Alan Turing",
  "authors": ["Alan Turing"],
  "year": "1936",
  "category": "misc",
  "doi": "10.1000/example",
  "metadata_confidence": "high"
}
JSON
render "$DOI_ONLY" DoNotRecompute2024 "$TMP/doi_only.bib"
assert_has "$TMP/doi_only.bib" '@article{DoNotRecompute2024,'
assert_has "$TMP/doi_only.bib" 'doi = {10.1000/example}'

ISBN_ONLY="$TMP/isbn_only.json"
cat > "$ISBN_ONLY" <<'JSON'
{
  "title": "ISBN Only Book",
  "author": "Donald Knuth",
  "authors": ["Donald Knuth"],
  "year": "1968",
  "category": "misc",
  "isbn": "9780201896831",
  "metadata_confidence": "high"
}
JSON
render "$ISBN_ONLY" knuth1968 "$TMP/knuth1968.bib"
assert_has "$TMP/knuth1968.bib" '@book{knuth1968,'
assert_has "$TMP/knuth1968.bib" 'isbn = {9780201896831}'

SPECIAL="$TMP/special.json"
cat > "$SPECIAL" <<'JSON'
{
  "title": "50% A_B & C# café",
  "author": "AT&T Labs",
  "authors": ["AT&T Labs"],
  "year": "2024",
  "category": "papers",
  "doi": "",
  "isbn": "",
  "arxiv_id": "",
  "metadata_confidence": "medium"
}
JSON
render "$SPECIAL" special2024 "$TMP/special2024.bib"
assert_has "$TMP/special2024.bib" 'title = {50\% A\_B \& C\# café}'
assert_has "$TMP/special2024.bib" 'author = {AT\&T Labs}'
render "$SPECIAL" special2024 "$TMP/special2024.ascii.bib" --ascii-tex
assert_has "$TMP/special2024.ascii.bib" 'title = {50\% A\_B \& C\# cafe}'
! grep -Fq 'café' "$TMP/special2024.ascii.bib" || exit 1

ACRONYM="$TMP/acronym.json"
cat > "$ACRONYM" <<'JSON'
{
  "title": "A NASA DNA Guide",
  "author": "Mary Jackson",
  "authors": ["Mary Jackson"],
  "year": "1961",
  "category": "papers",
  "metadata_confidence": "medium"
}
JSON
render "$ACRONYM" jackson1961 "$TMP/jackson1961.bib"
assert_has "$TMP/jackson1961.bib" 'title = {A {NASA} {DNA} Guide}'
! grep -Fq 'title = {{A NASA DNA Guide}}' "$TMP/jackson1961.bib" || exit 1

# Comma-packed multi-author (NASA-report style) must become biber-valid
# "Surname, Initials" groups joined by " and " (no bare triple-comma name).
PACKED="$TMP/packed.json"
cat > "$PACKED" <<'JSON'
{
  "title": "Packed Multi-Author Report",
  "author": "Salmi, R. J., Wanhainen, J. P., Hannum, N. P.",
  "authors": ["Salmi, R. J., Wanhainen, J. P., Hannum, N. P."],
  "year": "1968",
  "category": "propulsion",
  "entry_type": "techreport",
  "institution": "National Aeronautics and Space Administration",
  "metadata_confidence": "medium"
}
JSON
render "$PACKED" salmi1968packed "$TMP/salmi1968packed.bib"
assert_has "$TMP/salmi1968packed.bib" 'author = {Salmi, R. J. and Wanhainen, J. P. and Hannum, N. P.}'
assert_lacks_regex "$TMP/salmi1968packed.bib" 'Salmi, R\. J\., Wanhainen'

# Same packed string via bare author field (no authors list).
PACKED_AUTHOR_ONLY="$TMP/packed_author_only.json"
cat > "$PACKED_AUTHOR_ONLY" <<'JSON'
{
  "title": "Packed Author Field Only",
  "author": "Salmi, R. J., Wanhainen, J. P., Hannum, N. P.",
  "authors": [],
  "year": "1968",
  "category": "propulsion",
  "metadata_confidence": "medium"
}
JSON
render "$PACKED_AUTHOR_ONLY" packedauthoronly "$TMP/packed_author_only.bib"
assert_has "$TMP/packed_author_only.bib" 'author = {Salmi, R. J. and Wanhainen, J. P. and Hannum, N. P.}'

# Odd/unpairable comma count must fall back safely (keep original).
ODD_PACKED="$TMP/odd_packed.json"
cat > "$ODD_PACKED" <<'JSON'
{
  "title": "Odd Packed Author",
  "author": "Salmi, R. J., Wanhainen",
  "authors": ["Salmi, R. J., Wanhainen"],
  "year": "1968",
  "category": "misc",
  "metadata_confidence": "medium"
}
JSON
render "$ODD_PACKED" oddpacked "$TMP/odd_packed.bib"
assert_has "$TMP/odd_packed.bib" 'author = {Salmi, R. J., Wanhainen}'

MISSING="$TMP/missing.json"
cat > "$MISSING" <<'JSON'
{
  "title": "",
  "author": "unknown",
  "authors": [],
  "year": "nodate",
  "category": "misc",
  "doi": "",
  "isbn": "",
  "arxiv_id": "",
  "metadata_confidence": "medium"
}
JSON
render "$MISSING" missingfields "$TMP/missingfields.bib"
assert_has "$TMP/missingfields.bib" '@misc{missingfields,'
assert_lacks_regex "$TMP/missingfields.bib" '^  title ='
assert_lacks_regex "$TMP/missingfields.bib" '^  author ='
assert_lacks_regex "$TMP/missingfields.bib" '^  year ='

LOW="$TMP/low.json"
cat > "$LOW" <<'JSON'
{
  "title": "Uncertain Work",
  "author": "unknown",
  "authors": [],
  "year": "nodate",
  "category": "misc",
  "doi": "",
  "isbn": "",
  "arxiv_id": "",
  "metadata_confidence": "low"
}
JSON
render "$LOW" unknownnodate "$TMP/unknownnodate.bib"
assert_has "$TMP/unknownnodate.bib" '@misc{unknownnodate,'
assert_has "$TMP/unknownnodate.bib" 'note = {Alexandria metadata confidence: low}'

LIB="$TMP/library"
mkdir -p "$LIB/papers" "$LIB/misc"
cp "$TMP/lovelace1843.bib" "$LIB/papers/lovelace1843.bib"
cp "$TMP/unknownnodate.bib" "$LIB/misc/unknownnodate.bib"
(umask 022; "$ROOT/pipeline/alexandria_bibtex.py" --rebuild-root "$LIB")
assert_has "$LIB/alexandria.bib" '@article{lovelace1843,'
assert_has "$LIB/alexandria.bib" '@misc{unknownnodate,'

EXPECTED_ROOT="$TMP/expected-root.bib"
cat "$TMP/unknownnodate.bib" > "$EXPECTED_ROOT"
printf '\n' >> "$EXPECTED_ROOT"
cat "$TMP/lovelace1843.bib" >> "$EXPECTED_ROOT"
cmp "$EXPECTED_ROOT" "$LIB/alexandria.bib"
python3 -c 'import stat, sys; from pathlib import Path; assert stat.S_IMODE(Path(sys.argv[1]).stat().st_mode) == 0o644' "$LIB/alexandria.bib"

ROOT="$ROOT" TMP="$TMP" python3 - <<'PY'
import importlib.util
import os
import stat
import sys
from pathlib import Path

root = Path(os.environ["ROOT"])
library = Path(os.environ["TMP"]) / "umask-library"
(library / "papers").mkdir(parents=True)
(library / "papers" / "example.bib").write_text("@article{example,}\n", encoding="utf-8")
module_path = root / "pipeline" / "alexandria_bibtex.py"
sys.path.insert(0, str(module_path.parent))
spec = importlib.util.spec_from_file_location("alexandria_bibtex", module_path)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

previous_umask = os.umask(0o077)
try:
    aggregate = module.rebuild_root_bibliography(library)
finally:
    os.umask(previous_umask)
aggregate_mode = stat.S_IMODE(aggregate.stat().st_mode)
assert aggregate_mode == 0o600, oct(aggregate_mode)
PY

ROOT="$ROOT" LIB="$LIB" python3 - <<'PY'
import importlib.util
import os
import stat
import sys
from pathlib import Path
from unittest.mock import patch

root = Path(os.environ["ROOT"])
library = Path(os.environ["LIB"])
module_path = root / "pipeline" / "alexandria_bibtex.py"
sys.path.insert(0, str(module_path.parent))
spec = importlib.util.spec_from_file_location("alexandria_bibtex", module_path)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

aggregate = library / "alexandria.bib"
aggregate.chmod(0o640)
regular_file_modes = []
real_fsync = os.fsync

def record_mode_and_fsync(fd: int) -> None:
    fd_stat = os.fstat(fd)
    if stat.S_ISREG(fd_stat.st_mode):
        regular_file_modes.append(stat.S_IMODE(fd_stat.st_mode))
    real_fsync(fd)

with patch("os.fsync", side_effect=record_mode_and_fsync):
    module.rebuild_root_bibliography(library)
assert regular_file_modes == [0o640], regular_file_modes
PY

ROOT="$ROOT" LIB="$LIB" EXPECTED_ROOT="$EXPECTED_ROOT" python3 - <<'PY'
import importlib.util
import os
import stat
import sys
from pathlib import Path
from unittest.mock import patch

root = Path(os.environ["ROOT"])
library = Path(os.environ["LIB"])
module_path = root / "pipeline" / "alexandria_bibtex.py"
sys.path.insert(0, str(module_path.parent))
spec = importlib.util.spec_from_file_location("alexandria_bibtex", module_path)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

aggregate = library / "alexandria.bib"
original = b"existing aggregate must survive publication failure\n"
aggregate.write_bytes(original)
aggregate.chmod(0o640)
publication_failed = False
with patch("os.replace", side_effect=OSError("injected publication failure")):
    try:
        module.rebuild_root_bibliography(library)
    except OSError as error:
        assert str(error) == "injected publication failure"
        publication_failed = True

errors = []
if not publication_failed:
    errors.append("replacement failure was not raised")
if aggregate.read_bytes() != original:
    errors.append("existing aggregate changed")
if list(library.glob(".alexandria.bib.*.tmp")):
    errors.append("temporary publication file was not cleaned up")
assert not errors, "; ".join(errors)

module.rebuild_root_bibliography(library)
assert aggregate.read_bytes() == Path(os.environ["EXPECTED_ROOT"]).read_bytes()
assert stat.S_IMODE(aggregate.stat().st_mode) == 0o640
PY
