![alexandria banner](https://raw.githubusercontent.com/machine-made/alexandria/main/assets/readme-banner-v3.jpg)

# Alexandria

A local research library for AI agents. Books and papers become Markdown, are filed under a
citation key, and are searched over MCP. Every search hit carries a `bib_key`, so a claim can be
cited or shown to have no source.

Status: in preparation. This repository is the code half of a split. The library data (works,
vectors) lives elsewhere and is read through a path you configure.

## Parts

- `pipeline/alexandria_index.py`: SQLite FTS5 index and float32 cosine vector search over a
  memory-mapped sidecar, fused with reciprocal rank fusion.
- `pipeline/alexandria_mcp.py`: stdio MCP server with four tools: `search`, `get_work`,
  `get_chunk`, `bib`. See `docs/MCP_ALEXANDRIA.md`.
- Citation keys, BibTeX, metadata, duplicate detection and ingest: the other `pipeline/alexandria_*.py`.
- `docs/adr/`: decisions behind the design.

## Tests

```
bash pipeline/tests/run_all.sh
```

Run from the repository root with uv installed. The runner installs locked dependencies
and runs every shell test with synthetic fixtures. Optional LaTeX compile checks print
a skip reason when biber or pdflatex is missing.

## Licence

Apache License 2.0. See `LICENSE` and `NOTICE`.
