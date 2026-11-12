# mcp-alexandria — agent access to the library

stdio MCP server over the Alexandria search index. **Read-only.** Every search
hit (and every successful `get_work` / `get_chunk` / `bib` payload) carries a
non-empty `bib_key` after strip; blank or whitespace-only keys fail closed as a
tool error. A claim without a usable `bib_key` did not come from the library.

## Runtime

| Piece | Value |
|-------|--------|
| Interpreter | `<library>/.venv/bin/python` (needs `numpy` only; the server speaks the MCP protocol itself) |
| Server | `pipeline/alexandria_mcp.py` |
| Library | `--library` or `$ALEXANDRIA_LIBRARY` (default: current directory) |
| Index DB | `$ALEXANDRIA_INDEX_DB` or `<library>/.sc/alexandria-qim/index.db` |

Build / refresh the index (library root):

```bash
.venv/bin/python pipeline/alexandria_index.py rebuild --library .
```

## Claude Code

Add to `.mcp.json` (or project MCP config):

```json
{
  "mcpServers": {
    "alexandria": {
      "command": "/path/to/alexandria/.venv/bin/python",
      "args": ["/path/to/alexandria/pipeline/alexandria_mcp.py"],
      "env": {
        "ALEXANDRIA_INDEX_DB": "/path/to/library/.sc/alexandria-qim/index.db"
      }
    }
  }
}
```

Adjust absolute paths if the library lives elsewhere.

## Codex CLI

```toml
# ~/.codex/config.toml  (or project config)
[mcp_servers.alexandria]
command = "/path/to/alexandria/.venv/bin/python"
args = ["/path/to/alexandria/pipeline/alexandria_mcp.py"]

[mcp_servers.alexandria.env]
ALEXANDRIA_INDEX_DB = "/path/to/library/.sc/alexandria-qim/index.db"
```

## Tools

| Tool | Args | Returns |
|------|------|---------|
| `search` | `query`, `limit?`, `mode?` ∈ {fts,vector,hybrid}, `verbose?` | ranked hits |
| `get_work` | `key`, `include_markdown?` | metadata, paths, BibTeX, optional markdown |
| `get_chunk` | `chunk_id` **or** `key`+`ordinal` | exact chunk text + citation fields |
| `bib` | `key` | BibTeX entry + `bib_path` |

Tool names are plain identifiers (`search`, not `alexandria.search`); the client
namespaces by server name.

### Search hit contract

Lean by default — an agent pays context for every field it gets back, and
identity/BibTeX already live on `get_work`/`bib`, not on a search hit. Every
hit carries exactly:

`bib_key`, `title`, `chunk_id`, `heading`, `text`, `score`

- `text` is the full chunk body — always present, never gated behind a flag.
- `heading` is the *last* element of the chunk's `heading_path` (its
  immediate section); `heading_path[0]` is the document title and would only
  duplicate `title`, so it's dropped. Empty/missing `heading_path` → `""`.
- `title` stays even though `bib_key` alone would identify the work, because
  `bib_key` can't distinguish `smith2020` from `smith2020a` in a multi-hit
  result — worth the ~44 chars against a body typically 1–2k chars.

Set `verbose: true` to get today's full card instead: `bib_key`, `key`,
`title`, `authors`, `year`, `category`, `path`, `chunk_id`, `snippet`,
`score`, `score_breakdown` (plus `doi`/`isbn`/`arxiv_id` when non-empty),
*in addition to* `text` and `heading`. This is a debugging tool for
inspecting ranking, not the common path.

- `score_breakdown`: `{ "channel": "fts"|"vector"|"fts+vector", "fts_score"?, "vector_score"? }`

Authors and identifiers are joined from the `works` table in the MCP layer
(`SearchHit` alone does not carry them). For identity, BibTeX, and the full
markdown body of a work, use `get_work` / `bib` — search hits intentionally
do not carry that.

### Boilerplate filter

`search` hides hits whose own leaf heading (the last element of
`heading_path` — the chunk's *own* section, never an ancestor) is front/back
matter: References, Bibliography, Works Cited, Cited References, Contents,
Table of Contents, Index, Copyright, Acknowledgements, List of
Figures/Tables, About the Author(s), Further Reading (an optional leading
section number like `E.9.` or `7.1`, or a roman-numeral page-number prefix,
is tolerated). It matches conservatively — only the chunk's own leaf
heading, never an ancestor — so a section that merely *lives under* an
"Appendix" or inside a book titled "... Index" is never touched.

**Deliberately excluded from the blocklist, by evidence not taste** (audited
index-wide against the real 58,941-chunk index — do not re-add without
re-running that audit):

- **Glossary** (17 chunks) — definitional content is exactly what an agent
  wants for a "what is X" query. Real indexed example: `**quantum
  mechanics:** a theory of matter based on the concept of elementary
  particles possessing wave properties...`.
- **Preface** (151 chunks) — routinely carries the author's thesis and
  framing in prose. Real indexed example (*Virtual Barrels*): substantive
  narrative on the financialization of oil trading, fully quotable.
- **Notes** (315 chunks) — genuinely mixed: some chunks are endnote/citation
  soup, but some are substantive (an *Optimization: 100 Examples* Notes
  chunk explains counterexamples and defines the first-order extremum
  condition). The operating test for this blocklist is "contains no claims
  of its own"; "Notes" fails that test in both directions, so it stays
  out — one wasted result slot beats silently hiding real scholarship.

The server over-fetches from the index before applying this filter, then
backfills, so `limit` still returns that many usable hits when enough real
ones exist. When something was dropped, the payload carries
`"filtered_boilerplate": <count>`; the key is absent when nothing was
filtered. That count is a diagnostic — junk chunks skipped while assembling
this result page, not a count of hits you would otherwise have seen. This
applies identically under `verbose: true`, and there is no flag to disable
it — `get_chunk` and `get_work(include_markdown=true)` still reach every
chunk in the library, so nothing becomes unreachable.

## Worked workflow

1. `search` with `query="transpiration cooling"`, `mode="fts"`.
2. Take `bib_key` from a hit (e.g. `xu2029`).
3. Optionally `get_work` / `bib` to confirm the same `bib_key` and copy BibTeX.
4. In the manuscript write `\cite{xu2029}` (or the key you received).

## Network-free smoke

Against the real index on this machine:

```bash
# rebuild if needed
.venv/bin/python pipeline/alexandria_index.py rebuild --library .

# stdio handshake + one search (no Ollama; FTS only)
export ALEXANDRIA_INDEX_DB="$PWD/.sc/alexandria-qim/index.db"
python3 - <<'PY'
import json, os, select, subprocess, sys, time

cmd = [".venv/bin/python", "pipeline/alexandria_mcp.py"]
env = os.environ.copy()
proc = subprocess.Popen(
    cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, env=env, bufsize=1,
)

def send(obj):
    proc.stdin.write(json.dumps(obj) + "\n")
    proc.stdin.flush()

def recv(timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r, _, _ = select.select([proc.stdout], [], [], 0.1)
        if r:
            line = proc.stdout.readline()
            if line.strip():
                return json.loads(line)
    raise SystemExit("timeout")

send({
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "smoke", "version": "0"},
    },
})
print("init", recv().get("result", {}).get("serverInfo"))
send({"jsonrpc": "2.0", "method": "notifications/initialized"})
send({
    "jsonrpc": "2.0", "id": 2, "method": "tools/call",
    "params": {
        "name": "search",
        "arguments": {"query": "transpiration cooling", "mode": "fts", "limit": 3},
    },
})
resp = recv()
result = resp["result"]
payload = result.get("structuredContent")
if payload is None:
    payload = json.loads(result["content"][0]["text"])
for hit in payload["results"]:
    print(hit["bib_key"], hit["score"], hit["text"][:60])
assert payload["results"] and payload["results"][0]["bib_key"] == "xu2029"
print("smoke OK: bib_key=xu2029")
proc.stdin.close()
proc.wait(timeout=3)
PY
```

Offline unit suite (fixture index, no network):

```bash
bash pipeline/tests/test_alexandria_mcp.sh
```

## Deps

- `pyproject.toml` — `numpy` is the only runtime dependency (`uv sync`); `pypdf` is the optional `pdf` extra
