#!/usr/bin/env python3
"""Audited re-key of a filed Alexandria work (SHA identity stable).

Transaction: backup → amend_key → move artifacts → re-derive year → regen bibs
→ rebuild index → validate. On any failure, restore from the pre-mutation tar.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any

from alexandria_bibtex import bibtex_entry, rebuild_root_bibliography
from alexandria_key_registry import KeyRegistry
from alexandria_metadata_common import _apply_year_decision, load_json

DEFAULT_BACKUP = Path(".sc/qcn-rekey.backup.tgz")


def _pipeline_python() -> str:
    # Prefer the library venv when present; fall back to the running interpreter.
    here = Path(__file__).resolve().parent
    root = here.parent
    venv_py = root / ".venv" / "bin" / "python"
    if venv_py.is_file():
        return str(venv_py)
    return sys.executable


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _collect_backup_members(
    library_root: Path,
    category: str,
    old_key: str,
) -> list[Path]:
    """Paths to archive relative to library_root (must exist)."""
    candidates = [
        library_root / "key_registry.json",
        library_root / "alexandria.bib",
        library_root / category / f"{old_key}.md",
        library_root / category / f"{old_key}.bib",
        library_root / category / old_key,
    ]
    return [path for path in candidates if path.exists()]


def create_backup(
    library_root: Path,
    category: str,
    old_key: str,
    backup_path: Path,
) -> Path:
    members = _collect_backup_members(library_root, category, old_key)
    if not members:
        raise RuntimeError("create_backup: nothing to back up (old key paths missing?)")
    backup_path = backup_path.resolve()
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    if backup_path.exists():
        backup_path.unlink()
    library_root = library_root.resolve()
    with tarfile.open(backup_path, "w:gz") as tar:
        for path in members:
            arcname = path.resolve().relative_to(library_root)
            tar.add(path, arcname=str(arcname))
    return backup_path


def restore_backup(
    library_root: Path,
    category: str,
    old_key: str,
    new_key: str,
    backup_path: Path,
) -> None:
    """Remove post-mutation new-key paths, then extract the pre-mutation tar."""
    library_root = library_root.resolve()
    for path in (
        library_root / category / f"{new_key}.md",
        library_root / category / f"{new_key}.bib",
        library_root / category / new_key,
    ):
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    with tarfile.open(backup_path, "r:gz") as tar:
        # filter='data' is the safe default on 3.14+; older Pythons lack it.
        try:
            tar.extractall(path=library_root, filter="data")
        except TypeError:
            tar.extractall(path=library_root)


def _move_work_artifacts(library_root: Path, category: str, old_key: str, new_key: str) -> None:
    cat = library_root / category
    for suffix in (".md", ".bib"):
        src = cat / f"{old_key}{suffix}"
        dst = cat / f"{new_key}{suffix}"
        if not src.exists():
            raise RuntimeError(f"missing sidecar: {src}")
        if dst.exists():
            raise RuntimeError(f"destination already exists: {dst}")
        src.rename(dst)

    src_dir = cat / old_key
    dst_dir = cat / new_key
    if not src_dir.is_dir():
        raise RuntimeError(f"missing work dir: {src_dir}")
    if dst_dir.exists():
        raise RuntimeError(f"destination work dir already exists: {dst_dir}")
    src_dir.rename(dst_dir)

    # Rename filed source copy inside the work dir (e.g. wang2029.pdf → wang2026.pdf)
    for path in list(dst_dir.iterdir()):
        if path.is_file() and path.stem == old_key:
            new_path = path.with_name(f"{new_key}{path.suffix}")
            if new_path.exists():
                raise RuntimeError(f"filed source destination exists: {new_path}")
            path.rename(new_path)


def _rewrite_markdown_image_paths(md_path: Path, old_key: str, new_key: str) -> None:
    """Rewrite relative image links that embed the work-dir key prefix."""
    text = md_path.read_text(encoding="utf-8")
    # Marker embeds images as ![](old_key/_page_….jpeg) relative to category dir.
    old_prefix = f"{old_key}/"
    new_prefix = f"{new_key}/"
    if old_prefix not in text:
        return
    md_path.write_text(text.replace(old_prefix, new_prefix), encoding="utf-8")


def _rewrite_metadata(work_dir: Path, old_key: str, new_key: str) -> dict[str, Any]:
    meta_path = work_dir / "metadata.json"
    metadata = load_json(meta_path)
    if not isinstance(metadata, dict):
        raise RuntimeError(f"invalid metadata.json: {meta_path}")

    # Re-derive year from existing evidence (DOI path, etc.) — no hand-set year.
    _apply_year_decision(metadata)

    # Update filed_name on source_files when it still carries the old key.
    source_files = metadata.get("source_files")
    if isinstance(source_files, list):
        for row in source_files:
            if not isinstance(row, dict):
                continue
            filed = str(row.get("filed_name") or "")
            if filed.startswith(old_key + ".") or filed == old_key:
                suffix = Path(filed).suffix if "." in filed else ""
                row["filed_name"] = f"{new_key}{suffix}" if suffix else new_key

    # Optional key-like fields if present (do not touch source_stem — chunk ids).
    for field in ("key", "bib_key", "citation_key"):
        if field in metadata and str(metadata.get(field) or "") == old_key:
            metadata[field] = new_key

    _write_json(meta_path, metadata)
    return metadata


def _regen_sidecar_bib(library_root: Path, category: str, new_key: str, metadata: dict[str, Any]) -> None:
    bib_path = library_root / category / f"{new_key}.bib"
    bib_path.write_text(bibtex_entry(new_key, metadata), encoding="utf-8")


def _run_subprocess(cmd: list[str], *, cwd: Path) -> None:
    result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(cmd)}\n{detail}")


def rekey_work(
    library_root: Path,
    *,
    sha: str,
    old_key: str,
    new_key: str,
    category: str,
    reason: str,
    backup_path: Path,
    fields: list[str] | None = None,
) -> None:
    library_root = library_root.resolve()
    sha = sha.strip()
    old_key = old_key.strip()
    new_key = new_key.strip()
    category = category.strip()
    reason = reason.strip()

    if not sha or not old_key or not new_key or not category or not reason:
        raise RuntimeError("rekey_work: sha, old_key, new_key, category, and reason are required")
    if old_key == new_key:
        raise RuntimeError("rekey_work: old_key and new_key are identical")

    # Idempotence: already applied?
    new_md = library_root / category / f"{new_key}.md"
    old_md = library_root / category / f"{old_key}.md"
    if new_md.is_file() and not old_md.exists():
        registry = KeyRegistry.load(library_root)
        entry = registry.entries.get(sha)
        if entry and entry.get("key") == new_key:
            print(f"already re-keyed: {old_key} → {new_key} (sha {sha[:12]}…)", file=sys.stderr)
            return

    if not old_md.is_file():
        raise RuntimeError(f"rekey_work: missing old work markdown: {old_md}")

    backup = create_backup(library_root, category, old_key, backup_path)
    print(f"backup: {backup}", file=sys.stderr)

    try:
        registry = KeyRegistry.load(library_root)
        entry = registry.entries.get(sha)
        if entry is None:
            raise RuntimeError(f"rekey_work: sha not in registry: {sha}")
        if entry.get("key") != old_key:
            raise RuntimeError(
                f"rekey_work: registry key is {entry.get('key')!r}, expected old_key {old_key!r}"
            )

        registry.amend_key(
            sha,
            new_key,
            category,
            reason,
            fields=fields or ["year", "key"],
            persist=True,
        )
        print(f"registry: amend_key {old_key} → {new_key}", file=sys.stderr)

        _move_work_artifacts(library_root, category, old_key, new_key)
        print(f"fs: moved {category}/{old_key}* → {new_key}*", file=sys.stderr)

        md_path = library_root / category / f"{new_key}.md"
        _rewrite_markdown_image_paths(md_path, old_key, new_key)

        work_dir = library_root / category / new_key
        metadata = _rewrite_metadata(work_dir, old_key, new_key)
        print(
            f"metadata: year={metadata.get('year')} "
            f"source={((metadata.get('metadata_field_sources') or {}).get('year'))}",
            file=sys.stderr,
        )

        _regen_sidecar_bib(library_root, category, new_key, metadata)
        rebuild_root_bibliography(library_root)
        print("bib: sidecar + root rebuilt", file=sys.stderr)

        py = _pipeline_python()
        pipeline = Path(__file__).resolve().parent
        _run_subprocess(
            [py, str(pipeline / "alexandria_index.py"), "rebuild", "--library", str(library_root)],
            cwd=library_root,
        )
        print("index: rebuilt", file=sys.stderr)

        _run_subprocess(
            [py, str(pipeline / "alexandria_validate_work.py"), "--library", str(library_root)],
            cwd=library_root,
        )
        print("validate_work: ok", file=sys.stderr)

    except Exception:
        print("ERROR: rolling back from backup…", file=sys.stderr)
        try:
            restore_backup(library_root, category, old_key, new_key, backup)
            print("rollback: restored library from backup", file=sys.stderr)
        except Exception as restore_exc:  # noqa: BLE001 — surface both failures
            print(f"rollback FAILED: {restore_exc}", file=sys.stderr)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, default=Path("."), help="library root")
    parser.add_argument("--sha", required=True, help="source_sha256 of the work")
    parser.add_argument("--old-key", required=True, help="current citation key")
    parser.add_argument("--new-key", required=True, help="corrected citation key")
    parser.add_argument("--category", required=True, help="category folder (e.g. propulsion)")
    parser.add_argument("--reason", required=True, help="human-readable correction reason")
    parser.add_argument(
        "--backup",
        type=Path,
        default=None,
        help=f"backup tar path (default: <library>/{DEFAULT_BACKUP})",
    )
    parser.add_argument(
        "--field",
        action="append",
        dest="fields",
        default=None,
        help="field name recorded in the correction (repeatable); default year+key",
    )
    args = parser.parse_args()
    library = args.library.resolve()
    backup = args.backup if args.backup is not None else library / DEFAULT_BACKUP
    rekey_work(
        library,
        sha=args.sha,
        old_key=args.old_key,
        new_key=args.new_key,
        category=args.category,
        reason=args.reason,
        backup_path=backup,
        fields=args.fields,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
