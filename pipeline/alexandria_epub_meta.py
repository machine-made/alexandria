#!/usr/bin/env python3
"""Extract OPF/Dublin Core metadata into a durable epub_meta.json artifact."""
from __future__ import annotations

import hashlib
import json
import lzma
import os
import re
import sys
import tempfile
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
DC_NS = "http://purl.org/dc/elements/1.1/"
_MAX_XML_METADATA_SIZE = 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_handle(handle: object) -> str:
    digest = hashlib.sha256()
    handle.seek(0)
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
    handle.seek(0)
    return digest.hexdigest()


def read_xml_metadata(archive: zipfile.ZipFile, member: str) -> bytes:
    info = archive.getinfo(member)
    if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
        raise ValueError(f"EPUB XML metadata member uses unsupported compression: {member}")
    if info.file_size > _MAX_XML_METADATA_SIZE:
        raise ValueError(f"EPUB XML metadata member is too large: {member}")
    with archive.open(info) as source:
        data = source.read(_MAX_XML_METADATA_SIZE + 1)
    if len(data) > _MAX_XML_METADATA_SIZE:
        raise ValueError(f"EPUB XML metadata member is too large: {member}")
    return data


def text_values(metadata: ElementTree.Element, name: str) -> list[str]:
    values = []
    for element in metadata.findall(f"{{{DC_NS}}}{name}"):
        value = " ".join("".join(element.itertext()).split())
        if value:
            values.append(value)
    return values


def normalize_isbn(value: str, scheme: str = "", identifier_type: str = "") -> str:
    prefix = re.match(r"(?i)^\s*(?:urn:isbn:|isbn(?:-1[03])?\s*:?\s*)", value)
    if scheme.casefold() != "isbn" and prefix is None and identifier_type not in {"02", "15"}:
        return ""
    candidate = re.sub(r"[^0-9Xx]", "", value[prefix.end() :] if prefix else value).upper()
    if re.fullmatch(r"\d{9}[\dX]", candidate):
        if identifier_type == "15":
            return ""
        total = sum((10 - index) * (10 if digit == "X" else int(digit)) for index, digit in enumerate(candidate))
        return candidate if total % 11 == 0 else ""
    if re.fullmatch(r"\d{13}", candidate):
        if identifier_type == "02" or not candidate.startswith(("978", "979")):
            return ""
        total = sum(int(digit) * (1 if index % 2 == 0 else 3) for index, digit in enumerate(candidate))
        return candidate if total % 10 == 0 else ""
    return ""


def isbn_from_identifier(element: ElementTree.Element, identifier_type: str = "") -> str:
    value = " ".join("".join(element.itertext()).split())
    scheme = next(
        (attribute for name, attribute in element.attrib.items() if name.rsplit("}", 1)[-1].casefold() == "scheme"),
        "",
    )
    return normalize_isbn(value, scheme, identifier_type)


def onix_identifier_types(metadata: ElementTree.Element) -> dict[str, str]:
    identifier_types = {}
    for element in metadata.findall("{*}meta"):
        refines = element.get("refines", "")
        if (
            refines.startswith("#")
            and element.get("property", "").casefold() == "identifier-type"
            and element.get("scheme", "").casefold() == "onix:codelist5"
        ):
            value = " ".join("".join(element.itertext()).split())
            if value in {"02", "15"}:
                identifier_types[refines[1:]] = value
    return identifier_types


def extract_epub_metadata(epub_path: Path) -> dict[str, object]:
    epub_path = epub_path.resolve()
    with epub_path.open("rb") as source:
        source_stat = os.fstat(source.fileno())
        with zipfile.ZipFile(source) as archive:
            container = ElementTree.fromstring(read_xml_metadata(archive, "META-INF/container.xml"))
            rootfile = container.find(f".//{{{CONTAINER_NS}}}rootfile")
            if rootfile is None or not rootfile.get("full-path"):
                raise ValueError("EPUB container has no OPF rootfile")
            opf_path = rootfile.get("full-path", "")
            if PurePosixPath(opf_path).is_absolute() or ".." in PurePosixPath(opf_path).parts:
                raise ValueError("EPUB container has an unsafe OPF path")
            package = ElementTree.fromstring(read_xml_metadata(archive, opf_path))
        source_sha256 = sha256_handle(source)

    metadata = package.find("{*}metadata")
    if metadata is None:
        raise ValueError("EPUB OPF has no metadata element")
    titles = text_values(metadata, "title")
    creators = text_values(metadata, "creator")
    dates = [
        (element, " ".join("".join(element.itertext()).split()))
        for element in metadata.findall(f"{{{DC_NS}}}date")
    ]
    dates = [(element, value) for element, value in dates if value]
    publication_date = next(
        (
            value
            for element, value in dates
            if any(
                name.rsplit("}", 1)[-1] == "event" and attribute.casefold() in {"publication", "published"}
                for name, attribute in element.attrib.items()
            )
        ),
        "",
    )
    selected_date = publication_date or (dates[0][1] if dates else "")
    identifiers = metadata.findall(f"{{{DC_NS}}}identifier")
    identifier_types = onix_identifier_types(metadata)
    unique_id = package.get("unique-identifier", "")
    selected_identifier = next((element for element in identifiers if element.get("id") == unique_id), None)
    isbn = (
        isbn_from_identifier(selected_identifier, identifier_types.get(selected_identifier.get("id", ""), ""))
        if selected_identifier is not None
        else ""
    )
    if not isbn:
        isbn = next(
            (
                value
                for element in identifiers
                if (
                    value := isbn_from_identifier(element, identifier_types.get(element.get("id", ""), ""))
                )
            ),
            "",
        )

    data: dict[str, object] = {
        "schema_version": 1,
        "extractor": "alexandria_epub_meta.py",
        "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": {
            "name": epub_path.name,
            "size": source_stat.st_size,
            "sha256": source_sha256,
        },
        "opf_path": opf_path,
    }
    first_values = {
        "title": titles[0] if titles else "",
        "date": selected_date,
        "publisher": (text_values(metadata, "publisher") or [""])[0],
        "language": (text_values(metadata, "language") or [""])[0],
        "isbn": isbn,
    }
    if creators:
        data.update({"creators": creators, "authors": creators, "author": "; ".join(creators)})
    for key, value in first_values.items():
        if value:
            data[key] = value
    if selected_date:
        match = re.search(r"\b((?:18|19|20|21)\d{2})\b", selected_date)
        if match:
            data["year"] = match.group(1)
    return data


def write_epub_metadata(epub_path: Path, output_path: Path | None = None) -> Path:
    output_path = output_path or epub_path.with_name("epub_meta.json")
    data = extract_epub_metadata(epub_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{output_path.name}.", dir=output_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, output_path)
        directory_fd = os.open(output_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return output_path


def main(argv: list[str]) -> int:
    if len(argv) not in {2, 3}:
        print("Usage: alexandria_epub_meta.py <input.epub> [epub_meta.json]", file=sys.stderr)
        return 2
    epub_path = Path(argv[1])
    if not epub_path.is_file():
        print(f"ERROR: EPUB not found: {epub_path}", file=sys.stderr)
        return 1
    try:
        written = write_epub_metadata(epub_path, Path(argv[2]) if len(argv) == 3 else None)
    except (
        OSError,
        ValueError,
        zipfile.BadZipFile,
        ElementTree.ParseError,
        KeyError,
        NotImplementedError,
        RuntimeError,
        zlib.error,
        lzma.LZMAError,
    ) as exc:
        print(f"ERROR: cannot extract EPUB metadata: {exc}", file=sys.stderr)
        return 1
    print(written)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
