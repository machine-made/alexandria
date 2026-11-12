#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

WORK="$TMP/parsed/work"
mkdir -p "$WORK"
cat > "$WORK/work.md" <<MD
# A Small Paper

Author: Ada Lovelace
Year: 1843
Category: papers
MD
ALEXANDRIA_PARSED_WORK="$WORK" "$ROOT/pipeline/alexandria_metadata_from_markdown.py"
python3 - <<PY
import json
from pathlib import Path
m=json.loads(Path('$WORK/metadata.json').read_text())
assert m['title'] == 'A Small Paper'
assert m['author'] == 'Ada Lovelace'
assert m['year'] == '1843'
assert m['category'] == 'papers'
assert m['authors'] == ['Ada Lovelace']
assert m['metadata_source'] == 'heuristic'
# Title from heading heuristics without a structured id → low (format-agnostic
# author-identity gate; was epub-only before).
assert m['metadata_confidence'] == 'low'
assert m['source_stem'] == 'work'
assert m['source_files'] == []
assert m['source_sha256'] == {}
assert m['doi'] == ''
assert m['isbn'] == ''
assert m['arxiv_id'] == ''
PY

BOOK="$TMP/parsed/rocket_book"
mkdir -p "$BOOK"
cat > "$BOOK/rocket_book.md" <<MD
![](_page_0_Picture_0.jpeg)

# Introduction to Rocket Science and Space Exploration

A. Sivathanu Pillai

## Introduction to Rocket Science and Space Exploration

The chapters in the book cover rockets, satellites, rocket technology, and Earth 2.0.

First edition published 2023 by CRC Press

DOI: 10.1201/9781003226901.
ISBN 978-1-032-22690-1
arXiv:2401.01234

© 2023 A. Sivathanu Pillai
MD
printf pdf > "$TMP/rocket_book.pdf"
ALEXANDRIA_PARSED_WORK="$BOOK" ALEXANDRIA_DROP_DIR="$TMP" "$ROOT/pipeline/alexandria_metadata_from_markdown.py"
python3 - <<PY
import json
from pathlib import Path
m=json.loads(Path('$BOOK/metadata.json').read_text())
assert m['title'] == 'Introduction to Rocket Science and Space Exploration'
assert m['author'] == 'A. Sivathanu Pillai'
assert m['year'] == '2023'
assert m['category'] == 'propulsion'
assert m['doi'] == '10.1201/9781003226901'
assert m['isbn'] == '9781032226901'
assert m['arxiv_id'] == '2401.01234'
assert m['metadata_source'] == 'doi'
assert m['metadata_confidence'] == 'high'
assert m['source_stem'] == 'rocket_book'
assert len(m['source_files']) == 1
assert m['source_files'][0]['name'] == 'rocket_book.pdf'
assert m['source_sha256']['rocket_book.pdf']
PY

ORG="$TMP/parsed/org_author"
mkdir -p "$ORG"
cat > "$ORG/org_author.md" <<MD
# Industrial Paper

Author: AT&T Labs
Year: 2024
Category: papers
MD
ALEXANDRIA_PARSED_WORK="$ORG" "$ROOT/pipeline/alexandria_metadata_from_markdown.py"
python3 - <<PY
import json
from pathlib import Path
m=json.loads(Path('$ORG/metadata.json').read_text())
assert m['author'] == 'AT&T Labs'
assert m['authors'] == ['AT&T Labs']
PY

CITED="$TMP/parsed/cited_reference"
mkdir -p "$CITED"
cat > "$CITED/cited_reference.md" <<MD
# My Paper

Author: Jane Doe
Year: 2024
Category: papers

## References

DOI: 10.9999/reference
Smith J. https://doi.org/10.9999/reference.
MD
ALEXANDRIA_PARSED_WORK="$CITED" "$ROOT/pipeline/alexandria_metadata_from_markdown.py"
python3 - <<PY
import json
from pathlib import Path
m=json.loads(Path('$CITED/metadata.json').read_text())
assert m['doi'] == ''
assert m['metadata_source'] == 'heuristic'
# Heading-heuristic title, no structured id → low (format-agnostic hold).
assert m['metadata_confidence'] == 'low'
PY

# Same-stem EPUB metadata is extracted from the drop directory, reused by SHA,
# and regenerated rather than reused after the source changes.
EPUB_WORK="$TMP/parsed/epub_work"
mkdir -p "$EPUB_WORK"
cat > "$EPUB_WORK/epub_work.md" <<'MD'
# Misleading Subtitle

Excellent Writing and Speaking

First edition published 2016.
MD
python3 - "$TMP/epub_work.epub" "Original OPF Title" <<'PY'
import sys
import zipfile
container = '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="content.opf"/></rootfiles></container>'
opf = f'<package xmlns="http://www.idpf.org/2007/opf"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>{sys.argv[2]}</dc:title><dc:creator>Grant Barrett</dc:creator><dc:date>2016-03-17</dc:date></metadata></package>'
with zipfile.ZipFile(sys.argv[1], 'w') as archive:
    archive.writestr('META-INF/container.xml', container)
    archive.writestr('content.opf', opf)
PY
ALEXANDRIA_PARSED_WORK="$EPUB_WORK" ALEXANDRIA_DROP_DIR="$TMP" "$ROOT/pipeline/alexandria_metadata_from_markdown.py"
python3 - "$EPUB_WORK" <<'PY'
import json
import sys
from pathlib import Path
work = Path(sys.argv[1])
m = json.loads((work / 'metadata.json').read_text())
e = json.loads((work / 'epub_meta.json').read_text())
assert m['title'] == 'Original OPF Title', m
assert m['author'] == 'Grant Barrett', m
assert e['source']['name'] == 'epub_work.epub', e
e['reuse_sentinel'] = True
(work / 'epub_meta.json').write_text(json.dumps(e))
PY
ALEXANDRIA_PARSED_WORK="$EPUB_WORK" ALEXANDRIA_DROP_DIR="$TMP" "$ROOT/pipeline/alexandria_metadata_from_markdown.py"
python3 - "$EPUB_WORK/epub_meta.json" <<'PY'
import json
import sys
from pathlib import Path
assert json.loads(Path(sys.argv[1]).read_text())['reuse_sentinel'] is True
PY
python3 - "$TMP/epub_work.epub" "Changed OPF Title" <<'PY'
import sys
import zipfile
container = '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="content.opf"/></rootfiles></container>'
opf = f'<package xmlns="http://www.idpf.org/2007/opf"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>{sys.argv[2]}</dc:title><dc:creator>New Author</dc:creator><dc:date>2020</dc:date></metadata></package>'
with zipfile.ZipFile(sys.argv[1], 'w') as archive:
    archive.writestr('META-INF/container.xml', container)
    archive.writestr('content.opf', opf)
PY
ALEXANDRIA_PARSED_WORK="$EPUB_WORK" ALEXANDRIA_DROP_DIR="$TMP" "$ROOT/pipeline/alexandria_metadata_from_markdown.py"
python3 - "$EPUB_WORK" <<'PY'
import json
import sys
from pathlib import Path
work = Path(sys.argv[1])
e = json.loads((work / 'epub_meta.json').read_text())
m = json.loads((work / 'metadata.json').read_text())
assert 'reuse_sentinel' not in e, e
assert m['title'] == 'Changed OPF Title', m
assert m['author'] == 'New Author', m
PY

# A stale artifact for a different source SHA must not be selected when the
# replacement EPUB is malformed; metadata falls back to LOW instead of aborting.
STALE_WORK="$TMP/parsed/stale_epub"
mkdir -p "$STALE_WORK"
cat > "$STALE_WORK/stale_epub.md" <<'MD'
# Plausible Book Title

Plausible Person Name

First edition published 2019.
MD
printf 'not a zip' > "$TMP/stale_epub.epub"
cat > "$STALE_WORK/epub_meta.json" <<'JSON'
{"source":{"name":"old.epub","sha256":"wrong"},"title":"Stale Title","authors":["Stale Author"],"year":"2001"}
JSON
ALEXANDRIA_PARSED_WORK="$STALE_WORK" ALEXANDRIA_DROP_DIR="$TMP" "$ROOT/pipeline/alexandria_metadata_from_markdown.py"
python3 - "$STALE_WORK/metadata.json" <<'PY'
import json
import sys
from pathlib import Path
m = json.loads(Path(sys.argv[1]).read_text())
assert m['title'] == 'Plausible Book Title', m
assert m['title'] != 'Stale Title', m
assert m['metadata_field_sources']['title'] == 'heuristic', m
assert m['metadata_confidence'] == 'low', m
PY

# Unsupported/encrypted ZIP members must fall back to LOW metadata without
# replacing a stale durable EPUB artifact or aborting the Markdown extractor.
python3 - "$ROOT/pipeline" "$TMP" <<'PY'
import json
import os
import sys
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, sys.argv[1])
import alexandria_metadata_from_markdown

root = Path(sys.argv[2])
failures = []
for exception_type in (NotImplementedError, RuntimeError):
    stem = exception_type.__name__.lower()
    work = root / "parsed" / stem
    work.mkdir()
    (work / f"{stem}.md").write_text(
        "# Plausible Book Title\n\nPlausible Person Name\n\nFirst edition published 2019.\n",
        encoding="utf-8",
    )
    source = root / f"{stem}.epub"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("META-INF/container.xml", "unused")
    artifact = work / "epub_meta.json"
    artifact.write_text(
        '{"source":{"sha256":"wrong"},"title":"Stale Title"}\n',
        encoding="utf-8",
    )
    with mock.patch.object(zipfile.ZipFile, "open", side_effect=exception_type("unsupported ZIP feature")):
        try:
            with mock.patch.dict(
                os.environ,
                {"ALEXANDRIA_PARSED_WORK": str(work), "ALEXANDRIA_DROP_DIR": str(root)},
            ):
                status = alexandria_metadata_from_markdown.main()
        except exception_type:
            failures.append(f"{exception_type.__name__} aborted Markdown metadata extraction")
            continue
    if status != 0:
        failures.append(f"{exception_type.__name__} returned {status}, expected 0")
        continue
    metadata = json.loads((work / "metadata.json").read_text(encoding="utf-8"))
    if metadata["metadata_confidence"] != "low":
        failures.append(f"{exception_type.__name__} confidence was {metadata['metadata_confidence']}")
    if json.loads(artifact.read_text(encoding="utf-8"))["title"] != "Stale Title":
        failures.append(f"{exception_type.__name__} replaced durable artifact")

assert not failures, failures
PY

# Independently selectable regressions for canonical nested EPUB artifacts and
# corrupt decompressor fallback. The normal gate runs both.
python3 - "$ROOT/pipeline" "$TMP" "${MARKDOWN_EPUB_REGRESSION:-all}" <<'PY'
import hashlib
import json
import lzma
import os
import sys
import zipfile
import zlib
from pathlib import Path
from unittest import mock

sys.path.insert(0, sys.argv[1])
import alexandria_metadata_from_markdown

root = Path(sys.argv[2])
selected = sys.argv[3]
container = (
    '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
    '<rootfiles><rootfile full-path="content.opf"/></rootfiles></container>'
)


def make_epub(path, title):
    opf = (
        '<package xmlns="http://www.idpf.org/2007/opf" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<metadata><dc:title>{title}</dc:title></metadata></package>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("content.opf", opf)


def nested_artifact():
    stem = "nested_marker"
    work = root / "parsed" / stem
    nested = work / stem
    nested.mkdir(parents=True)
    source = root / f"{stem}.epub"
    make_epub(source, "Nested Artifact Title")
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    root_artifact = work / "epub_meta.json"
    root_artifact.write_text(
        json.dumps({"source": {"sha256": source_sha}, "title": "Wrong Root Artifact"}),
        encoding="utf-8",
    )

    first = alexandria_metadata_from_markdown.epub_metadata_for_work(work, root, stem)
    nested_artifact_path = nested / "epub_meta.json"
    assert nested_artifact_path.is_file(), "canonical nested artifact was not written"
    assert first["title"] == "Nested Artifact Title", first
    assert json.loads(root_artifact.read_text(encoding="utf-8"))["title"] == "Wrong Root Artifact"

    cached = json.loads(nested_artifact_path.read_text(encoding="utf-8"))
    cached["reuse_sentinel"] = True
    nested_artifact_path.write_text(json.dumps(cached), encoding="utf-8")
    with mock.patch.object(
        alexandria_metadata_from_markdown,
        "write_epub_metadata",
        side_effect=AssertionError("SHA-valid nested artifact was regenerated"),
    ):
        second = alexandria_metadata_from_markdown.epub_metadata_for_work(work, root, stem)
    assert second["reuse_sentinel"] is True, second


def decompressor_failures():
    failures = []
    for exception_type in (zlib.error, lzma.LZMAError):
        stem = f"corrupt_{exception_type.__name__.lower()}"
        work = root / "parsed" / stem
        work.mkdir()
        (work / f"{stem}.md").write_text(
            "# Plausible Book Title\n\nPlausible Person Name\n\nFirst edition published 2019.\n",
            encoding="utf-8",
        )
        source = root / f"{stem}.epub"
        make_epub(source, "Unused Title")
        artifact = work / "epub_meta.json"
        artifact.write_text(
            '{"source":{"sha256":"wrong"},"title":"Stale Title"}\n',
            encoding="utf-8",
        )
        with mock.patch.object(zipfile.ZipFile, "open", side_effect=exception_type("corrupt stream")):
            try:
                with mock.patch.dict(
                    os.environ,
                    {"ALEXANDRIA_PARSED_WORK": str(work), "ALEXANDRIA_DROP_DIR": str(root)},
                ):
                    status = alexandria_metadata_from_markdown.main()
            except exception_type:
                failures.append(f"{exception_type.__name__} aborted Markdown metadata extraction")
                continue
        if status != 0:
            failures.append(f"{exception_type.__name__} returned {status}, expected 0")
            continue
        metadata = json.loads((work / "metadata.json").read_text(encoding="utf-8"))
        if metadata["metadata_confidence"] != "low":
            failures.append(f"{exception_type.__name__} confidence was {metadata['metadata_confidence']}")
        if json.loads(artifact.read_text(encoding="utf-8"))["title"] != "Stale Title":
            failures.append(f"{exception_type.__name__} replaced durable artifact")
    assert not failures, failures


def non_ocf_compression_fallback():
    unsupported = getattr(zipfile, "ZIP_ZSTANDARD", 93)
    stem = "unsupported_compression"
    work = root / "parsed" / stem
    work.mkdir()
    (work / f"{stem}.md").write_text(
        "# Plausible Book Title\n\nPlausible Person Name\n\nFirst edition published 2019.\n",
        encoding="utf-8",
    )
    source = root / f"{stem}.epub"
    make_epub(source, "Must Not Be Used")
    artifact = work / "epub_meta.json"
    artifact.write_text(
        '{"source":{"sha256":"wrong"},"title":"Stale Title"}\n',
        encoding="utf-8",
    )
    original_getinfo = zipfile.ZipFile.getinfo
    original_open = zipfile.ZipFile.open
    opens = []

    def unsupported_getinfo(archive, name):
        info = original_getinfo(archive, name)
        info.compress_type = unsupported
        return info

    def tracked_open(archive, name, *args, **kwargs):
        opens.append(name)
        return original_open(archive, name, *args, **kwargs)

    with mock.patch.object(zipfile.ZipFile, "getinfo", unsupported_getinfo), mock.patch.object(
        zipfile.ZipFile, "open", tracked_open
    ), mock.patch.dict(
        os.environ,
        {"ALEXANDRIA_PARSED_WORK": str(work), "ALEXANDRIA_DROP_DIR": str(root)},
    ):
        status = alexandria_metadata_from_markdown.main()
    assert status == 0, status
    assert not opens, opens
    metadata = json.loads((work / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["title"] == "Plausible Book Title", metadata
    assert metadata["metadata_confidence"] == "low", metadata
    assert json.loads(artifact.read_text(encoding="utf-8"))["title"] == "Stale Title"


cases = {
    "nested-artifact": nested_artifact,
    "decompressor": decompressor_failures,
    "non-ocf-compression": non_ocf_compression_fallback,
}
if selected == "all":
    for case in cases.values():
        case()
else:
    cases[selected]()
PY
