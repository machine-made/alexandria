#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TMP="$(mktemp -d "$ROOT/.tmp.bib_import.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
export PYTHONDONTWRITEBYTECODE=1
IMPORT="$ROOT/pipeline/alexandria_bib_import.py"
BIBTEX="$ROOT/pipeline/alexandria_bibtex.py"
VALIDATE="$ROOT/pipeline/alexandria_validate_work.py"

assert_has() {
    local file="$1" needle="$2"
    grep -Fq -- "$needle" "$file"
}

write_work() {
    local lib="$1" category="$2" key="$3" title="$4" author="$5" year="$6" doi="${7:-}"
    mkdir -p "$lib/$category/$key"
    cat > "$lib/$category/$key.md" <<MD
# $title

Filed body for $key.
MD
    python3 - "$lib/$category/$key/metadata.json" "$key" "$title" "$author" "$year" "$category" "$doi" <<'PY'
import json
import sys
path, key, title, author, year, category, doi = sys.argv[1:]
authors = [part.strip() for part in author.split(' and ') if part.strip()]
metadata = {
    "key": key,
    "title": title,
    "author": author,
    "authors": authors,
    "year": year,
    "category": category,
    "metadata_confidence": "medium",
}
if doi:
    metadata["doi"] = doi
with open(path, "w", encoding="utf-8") as handle:
    json.dump(metadata, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY
    "$BIBTEX" --metadata "$lib/$category/$key/metadata.json" --key "$key" --output "$lib/$category/$key.bib"
    "$BIBTEX" --rebuild-root "$lib"
}

tree_manifest() {
    local dir="$1"
    (
        cd "$dir"
        find . -mindepth 1 -printf '%P %y %s %T@\n' | sort
        find . -type f -print0 | sort -z | xargs -0 sha256sum
    )
}

# 1. Scholar-style imported BibTeX enriches a filed work, preserves the Alexandria key, and validates.
CASE1="$TMP/scholar"
LIB1="$CASE1/library"
mkdir -p "$LIB1"
write_work "$LIB1" misc filedkey "A Google Scholar Paper" "Jane Smith" 2020
cat > "$CASE1/scholar.bib" <<'BIB'
@article{smith2020google,
  title = {A {Google Scholar} Paper},
  author = {Jane Smith},
  year = {2020},
  journal = {Journal of Reusable Parsers},
  volume = {12},
  number = {3},
  pages = {1--10},
  doi = {https://doi.org/10.1000/scholar},
  url = {https://example.test/scholar}
}
BIB
md_sha_before="$(sha256sum "$LIB1/misc/filedkey.md" | awk '{print $1}')"
"$IMPORT" filedkey --bibtex "$CASE1/scholar.bib" --library "$LIB1" > "$CASE1/import.out"
assert_has "$CASE1/import.out" 'imported BibTeX into key=filedkey category=misc'
assert_has "$CASE1/import.out" '- doi: <missing> -> "10.1000/scholar"'
assert_has "$CASE1/import.out" '- journal: <missing> -> "Journal of Reusable Parsers"'
python3 - "$LIB1" "$CASE1/scholar.bib" <<'PY'
import json
import sys
from pathlib import Path
lib = Path(sys.argv[1])
bib_source = str(Path(sys.argv[2]).resolve())
metadata = json.loads((lib / 'misc' / 'filedkey' / 'metadata.json').read_text(encoding='utf-8'))
assert metadata['key'] == 'filedkey', metadata
assert metadata['category'] == 'misc', metadata
assert metadata['doi'] == '10.1000/scholar', metadata
assert metadata['journal'] == 'Journal of Reusable Parsers', metadata
assert metadata['volume'] == '12', metadata
assert metadata['number'] == '3', metadata
assert metadata['pages'] == '1--10', metadata
assert metadata['url'] == 'https://example.test/scholar', metadata
assert metadata['metadata_source'] == 'manual_bib', metadata
assert metadata['scholar_key'] == 'smith2020google', metadata
assert metadata['manual_bib_source'] == bib_source, metadata
sidecar = (lib / 'misc' / 'filedkey.bib').read_text(encoding='utf-8')
root = (lib / 'alexandria.bib').read_text(encoding='utf-8')
assert '@article{filedkey,' in sidecar, sidecar
assert '@article{smith2020google,' not in sidecar, sidecar
assert sidecar.strip() in root, root
PY
[[ "$(sha256sum "$LIB1/misc/filedkey.md" | awk '{print $1}')" == "$md_sha_before" ]]
"$VALIDATE" --work-md "$LIB1/misc/filedkey.md" > "$CASE1/validate.out"
assert_has "$CASE1/validate.out" 'validated 1 work(s)'

# 2. DOI/resolver-style BibTeX import fills resolver fields and regenerates a validating sidecar.
CASE2="$TMP/resolver"
LIB2="$CASE2/library"
mkdir -p "$LIB2"
write_work "$LIB2" misc resolverkey "Resolver Work" "Alan Turing" 1936
cat > "$CASE2/resolver.bib" <<'BIB'
@article{doi_10_5555_12345678,
  title = {Resolver Work},
  author = {Alan Turing},
  date = {1936-11-12},
  doi = {10.5555/12345678},
  journal = {Proceedings of Computation},
  month = {November}
}
BIB
"$IMPORT" "$LIB2/misc/resolverkey.md" --bibtex "$CASE2/resolver.bib" > "$CASE2/import.out"
assert_has "$CASE2/import.out" 'imported BibTeX into key=resolverkey category=misc'
python3 - "$LIB2" <<'PY'
import json
import sys
from pathlib import Path
lib = Path(sys.argv[1])
metadata = json.loads((lib / 'misc' / 'resolverkey' / 'metadata.json').read_text(encoding='utf-8'))
assert metadata['key'] == 'resolverkey', metadata
assert metadata['doi'] == '10.5555/12345678', metadata
assert metadata['journal'] == 'Proceedings of Computation', metadata
assert metadata['month'] == 'November', metadata
assert metadata['year'] == '1936', metadata
assert metadata['scholar_key'] == 'doi_10_5555_12345678', metadata
sidecar = (lib / 'misc' / 'resolverkey.bib').read_text(encoding='utf-8')
assert '@article{resolverkey,' in sidecar, sidecar
assert 'doi = {10.5555/12345678}' in sidecar, sidecar
PY
"$VALIDATE" --work-md "$LIB2/misc/resolverkey.md" > "$CASE2/validate.out"
assert_has "$CASE2/validate.out" 'validated 1 work(s)'

# 3. Key normalization from stdin: the imported entry key is provenance only, never the Alexandria key.
CASE3="$TMP/stdin_key"
LIB3="$CASE3/library"
mkdir -p "$LIB3"
write_work "$LIB3" misc stablekey "Stable Filed Work" "Grace Hopper" 1952
cat > "$CASE3/stdin.bib" <<'BIB'
@article{wildImportedKey,
  title = {Stable Filed Work},
  author = {Grace Hopper},
  year = {1952},
  doi = {10.7777/stable-key}
}
BIB
"$IMPORT" stablekey --library "$LIB3" < "$CASE3/stdin.bib" > "$CASE3/import.out"
python3 - "$LIB3" <<'PY'
import json
import sys
from pathlib import Path
lib = Path(sys.argv[1])
metadata = json.loads((lib / 'misc' / 'stablekey' / 'metadata.json').read_text(encoding='utf-8'))
assert metadata['key'] == 'stablekey', metadata
assert metadata['scholar_key'] == 'wildImportedKey', metadata
assert metadata['manual_bib_source'] == 'stdin', metadata
sidecar = (lib / 'misc' / 'stablekey.bib').read_text(encoding='utf-8')
assert '@article{stablekey,' in sidecar, sidecar
assert '@article{wildImportedKey,' not in sidecar, sidecar
assert 'wildImportedKey' not in (lib / 'alexandria.bib').read_text(encoding='utf-8'), sidecar
PY

# 4. Conflict reporting keeps existing values by default and --prefer-import takes imported values.
CASE4="$TMP/conflict"
LIB4="$CASE4/library"
mkdir -p "$LIB4"
write_work "$LIB4" papers conflictkey "Original Title" "Original Author" 2001 "10.1000/original"
cat > "$CASE4/conflict.bib" <<'BIB'
@article{conflictImport,
  title = {Imported Title},
  author = {Imported Author},
  year = {2002},
  doi = {10.1000/imported},
  journal = {Conflict Journal}
}
BIB
"$IMPORT" conflictkey --bibtex "$CASE4/conflict.bib" --library "$LIB4" > "$CASE4/default.out"
assert_has "$CASE4/default.out" '- title: existing="Original Title" imported="Imported Title" action=keep-existing'
assert_has "$CASE4/default.out" '- doi: existing="10.1000/original" imported="10.1000/imported" action=keep-existing'
python3 - "$LIB4" <<'PY'
import json
import sys
from pathlib import Path
metadata = json.loads((Path(sys.argv[1]) / 'papers' / 'conflictkey' / 'metadata.json').read_text(encoding='utf-8'))
assert metadata['title'] == 'Original Title', metadata
assert metadata['author'] == 'Original Author', metadata
assert metadata['year'] == '2001', metadata
assert metadata['doi'] == '10.1000/original', metadata
assert metadata['journal'] == 'Conflict Journal', metadata
PY
"$IMPORT" conflictkey --bibtex "$CASE4/conflict.bib" --library "$LIB4" --prefer-import > "$CASE4/prefer.out"
assert_has "$CASE4/prefer.out" '- title: existing="Original Title" imported="Imported Title" action=prefer-import'
assert_has "$CASE4/prefer.out" '- doi: existing="10.1000/original" imported="10.1000/imported" action=prefer-import'
python3 - "$LIB4" <<'PY'
import json
import sys
from pathlib import Path
lib = Path(sys.argv[1])
metadata = json.loads((lib / 'papers' / 'conflictkey' / 'metadata.json').read_text(encoding='utf-8'))
assert metadata['key'] == 'conflictkey', metadata
assert metadata['title'] == 'Imported Title', metadata
assert metadata['author'] == 'Imported Author', metadata
assert metadata['authors'] == ['Imported Author'], metadata
assert metadata['year'] == '2002', metadata
assert metadata['doi'] == '10.1000/imported', metadata
assert metadata['scholar_key'] == 'conflictImport', metadata
sidecar = (lib / 'papers' / 'conflictkey.bib').read_text(encoding='utf-8')
assert '@article{conflictkey,' in sidecar, sidecar
assert '10.1000/imported' in sidecar, sidecar
PY
"$VALIDATE" --work-md "$LIB4/papers/conflictkey.md" > "$CASE4/validate.out"
assert_has "$CASE4/validate.out" 'validated 1 work(s)'

# 5. Dry-run prints planned overlays/conflicts and writes nothing.
CASE5="$TMP/dry_run"
LIB5="$CASE5/library"
mkdir -p "$LIB5"
write_work "$LIB5" misc drykey "Dry Run Work" "Mary Jackson" 1961
cat > "$CASE5/dry.bib" <<'BIB'
@article{dryImport,
  title = {Dry Run Work},
  author = {Mary Jackson},
  year = {1961},
  doi = {10.8888/dry-run},
  journal = {Dry Run Journal}
}
BIB
tree_manifest "$LIB5" > "$CASE5/before.txt"
"$IMPORT" drykey --bibtex "$CASE5/dry.bib" --library "$LIB5" --dry-run > "$CASE5/dry.out"
assert_has "$CASE5/dry.out" 'DRY RUN: import BibTeX into Alexandria work'
assert_has "$CASE5/dry.out" '- doi: <missing> -> "10.8888/dry-run"'
assert_has "$CASE5/dry.out" '- journal: <missing> -> "Dry Run Journal"'
assert_has "$CASE5/dry.out" 'No files written.'
tree_manifest "$LIB5" > "$CASE5/after.txt"
cmp "$CASE5/before.txt" "$CASE5/after.txt"
python3 - "$LIB5" <<'PY'
import json
import sys
from pathlib import Path
lib = Path(sys.argv[1])
metadata = json.loads((lib / 'misc' / 'drykey' / 'metadata.json').read_text(encoding='utf-8'))
assert 'doi' not in metadata, metadata
assert 'journal' not in metadata, metadata
assert metadata['key'] == 'drykey', metadata
assert '@misc{drykey,' in (lib / 'alexandria.bib').read_text(encoding='utf-8')
PY
