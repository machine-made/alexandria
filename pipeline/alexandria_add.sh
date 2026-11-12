#!/usr/bin/env bash
# alexandria_add — one command to add a work: file + chunk + embed + index.
# Forwards all args to alexandria_ingest_markdown.py, then embeds the new work's chunks
# with the 8B model (docs raw, 4096-dim) and upserts it into the index. Afterwards copy the
# rebuilt index to the machine that serves it.
#
#   pipeline/alexandria_add.sh --md talk.md --category misc --kind informal \
#       --title "..." --source "Podcast #12" --url https://...
#
# Library defaults to the repo; override with ALEXANDRIA_LIB (used for the demo).
set -euo pipefail
CODE="$(cd "$(dirname "$0")/.." && pwd)"     # repo root (code lives here)
PY="$CODE/.venv/bin/python"
LIB="${ALEXANDRIA_LIB:-$CODE}"               # data library (override for isolated tests)
EMBED_MODEL="${EMBED_MODEL:-qwen3-embedding:8b-q8_0}"
OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://127.0.0.1:11434}"

echo "== 1/3 file + chunk =="
out="$("$PY" "$CODE/pipeline/alexandria_ingest_markdown.py" --library "$LIB" "$@")"
echo "$out"
# parse: "filed <key> category=<cat> confidence=<c>"
read -r key cat <<<"$(awk '/^filed /{k=$2; for(i=1;i<=NF;i++) if($i ~ /^category=/){sub(/category=/,"",$i); c=$i}} END{print k, c}' <<<"$out")"
if [ -z "${key:-}" ] || [ -z "${cat:-}" ]; then
  echo "alexandria_add: nothing to embed (dry-run, or ingest did not file)."; exit 0
fi
work="$LIB/$cat/$key"

echo "== 2/3 embed $key with $EMBED_MODEL (docs raw) =="
s=$(date +%s)
EMBED_MODEL="$EMBED_MODEL" OLLAMA_BASE_URL="$OLLAMA_BASE_URL" \
  ALEXANDRIA_CHUNKS_JSONL="$work/chunks.jsonl" \
  ALEXANDRIA_EMBEDDINGS_JSONL="$work/embeddings.jsonl" \
  "$PY" "$CODE/pipeline/c9_embed_chunks.py"
echo "   embed: $(($(date +%s)-s))s  ($(wc -l < "$work/embeddings.jsonl") vectors)"

echo "== 3/3 index (upsert-work) =="
"$PY" "$CODE/pipeline/alexandria_index.py" upsert-work --key "$key" --category "$cat" --library "$LIB"

echo "== added: $key ($cat). copy the rebuilt index to the serving host =="
