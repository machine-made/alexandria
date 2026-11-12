# ADR 0008 — Key retraction (retract_key): unbinding a SHA for non-recoverable identity

Status: Accepted (2026-07-17)
Bead: alexandria-smq (step 4) · Impl: see `impl(...): remediate 6 bogus book keys`
Extends: ADR 0001 (citation key registry), ADR 0007 (amend_key)

## Context

ADR 0007 gave the registry a sanctioned way to *correct* a wrong key (amend_key:
remap SHA→new-key, tombstone the old key, keep provenance). But some works have no
recoverable identity at all — a book whose author was mis-extracted as a title
fragment and whose real author is not present in the parsed text. These must not
stay filed under a bogus key, but they also cannot be re-keyed to anything correct;
the right destination is `_hold/<stem>/` for later manual/ISBN resolution.

`record_existing` forbids silent SHA→key remaps (immutability), and hand-editing
`key_registry.json` is forbidden (ADR 0001/0007). There was no sanctioned way to
*remove* a filed binding.

## Decision

Add `KeyRegistry.retract_key(sha, reason)`: it pops the SHA→key entry from the live
registry (the work is no longer filed) and appends the retired key to the existing
`tombstones` list so it can never be re-allocated. It requires a non-empty reason
and raises on an unknown SHA. No schema change — it reuses the same `entries` +
`tombstones` shape as amend_key. The human-readable *why* lives with the
`_hold/<stem>/hold_reason.txt` package; the registry keeps only the tombstone
(machine guarantee that the key is dead).

## Consequences

- Retraction is auditable and irreversible-by-accident: the tombstone survives, so a
  future drop of the same book cannot silently reuse the retired key.
- The registry no longer binds the retracted SHA, so re-dropping the work routes it
  through normal filing again (and, once its identity is recoverable, it files fresh).
- Applied 2026-07-17 to editors2023, detonation2017, materials2016 (batch-1 books
  with title-fragment authors); their entries were never pushed, so this is a
  working-tree cleanup, not a rewrite of published history.
- Follow-up: retract_key ships without a dedicated unit test (verified via the
  remediation gate). A focused regression test is filed as a follow-up bead.
