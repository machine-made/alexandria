#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

EPUB="$TMP/sample.epub"
NON_ISBN_EPUB="$TMP/non-isbn-identifiers.epub"
SCHEME_EPUB="$TMP/isbn-scheme.epub"
PUBLICATION_DATE_EPUB="$TMP/publication-date.epub"
python3 - "$EPUB" "$NON_ISBN_EPUB" "$SCHEME_EPUB" "$PUBLICATION_DATE_EPUB" <<'PY'
import sys
import zipfile

container = '''<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>'''
opf = '''<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>The Indispensable Guide to Excellent Writing and Speaking</dc:title>
    <dc:creator>Grant Barrett</dc:creator>
    <dc:creator>Jane Editor</dc:creator>
    <dc:date>2016-03-17</dc:date>
    <dc:identifier id="other">urn:uuid:1234</dc:identifier>
    <dc:identifier id="book-id">urn:isbn:978-1-234-56789-7</dc:identifier>
    <dc:publisher>Example Press</dc:publisher>
    <dc:language>en-US</dc:language>
  </metadata>
</package>'''
with zipfile.ZipFile(sys.argv[1], "w") as archive:
    archive.writestr("META-INF/container.xml", container)
    archive.writestr("OEBPS/content.opf", opf)

non_isbn_opf = '''<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Identifiers Are Not ISBNs</dc:title>
    <dc:identifier id="book-id">doi:10.1234/5678</dc:identifier>
    <dc:identifier>urn:uuid:9781234567897</dc:identifier>
    <dc:identifier>ISBN: 978-1-234-56789-0</dc:identifier>
  </metadata>
</package>'''
with zipfile.ZipFile(sys.argv[2], "w") as archive:
    archive.writestr("META-INF/container.xml", container)
    archive.writestr("OEBPS/content.opf", non_isbn_opf)

scheme_opf = '''<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/"
         xmlns:opf="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="book-id">
  <metadata>
    <dc:title>Scheme-Marked ISBN</dc:title>
    <dc:identifier id="book-id" opf:scheme="ISBN">0-306-40615-2</dc:identifier>
  </metadata>
</package>'''
with zipfile.ZipFile(sys.argv[3], "w") as archive:
    archive.writestr("META-INF/container.xml", container)
    archive.writestr("OEBPS/content.opf", scheme_opf)

publication_date_opf = '''<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/"
         xmlns:custom="urn:example:metadata" version="2.0">
  <metadata>
    <dc:title>Publication Date Priority</dc:title>
    <dc:date custom:event="modification">2024-01-02</dc:date>
    <dc:date custom:event="PuBlIsHeD">2016-03-17</dc:date>
  </metadata>
</package>'''
with zipfile.ZipFile(sys.argv[4], "w") as archive:
    archive.writestr("META-INF/container.xml", container)
    archive.writestr("OEBPS/content.opf", publication_date_opf)
PY

"$ROOT/pipeline/alexandria_epub_meta.py" "$EPUB" "$TMP/epub_meta.json"
python3 - "$EPUB" "$TMP/epub_meta.json" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

source = Path(sys.argv[1])
meta = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
assert meta["source"]["name"] == "sample.epub", meta
assert meta["source"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest(), meta
assert meta["title"] == "The Indispensable Guide to Excellent Writing and Speaking", meta
assert meta["creators"] == ["Grant Barrett", "Jane Editor"], meta
assert meta["author"] == "Grant Barrett; Jane Editor", meta
assert meta["authors"] == ["Grant Barrett", "Jane Editor"], meta
assert meta["date"] == "2016-03-17", meta
assert meta["year"] == "2016", meta
assert meta["isbn"] == "9781234567897", meta
assert meta["publisher"] == "Example Press", meta
assert meta["language"] == "en-US", meta
assert meta["opf_path"] == "OEBPS/content.opf", meta
PY

"$ROOT/pipeline/alexandria_epub_meta.py" "$NON_ISBN_EPUB" "$TMP/non-isbn-meta.json"
python3 - "$TMP/non-isbn-meta.json" <<'PY'
import json
import sys
from pathlib import Path

meta = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert "isbn" not in meta, meta
PY


"$ROOT/pipeline/alexandria_epub_meta.py" "$SCHEME_EPUB" "$TMP/isbn-scheme-meta.json"
python3 - "$TMP/isbn-scheme-meta.json" <<'PY'
import json
import sys
from pathlib import Path

meta = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert meta["isbn"] == "0306406152", meta
PY

"$ROOT/pipeline/alexandria_epub_meta.py" "$PUBLICATION_DATE_EPUB" "$TMP/publication-date-meta.json"
python3 - "$TMP/publication-date-meta.json" <<'PY'
import json
import sys
from pathlib import Path

meta = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert meta["date"] == "2016-03-17", meta
assert meta["year"] == "2016", meta
PY

# A malformed package must fail without replacing a previously durable artifact.
printf 'not an epub' > "$TMP/broken.epub"
printf '{"sentinel": true}\n' > "$TMP/existing.json"
if "$ROOT/pipeline/alexandria_epub_meta.py" "$TMP/broken.epub" "$TMP/existing.json"; then
    echo "malformed EPUB unexpectedly succeeded" >&2
    exit 1
fi
grep -q '"sentinel": true' "$TMP/existing.json"

# ZIP features unsupported by Python, including encrypted members, must be
# reported as extraction failures without replacing a durable artifact.
python3 - "$ROOT/pipeline" "$EPUB" "$TMP/existing.json" <<'PY'
import contextlib
import io
import json
import sys
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, sys.argv[1])
import alexandria_epub_meta

epub = Path(sys.argv[2])
artifact = Path(sys.argv[3])
failures = []
for exception_type in (NotImplementedError, RuntimeError):
    artifact.write_text('{"sentinel": true}\n', encoding="utf-8")
    with mock.patch.object(zipfile.ZipFile, "open", side_effect=exception_type("unsupported ZIP feature")):
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                status = alexandria_epub_meta.main(["alexandria_epub_meta.py", str(epub), str(artifact)])
        except exception_type:
            failures.append(f"{exception_type.__name__} escaped extractor CLI")
            continue
    if status != 1:
        failures.append(f"{exception_type.__name__} returned {status}, expected 1")
    if json.loads(artifact.read_text(encoding="utf-8")) != {"sentinel": True}:
        failures.append(f"{exception_type.__name__} replaced durable artifact")

assert not failures, failures
PY

# Independently selectable regression cases. Set EPUB_META_REGRESSION to one
# case name during RED/GREEN verification; the normal gate runs all five.
python3 - "$ROOT/pipeline" "$TMP" "${EPUB_META_REGRESSION:-all}" <<'PY'
import hashlib
import contextlib
import io
import json
import lzma
import os
import sys
import zipfile
import zlib
from pathlib import Path
from unittest import mock

sys.path.insert(0, sys.argv[1])
import alexandria_epub_meta

root = Path(sys.argv[2])
selected = sys.argv[3]
container = (
    '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
    '<rootfiles><rootfile full-path="content.opf"/></rootfiles></container>'
)


def make_epub(path, opf):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("content.opf", opf)


def package(metadata, unique_identifier=""):
    unique = f' unique-identifier="{unique_identifier}"' if unique_identifier else ""
    return (
        f'<package xmlns="http://www.idpf.org/2007/opf" '
        f'xmlns:dc="http://purl.org/dc/elements/1.1/" version="3.0"{unique}>'
        f"<metadata>{metadata}</metadata></package>"
    )


def snapshot_consistency():
    path = root / "snapshot.epub"
    replacement = root / "snapshot-replacement.epub"
    old_opf = package("<dc:title>Old Snapshot</dc:title>")
    new_opf = package("<dc:title>New Path Target</dc:title>")
    make_epub(path, old_opf)
    make_epub(replacement, new_opf)
    old_bytes = path.read_bytes()
    old_size = len(old_bytes)
    original_open = zipfile.ZipFile.open

    def replace_after_opf(archive, name, *args, **kwargs):
        stream = original_open(archive, name, *args, **kwargs)
        original_read = stream.read

        def read(size=-1):
            value = original_read(size)
            if name.filename == "content.opf":
                os.replace(replacement, path)
            return value

        stream.read = read
        return stream

    with mock.patch.object(zipfile.ZipFile, "open", replace_after_opf):
        result = alexandria_epub_meta.extract_epub_metadata(path)
    assert result["title"] == "Old Snapshot", result
    assert result["source"]["size"] == old_size, result
    assert result["source"]["sha256"] == hashlib.sha256(old_bytes).hexdigest(), result


def zip_bomb_guard():
    path = root / "oversized.epub"
    make_epub(path, package("<dc:title>Oversized Metadata</dc:title>"))
    original_getinfo = zipfile.ZipFile.getinfo
    original_open = zipfile.ZipFile.open
    for oversized_member in ("META-INF/container.xml", "content.opf"):
        reads = []

        def oversized_getinfo(archive, name):
            info = original_getinfo(archive, name)
            if name == oversized_member:
                info.file_size = 1 << 30
            return info

        def tracked_open(archive, name, *args, **kwargs):
            reads.append(name.filename if isinstance(name, zipfile.ZipInfo) else name)
            return original_open(archive, name, *args, **kwargs)

        with mock.patch.object(zipfile.ZipFile, "getinfo", oversized_getinfo), mock.patch.object(
            zipfile.ZipFile, "open", tracked_open
        ):
            try:
                alexandria_epub_meta.extract_epub_metadata(path)
            except ValueError as exc:
                assert "too large" in str(exc).lower(), exc
            else:
                raise AssertionError(f"oversized XML metadata member was accepted: {oversized_member}")
        assert oversized_member not in reads, (oversized_member, reads)


def quoted_unique_identifier():
    path = root / "quoted-id.epub"
    opf = package(
        '<dc:title>Quoted Identifier</dc:title>'
        '<dc:identifier id="bad\'quoted">urn:isbn:978-1-234-56789-7</dc:identifier>',
        "bad'quoted",
    )
    make_epub(path, opf)
    result = alexandria_epub_meta.extract_epub_metadata(path)
    assert result["isbn"] == "9781234567897", result


def isbn13_book_prefix():
    path = root / "non-book-ean.epub"
    make_epub(
        path,
        package('<dc:identifier id="ean">urn:isbn:4006381333931</dc:identifier>', "ean"),
    )
    result = alexandria_epub_meta.extract_epub_metadata(path)
    assert "isbn" not in result, result


def epub3_identifier_type():
    positive = root / "onix-positive.epub"
    make_epub(
        positive,
        package(
            '<dc:identifier id="isbn13">9781234567897</dc:identifier>'
            '<meta refines="#isbn13" property="identifier-type" scheme="onix:codelist5">15</meta>'
        ),
    )
    result = alexandria_epub_meta.extract_epub_metadata(positive)
    assert result["isbn"] == "9781234567897", result

    negatives = {
        "wrong-refines": (
            '<dc:identifier id="isbn13">9781234567897</dc:identifier>'
            '<meta refines="#other" property="identifier-type" scheme="onix:codelist5">15</meta>'
        ),
        "wrong-code": (
            '<dc:identifier id="isbn13">9781234567897</dc:identifier>'
            '<meta refines="#isbn13" property="identifier-type" scheme="onix:codelist5">02</meta>'
        ),
        "bad-checksum": (
            '<dc:identifier id="isbn13">9781234567890</dc:identifier>'
            '<meta refines="#isbn13" property="identifier-type" scheme="onix:codelist5">15</meta>'
        ),
    }
    for name, metadata in negatives.items():
        path = root / f"onix-{name}.epub"
        make_epub(path, package(metadata))
        result = alexandria_epub_meta.extract_epub_metadata(path)
        assert "isbn" not in result, (name, result)


def decompressor_failures():
    path = root / "decompressor.epub"
    artifact = root / "decompressor.json"
    make_epub(path, package("<dc:title>Decompressor Failure</dc:title>"))
    failures = []
    for exception_type in (zlib.error, lzma.LZMAError):
        artifact.write_text('{"sentinel": true}\n', encoding="utf-8")
        with mock.patch.object(zipfile.ZipFile, "open", side_effect=exception_type("corrupt stream")):
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    status = alexandria_epub_meta.main(
                        ["alexandria_epub_meta.py", str(path), str(artifact)]
                    )
            except exception_type:
                failures.append(f"{exception_type.__name__} escaped extractor CLI")
                continue
        if status != 1:
            failures.append(f"{exception_type.__name__} returned {status}, expected 1")
        if json.loads(artifact.read_text(encoding="utf-8")) != {"sentinel": True}:
            failures.append(f"{exception_type.__name__} replaced durable artifact")
    assert not failures, failures


def bounded_xml_read():
    limit = alexandria_epub_meta._MAX_XML_METADATA_SIZE

    class Info:
        file_size = 1
        compress_type = zipfile.ZIP_DEFLATED

    class Stream:
        def __init__(self):
            self.requests = []
            self.entered = False
            self.exited = False

        def __enter__(self):
            self.entered = True
            return self

        def __exit__(self, *args):
            self.exited = True

        def read(self, size=-1):
            self.requests.append(size)
            return b"x" * (limit + 1)

    class Archive:
        def __init__(self):
            self.info = Info()
            self.stream = Stream()
            self.opened = []

        def getinfo(self, member):
            return self.info

        def read(self, *args, **kwargs):
            raise AssertionError("read_xml_metadata used unbounded archive.read")

        def open(self, info):
            self.opened.append(info)
            return self.stream

    archive = Archive()
    try:
        alexandria_epub_meta.read_xml_metadata(archive, "content.opf")
    except ValueError as exc:
        assert "too large" in str(exc).lower(), exc
    else:
        raise AssertionError("limit+1 XML bytes were accepted with understated file_size")
    assert archive.opened == [archive.info], archive.opened
    assert archive.stream.entered and archive.stream.exited
    assert archive.stream.requests == [limit + 1], archive.stream.requests


def non_ocf_compression():
    unsupported = getattr(zipfile, "ZIP_ZSTANDARD", 93)

    class Info:
        file_size = 1
        compress_type = unsupported

    class Archive:
        def getinfo(self, member):
            return Info()

        def read(self, *args, **kwargs):
            return b"<container/>"

        def open(self, *args, **kwargs):
            raise AssertionError("unsupported compression member was opened")

    try:
        alexandria_epub_meta.read_xml_metadata(Archive(), "META-INF/container.xml")
    except ValueError as exc:
        assert "compression" in str(exc).lower(), exc
    else:
        raise AssertionError("non-OCF compression method was accepted")

    path = root / "unsupported-compression.epub"
    artifact = root / "unsupported-compression.json"
    make_epub(path, package("<dc:title>Must Not Replace</dc:title>"))
    artifact.write_text('{"sentinel": true}\n', encoding="utf-8")
    original_getinfo = zipfile.ZipFile.getinfo

    def unsupported_getinfo(archive, name):
        info = original_getinfo(archive, name)
        info.compress_type = unsupported
        return info

    with mock.patch.object(zipfile.ZipFile, "getinfo", unsupported_getinfo):
        with contextlib.redirect_stderr(io.StringIO()):
            status = alexandria_epub_meta.main(
                ["alexandria_epub_meta.py", str(path), str(artifact)]
            )
    assert status == 1, status
    assert json.loads(artifact.read_text(encoding="utf-8")) == {"sentinel": True}


cases = {
    "snapshot": snapshot_consistency,
    "zip-bomb": zip_bomb_guard,
    "quoted-id": quoted_unique_identifier,
    "isbn-prefix": isbn13_book_prefix,
    "epub3-onix": epub3_identifier_type,
    "decompressor": decompressor_failures,
    "bounded-xml-read": bounded_xml_read,
    "non-ocf-compression": non_ocf_compression,
}
if selected == "all":
    for case in cases.values():
        case()
else:
    cases[selected]()
PY
