# ADR 0006 — mcp-alexandria: official MCP SDK at the agent-facing edge

Status: Superseded by ADR 0009 (the server no longer uses the SDK). Was: Accepted (2026-07-16)
Qualifies: ADR 0005 (dependency-free search index)

## Context

ADR 0005 built the search index with zero new dependencies — it rejected `sqlite-vec`
and hand-rolled brute-force cosine, reasoning that the corpus is small and the index is
derived, rebuildable state. `pipeline/requirements.txt` claimed "the core pipeline is
stdlib-only".

VISION's second consumer needs a local MCP server so agents can search the library and
cite it. The obvious move was to apply ADR 0005's precedent again: hand-roll MCP as
newline-delimited JSON-RPC over stdio (~150–200 LOC of `initialize` / `tools/list` /
`tools/call`) and keep the dependency count at zero.

The official `mcp` SDK resolves to **29 packages** — including `starlette`, `uvicorn`,
`sse-starlette`, `python-multipart` (HTTP/SSE transport) and `cryptography`, `pyjwt`
(OAuth) — none of which a local stdio server ever executes. There is no slim stdio-only
extra; the SDK is a monolith. That is a large supply-chain surface for dead transport
code in a repo that had two packages.

## Decision

**Use the official `mcp` SDK, scoped to the agent-facing edge.** The maintainer approved the
dependency explicitly.

ADR 0005's precedent does **not** transfer, and the reason is the asymmetry between the
two choices:

| ADR 0005 (`sqlite-vec`) | This ADR (MCP) |
|---|---|
| Closed math — cosine is stable for years | Interop protocol — spec and clients evolve independently |
| Internal to the index | The product boundary, facing third-party clients |
| Failure = wrong ranking, costs a rebuild | Failure = agents silently lose the library |

Hand-rolling would move ownership of protocol correctness — version negotiation,
handshake quirks, framing, error shapes — onto this repo permanently, in exchange for
avoiding a dependency at the one place where the failure mode is silent and the blast
radius is VISION's whole second consumer. Zero-deps on cosine was discipline; zero-deps
on MCP would be pride.

**Scoping keeps ADR 0005's spirit intact:**
- `pipeline/requirements-mcp.txt` declares `mcp` alone — the agent-facing edge.
- The index, filing, and metadata paths stay free of it; `alexandria_index.py` is
  unmodified and imports nothing from the server.
- The server is a **read-only consumer**: it opens the index `file:…?mode=ro` and has
  no write path. Derived state stays derived.

**numpy was already an undeclared dependency.** `alexandria_index.py` imports it, but no
requirements file listed it; it worked only because system `python3` happens to ship it,
and the project venv (which lacked it) could not run the index at all. ADR 0005's
"dependency-free" wording described the *absence of new* dependencies, not the absence of
numpy. It is now declared in `pipeline/requirements.txt`. This is not a new dependency —
it is writing down one that already existed.

## Consequences

- Agents get search + retrieval with the citation key attached; `\cite{bib_key}` works
  off a search result directly. VISION north star #2 is reachable.
- The server's interpreter is `.venv/bin/python` — the only one carrying both `mcp` and
  `numpy`. Documented in `docs/MCP_ALEXANDRIA.md` for both Claude Code and Codex.
- The dependency tree grew from 2 to ~31 packages, confined to the MCP layer. Most of it
  is transport machinery that never runs under stdio. Accepted cost, revisitable if the
  SDK ever ships a slim extra.
- **The citation key is enforced, not assumed.** A single `_require_bib_key` guard
  (non-empty after `strip()`) gates every return path carrying a `bib_key`; blank or
  whitespace-only keys fail closed. This was NOT free — the first implementation checked
  truthiness in two of four tools and leaked `'   '` from all four (`\cite{   }` is not a
  citation). One shared predicate, not four checks, is the shape that holds — the same
  lesson an earlier change taught about duplicated surfaces drifting silently.
- Swapping the SDK for something slimmer later stays a contained change behind the same
  4-tool surface.
