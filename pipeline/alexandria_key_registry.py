#!/usr/bin/env python3
"""Append-only Alexandria citation-key registry.

The registry is keyed by source SHA-256.  Once a source hash has a citation key,
future filing must reuse that key instead of allocating another suffix.

Audited key corrections go through ``amend_key`` only — the sole sanctioned path
that remaps an established SHA→key binding, with provenance and tombstones.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from alexandria_metadata_common import unique_key

REGISTRY_FILENAME = "key_registry.json"
REGISTRY_VERSION = 1
ENTRY_FIELDS = ("key", "category", "base_key", "first_filed_utc")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_sha_values(sha_values: Iterable[object] | object) -> list[str]:
    if sha_values is None:
        values: Iterable[object] = []
    elif isinstance(sha_values, dict):
        values = sha_values.values()
    elif isinstance(sha_values, (str, bytes)):
        values = [sha_values.decode() if isinstance(sha_values, bytes) else sha_values]
    else:
        values = sha_values  # type: ignore[assignment]
    return sorted({str(value).strip() for value in values if str(value).strip()})


def _entry_identity(entry: dict[str, Any]) -> tuple[str, str]:
    return str(entry["category"]), str(entry["key"])


def _format_identity(identity: tuple[str, str]) -> str:
    return f"{identity[0]}/{identity[1]}"


def _normalize_tombstones(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return sorted({str(item).strip() for item in raw if str(item).strip()})
    if isinstance(raw, dict):
        return sorted({str(key).strip() for key in raw if str(key).strip()})
    raise ValueError("invalid key registry tombstones")


class KeyRegistry:
    """Loaded append-only key registry for one Alexandria library root."""

    def __init__(
        self,
        path: Path,
        entries: dict[str, dict[str, Any]] | None = None,
        tombstones: list[str] | None = None,
    ) -> None:
        self.path = path
        self.entries: dict[str, dict[str, Any]] = entries or {}
        self.tombstones: list[str] = list(tombstones or [])

    @classmethod
    def load(cls, library_root: Path) -> "KeyRegistry":
        path = Path(library_root) / REGISTRY_FILENAME
        if not path.is_file():
            return cls(path, {})
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid key registry JSON: {path}") from exc
        if not isinstance(data, dict) or data.get("version") != REGISTRY_VERSION:
            raise ValueError(f"unsupported key registry schema: {path}")
        raw_entries = data.get("entries")
        if not isinstance(raw_entries, dict):
            raise ValueError(f"invalid key registry entries: {path}")
        entries: dict[str, dict[str, Any]] = {}
        for raw_sha, raw_entry in raw_entries.items():
            sha = str(raw_sha).strip()
            if not sha:
                raise ValueError(f"invalid empty source_sha256 in key registry: {path}")
            if not isinstance(raw_entry, dict):
                raise ValueError(f"invalid key registry entry for {sha}: {path}")
            entry: dict[str, Any] = {}
            for field in ENTRY_FIELDS:
                value = raw_entry.get(field)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"invalid key registry field {field!r} for {sha}: {path}")
                entry[field] = value.strip()
            raw_corr = raw_entry.get("corrections")
            if raw_corr is not None:
                if not isinstance(raw_corr, list):
                    raise ValueError(f"invalid corrections list for {sha}: {path}")
                entry["corrections"] = raw_corr
            entries[sha] = entry
        try:
            tombstones = _normalize_tombstones(data.get("tombstones") or data.get("retired_keys"))
        except ValueError as exc:
            raise ValueError(f"{exc}: {path}") from exc
        return cls(path, entries, tombstones=tombstones)

    def data(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "version": REGISTRY_VERSION,
            "entries": {sha: self.entries[sha] for sha in sorted(self.entries)},
        }
        if self.tombstones:
            payload["tombstones"] = sorted(set(self.tombstones))
        return payload

    def persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.tmp")
        tmp.write_text(json.dumps(self.data(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    def reserved_keys(self) -> set[str]:
        keys = {str(entry["key"]) for entry in self.entries.values()}
        keys.update(self.tombstones)
        for entry in self.entries.values():
            for correction in entry.get("corrections") or []:
                if not isinstance(correction, dict):
                    continue
                from_key = str(correction.get("from_key") or "").strip()
                if from_key:
                    keys.add(from_key)
        return keys

    def lookup_by_sha(self, sha_values: Iterable[object]) -> tuple[str, str] | None:
        matches: list[tuple[str, dict[str, Any]]] = []
        for sha in clean_sha_values(sha_values):
            entry = self.entries.get(sha)
            if entry is not None:
                matches.append((sha, entry))
        if not matches:
            return None
        identities = {_entry_identity(entry) for _, entry in matches}
        if len(identities) > 1:
            detail = ", ".join(f"{sha}->{entry['category']}/{entry['key']}" for sha, entry in matches)
            raise RuntimeError(f"conflicting registry entries for source hashes: {detail}")
        return next(iter(identities))

    def record_existing(
        self,
        sha_values: Iterable[object],
        category: str,
        key: str,
        base_key: str,
        *,
        persist: bool = True,
    ) -> bool:
        """Append sha_values to an already chosen key, refusing key mutation."""
        shas = clean_sha_values(sha_values)
        if not shas:
            return False
        target = (str(category).strip(), str(key).strip())
        if not target[0] or not target[1]:
            raise RuntimeError("cannot record registry entry with empty category or key")
        stamp = utc_now()
        changed = False
        for sha in shas:
            existing = self.entries.get(sha)
            if existing is not None:
                existing_identity = _entry_identity(existing)
                if existing_identity != target:
                    raise RuntimeError(
                        "registry immutability violation: "
                        f"{sha} already maps to {_format_identity(existing_identity)}; "
                        f"refusing to remap to {_format_identity(target)}"
                    )
                continue
            self.entries[sha] = {
                "key": target[1],
                "category": target[0],
                "base_key": str(base_key).strip() or target[1],
                "first_filed_utc": stamp,
            }
            changed = True
        if changed and persist:
            self.persist()
        return changed

    def amend_key(
        self,
        sha: str,
        new_key: str,
        new_category: str,
        reason: str,
        *,
        fields: Sequence[str] | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        """Audited correction of an established key — the only sanctioned remap.

        Keeps ``first_filed_utc`` stable, appends a corrections record, and
        tombstones the prior key so it can never be reused.
        """
        sha = str(sha).strip()
        new_key = str(new_key).strip()
        new_category = str(new_category).strip()
        reason = str(reason).strip()
        if not sha or sha not in self.entries:
            raise RuntimeError(f"amend_key: unknown sha {sha!r}")
        if not new_key or not new_category:
            raise RuntimeError("amend_key: empty new_key or new_category")
        if not reason:
            raise RuntimeError("amend_key: reason required")

        for other_sha, other in self.entries.items():
            if other_sha == sha:
                continue
            if str(other.get("key") or "") == new_key:
                raise RuntimeError(
                    f"amend_key: target collision — {new_key!r} already allocated to {other_sha}"
                )

        entry = self.entries[sha]
        from_key = str(entry["key"])
        from_category = str(entry["category"])
        # first_filed_utc must never move
        first_filed = entry["first_filed_utc"]

        entry["key"] = new_key
        entry["base_key"] = new_key
        entry["category"] = new_category
        entry["first_filed_utc"] = first_filed

        correction: dict[str, Any] = {
            "from_key": from_key,
            "to_key": new_key,
            "from_category": from_category,
            "to_category": new_category,
            "reason": reason,
            "utc": utc_now(),
            "fields": list(fields) if fields is not None else [],
            "tool": "amend_key",
        }
        corrections = entry.get("corrections")
        if not isinstance(corrections, list):
            corrections = []
            entry["corrections"] = corrections
        corrections.append(correction)

        if from_key and from_key != new_key and from_key not in self.tombstones:
            self.tombstones.append(from_key)
            self.tombstones = sorted(set(self.tombstones))

        if persist:
            self.persist()
        return entry

    def retract_key(
        self,
        sha: str,
        reason: str,
        *,
        persist: bool = True,
    ) -> dict[str, Any]:
        """Unbind a SHA and tombstone its key — for non-recoverable identity holds.

        The work is removed from the live registry (no SHA→key binding) and the
        retired key is tombstoned so it can never be re-allocated. Provenance for
        the hold itself lives with the ``_hold/<stem>/`` package; this method
        only records the registry half (tombstone + unbind). Does not rewrite
        schema: same ``entries`` + ``tombstones`` shape as amend_key.
        """
        sha = str(sha).strip()
        reason = str(reason).strip()
        if not sha or sha not in self.entries:
            raise RuntimeError(f"retract_key: unknown sha {sha!r}")
        if not reason:
            raise RuntimeError("retract_key: reason required")

        entry = dict(self.entries.pop(sha))
        from_key = str(entry.get("key") or "").strip()
        from_category = str(entry.get("category") or "").strip()
        if from_key and from_key not in self.tombstones:
            self.tombstones.append(from_key)
            self.tombstones = sorted(set(self.tombstones))

        # Surface an audit record for the caller (hold_reason / logs); not stored
        # on a live entry because the SHA is unbound.
        entry["retract"] = {
            "from_key": from_key,
            "from_category": from_category,
            "reason": reason,
            "utc": utc_now(),
            "tool": "retract_key",
        }

        if persist:
            self.persist()
        return entry

    def reserve_key(
        self,
        sha_values: Iterable[object],
        category: str,
        base_key: str,
        *,
        category_dir: Path,
        reserved_keys: Iterable[str] | None = None,
        persist: bool = True,
    ) -> tuple[str, str]:
        """Return the immutable key for sha_values, allocating one if needed."""
        shas = clean_sha_values(sha_values)
        if not shas:
            raise RuntimeError("cannot reserve a registry key without source_sha256")
        existing = self.lookup_by_sha(shas)
        if existing is not None:
            self.record_existing(shas, existing[0], existing[1], base_key, persist=persist)
            return existing
        reserved = {str(key) for key in (reserved_keys or []) if str(key)} | self.reserved_keys()
        key = unique_key(category_dir, str(base_key), reserved=reserved)
        self.record_existing(shas, category, key, base_key, persist=persist)
        return str(category), key

    def reserve_key_batch(
        self,
        requests: Sequence[dict[str, object]],
        *,
        category_dirs: dict[str, Path],
        reserved_keys: Iterable[str] | None = None,
        persist: bool = True,
    ) -> list[tuple[str, str]]:
        """Reserve many new keys in source-hash order, independent of input order.

        Each request must contain ``source_sha256``, ``category``, and ``base_key``.
        This helper is intentionally small, but it gives tests and future batch
        callers an order-independent allocator for a known set of colliding works.
        """
        results: list[tuple[str, str] | None] = [None] * len(requests)
        planned_by_sha: dict[str, tuple[str, str]] = {}
        pending: list[tuple[str, int, list[str], str, str]] = []
        changed = False

        for index, request in enumerate(requests):
            shas = clean_sha_values(request.get("source_sha256", []))
            if not shas:
                raise RuntimeError("cannot reserve a registry key without source_sha256")
            existing = self.lookup_by_sha(shas)
            if existing is not None:
                changed = self.record_existing(
                    shas, existing[0], existing[1], str(request.get("base_key") or ""), persist=False
                ) or changed
                for sha in shas:
                    planned_by_sha[sha] = existing
                results[index] = existing
                continue
            planned = next((planned_by_sha[sha] for sha in shas if sha in planned_by_sha), None)
            if planned is not None:
                changed = self.record_existing(
                    shas, planned[0], planned[1], str(request.get("base_key") or ""), persist=False
                ) or changed
                for sha in shas:
                    planned_by_sha[sha] = planned
                results[index] = planned
                continue
            category = str(request.get("category") or "").strip()
            base = str(request.get("base_key") or "").strip()
            if not category or not base:
                raise RuntimeError("cannot reserve a registry key without category and base_key")
            pending.append((min(shas), index, shas, category, base))

        reserved = {str(key) for key in (reserved_keys or []) if str(key)} | self.reserved_keys()
        for _, index, shas, category, base in sorted(pending, key=lambda row: (row[4], row[0], row[3], row[1])):
            planned = next((planned_by_sha[sha] for sha in shas if sha in planned_by_sha), None)
            if planned is None:
                category_dir = category_dirs.get(category)
                if category_dir is None:
                    raise RuntimeError(f"missing category_dir for category {category!r}")
                key = unique_key(category_dir, base, reserved=reserved)
                reserved.add(key)
                planned = (category, key)
            changed = self.record_existing(shas, planned[0], planned[1], base, persist=False) or changed
            for sha in shas:
                planned_by_sha[sha] = planned
            results[index] = planned

        if changed and persist:
            self.persist()
        resolved: list[tuple[str, str]] = []
        for result in results:
            if result is None:
                raise RuntimeError("internal key registry error: unresolved batch request")
            resolved.append(result)
        return resolved
