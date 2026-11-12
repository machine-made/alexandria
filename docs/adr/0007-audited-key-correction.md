# ADR 0007 — Audited key correction (amend_key): the sanctioned exception to key immutability

Status: Accepted (2026-07-16)
Bead: alexandria-qcn (Part B) · Impl: see `impl(...): alexandria-qcn Part B audited re-key`
Extends: ADR 0001 (citation key registry)

## Context

VISION holds keys immutable: "Once `<category>/<key>` is assigned it never silently
changes; the registry enforces sha→key stability." `KeyRegistry.record_existing` enforces
it in code — it RAISES `registry immutability violation` on any attempt to remap a SHA to a
different key.

But a derived key can be *wrong*. wang2029 was filed because year inference misread a
data-table value (2029 J/kg/K specific heat) as the publication year; the true year is 2026
(from the DOI). The source PDF — the work's identity — never changed; only a derived field
was wrong. The immutability guard correctly blocks the fix, which means the library needs a
*sanctioned* way to make the correction. Hand-editing `key_registry.json` to route around
the guard is exactly the "silent mutation" the principle forbids — "immutable" needs its
audited-correction half to be complete, not absent.

This will recur: three identity-plausibility misfilings (author, category, year) happened
in one week, and the bulk ingestion of ~85 works is imminent.

## Decision

Add **`KeyRegistry.amend_key(sha, new_key, new_category, reason, *, fields)`** — the ONLY
sanctioned path that remaps an established SHA→key binding. It:

- refuses an unknown SHA, an empty new key/category, and an empty reason;
- refuses a **target collision** (the new key already allocated to a different SHA);
- keeps `first_filed_utc` stable — the work's history is not rewritten as if it were always
  wang2026;
- appends a **correction record** to the entry: `{from_key, to_key, from_category,
  to_category, reason, utc, fields, tool}`;
- **tombstones the old key** (top-level `tombstones` list) so it can never be re-allocated;
  `reserved_keys()` unions the tombstones, so a future work with the same base gets a
  suffixed key, never the retired one.

Provenance lives **on the registry entry**, not in git, because `key_registry.json` is
gitignored (local-only) — `git blame` will never explain the change, so the
entry is the sole durable record.

A thin driver, **`alexandria_rekey.py`**, performs the whole correction as one transaction:
back up (work dir + sidecars + registry + root bib) → `amend_key` → move artifacts →
re-derive the year and rewrite `metadata.json` → regenerate the sidecar and root `.bib` →
rebuild the search index → validate. On any failure it restores from the pre-mutation
backup (removing the new-key paths first), so a half-applied correction cannot persist.

`record_existing`'s immutability guard is unchanged for every non-amended entry.

## Consequences

- The library gains a durable, auditable correction path for the class "derived identity was
  wrong; the SHA is right." Every future correction is the same operation, not another
  ad-hoc hand-edit — which matters most during bulk ingestion, when corrections cluster.
- The search index is rebuilt as part of the correction, so derived consumers (the MCP
  server) never serve the dead key. wang2029 → wang2026 is applied: search returns
  `wang2026`, `\cite{wang2026}` resolves, SHA `853068a7…` intact, song2023 untouched.
- Git is not the undo path (the filed data is untracked, the registry gitignored), so the
  driver's own backup/restore is the safety net.
- Known hardening deferred (filed as a follow-up, none affect the applied result): amend_key
  blocks a tombstoned key from auto-allocation but not from a deliberate future amend
  (resurrection); the rollback backup covers files+registry but not the rebuildable index
  DB; the driver assumes a same-category correction though amend_key itself supports a
  category move.
