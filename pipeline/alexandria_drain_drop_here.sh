#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PIPELINE="$ROOT/pipeline"
DROP_DIR="${ALEXANDRIA_DROP_DIR:-$ROOT/DROP_HERE}"
PARSED_DIR="${ALEXANDRIA_PARSED_DIR:-$PIPELINE/parsed}"
LOG_FILE="${ALEXANDRIA_DRAIN_LOG:-$PIPELINE/logs/drain-drop-here.log}"
PARSER_CMD="${ALEXANDRIA_PARSER_CMD:-}"
FILE_AFTER_PARSE="${ALEXANDRIA_FILE_AFTER_PARSE:-0}"
LIBRARY_ROOT="${ALEXANDRIA_LIBRARY_ROOT:-$ROOT}"

mkdir -p "$PARSED_DIR" "$(dirname "$LOG_FILE")"
log() { printf '[%s] %s\n' "$(date -Is)" "$*" | tee -a "$LOG_FILE"; }

parse_one() {
    local src="$1" out="$2"
    if [ -n "$PARSER_CMD" ]; then
        "$PARSER_CMD" "$src" "$out"
    else
        printf 'y\n' | "$PIPELINE/secure_parse_c9.sh" --no-encrypt "$src" "$out"
    fi
}

mapfile -t stems < <(
    find "$DROP_DIR" -maxdepth 1 -type f \( -iname '*.pdf' -o -iname '*.epub' \) -printf '%f\n' \
      | sed -E 's/\.[Pp][Dd][Ff]$//; s/\.[Ee][Pp][Uu][Bb]$//' \
      | sort -u
)

if [ "${#stems[@]}" -eq 0 ]; then
    log 'NO_WORK'
    exit 0
fi

stem=""
for candidate in "${stems[@]}"; do
    result="$PARSED_DIR/$candidate/file_result.json"
    if [ -f "$result" ] && python3 - "$result" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as handle:
    status = json.load(handle).get("status")
raise SystemExit(0 if status == "held" else 1)
PY
    then
        log "SKIP_HELD $candidate"
        continue
    fi
    stem="$candidate"
    break
done

if [ -z "$stem" ]; then
    log 'NO_ACTIONABLE_WORK'
    exit 0
fi

pdf=""
epub=""
for candidate in "$DROP_DIR/$stem".*; do
    [ -f "$candidate" ] || continue
    case "${candidate##*.}" in
        [Pp][Dd][Ff]) [ -z "$pdf" ] && pdf="$candidate" ;;
        [Ee][Pp][Uu][Bb]) [ -z "$epub" ] && epub="$candidate" ;;
    esac
done

if [ -n "$pdf" ]; then src="$pdf"; else src="$epub"; fi
[ -n "$src" ] || { log "SKIP_NO_SOURCE $stem"; exit 0; }

out="$PARSED_DIR/$stem"
md="$out/$stem.md"
if [ -s "$md" ]; then
    if [ "$FILE_AFTER_PARSE" != "1" ]; then
        log "SKIP_PARSED $stem"
        exit 0
    fi
    log "FILE_EXISTING_PARSED $stem"
else
    parse_one "$src" "$out"
    [ -s "$md" ] || { log "ERROR_NO_MD $stem"; exit 1; }
    log "PARSED $stem source=$(basename "$src")"
fi

if [ "$FILE_AFTER_PARSE" = "1" ]; then
    ALEXANDRIA_PARSED_WORK="$out" ALEXANDRIA_DROP_DIR="$DROP_DIR" "$PIPELINE/alexandria_metadata_from_markdown.py"
    ALEXANDRIA_PARSED_WORK="$out"     ALEXANDRIA_DROP_DIR="$DROP_DIR"     ALEXANDRIA_LIBRARY_ROOT="$LIBRARY_ROOT"     ALEXANDRIA_FILE_LOG="$LOG_FILE"         "$PIPELINE/alexandria_file_parsed_work.py"
    result="$out/file_result.json"
    if [ -f "$result" ] && python3 - "$result" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as handle:
    status = json.load(handle).get("status")
raise SystemExit(0 if status == "held" else 1)
PY
    then
        log "HELD $stem"
    else
        log "FILED $stem"
    fi
fi
