# Library layout

Alexandria reads a library from a directory given by `--library` or `$ALEXANDRIA_LIBRARY`.

```
<library>/
  scriptorium.toml                 format_version, categories, active_embedder (optional)
  <category>/<key>.md              text of one work
  <category>/<key>.bib             BibTeX entry, key equal to the file name
  <category>/<key>/                metadata.json, chunks.jsonl, run_meta.json, source copies
  key_registry.json                content hash -> citation key (identity of record)
  alexandria.bib                   root bibliography
  vectors/<embedder-id>/
    embedder.json                  id, runtime_tag, dim, query_instruction, ...
    <category>/<key>.jsonl         one row per chunk: chunk_id, chunk_index, model, embedding_dim, embedding
```

## Vectors

- With `scriptorium.toml`, the vector set named by `active_embedder` is the only one read.
  Every row must carry the model tag (`runtime_tag`) and dimension from `embedder.json`;
  a work with rows from another model is invalid and stays out of the index.
- Without `scriptorium.toml` (legacy layout), vectors are `<category>/<key>/embeddings.jsonl`.
- A row is keyed by `chunk_id`, which contains a hash of the chunk text. A vector is valid only
  for the text its id names.

## Index

`pipeline/alexandria_index.py rebuild --library L --db D` writes the SQLite FTS5 index and a
memory-mapped float32 sidecar (`D.vectors.npy`, `.norms.npy`, `.ids.json`, `.meta.json`).
The server only reads them. Build on one host, copy to the serving host: database first,
then sidecar, `meta.json` last.
