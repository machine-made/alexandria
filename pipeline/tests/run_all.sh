#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
export LC_ALL=C
shopt -s nullglob
tests=("$ROOT"/pipeline/tests/test_*.sh)
if ((${#tests[@]} == 0)); then
    echo "No test scripts found" >&2
    exit 1
fi

uv --directory "$ROOT" sync --locked --extra pdf
logs="$(mktemp -d)"
trap 'rm -rf -- "$logs"' EXIT
passed=0
for test in "${tests[@]}"; do
    name="${test##*/}"
    if uv --directory "$ROOT" run --no-sync bash "$test" > "$logs/$name" 2>&1; then
        printf 'PASS %s\n' "$name"
        passed=$((passed + 1))
        # Some suites report optional checks they could not run.
        sed -n "s/^skipped: /SKIP $name: /p" "$logs/$name"
    else
        printf 'FAIL %s\n' "$name"
        cat "$logs/$name" >&2
    fi
done

printf '%d of %d tests passed\n' "$passed" "${#tests[@]}"
((passed == ${#tests[@]}))
